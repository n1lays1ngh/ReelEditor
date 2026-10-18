"""Edit planner: maps the song segment onto the painting's timeline and produces an EDL.

Two strategies share the output grid, the end hold and the segment builder:
  events  (plan_events.py) - visual events (washes, new colours, detail...) aligned to beats by dynamic programming;
                             each cut reveals a visible step of the painting. Default.
  smooth  (plan_smooth.py) - one continuous timelapse whose speed follows the music energy.
Both only ever move forward through the source (validate_edl enforces it).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .config import PlannerConfig
from .models import (EDL, Crop, EDLSegment, SegmentAudio, SongRef, Transition, VideoAnalysis, VideoRef, Zoom,
                     validate_edl)

log = logging.getLogger(__name__)
EPS = 1e-6


@dataclass
class Music:
    length: float
    fps: int
    beats: np.ndarray
    downbeats: np.ndarray
    beat_period: float
    energy_t: np.ndarray
    energy: np.ndarray

    def snap(self, t: float) -> float:
        return round(t * self.fps) / self.fps

    def energy_between(self, t0: float, t1: float) -> float:
        mask = (self.energy_t >= t0) & (self.energy_t <= t1)
        if mask.any():
            return float(self.energy[mask].mean())
        return float(np.interp((t0 + t1) / 2, self.energy_t, self.energy))


@dataclass
class Piece:
    """A planned segment before transitions are decided."""

    out_start: float
    out_end: float
    src_start: float
    src_end: float
    style: str
    energy: float | None = None
    curve: list[tuple[float, float]] | None = None
    event: str | None = None


@dataclass
class Context:
    music: Music
    audio: SegmentAudio
    video: VideoAnalysis
    cfg: PlannerConfig
    src_open: float
    src_final: float
    hold_start: float
    clean_threshold: float
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def plan_edit(audio: SegmentAudio, video: VideoAnalysis, cfg: PlannerConfig, crop: Crop, width: int,
              height: int, clean_threshold: float, meta: dict | None = None) -> EDL:
    from . import plan_events, plan_smooth

    music = make_music(audio, cfg)
    L = music.snap(music.length)
    src_open = opening_source(video, cfg)
    src_final = video.final_time
    if src_final <= src_open:
        src_open = 0.0
    ctx = Context(music, audio, video, cfg, src_open, src_final, hold_start_time(music, cfg, L), clean_threshold)

    strategy = cfg.strategy
    pieces: list[Piece] | None = None
    if strategy == "events":
        pieces = plan_events.plan(ctx)
        if pieces is None:
            ctx.warnings.append("no usable painting events were detected: used a smooth timelapse instead")
            strategy = "smooth"
    if pieces is None:
        pieces = plan_smooth.plan(ctx)
    if ctx.hold_start < L - EPS:
        pieces.append(Piece(ctx.hold_start, L, src_final, src_final, "hold"))

    segments = build_segments(pieces, ctx)
    edl = EDL(
        fps=cfg.fps, width=width, height=height,
        song=SongRef(path=audio.path, start=audio.start, end=audio.start + L),
        video=VideoRef(path=video.path, duration=video.duration, fps=video.fps, width=video.width,
                       height=video.height, keyframe_interval=video.keyframe_interval),
        crop=crop, segments=segments, beats=[round(b, 4) for b in music.beats.tolist()],
        downbeats=[round(b, 4) for b in music.downbeats.tolist()],
        meta={**(meta or {}), **ctx.meta, "strategy": strategy, "src_window": [src_open, src_final],
              "drop": audio.drop, "sections": audio.sections, "warnings": ctx.warnings, "tempo": audio.tempo},
    )
    validate_edl(edl)
    log.info("planned %d segments over %.2fs (%s, source %.1f-%.1fs)", len(segments), L, strategy, src_open,
             src_final)
    return edl


# ------------------------------------------------------------------ shared helpers


def make_music(audio: SegmentAudio, cfg: PlannerConfig) -> Music:
    beats = np.array(audio.beats, float)
    if len(beats) < 2:  # silence / beat tracking failed: fall back to a 120 bpm grid
        beats = np.arange(0.0, audio.duration, 0.5)
    downbeats = np.array(audio.downbeats, float) if audio.downbeats else beats[::4]
    period = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5
    et = np.array(audio.energy_times, float) if audio.energy_times else np.array([0.0, audio.duration])
    ev = np.array(audio.energy, float) if audio.energy else np.array([0.5, 0.5])
    return Music(audio.duration, cfg.fps, beats, downbeats, period, et, ev)


def hold_start_time(music: Music, cfg: PlannerConfig, L: float) -> float:
    e = cfg.ending
    if e.hold_seconds <= 0:
        return L
    target = L - e.hold_seconds
    lo, hi = L - e.hold_max, L - e.hold_min
    for cands in (music.downbeats, music.beats):
        ok = cands[(cands >= lo) & (cands <= hi)]
        if len(ok):
            return music.snap(float(ok[np.argmin(np.abs(ok - target))]))
    return music.snap(max(target, 1.0 / music.fps))


def opening_source(video: VideoAnalysis, cfg: PlannerConfig) -> float:
    prog = np.array(video.progress)
    times = np.array(video.times)
    if cfg.opening.start_progress > 0:
        idx = np.where(prog >= cfg.opening.start_progress)[0]
        t = float(times[idx[0]]) if len(idx) else video.start_time
    else:
        t = video.start_time
    return max(0.0, t - cfg.opening.lead_seconds)


def min_segment(cfg: PlannerConfig) -> float:
    return max(cfg.cuts.min_segment_seconds, 1.0 / cfg.fps)


def beats_per_cut(cfg: PlannerConfig, energy: float) -> float:
    c = cfg.cuts
    return max(1.0, c.max_beats_per_cut + (c.min_beats_per_cut - c.max_beats_per_cut) * float(np.clip(energy, 0, 1)))


def _mode(speed: float, cfg: PlannerConfig) -> str:
    if speed <= EPS:
        return "hold"
    return "realtime" if speed <= cfg.modes.realtime_max_speed else "timelapse"


def _xfade_frames(music: Music, cfg: PlannerConfig) -> int:
    t = cfg.transitions
    if t.xfade_beat_fraction > 0:
        return max(t.xfade_min_frames, int(round(music.beat_period * t.xfade_beat_fraction * cfg.fps)))
    return max(t.xfade_min_frames, t.xfade_frames)


def _transition(frames: int, n_prev: int, n_next: int, cfg: PlannerConfig) -> Transition:
    frames = min(frames, n_prev // 2, n_next // 2)
    if frames < cfg.transitions.xfade_min_frames:
        return Transition()
    return Transition(type="xfade", duration=round(frames / cfg.fps, 6))


def build_segments(pieces: list[Piece], ctx: Context) -> list[EDLSegment]:
    cfg, music, video = ctx.cfg, ctx.music, ctx.video
    fps = cfg.fps
    prog_at = lambda t: round(float(np.interp(t, video.times, video.progress)), 4)  # noqa: E731
    xf = _xfade_frames(music, cfg)
    segs: list[EDLSegment] = []
    for k, p in enumerate(pieces):
        n_frames = int(round((p.out_end - p.out_start) * fps))
        speed = (p.src_end - p.src_start) / max(p.out_end - p.out_start, EPS)
        is_hold = p.style == "hold" or p.src_end - p.src_start <= EPS
        tr = Transition()
        if k > 0:
            prev = pieces[k - 1]
            n_prev = int(round((prev.out_end - prev.out_start) * fps))
            if p.style == "hold":
                if cfg.ending.transition == "xfade":
                    tr = _transition(cfg.ending.transition_frames, n_prev, n_frames, cfg)
            elif cfg.transitions.xfade_enabled:
                e_b = music.energy_between(p.out_start - 0.25, p.out_start + 0.25)
                if e_b < cfg.transitions.xfade_energy_max:
                    tr = _transition(xf, n_prev, n_frames, cfg)
        zoom = Zoom(start=cfg.ending.zoom_from, end=1.0) if p.style == "hold" and cfg.ending.zoom_out else None
        curve = None if is_hold else p.curve
        segs.append(EDLSegment(
            index=k, out_start=round(p.out_start, 6), out_end=round(p.out_end, 6),
            src_start=round(p.src_start, 3), src_end=round(p.src_end, 3),
            mode="hold" if is_hold else _mode(speed, cfg),  # type: ignore[arg-type]
            transition_in=tr, zoom=zoom, curve=curve, style=p.style,  # type: ignore[arg-type]
            energy=None if p.energy is None else round(p.energy, 4),
            progress_start=prog_at(p.src_start), progress_end=prog_at(p.src_end), event=p.event,
        ))
    return segs
