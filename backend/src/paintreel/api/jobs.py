"""Background job manager: a thread pool, in-memory job table, job.json persisted per output dir."""
from __future__ import annotations

import logging
import secrets
import shutil
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from ..config import AppSettings
from ..models import FINAL_STATES, Job, JobKind
from ..pipeline import RenderResult, Reporter
from ..tools import Cancelled

log = logging.getLogger(__name__)

# Overall progress ranges per stage, by job kind.
STAGE_RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "render": {"analyzing": (0.0, 0.4), "planning": (0.4, 0.5), "rendering": (0.5, 1.0)},
    "render_edl": {"rendering": (0.0, 1.0)},
    "analyze": {"analyzing": (0.0, 1.0)},
}
JobFn = Callable[[Path, Reporter, threading.Event], RenderResult]


class JobBusy(RuntimeError):
    pass


class JobManager:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.cancels: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.workers), thread_name_prefix="paintreel-job")
        self._load_history()

    # ------------------------------------------------------------ persistence

    def job_dir(self, job_id: str) -> Path:
        return self.settings.outputs_dir / job_id

    def _persist(self, job: Job) -> None:
        d = self.job_dir(job.id)
        if not d.exists():
            return  # deleted meanwhile
        tmp = d / "job.json.tmp"
        tmp.write_text(job.model_dump_json(indent=2))
        tmp.replace(d / "job.json")

    def _load_history(self) -> None:
        for f in sorted(self.settings.outputs_dir.glob("*/job.json")):
            try:
                job = Job.model_validate_json(f.read_text())
            except Exception:
                continue
            if job.state not in FINAL_STATES:  # server stopped mid-job
                job.state, job.error = "failed", "interrupted (the server was restarted)"
            self.jobs[job.id] = job

    # ------------------------------------------------------------ api

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def list(self, kinds: tuple[str, ...] | None = None) -> list[Job]:
        with self.lock:
            jobs = [j.model_copy(deep=True) for j in self.jobs.values() if not kinds or j.kind in kinds]
        return sorted(jobs, key=lambda j: j.created, reverse=True)

    def active(self, kind: str | None = None) -> list[Job]:
        return [j for j in self.list() if j.state not in FINAL_STATES and (kind is None or j.kind == kind)]

    def submit(self, kind: JobKind, params: BaseModel, fn: JobFn) -> Job:
        now = time.time()
        job_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-" + secrets.token_hex(2)
        job = Job(id=job_id, kind=kind, created=now, updated=now, params=params.model_dump(mode="json"),
                  message="Waiting to start")
        d = self.job_dir(job_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "params.json").write_text(params.model_dump_json(indent=2))
        with self.lock:
            self.jobs[job_id] = job
            self.cancels[job_id] = threading.Event()
        self._persist(job)
        self.pool.submit(self._run, job_id, fn)
        return job.model_copy()

    def cancel(self, job_id: str) -> Job | None:
        with self.lock:
            job = self.jobs.get(job_id)
            ev = self.cancels.get(job_id)
        if job is None:
            return None
        if job.state in FINAL_STATES:
            return job.model_copy()
        if ev is not None:
            ev.set()
        if job.state == "queued":
            self._update(job_id, persist=True, state="cancelled", stage="cancelled", message="Cancelled")
        else:
            self._update(job_id, message="Cancelling…")
        return self.get(job_id)

    def delete(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        if job.state not in FINAL_STATES:
            raise JobBusy("cancel the job before deleting it")
        with self.lock:
            self.jobs.pop(job_id, None)
            self.cancels.pop(job_id, None)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
        return True

    # ------------------------------------------------------------ worker

    def _update(self, job_id: str, persist: bool = False, **fields) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            job.updated = time.time()
            snapshot = job.model_copy(deep=True)
        if persist:
            self._persist(snapshot)

    def _reporter(self, job_id: str, kind: str, cancel: threading.Event) -> Reporter:
        ranges = STAGE_RANGES[kind]

        def report(stage: str, frac: float, msg: str) -> None:
            if cancel.is_set():
                raise Cancelled("cancelled")
            lo, hi = ranges.get(stage, (0.0, 1.0))
            self._update(job_id, state=stage, stage=stage, progress=round(lo + (hi - lo) * min(max(frac, 0), 1), 4),
                         message=msg)

        return report

    def _run(self, job_id: str, fn: JobFn) -> None:
        job = self.get(job_id)
        cancel = self.cancels.get(job_id)
        if job is None or cancel is None or job.state in FINAL_STATES:
            return  # deleted or cancelled while queued
        log.info("job %s (%s) started", job_id, job.kind)
        try:
            result = fn(self.job_dir(job_id), self._reporter(job_id, job.kind, cancel), cancel)
            if cancel.is_set():
                raise Cancelled("cancelled")
            self._update(job_id, persist=True, state="done", stage="done", progress=1.0, message="Done",
                         outputs=result.outputs, summary=result.summary)
            log.info("job %s done", job_id)
        except Cancelled:
            log.info("job %s cancelled", job_id)
            self._update(job_id, persist=True, state="cancelled", stage="cancelled", message="Cancelled")
        except Exception as exc:  # report every failure to the UI
            log.error("job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
            self._update(job_id, persist=True, state="failed", stage="failed", error=_friendly(exc),
                         message="Failed")

    def shutdown(self) -> None:
        for ev in self.cancels.values():
            ev.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


def _friendly(exc: Exception) -> str:
    """A readable message for the UI (the full traceback is in the server log)."""
    text = str(exc).strip() or type(exc).__name__
    if type(exc).__name__ in ("FFmpegError",):
        first = text.splitlines()[0]
        return f"Video processing failed ({first}). Check that the files are valid videos/songs."
    if isinstance(exc, FileNotFoundError):
        return text[0].upper() + text[1:]
    return f"{type(exc).__name__}: {text}" if not isinstance(exc, ValueError) else text
