"""Renderer: EDL -> 9:16 H.264 reel (with song), silent copy, cover image and optional debug overlay version.

Every moving segment is rendered frame-accurately: the source time of each output frame comes from the segment's
speed curve; the frame shown is picked among decoded candidates (the least occluded one inside the frame's time
slot when the timelapse is fast enough for the hand to flicker, otherwise the nearest one).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import threading
from bisect import bisect_right
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import AudioRenderConfig, RenderConfig
from .models import EDL, Crop, EDLSegment, validate_edl
from .tools import Cancelled, FFmpegError, even, ffmpeg, iter_frames, run

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]

OUTPUT_NAMES = {"reel": "reel.mp4", "silent": "reel_silent.mp4", "debug": "reel_debug.mp4", "edl": "edl.json",
                "cover": "cover.jpg"}


def _noop(frac: float, msg: str) -> None:
    pass


@dataclass
class Occlusion:
    """Occlusion curve from the analysis, used to prefer hand-free frames."""

    times: np.ndarray
    values: np.ndarray

    def at(self, t: np.ndarray) -> np.ndarray:
        return np.interp(t, self.times, self.values)


# ------------------------------------------------------------------ geometry


def resolve_crop(canvas: Crop, video_w: int, video_h: int, cfg: RenderConfig) -> Crop:
    """Decide crop vs pad and return the source rectangle actually used."""
    x, y = max(0, min(canvas.x, video_w - 2)), max(0, min(canvas.y, video_h - 2))
    c = Crop(x=x, y=y, w=min(even(canvas.w), (video_w - x) // 2 * 2), h=min(even(canvas.h), (video_h - y) // 2 * 2),
             mode=canvas.mode)
    target = cfg.width / cfg.height
    if c.w / c.h > target:
        cw, ch = c.h * target, float(c.h)
        loss = 1 - cw / c.w
    else:
        cw, ch = float(c.w), c.w / target
        loss = 1 - ch / c.h
    mode = c.mode if c.mode != "auto" else cfg.crop_mode
    if mode == "auto":
        mode = "crop" if loss <= cfg.max_crop_loss else "pad"
    if mode == "pad":
        return c.model_copy(update={"mode": "pad"})
    w, h = min(even(cw), c.w), min(even(ch), c.h)
    return Crop(x=c.x + (c.w - w) // 2, y=c.y + (c.h - h) // 2, w=w, h=h, mode="crop")


def fg_size(crop: Crop, cfg: RenderConfig) -> tuple[int, int]:
    """Size of the painting inside the 9:16 frame (the whole frame when cropping)."""
    if crop.mode == "crop":
        return cfg.width, cfg.height
    scale = min(cfg.width / crop.w, cfg.height / crop.h)
    return min(cfg.width, even(crop.w * scale)), min(cfg.height, even(crop.h * scale))


def source_filter(crop: Crop, cfg: RenderConfig) -> str:
    """Source frame -> painting at its final size."""
    fw, fh = fg_size(crop, cfg)
    return f"crop={crop.w}:{crop.h}:{crop.x}:{crop.y},scale={fw}:{fh}:flags=lanczos,setsar=1"


def compose_filter(crop: Crop, cfg: RenderConfig) -> str:
    """Painting at its final size -> full 9:16 frame (blurred background when padding)."""
    W, H = cfg.width, cfg.height
    if crop.mode == "crop":
        return "setsar=1"
    qw, qh = even(W / 4), even(H / 4)
    return (f"split=2[g_a][g_b];"
            f"[g_a]scale={qw}:{qh}:force_original_aspect_ratio=increase,crop={qw}:{qh},"
            f"boxblur={cfg.blur_radius}:2,scale={W}:{H}[g_bg];"
            f"[g_bg][g_b]overlay=(W-w)/2:(H-h)/2,setsar=1")


def geometry_filter(crop: Crop, cfg: RenderConfig) -> str:
    return f"{source_filter(crop, cfg)},{compose_filter(crop, cfg)}"


def _encode_args(cfg: RenderConfig, preset: str, fps: int) -> list[str]:
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(cfg.crf), "-pix_fmt", "yuv420p",
            "-r", str(fps), "-fps_mode", "cfr"]


# ------------------------------------------------------------------ frame selection


def frame_count(seg: EDLSegment, fps: int) -> int:
    return int(round(seg.out_end * fps)) - int(round(seg.out_start * fps))


def target_times(seg: EDLSegment, fps: int) -> np.ndarray:
    """Source time for every output frame of the segment (frame 0 shows src_start)."""
    n = frame_count(seg, fps)
    return seg.source_at(np.arange(n) / max(n, 1))


@dataclass
class FramePlan:
    rate: float  # candidate frames decoded per source second
    count: int  # candidates to decode
    picks: np.ndarray  # candidate index shown on each output frame (non-decreasing)
    keyframes_only: bool
    clean: bool


def plan_frames(tau: np.ndarray, src_fps: float, keyint: float, cfg: RenderConfig,
                occlusion: Occlusion | None) -> FramePlan:
    """Which decoded candidate each output frame shows. Candidates are at tau[0] + k / rate."""
    n = len(tau)
    span = float(tau[-1] - tau[0])
    steps = np.diff(tau)
    moving = steps[steps > 1e-9]
    mean_step = span / max(n - 1, 1)
    clean = bool(cfg.clean_sampling and occlusion is not None and mean_step >= cfg.clean_min_step)
    per_frame = cfg.candidates_per_frame if clean else 1
    min_step = float(moving.min()) if len(moving) else max(span, 1e-3)
    rate = min(per_frame / min_step, src_fps)
    rate = min(rate, cfg.max_candidates_factor * n / max(span, 1e-6))
    rate = max(rate, 1e-3)
    count = int(np.floor(span * rate + 1e-9)) + 1
    rel = (tau - tau[0]) * rate  # target position in candidate units
    nearest = np.clip(np.rint(rel).astype(int), 0, count - 1)
    if not clean:
        picks = nearest
    else:
        assert occlusion is not None
        edges = np.concatenate([[rel[0]], (rel[:-1] + rel[1:]) / 2, [rel[-1]]])
        picks = nearest.copy()
        cand_occ = occlusion.at(tau[0] + np.arange(count) / rate)
        for f in range(n):
            lo = int(np.ceil(edges[f] - 1e-9))
            hi = int(np.floor(edges[f + 1] - 1e-9)) if f + 1 < n else int(np.floor(edges[f + 1] + 1e-9))
            lo, hi = max(lo, 0), min(hi, count - 1)
            if hi < lo:
                continue
            ks = np.arange(lo, hi + 1)
            half = max((edges[f + 1] - edges[f]) / 2, 1e-6)
            score = cand_occ[ks] + cfg.clean_bias * np.abs(ks - rel[f]) / half + 1e-9 * np.abs(ks - rel[f])
            picks[f] = ks[int(np.argmin(score))]
        picks = np.maximum.accumulate(picks)
    keyframes_only = (1.0 / rate) > cfg.keyframe_skip_factor * max(keyint, 1.0 / max(src_fps, 1e-6))
    return FramePlan(rate=rate, count=count, picks=picks, keyframes_only=keyframes_only, clean=clean)


# ------------------------------------------------------------------ segments


def render_segment(seg: EDLSegment, edl: EDL, cfg: RenderConfig, out: Path, occlusion: Occlusion | None = None,
                   cancel: threading.Event | None = None) -> Path:
    fps = edl.fps
    n = frame_count(seg, fps)
    tau = target_times(seg, fps)
    if seg.mode == "hold" or n < 2 or float(tau[-1] - tau[0]) <= 1e-6:
        return _render_hold(seg, edl, cfg, out, n, float(tau[0]) if n else seg.src_start)
    fp = plan_frames(tau, edl.video.fps, edl.video.keyframe_interval, cfg, occlusion)
    fw, fh = fg_size(edl.crop, cfg)
    span = float(tau[-1] - tau[0])
    vf = f"setpts=PTS-STARTPTS,fps={fp.rate:.6f},{source_filter(edl.crop, cfg)}"
    frames = iter_frames(Path(edl.video.path), fp.rate, fw, fh, keyframes_only=fp.keyframes_only,
                         start=float(tau[0]), duration=span + max(cfg.seek_margin_seconds, 2.0 / fp.rate), vf=vf)
    cmd = ffmpeg("-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{fw}x{fh}", "-r", str(fps), "-i", "-",
                 "-vf", compose_filter(edl.crop, cfg), "-frames:v", str(n),
                 *_encode_args(cfg, cfg.segment_preset, fps), str(out))
    with tempfile.TemporaryFile() as errf:
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errf)
        assert enc.stdin is not None
        written = 0
        last: np.ndarray | None = None
        try:
            for k, frame in enumerate(frames):
                if cancel is not None and cancel.is_set():
                    raise Cancelled("cancelled")
                last = frame
                while written < n and fp.picks[written] == k:
                    enc.stdin.write(memoryview(frame))  # no copy: the frame is already a contiguous buffer
                    written += 1
                if written >= n:
                    break
            if last is None:
                raise FFmpegError(f"segment {seg.index}: no frames decoded at {tau[0]:.2f}s")
            while written < n:  # decoding ended early (end of file): hold the last frame
                enc.stdin.write(memoryview(last))
                written += 1
        except BrokenPipeError:
            pass
        finally:
            frames.close()
            try:
                enc.stdin.close()
            except BrokenPipeError:
                pass
            code = enc.wait()
        if code != 0:
            errf.seek(0)
            raise FFmpegError(f"segment {seg.index} encode failed: {errf.read().decode(errors='replace')[-600:]}")
    return out


def _render_hold(seg: EDLSegment, edl: EDL, cfg: RenderConfig, out: Path, n: int, src_t: float) -> Path:
    fps = edl.fps
    W, H = cfg.width, cfg.height
    still = out.with_suffix(".png")
    src_t = min(max(src_t, 0.0), max(0.0, edl.video.duration - 1.0 / edl.video.fps))
    geom = geometry_filter(edl.crop, cfg)
    run(ffmpeg("-ss", f"{src_t:.3f}", "-i", edl.video.path, "-frames:v", "1", "-vf", geom, str(still)), "hold frame")
    if not still.exists():
        run(ffmpeg("-sseof", "-1", "-i", edl.video.path, "-update", "1", "-frames:v", "1", "-vf", geom, str(still)),
            "hold frame")
    if seg.zoom and n > 1:
        z0, z1 = seg.zoom.start, seg.zoom.end
        vf = (f"scale={2 * W}:{2 * H}:flags=lanczos,zoompan=z='{z0}+({z1}-{z0})*on/{n - 1}'"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps},setsar=1")
    else:
        vf = "setsar=1"
    run(ffmpeg("-loop", "1", "-framerate", str(fps), "-i", str(still), "-vf", vf, "-frames:v", str(max(n, 1)),
               *_encode_args(cfg, cfg.segment_preset, fps), str(out)), f"hold segment {seg.index}")
    return out


# ------------------------------------------------------------------ join


def join_filter(edl: EDL) -> str:
    """concat for runs of cuts, xfade (left side frozen with tpad) between runs: timing stays on the frame grid."""
    fps = edl.fps
    parts = [f"[{k}:v]setpts=PTS-STARTPTS,fps={fps},settb=1/{fps},format=yuv420p,setsar=1[v{k}]"
             for k in range(len(edl.segments))]
    groups: list[list[int]] = []
    for k, seg in enumerate(edl.segments):
        if k == 0 or seg.transition_in.type == "xfade":
            groups.append([k])
        else:
            groups[-1].append(k)
    labels = []
    for g, ks in enumerate(groups):
        if len(ks) == 1:
            labels.append(f"v{ks[0]}")
        else:
            parts.append("".join(f"[v{k}]" for k in ks) + f"concat=n={len(ks)}:v=1:a=0,settb=1/{fps}[g{g}]")
            labels.append(f"g{g}")
    cur = labels[0]
    cum = sum(frame_count(edl.segments[k], fps) for k in groups[0])
    for g in range(1, len(groups)):
        first = edl.segments[groups[g][0]]
        d = first.transition_in.duration
        parts.append(f"[{cur}]tpad=stop_mode=clone:stop_duration={d:.6f},settb=1/{fps}[p{g}]")
        parts.append(f"[p{g}][{labels[g]}]xfade=transition=fade:duration={d:.6f}:offset={cum / fps:.6f},"
                     f"settb=1/{fps}[x{g}]")
        cur = f"x{g}"
        cum += sum(frame_count(edl.segments[k], fps) for k in groups[g])
    parts.append(f"[{cur}]null[vout]")
    return ";".join(parts)


def join_segments(files: list[Path], edl: EDL, cfg: RenderConfig, out: Path, work: Path) -> Path:
    n_total = int(round(edl.duration * edl.fps))
    script = work / "join_filter.txt"
    script.write_text(join_filter(edl))
    inputs = [a for f in files for a in ("-i", str(f))]
    run(ffmpeg(*inputs, "-/filter_complex", str(script), "-map", "[vout]", "-frames:v", str(n_total),
               *_encode_args(cfg, cfg.final_preset, edl.fps), "-profile:v", "high", "-movflags", "+faststart",
               str(out)), "join")
    return out


# ------------------------------------------------------------------ audio


def _loudnorm_measure(song: str, start: float, length: float, pre: str, a: AudioRenderConfig) -> dict:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", song,
           "-af", f"{pre},loudnorm=I={a.lufs}:TP={a.true_peak}:LRA={a.lra}:print_format=json", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr)
    if proc.returncode != 0 or not m:
        raise FFmpegError("loudnorm measurement failed:\n" + proc.stderr[-800:])
    return json.loads(m.group(0))


def render_audio(song: str, start: float, length: float, a: AudioRenderConfig, out: Path) -> Path:
    fo = min(a.fade_out, length / 2)
    pre = f"afade=t=in:st=0:d={min(a.fade_in, length / 2):.3f},afade=t=out:st={length - fo:.3f}:d={fo:.3f}"
    ln = f"loudnorm=I={a.lufs}:TP={a.true_peak}:LRA={a.lra}"
    if a.two_pass:
        m = _loudnorm_measure(song, start, length, pre, a)
        if float(m["input_i"]) > -70:  # silent input can't be measured
            ln += (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                   f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    af = f"{pre},{ln},aresample={a.sample_rate},apad=whole_dur={length:.6f},atrim=end={length:.6f}"
    run(ffmpeg("-ss", f"{start:.3f}", "-t", f"{length + 0.5:.3f}", "-i", song, "-vn", "-af", af,
               "-ar", str(a.sample_rate), "-ac", "2", "-c:a", "aac", "-b:a", a.bitrate, str(out)), "audio")
    return out


def mux(video: Path, audio: Path | None, out: Path) -> Path:
    if audio is None:
        run(ffmpeg("-i", str(video), "-map", "0:v", "-c", "copy", "-movflags", "+faststart", str(out)), "mux silent")
    else:
        run(ffmpeg("-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c", "copy",
                   "-movflags", "+faststart", str(out)), "mux")
    return out


def render_cover(video_only: Path, edl: EDL, cfg: RenderConfig, out: Path) -> Path:
    """Still of the finished painting as framed in the reel (the very last frame, after the zoom-out)."""
    q = max(2, min(31, int(round(2 + (100 - cfg.cover_quality) / 100 * 29))))
    out.unlink(missing_ok=True)
    # decode the last fraction of a second and keep overwriting the image: the last frame wins
    run(ffmpeg("-sseof", "-0.3", "-i", str(video_only), "-vf", "scale=out_range=pc,format=yuvj420p",
               "-update", "1", "-q:v", str(q), str(out)), "cover")
    if not out.exists():
        raise FFmpegError("cover image was not written")
    return out


# ------------------------------------------------------------------ debug overlay


def render_debug(video_only: Path, audio: Path, edl: EDL, cfg: RenderConfig, out: Path, work: Path) -> Path:
    """Overlay beat flashes, segment info, live source time and progress (drawn with OpenCV)."""
    W, H, fps = even(cfg.debug_width), even(cfg.debug_height), edl.fps
    starts = [s.out_start for s in edl.segments]
    beats = np.array(edl.beats)
    downs = np.array(edl.downbeats)
    tmp = work / "debug_video.mp4"
    with tempfile.TemporaryFile() as errf:
        enc = subprocess.Popen(
            ffmpeg("-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(tmp)),
            stdin=subprocess.PIPE, stderr=errf)
        assert enc.stdin is not None
        font = cv2.FONT_HERSHEY_SIMPLEX
        for f, frame in enumerate(iter_frames(video_only, fps, W, H)):
            img = frame.copy()
            t = f / fps
            seg = edl.segments[max(0, bisect_right(starts, t + 1e-6) - 1)]
            local = (t - seg.out_start) / max(seg.out_duration, 1e-6)
            src = float(seg.source_at(local))
            p0, p1 = seg.progress_start or 0.0, seg.progress_end or 0.0
            span = seg.src_end - seg.src_start
            prog = p0 + ((src - seg.src_start) / span if span > 1e-6 else 0.0) * (p1 - p0)
            cv2.rectangle(img, (0, 0), (W, 124), (0, 0, 0), -1)
            lines = [f"seg {seg.index}/{len(edl.segments) - 1}  {seg.style or seg.mode}  avg {seg.speed:.1f}x",
                     f"t {t:6.2f}s  src {int(src // 60):02d}:{src % 60:05.2f}",
                     f"progress {prog:.2f}  energy {seg.energy if seg.energy is not None else '-'}",
                     (seg.event or "")[:40]]
            for i, line in enumerate(lines):
                cv2.putText(img, line, (10, 26 + 28 * i), font, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
            if seg.transition_in.type == "xfade" and t - seg.out_start < seg.transition_in.duration:
                cv2.putText(img, "XFADE", (W - 100, 26), font, 0.62, (0, 200, 255), 2, cv2.LINE_AA)
            if abs(t - seg.out_start) < 0.5 / fps and seg.index > 0:
                cv2.rectangle(img, (0, 0), (W - 1, H - 1), (0, 0, 255), 8)  # cut frame: red border
            near = lambda arr: len(arr) and np.min(np.abs(arr - t)) < 1.5 / fps  # noqa: E731
            if near(downs):
                cv2.rectangle(img, (0, H - 60), (W, H - 30), (0, 255, 255), -1)
            elif near(beats):
                cv2.rectangle(img, (0, H - 50), (W, H - 30), (255, 255, 255), -1)
            cv2.rectangle(img, (0, H - 24), (W, H), (40, 40, 40), -1)
            cv2.rectangle(img, (0, H - 24), (int(W * min(max(prog, 0), 1)), H), (80, 200, 80), -1)
            enc.stdin.write(img.tobytes())
        enc.stdin.close()
        if enc.wait() != 0:
            errf.seek(0)
            raise FFmpegError("debug encode failed: " + errf.read().decode(errors="replace")[-500:])
    return mux(tmp, audio, out)


# ------------------------------------------------------------------ top level


def render_edl(edl: EDL, cfg: RenderConfig, out_dir: Path, debug: bool = False, progress: Progress = _noop,
               occlusion: Occlusion | None = None, cancel: threading.Event | None = None) -> dict[str, str]:
    validate_edl(edl)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    (out_dir / OUTPUT_NAMES["edl"]).write_text(edl.model_dump_json(indent=2))

    n = len(edl.segments)
    files = [work / f"seg_{k:04d}.mp4" for k in range(n)]
    cancel = cancel or threading.Event()  # also used to stop running segments when one of them fails
    pool = ThreadPoolExecutor(max_workers=max(1, cfg.parallel_segments))
    futures: list[Future] = [pool.submit(render_segment, edl.segments[k], edl, cfg, files[k], occlusion, cancel)
                             for k in range(n)]
    try:
        for done, fut in enumerate(futures, start=1):
            fut.result()
            progress(0.75 * done / n, f"Cutting to the beat ({done}/{n} segments)")
    except BaseException:
        cancel.set()  # stop segments that are already running
        for fut in futures:
            fut.cancel()
        raise
    finally:
        pool.shutdown(wait=True)
    progress(0.78, "Joining segments and transitions")
    video_only = join_segments(files, edl, cfg, work / "video_only.mp4", work)
    progress(0.9, "Mixing the audio")
    audio = render_audio(edl.song.path, edl.song.start, edl.duration, cfg.audio, work / "audio.m4a")
    mux(video_only, audio, out_dir / OUTPUT_NAMES["reel"])
    mux(video_only, None, out_dir / OUTPUT_NAMES["silent"])
    render_cover(video_only, edl, cfg, out_dir / OUTPUT_NAMES["cover"])
    outputs = {k: OUTPUT_NAMES[k] for k in ("reel", "silent", "edl", "cover")}
    if debug:
        progress(0.94, "Drawing the debug overlay")
        render_debug(video_only, audio, edl, cfg, out_dir / OUTPUT_NAMES["debug"], work)
        outputs["debug"] = OUTPUT_NAMES["debug"]
    for f in files:  # keep video_only/audio for inspection, drop the per-segment files
        f.unlink(missing_ok=True)
        f.with_suffix(".png").unlink(missing_ok=True)
    progress(1.0, "Finalizing")
    return outputs
