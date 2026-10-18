"""FastAPI app for the web UI. Thin layer: all work happens in paintreel.pipeline."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import cv2
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ValidationError

from .. import __version__, pipeline
from ..cache import fingerprint
from ..config import (AppSettings, app_settings, control_default, controls, preset_info, resolve, resolve_dict)
from ..models import FINAL_STATES, AnalyzeParams, Job, JobParams, RenderEdlParams, validate_edl
from ..render import OUTPUT_NAMES
from ..tools import extract_frame, ffmpeg_status, probe_video
from ..video_analysis import load_cached, sample_image
from .files import PathGuard, UploadTooLarge, delete_asset, list_media, save_upload
from .jobs import JobBusy, JobManager

log = logging.getLogger(__name__)


class OverviewRequest(BaseModel):
    path: str


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """App factory (run with `uvicorn paintreel.api.app:create_app --factory`)."""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            datefmt="%H:%M:%S")
    settings = settings or app_settings()
    manager = JobManager(settings)
    guard = PathGuard(settings)
    for job in manager.list():  # paths used by earlier jobs stay playable
        for key in ("video", "song"):
            if isinstance(job.params.get(key), str):
                guard.allow(job.params[key])

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        manager.shutdown()

    app = FastAPI(title="PaintReel", version=__version__, lifespan=lifespan)
    app.state.manager = manager
    app.state.settings = settings

    def require_file(path: str, what: str) -> Path:
        p = Path(path).expanduser()
        if not p.is_file():
            raise HTTPException(400, f"{what} not found: {p.name}")
        try:
            return guard.check(p)
        except PermissionError as exc:
            raise HTTPException(403, f"{what} is outside the uploads/media folders") from exc

    def check_config(preset: str | None, overrides: dict[str, Any], control_values: dict[str, float] | None = None):
        try:
            resolve(preset, overrides, control_values)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, f"invalid settings: {exc}") from exc

    def analysis_cfg():
        return resolve().analysis

    # -------------------------------------------------------------- info

    @app.get("/api/health")
    def health() -> dict:
        return {"version": __version__, "ffmpeg": ffmpeg_status(), "max_upload_mb": settings.max_upload_mb}

    @app.get("/api/presets")
    def presets() -> dict:
        infos = preset_info()
        resolved = {i.name: resolve_dict(i.name) for i in infos}
        ctrl = []
        for c in controls():
            ctrl.append({**c.model_dump(exclude={"targets", "default", "default_from"}),
                         "defaults": {name: round(control_default(c, cfg), 4) for name, cfg in resolved.items()}})
        return {"presets": [i.model_dump() for i in infos], "default": infos[0].name if infos else "energetic",
                "controls": ctrl, "max_crop_loss": {n: r["render"]["max_crop_loss"] for n, r in resolved.items()},
                "zoom_out": {n: r["planner"]["ending"]["zoom_out"] for n, r in resolved.items()}}

    # -------------------------------------------------------------- assets

    @app.get("/api/assets")
    def assets(kind: Literal["video", "song"]) -> list[dict]:
        items = list_media(settings, kind)
        if kind == "video":
            cfg = analysis_cfg()
            for it in items:
                va = load_cached(Path(it["path"]), cfg, settings.cache_dir)
                it["analyzed"] = va is not None
                it["final_time"] = va.final_time if va else None
                it["events"] = len(va.events) if va else None
        for it in items:
            guard.allow(it["path"])
        return items

    @app.post("/api/upload")
    async def upload(kind: Literal["video", "song"] = Form(...), file: UploadFile = File(...)) -> dict:
        try:
            dest = await save_upload(settings, kind, file)
        except UploadTooLarge as exc:
            raise HTTPException(413, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(415, str(exc)) from exc
        guard.allow(dest)
        return {"path": str(dest), "name": dest.name, "size": dest.stat().st_size}

    @app.delete("/api/assets")
    def remove_asset(path: str) -> dict:
        busy = [j for j in manager.active() if path in (j.params.get("video"), j.params.get("song"))]
        if busy:
            raise HTTPException(409, "this file is used by a running job")
        try:
            delete_asset(settings, path)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"deleted": path}

    @app.get("/api/media")
    def media(path: str) -> FileResponse:
        try:
            p = guard.check(path)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        if not p.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(p)

    # -------------------------------------------------------------- song / video helpers

    @app.post("/api/songs/overview")
    def song_overview(req: OverviewRequest) -> dict:
        p = require_file(req.path, "song")
        try:
            ov = pipeline.get_song_overview(p, resolve(), settings)
        except Exception as exc:
            raise HTTPException(422, f"could not read this song: {exc}") from exc
        return ov.model_dump()

    @app.get("/api/video/info")
    def video_info(path: str) -> dict:
        p = require_file(path, "video")
        try:
            meta = probe_video(p)
        except Exception as exc:
            raise HTTPException(422, f"could not read this video: {exc}") from exc
        va = load_cached(p, analysis_cfg(), settings.cache_dir)
        return {"width": meta.width, "height": meta.height, "duration": meta.duration, "fps": meta.fps,
                "analysis": pipeline.analysis_summary(va) if va else None}

    @app.get("/api/video/analysis")
    def video_analysis(path: str) -> dict | None:
        """Curves, events and summary of the analysis; null while the video hasn't been analyzed."""
        p = require_file(path, "video")
        va = load_cached(p, analysis_cfg(), settings.cache_dir)
        return pipeline.analysis_curves(va) if va else None

    @app.get("/api/video/frame")
    def video_frame(path: str, t: float = 0.0, width: int = Query(640, ge=16, le=1920)) -> FileResponse:
        p = require_file(path, "video")
        out = settings.cache_dir / "frames" / f"{fingerprint(p)}-{t:.2f}-{width}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            extract_frame(p, max(0.0, t), out, width)
        return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/video/thumb")
    def video_thumb(path: str, t: float = 0.0, clean: bool = True, width: int = Query(240, ge=16, le=640)) -> Response:
        """Fast thumbnail from the analysis cache (clean = hand-free canvas)."""
        p = require_file(path, "video")
        try:
            img = sample_image(p, analysis_cfg(), settings.cache_dir, t, clean)
        except FileNotFoundError as exc:
            raise HTTPException(404, "not analyzed yet") from exc
        h = max(2, int(round(img.shape[0] * width / img.shape[1])))
        img = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA if width < img.shape[1] else cv2.INTER_CUBIC)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise HTTPException(500, "could not encode thumbnail")
        return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    # -------------------------------------------------------------- planning preview

    @app.post("/api/plan")
    def plan_preview(params: JobParams) -> dict:
        require_file(params.video, "video")
        require_file(params.song, "song")
        check_config(params.preset, params.overrides, params.controls)
        try:
            edl, va = pipeline.preview_plan(params, settings)
        except pipeline.NotAnalyzed as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        board = [{"index": s.index, "out_start": s.out_start, "out_end": s.out_end, "style": s.style,
                  "mode": s.mode, "event": s.event, "transition": s.transition_in.type,
                  "src_start": s.src_start, "src_end": s.src_end, "speed": round(s.speed, 2),
                  "progress": s.progress_end} for s in edl.segments]
        return {"edl": edl.model_dump(), "storyboard": board, "events_total": len(va.events),
                "summary": pipeline.edl_summary(edl, {})}

    # -------------------------------------------------------------- jobs

    @app.post("/api/jobs/analyze")
    def start_analyze(params: AnalyzeParams) -> Job:
        p = require_file(params.video, "video")
        check_config(params.preset, params.overrides)
        params.video = str(p)
        for j in manager.active("analyze"):  # don't analyze the same video twice at once
            if j.params.get("video") == params.video:
                return j
        return manager.submit("analyze", params,
                              lambda out, rep, cancel: pipeline.run_analysis_job(params, out, settings, rep))

    @app.post("/api/jobs/render")
    def start_render(params: JobParams) -> Job:
        params.video = str(require_file(params.video, "video"))
        params.song = str(require_file(params.song, "song"))
        check_config(params.preset, params.overrides, params.controls)
        return manager.submit("render", params,
                              lambda out, rep, cancel: pipeline.run_render_job(params, out, settings, rep, cancel))

    @app.post("/api/jobs/render-edl")
    def start_render_edl(params: RenderEdlParams) -> Job:
        require_file(params.edl.video.path, "video")
        require_file(params.edl.song.path, "song")
        check_config(params.preset, params.overrides)
        try:
            validate_edl(params.edl)
        except ValueError as exc:
            raise HTTPException(422, f"invalid EDL: {exc}") from exc
        return manager.submit("render_edl", params,
                              lambda out, rep, cancel: pipeline.run_render_edl(params, out, settings, rep, cancel))

    @app.get("/api/jobs")
    def jobs(limit: int = 100) -> list[Job]:
        return manager.list()[:limit]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> Job:
        job = manager.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> Job:
        job = manager.cancel(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request) -> StreamingResponse:
        if not manager.get(job_id):
            raise HTTPException(404, "job not found")

        async def stream():
            last = None
            idle = 0
            while True:
                if await request.is_disconnected():
                    break
                job = manager.get(job_id)
                if job is None:
                    break
                data = job.model_dump_json()
                if data != last:
                    last, idle = data, 0
                    yield f"event: job\ndata: {data}\n\n"
                    if job.state in FINAL_STATES:
                        break
                else:
                    idle += 1
                    if idle % 50 == 0:
                        yield ": keepalive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/jobs/{job_id}/files/{name}")
    def job_file(job_id: str, name: str) -> FileResponse:
        job = manager.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        allowed = set(job.outputs.values()) | {"params.json", "job.json", *OUTPUT_NAMES.values()}
        if name not in allowed or "/" in name:
            raise HTTPException(404, "unknown output")
        f = manager.job_dir(job_id) / name
        if not f.is_file():
            raise HTTPException(404, "output not found")
        return FileResponse(f)

    @app.get("/api/renders")
    def renders(limit: int = 100) -> list[Job]:
        return manager.list(("render", "render_edl"))[:limit]

    @app.delete("/api/renders/{job_id}")
    def delete_render(job_id: str) -> dict:
        try:
            ok = manager.delete(job_id)
        except JobBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        if not ok:
            raise HTTPException(404, "job not found")
        return {"deleted": job_id}

    return app
