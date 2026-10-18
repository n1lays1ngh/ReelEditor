"""Shared pipeline entry points. The CLI and the API call only these."""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import audio as audio_mod
from . import plots, planner, render, video_analysis
from .config import AppSettings, PipelineConfig, app_settings, resolve
from .models import EDL, AnalyzeParams, JobParams, RenderEdlParams, SegmentAudio, SongOverview, VideoAnalysis
from .tools import require_ffmpeg

log = logging.getLogger(__name__)
Reporter = Callable[[str, float, str], None]  # (stage, fraction 0..1 within stage, message)


def _quiet(stage: str, frac: float, msg: str) -> None:
    log.info("[%s %3.0f%%] %s", stage, frac * 100, msg)


@dataclass
class RenderResult:
    outputs: dict[str, str]
    summary: dict[str, Any] = field(default_factory=dict)


class NotAnalyzed(RuntimeError):
    pass


def _existing(path: str | Path, what: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"{what} not found: {p}")
    return p


def job_config(params: JobParams) -> PipelineConfig:
    """Preset <- simple controls <- explicit overrides."""
    return resolve(params.preset, params.overrides, params.controls)


# ------------------------------------------------------------------ analysis


def get_song_overview(song: str | Path, cfg: PipelineConfig | None = None,
                      settings: AppSettings | None = None) -> SongOverview:
    cfg = cfg or resolve()
    settings = settings or app_settings()
    return audio_mod.song_overview(_existing(song, "song"), cfg.audio, settings.cache_dir)


def run_analysis(video: str | Path, cfg: PipelineConfig | None = None, settings: AppSettings | None = None,
                 report: Reporter = _quiet, force: bool = False) -> VideoAnalysis:
    require_ffmpeg()
    cfg = cfg or resolve()
    settings = settings or app_settings()
    path = _existing(video, "video")
    va = video_analysis.analyze_video(path, cfg.analysis, settings.cache_dir,
                                      lambda f, m: report("analyzing", f, m), force=force)
    plot = analysis_plot_path(path, cfg, settings)
    if not plot.exists():
        plots.plot_analysis(va, plot)
    return va


def cached_analysis(video: str | Path, cfg: PipelineConfig | None = None,
                    settings: AppSettings | None = None) -> VideoAnalysis | None:
    cfg = cfg or resolve()
    settings = settings or app_settings()
    return video_analysis.load_cached(_existing(video, "video"), cfg.analysis, settings.cache_dir)


def analysis_plot_path(video: str | Path, cfg: PipelineConfig, settings: AppSettings) -> Path:
    return video_analysis.cache_dir_for(Path(video), cfg.analysis, settings.cache_dir) / "analysis.png"


def analysis_summary(va: VideoAnalysis) -> dict[str, Any]:
    return {
        "duration": va.duration, "width": va.width, "height": va.height, "fps": va.fps,
        "canvas": va.canvas.model_dump(), "canvas_detected": va.canvas_detected,
        "final_time": va.final_time, "complete_time": va.complete_time, "start_time": va.start_time,
        "keyframe_interval": va.keyframe_interval, "keyframe_decode": va.keyframe_decode,
        "bad_segments": [b.model_dump() for b in va.bad_segments], "analysis_seconds": va.analysis_seconds,
        "samples": len(va.times), "events": len(va.events),
        "event_kinds": dict(Counter(e.kind for e in va.events)),
    }


def analysis_curves(va: VideoAnalysis, max_points: int = 600) -> dict[str, Any]:
    """Summary plus downsampled curves and events (for the UI timeline)."""
    n = len(va.times)
    step = max(1, int(np.ceil(n / max_points)))
    idx = np.arange(0, n, step)

    def pick(values: list[float]) -> list[float]:
        arr = np.asarray(values, float)
        # max over each bucket keeps short peaks visible
        return [round(float(arr[i:i + step].max()), 4) for i in idx]

    return {
        **analysis_summary(va),
        "times": [va.times[i] for i in idx],
        "progress": [va.progress[min(i + step - 1, n - 1)] for i in idx],
        "activity": pick(va.activity),
        "occlusion": pick(va.occlusion),
        "detail": pick(va.detail),
        "colorfulness": pick(va.colorfulness),
        "visual_change": pick(va.visual_change),
        "events_list": [e.model_dump() for e in va.events],
    }


def run_analysis_job(params: AnalyzeParams, out_dir: Path, settings: AppSettings | None = None,
                     report: Reporter = _quiet, force: bool = False) -> RenderResult:
    settings = settings or app_settings()
    cfg = resolve(params.preset, params.overrides)
    va = run_analysis(params.video, cfg, settings, report, force=force)
    out_dir.mkdir(parents=True, exist_ok=True)
    plots.plot_analysis(va, out_dir / "analysis.png")
    return RenderResult({"analysis_plot": "analysis.png"}, analysis_summary(va))


# ------------------------------------------------------------------ planning


def song_segment(song: str | Path, start: float | None, end: float | None, cfg: PipelineConfig,
                 settings: AppSettings) -> tuple[SongOverview, SegmentAudio]:
    ov = get_song_overview(song, cfg, settings)
    if start is None and end is None:
        start, end = ov.suggested_start, ov.suggested_end
    elif start is None:
        start = max(0.0, float(end) - cfg.audio.auto_segment_seconds)
    elif end is None:
        end = min(ov.duration, start + cfg.audio.auto_segment_seconds)
    start, end = float(start), float(min(end, ov.duration))
    if end - start < 1.0:
        raise ValueError(f"the song segment is too short ({end - start:.2f}s)")
    seg = audio_mod.analyze_segment(Path(song), start, end, cfg.audio, ov, settings.cache_dir)
    return ov, seg


def _plan(params: JobParams, cfg: PipelineConfig, settings: AppSettings, va: VideoAnalysis,
          report: Reporter) -> tuple[EDL, SegmentAudio]:
    report("planning", 0.1, "Listening to the song segment")
    _, seg = song_segment(params.song, params.start, params.end, cfg, settings)
    report("planning", 0.6, "Matching painting moments to the beats")
    canvas = params.crop or va.canvas
    crop = render.resolve_crop(canvas, va.width, va.height, cfg.render)
    edl = planner.plan_edit(seg, va, cfg.planner, crop, cfg.render.width, cfg.render.height,
                            cfg.analysis.clean_threshold,
                            meta={"preset": params.preset, "controls": params.controls, "overrides": params.overrides,
                                  "manual_crop": params.crop is not None})
    report("planning", 1.0, f"Planned {len(edl.segments)} segments")
    return edl, seg


def plan_job(params: JobParams, cfg: PipelineConfig, settings: AppSettings,
             report: Reporter = _quiet) -> tuple[EDL, VideoAnalysis, SegmentAudio]:
    va = run_analysis(params.video, cfg, settings, report)
    edl, seg = _plan(params, cfg, settings, va, report)
    return edl, va, seg


def preview_plan(params: JobParams, settings: AppSettings | None = None) -> tuple[EDL, VideoAnalysis]:
    """Plan without rendering (needs a finished analysis; never starts one)."""
    settings = settings or app_settings()
    cfg = job_config(params)
    va = video_analysis.load_cached(_existing(params.video, "video"), cfg.analysis, settings.cache_dir)
    if va is None:
        raise NotAnalyzed("the painting video has not been analyzed yet")
    _existing(params.song, "song")
    edl, _ = _plan(params, cfg, settings, va, lambda *_: None)
    return edl, va


# ------------------------------------------------------------------ rendering


def occlusion_for(va: VideoAnalysis | None) -> render.Occlusion | None:
    if va is None:
        return None
    return render.Occlusion(np.asarray(va.times, float), np.asarray(va.occlusion, float))


def edl_summary(edl: EDL, extra: dict[str, Any]) -> dict[str, Any]:
    speeds = [s.speed for s in edl.segments if s.mode != "hold"]
    return {
        "segments": len(edl.segments),
        "duration": round(edl.duration, 3),
        "song_start": edl.song.start,
        "song_end": edl.song.end,
        "max_speed": round(max(speeds), 1) if speeds else 0,
        "min_speed": round(min(speeds), 2) if speeds else 0,
        "xfades": sum(s.transition_in.type == "xfade" for s in edl.segments),
        "styles": dict(Counter(s.style or s.mode for s in edl.segments)),
        "events_shown": len(edl.meta.get("events_shown", [])),
        "strategy": edl.meta.get("strategy"),
        "crop_mode": edl.crop.mode,
        "warnings": edl.meta.get("warnings", []),
        **extra,
    }


def run_render_job(params: JobParams, out_dir: Path, settings: AppSettings | None = None,
                   report: Reporter = _quiet, cancel: threading.Event | None = None) -> RenderResult:
    require_ffmpeg()
    settings = settings or app_settings()
    cfg = job_config(params)
    t0 = time.time()
    edl, va, seg = plan_job(params, cfg, settings, report)
    t_plan = time.time() - t0
    out_dir.mkdir(parents=True, exist_ok=True)
    report("rendering", 0.0, "Cutting to the beat")
    outputs = render.render_edl(edl, cfg.render, out_dir, params.debug, lambda f, m: report("rendering", f, m),
                                occlusion_for(va), cancel)
    outputs["mapping_plot"] = plots.plot_mapping(edl, out_dir / "mapping.png", va, seg).name
    outputs["analysis_plot"] = plots.plot_analysis(va, out_dir / "analysis.png").name
    return RenderResult(outputs, edl_summary(edl, {"plan_seconds": round(t_plan, 2),
                                               "render_seconds": round(time.time() - t0 - t_plan, 2),
                                               "analysis_seconds": va.analysis_seconds}))


def run_render_edl(params: RenderEdlParams, out_dir: Path, settings: AppSettings | None = None,
                   report: Reporter = _quiet, cancel: threading.Event | None = None) -> RenderResult:
    """Render an (edited) EDL directly, skipping analysis and planning."""
    require_ffmpeg()
    settings = settings or app_settings()
    cfg = resolve(params.preset, params.overrides)
    edl = params.edl
    _existing(edl.video.path, "video")
    _existing(edl.song.path, "song")
    t0 = time.time()
    report("rendering", 0.0, "Rendering the edited plan")
    va = video_analysis.load_cached(Path(edl.video.path), cfg.analysis, settings.cache_dir)
    outputs = render.render_edl(edl, cfg.render, out_dir, params.debug, lambda f, m: report("rendering", f, m),
                                occlusion_for(va), cancel)
    outputs["mapping_plot"] = plots.plot_mapping(edl, out_dir / "mapping.png", va).name
    if va:
        outputs["analysis_plot"] = plots.plot_analysis(va, out_dir / "analysis.png").name
    return RenderResult(outputs, edl_summary(edl, {"render_seconds": round(time.time() - t0, 2),
                                                   "from_edl": True}))
