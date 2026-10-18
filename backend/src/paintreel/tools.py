"""ffmpeg/ffprobe helpers: availability check, probing, running and frame piping."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class FFmpegMissing(RuntimeError):
    pass


class FFmpegError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    """Raised inside long-running steps when the job was cancelled."""


INSTALL_HINT = "Install it (macOS: `brew install ffmpeg`, Ubuntu: `sudo apt install ffmpeg`) and make sure it is on PATH."


def ffmpeg_status() -> dict[str, str | bool | None]:
    """Report whether ffmpeg and ffprobe are available (for the CLI and /api/health)."""
    status: dict[str, str | bool | None] = {"ok": True, "ffmpeg": None, "ffprobe": None, "error": None}
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if not path:
            status["ok"] = False
            status["error"] = f"{tool} was not found. {INSTALL_HINT}"
            continue
        out = subprocess.run([path, "-version"], capture_output=True, text=True).stdout
        status[tool] = out.splitlines()[0] if out else path
    return status


def require_ffmpeg() -> None:
    status = ffmpeg_status()
    if not status["ok"]:
        raise FFmpegMissing(str(status["error"]))


def run(cmd: list[str], what: str = "ffmpeg") -> str:
    """Run a command, raise FFmpegError with the tail of stderr on failure."""
    log.debug("run: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"{what} failed (exit {proc.returncode}):\n{tail}")
    return proc.stdout


def ffmpeg(*args: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]


@dataclass
class VideoMeta:
    duration: float
    fps: float
    width: int  # display width (after rotation)
    height: int
    codec: str


def _rate(s: str | None) -> float:
    if not s or s == "0/0":
        return 0.0
    num, _, den = s.partition("/")
    return float(num) / float(den or 1)


def probe_video(path: Path) -> VideoMeta:
    out = run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate,r_frame_rate,codec_name,duration:stream_side_data=rotation"
         ":stream_tags=rotate:format=duration", "-of", "json", str(path)],
        "ffprobe",
    )
    data = json.loads(out)
    if not data.get("streams"):
        raise FFmpegError(f"{path} has no video stream")
    st = data["streams"][0]
    w, h = int(st["width"]), int(st["height"])
    rotation = 0
    for sd in st.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    rotation = rotation or int(st.get("tags", {}).get("rotate", 0) or 0)
    if abs(rotation) % 180 == 90:
        w, h = h, w
    fps = _rate(st.get("avg_frame_rate")) or _rate(st.get("r_frame_rate")) or 30.0
    duration = float(st.get("duration") or data.get("format", {}).get("duration") or 0)
    return VideoMeta(duration=duration, fps=fps, width=w, height=h, codec=st.get("codec_name", "?"))


def probe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], "ffprobe")
    return float(json.loads(out)["format"]["duration"])


def keyframe_interval(path: Path, probe_seconds: float = 120.0) -> float:
    """Median spacing of keyframes in the first probe_seconds (packet flags, no decoding)."""
    out = run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", f"%+{probe_seconds}",
         "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(path)],
        "ffprobe",
    )
    times = []
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and "K" in parts[1] and parts[0] not in ("", "N/A"):
            times.append(float(parts[0]))
    times = sorted(times)
    if len(times) < 2:
        return probe_seconds
    return float(np.median(np.diff(times)))


def iter_frames(
    path: Path, fps: float, width: int, height: int, keyframes_only: bool = False,
    start: float = 0.0, duration: float | None = None, vf: str | None = None,
) -> Iterator[np.ndarray]:
    """Yield BGR frames piped from ffmpeg (never the whole video in memory).

    Default filter: sample at `fps` and scale to width x height. A custom `vf` must output width x height.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]
    if keyframes_only:
        cmd += ["-skip_frame", "nokey"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    filt = vf or f"fps={fps},scale={width}:{height}:flags=area"
    cmd += ["-i", str(path), "-an", "-vf", filt, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    frame_bytes = width * height * 3
    with tempfile.TemporaryFile() as errf:  # a file, not a pipe: a chatty ffmpeg can never block on stderr
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, bufsize=frame_bytes * 2)
        assert proc.stdout is not None
        stopped_early = False
        try:
            while True:
                buf = proc.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                try:
                    yield np.frombuffer(buf, np.uint8).reshape(height, width, 3)
                except GeneratorExit:
                    stopped_early = True  # the consumer has what it needs; ffmpeg's broken pipe is expected
                    raise
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if proc.returncode not in (0, -9) and not stopped_early:
                errf.seek(0)
                err = errf.read().decode(errors="replace").strip()
                if err:
                    log.warning("ffmpeg frame pipe: %s", err[-500:])


def extract_frame(path: Path, t: float, out: Path, width: int | None = None) -> Path:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    run(ffmpeg("-ss", f"{max(t, 0):.3f}", "-i", str(path), "-frames:v", "1", *vf, str(out)), "frame extract")
    if not out.exists():  # seeking past the last frame yields nothing; take the last decodable one
        run(ffmpeg("-sseof", "-1", "-i", str(path), "-update", "1", "-frames:v", "1", *vf, str(out)), "frame extract")
    return out


def even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)
