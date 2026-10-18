"""Smooth strategy: one continuous timelapse whose speed follows the music energy.

  1. cut points walked over beats, density driven by energy
  2. a "content clock" C(s) over source time: usable seconds weighted by activity, zero on bad segments
  3. each chunk consumes content proportional to rate(energy) * duration, scaled to use the whole painting
  4. chunk boundaries -> source times via C^-1 (monotonic => chronological); bad segments jumped over
  5. occluded cut source times nudged to clean (low-occlusion) samples
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import PlannerConfig
from .models import SegmentAudio, VideoAnalysis
from .planner import EPS, Context, Music, Piece, min_segment

# ------------------------------------------------------------------ content clock


@dataclass
class Clock:
    """Content clock over source time, sampled at the analysis sample times."""

    times: np.ndarray
    C: np.ndarray
    bad: list[tuple[float, float]]

    @property
    def total(self) -> float:
        return float(self.C[-1])

    def at(self, t: float) -> float:
        return float(np.interp(t, self.times, self.C))

    def inv_left(self, c: float) -> float:
        """Earliest source time with C(t) >= c."""
        c = float(np.clip(c, 0.0, self.total))
        k = int(np.searchsorted(self.C, c - EPS, side="left"))
        if k <= 0:
            return float(self.times[0])
        if k >= len(self.C):
            return float(self.times[-1])
        c0, c1 = self.C[k - 1], self.C[k]
        frac = 0.0 if c1 <= c0 else (c - c0) / (c1 - c0)
        return float(self.times[k - 1] + np.clip(frac, 0, 1) * (self.times[k] - self.times[k - 1]))

    def inv_right(self, c: float) -> float:
        """Latest source time with C(t) <= c (end of a plateau)."""
        k = int(np.searchsorted(self.C, c + EPS, side="right")) - 1
        return float(self.times[max(0, min(k, len(self.times) - 1))])


@dataclass
class _Plan:
    bounds: list[float]  # output times of chunk boundaries, incl. start and hold start
    starts: list[float] = field(default_factory=list)  # source start per chunk
    ends: list[float] = field(default_factory=list)
    snapped: set[int] = field(default_factory=set)  # boundary indices placed on a bad-segment jump


def plan(ctx: Context) -> list[Piece]:
    cfg, music, video = ctx.cfg, ctx.music, ctx.video
    open_end = 0.0
    if cfg.opening.hold_beats > 0 and len(music.beats) > cfg.opening.hold_beats:
        open_end = music.snap(music.beats[cfg.opening.hold_beats])
    clock = content_clock(video, cfg, ctx.src_open, ctx.src_final)
    forced = forced_cuts(ctx.audio, cfg)
    for _ in range(6):
        warnings: list[str] = []
        p = _Plan(bounds=cut_points(music, cfg, open_end, ctx.hold_start, forced))
        durations = np.diff(p.bounds)
        energies = np.array([music.energy_between(a, b) for a, b in zip(p.bounds[:-1], p.bounds[1:])])
        content = _allocate_content(p.bounds, durations, energies, clock, video, ctx.audio, cfg, warnings)
        missed = _map_to_source(p, content, clock)
        extra = _cuts_for(missed, content, p.bounds, music, cfg)
        if not extra:
            break
        forced = sorted(set(forced) | set(extra))  # cut on the beat where the edit reaches each excluded stretch
    for b0, b1 in _unresolved(missed, clock):
        warnings.append(f"excluded stretch {b0:.0f}-{b1:.0f}s could not be cut around; shown sped up")
    ctx.warnings.extend(warnings)
    _nudge_clean(p, video, cfg, ctx.clean_threshold)

    pieces: list[Piece] = []
    if open_end > 0:
        pieces.append(Piece(0.0, open_end, ctx.src_open, ctx.src_open, "opening"))
    for i, (a, b) in enumerate(zip(p.bounds[:-1], p.bounds[1:])):
        pieces.append(Piece(a, b, p.starts[i], p.ends[i], "timelapse", energy=float(energies[i])))
    return pieces


# ------------------------------------------------------------------ cut points


def forced_cuts(audio: SegmentAudio, cfg: PlannerConfig) -> list[float]:
    forced = list(audio.sections) if cfg.cuts.cut_on_sections else []
    if audio.drop is not None and (cfg.reveal.enabled or cfg.cuts.cut_on_sections):
        forced.append(audio.drop)
    return sorted(forced)


def cut_points(music: Music, cfg: PlannerConfig, t_lo: float, t_hi: float, forced: list[float]) -> list[float]:
    """Walk the beats; beats-per-cut shrinks as energy rises. Returns snapped boundaries incl. t_lo and t_hi."""
    c = cfg.cuts
    min_seg = min_segment(cfg)
    beats = music.beats
    down = set(np.round(music.downbeats, 4).tolist())
    is_down = np.array([round(b, 4) in down for b in beats])
    cuts = [t_lo]
    idx = int(np.searchsorted(beats, t_lo + min_seg))
    if idx < len(beats) and beats[idx] < t_hi - min_seg and beats[idx] - t_lo > 0.5 * music.beat_period:
        cuts.append(float(beats[idx]))  # cut on the first beat after the (non-beat) start
    elif idx > 0:
        idx -= 1  # the start is (close to) a beat: walk from it
    while idx < len(beats):
        e = music.energy_between(beats[idx], beats[idx] + music.beat_period)
        if cfg.strategy == "uniform":
            e = 0.0
        bpc = max(1, int(round(c.max_beats_per_cut + (c.min_beats_per_cut - c.max_beats_per_cut) * e)))
        nxt = idx + bpc
        if c.prefer_downbeats and bpc >= 2:
            near = [j for j in range(max(idx + 1, nxt - bpc // 2), min(len(beats), nxt + bpc // 2 + 1)) if is_down[j]]
            if near:
                nxt = min(near, key=lambda j: abs(j - nxt))
        if nxt >= len(beats) or beats[nxt] > t_hi - min_seg:
            break
        cuts.append(float(beats[nxt]))
        idx = nxt
    for f in forced:
        if t_lo + min_seg <= f <= t_hi - min_seg:
            cuts = [x for x in cuts if abs(x - f) >= min_seg or x == t_lo] + [f]
    snapped = sorted({music.snap(x) for x in cuts} | {music.snap(t_hi)})
    out = [snapped[0]]
    for x in snapped[1:]:
        if x - out[-1] >= min_seg - EPS or x == snapped[-1]:
            out.append(x)
    if len(out) >= 3 and out[-1] - out[-2] < min_seg - EPS:
        out.pop(-2)
    return out


# ------------------------------------------------------------------ source side


def content_clock(video: VideoAnalysis, cfg: PlannerConfig, src_open: float, src_final: float) -> Clock:
    times = np.array(video.times, float)
    act = np.clip(np.array(video.activity, float), 0, None)
    w = cfg.content.base_weight + cfg.content.activity_weight * act ** cfg.content.activity_power
    if cfg.strategy == "uniform":
        w = np.ones_like(w)
    bad = [(b.start, b.end) for b in video.bad_segments if b.end > src_open and b.start < src_final]
    for b0, b1 in bad:
        w[(times >= b0) & (times <= b1)] = 0.0
    w[(times < src_open) | (times >= src_final)] = 0.0  # a trailing bad stretch then merges into the jump to the hold
    usable = w > 0
    if usable.any():
        w = w * usable.sum() / w.sum()  # mean weight 1 => content ~ source seconds
    dt = np.diff(times)
    C = np.concatenate([[0.0], np.cumsum(0.5 * (w[:-1] + w[1:]) * dt)])
    return Clock(times=times, C=C, bad=bad)


def _rates(energies: np.ndarray, cfg: PlannerConfig) -> np.ndarray:
    if cfg.strategy == "uniform":
        return np.ones_like(energies)
    s = cfg.speed
    e = np.clip(energies, 0, 1) ** s.gamma
    return np.exp(np.log(s.min) + (np.log(s.max) - np.log(s.min)) * e)


def _solve_group(d: np.ndarray, r: np.ndarray, budget: float, cfg: PlannerConfig) -> np.ndarray:
    """Content per chunk = k * r * d with sum = budget, realised rates clamped to [floor, ceiling]."""
    fixed: dict[int, float] = {}
    k = 0.0
    for _ in range(len(d) + 1):
        free = [i for i in range(len(d)) if i not in fixed]
        if not free:
            break
        remaining = budget - sum(fixed.values())
        k = remaining / max(sum(r[i] * d[i] for i in free), EPS)
        changed = False
        for i in free:
            rate = k * r[i]
            if rate < cfg.speed.floor:
                fixed[i], changed = cfg.speed.floor * d[i], True
            elif rate > cfg.speed.ceiling:
                fixed[i], changed = cfg.speed.ceiling * d[i], True
        if not changed:
            break
    out = np.array([fixed.get(i, k * r[i] * d[i]) for i in range(len(d))])
    return out * (budget / out.sum()) if out.sum() > 0 else np.full(len(d), budget / len(d))


def _allocate_content(bounds: list[float], d: np.ndarray, energies: np.ndarray, clock: Clock,
                      video: VideoAnalysis, audio: SegmentAudio, cfg: PlannerConfig,
                      warnings: list[str]) -> np.ndarray:
    """Cumulative content at each boundary (len = len(bounds))."""
    r = _rates(energies, cfg)
    if cfg.reveal.enabled and audio.drop is not None:
        split = int(np.argmin(np.abs(np.array(bounds) - audio.drop)))
        if 0 < split < len(bounds) - 1:
            prog = np.array(video.progress)
            idx = np.where(prog >= cfg.reveal.pre_drop_progress)[0]
            c_drop = clock.at(float(video.times[idx[0]])) if len(idx) else 0.5 * clock.total
            pre = _solve_group(d[:split], r[:split], c_drop, cfg)
            post = _solve_group(d[split:], r[split:], clock.total - c_drop, cfg)
            return np.concatenate([[0.0], np.cumsum(np.concatenate([pre, post]))])
        warnings.append("reveal: the drop is not on a cut; the painting is revealed gradually instead")
    return np.concatenate([[0.0], np.cumsum(_solve_group(d, r, clock.total, cfg))])


def _map_to_source(p: _Plan, content: np.ndarray, clock: Clock) -> list[float]:
    """Source times for the chunk boundaries. Returns the content positions of bad stretches not cut around."""
    content = content.copy()
    m = len(content) - 1  # number of chunks
    jump_after: dict[int, float] = {}  # boundary index -> source time where the next chunk starts
    missed: list[float] = []
    for b0, _ in clock.bad:
        cb = clock.at(b0)
        if cb <= EPS or cb >= clock.total - EPS:
            continue  # leading/trailing: already skipped by the opening start / jump to the end hold
        cands = [j for j in range(1, m)
                 if j not in jump_after and content[j - 1] <= cb + EPS <= content[j + 1] + 2 * EPS]
        if not cands:
            missed.append(cb)
            continue
        j = min(cands, key=lambda j: abs(content[j] - cb))
        content[j] = cb
        jump_after[j] = clock.inv_right(cb)
    src = [clock.inv_left(c) for c in content]
    p.starts = [clock.inv_right(content[0])] + [jump_after.get(j, src[j]) for j in range(1, m)]
    p.ends = src[1:]
    p.snapped = set(jump_after)
    return missed


def _cuts_for(missed: list[float], content: np.ndarray, bounds: list[float], music: Music,
              cfg: PlannerConfig) -> list[float]:
    """Beats where extra cuts let the edit jump over the missed stretches."""
    min_seg = min_segment(cfg)
    out = []
    for cb in missed:
        t = float(np.interp(cb, content, bounds))  # output time at which the edit reaches the stretch
        ok = [b for b in music.beats if bounds[0] + min_seg <= b <= bounds[-1] - min_seg
              and min(abs(b - x) for x in bounds) >= min_seg]
        if ok:
            out.append(float(min(ok, key=lambda b: abs(b - t))))
    return out


def _unresolved(missed: list[float], clock: Clock) -> list[tuple[float, float]]:
    return [(b0, b1) for b0, b1 in clock.bad if any(abs(clock.at(b0) - cb) < 1e-6 for cb in missed)]


def _nudge_clean(p: _Plan, video: VideoAnalysis, cfg: PlannerConfig, clean_thr: float) -> None:
    """Move each occluded cut's source time to the least-occluded sample nearby (keeps order)."""
    times = np.array(video.times)
    occ = np.array(video.occlusion)
    bad = np.zeros(len(times), bool)
    for b in video.bad_segments:
        bad[(times >= b.start) & (times <= b.end)] = True
    for j in range(1, len(p.starts)):
        if j in p.snapped:
            continue
        s = p.starts[j]
        left = s - p.starts[j - 1]
        right = p.ends[j] - s
        if np.interp(s, times, occ) < clean_thr:
            continue  # already clean: keep the planned speed
        win = min(cfg.cuts.clean_search_seconds, cfg.cuts.clean_search_fraction * min(left, right))
        if win <= 0:
            continue
        mask = (times >= s - win) & (times <= s + win) & ~bad
        if not mask.any():
            continue
        idx = np.where(mask)[0]
        best = idx[np.argmin(occ[idx] + 1e-4 * np.abs(times[idx] - s))]
        p.starts[j] = p.ends[j - 1] = float(times[best])
