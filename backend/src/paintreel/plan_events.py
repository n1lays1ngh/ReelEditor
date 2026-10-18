"""Events strategy: align the painting's visual events to the song's beats.

Both sequences are in time order, so the edit is an order-preserving alignment, solved by dynamic programming:
  score = sum over shown events of  show_reward * strength + beat_match * strength * beat_strength
          - pace_weight * ((cut time - paced time) / (reel length * pace_tolerance))^2
          - gap_penalty * log2(gap between cuts / target beats-per-cut at that energy)^2
The paced time is where the event would fall if the reel moved evenly through the painting's work, so the
whole painting is covered (underpainting included) instead of only its strongest moments.
Strong events (big washes, new colours) land on strong beats (downbeats, sections, the drop); busy music gets
dense cuts, calm music fewer. Each cut then shows its event in a style chosen by the music energy:
  jump   - play the event quickly, then hold its hand-free result (stop-motion build-up on the beat)
  flow   - an eased timelapse from this event up to the next one
  moment - the event itself near real time (calm passages)
"""
from __future__ import annotations

import numpy as np

from .models import VisualEvent
from .planner import EPS, Context, Piece, beats_per_cut, min_segment

NEG = -1e18


def plan(ctx: Context) -> list[Piece] | None:
    cfg, music, video = ctx.cfg, ctx.music, ctx.video
    ec = cfg.events
    min_seg = min_segment(cfg)
    H = ctx.hold_start
    events = [e for e in video.events
              if e.strength >= ec.min_strength and e.before >= ctx.src_open - EPS and e.after <= ctx.src_final + EPS]
    if not events:
        return None
    T = np.unique([music.snap(b) for b in music.beats])
    T = T[(T >= min_seg - EPS) & (T <= H - min_seg + EPS)]
    if not len(T):
        return None

    chosen = _align(events, T, ctx)
    if not chosen:
        return None
    ctx.meta["events_shown"] = [
        {"time": events[i].time, "kind": events[i].kind, "strength": events[i].strength, "out": float(T[j])}
        for i, j in chosen]
    ctx.meta["events_total"] = len(events)
    return _pieces(events, T, chosen, ctx)


# ------------------------------------------------------------------ alignment


def _beat_strengths(T: np.ndarray, ctx: Context) -> np.ndarray:
    ec, music, audio = ctx.cfg.events, ctx.music, ctx.audio
    tol = 1.5 / ctx.cfg.fps
    downs = np.array([music.snap(d) for d in music.downbeats]) if len(music.downbeats) else np.array([])
    specials = np.array(list(audio.sections) + ([audio.drop] if audio.drop is not None else []))
    onset = np.interp(T, audio.onset_times, audio.onset) if audio.onset_times else np.zeros(len(T))
    b = np.full(len(T), ec.base_strength)
    if len(downs):
        b += ec.downbeat_strength * (np.min(np.abs(downs[None, :] - T[:, None]), axis=1) <= tol)
    if len(specials):
        b += ec.section_strength * (np.min(np.abs(specials[None, :] - T[:, None]), axis=1) <= tol + 0.05)
    b += ec.onset_strength * np.clip(onset, 0, 1)
    return np.clip(b, 0, 1)


def _paced_time(events: list[VisualEvent], ctx: Context) -> np.ndarray:
    """Where in the reel each event would land if the edit moved evenly through the painting's work."""
    video = ctx.video
    prog = np.interp([e.time for e in events], video.times, video.progress)
    p0 = float(np.interp(ctx.src_open, video.times, video.progress))
    frac = np.clip((prog - p0) / max(1.0 - p0, EPS), 0, 1)
    H = ctx.hold_start
    reveal = ctx.cfg.reveal
    if reveal.enabled and ctx.audio.drop is not None and 0 < ctx.audio.drop < H:
        pre, drop = reveal.pre_drop_progress, ctx.audio.drop
        return np.where(frac <= pre, frac / max(pre, EPS) * drop, drop + (frac - pre) / max(1 - pre, EPS) * (H - drop))
    return frac * H


def _coverage_strengths(events: list[VisualEvent], ctx: Context, expected_cuts: float) -> np.ndarray:
    """Blend global strength with strength relative to the event's neighbourhood in the painting.

    Without this the alignment only picks the globally strongest moments and can skip whole phases
    (e.g. a pale underpainting that is weaker than the later washes).
    """
    s = np.array([e.strength for e in events])
    s = s / max(s.max(), EPS)
    t = np.array([e.time for e in events])
    window = (ctx.src_final - ctx.src_open) / max(expected_cuts, 1.0)
    local = np.array([s[i] / max(s[np.abs(t - t[i]) <= window / 2].max(), EPS) for i in range(len(s))])
    c = float(np.clip(ctx.cfg.events.coverage, 0, 1))
    return (1 - c) * s + c * local


def _align(events: list[VisualEvent], T: np.ndarray, ctx: Context) -> list[tuple[int, int]]:
    """Best order-preserving assignment of events to cut beats: [(event index, beat index), ...]."""
    cfg, music = ctx.cfg, ctx.music
    ec = cfg.events
    min_seg = min_segment(cfg)
    period = music.beat_period
    n, m = len(events), len(T)
    b = _beat_strengths(T, ctx)
    target = np.array([beats_per_cut(cfg, music.energy_between(t, t + period)) for t in T])
    s = _coverage_strengths(events, ctx, expected_cuts=len(T) / max(float(target.mean()), 1.0))

    def gap_score(dt: np.ndarray, tgt: np.ndarray | float) -> np.ndarray:
        beats = np.maximum(dt, 1e-6) / period
        out = -ec.gap_penalty * np.log2(beats / tgt) ** 2  # convex: several medium gaps beat one huge one
        return np.where(dt < min_seg - EPS, NEG, out)

    G = gap_score(T[None, :] - T[:, None], target[:, None])  # rows: previous cut j', cols: next cut j
    start = gap_score(T, beats_per_cut(cfg, music.energy_between(0.0, period)))
    end = gap_score(ctx.hold_start - T, target)
    gain = ec.show_reward * s[:, None] + ec.beat_match * s[:, None] * b[None, :]
    # pacing: a moment should appear about as far into the reel as it is into the painting's work
    expected = _paced_time(events, ctx)
    dev = (T[None, :] - expected[:, None]) / max(ctx.hold_start, EPS)
    gain -= ec.pace_weight * (dev / max(ec.pace_tolerance, EPS)) ** 2
    if ctx.audio.drop is not None:
        on_drop = np.abs(T - music.snap(ctx.audio.drop)) <= 1.5 / cfg.fps
        gain[:, on_drop] += ec.drop_bonus * s[:, None]
    if cfg.reveal.enabled and ctx.audio.drop is not None:
        late = np.array([e.progress > cfg.reveal.pre_drop_progress + 0.02 for e in events])
        early_beats = T < ctx.audio.drop - EPS
        gain[np.ix_(late, early_beats)] = NEG  # keep the finished look hidden until the drop

    dp = np.full((n, m), NEG)
    back = np.full((n, m, 2), -1, int)
    best_prev = np.full(m, NEG)  # best dp over earlier events, per beat
    best_arg = np.full(m, -1, int)
    for i in range(n):
        trans = best_prev[:, None] + G  # (j', j)
        from_prev = trans.max(axis=0)
        jp = trans.argmax(axis=0)
        use_start = start >= from_prev
        dp[i] = gain[i] + np.where(use_start, start, from_prev)
        back[i, :, 0] = np.where(use_start, -1, best_arg[jp])
        back[i, :, 1] = np.where(use_start, -1, jp)
        better = dp[i] > best_prev
        best_prev = np.where(better, dp[i], best_prev)
        best_arg = np.where(better, i, best_arg)

    total = dp + end[None, :]
    i, j = np.unravel_index(int(np.argmax(total)), total.shape)
    if total[i, j] <= NEG / 2:
        return []
    chosen = []
    while i >= 0 and j >= 0:
        chosen.append((int(i), int(j)))
        i, j = back[i, j]
    return chosen[::-1]


# ------------------------------------------------------------------ pieces


def ease_curve(a: float, points: int) -> list[tuple[float, float]] | None:
    """Slow-fast-slow speed ramp; a=0 is constant speed."""
    a = float(np.clip(a, 0, 1))
    if a <= 0:
        return None
    u = np.linspace(0, 1, max(3, points))
    v = (1 - a) * u + a * (3 * u ** 2 - 2 * u ** 3)
    return [(round(float(x), 5), round(float(y), 5)) for x, y in zip(u, v)]


def jump_curve(play_fraction: float, points: int) -> list[tuple[float, float]]:
    """Play the event quickly (decelerating into its result), then hold the result."""
    pf = float(np.clip(play_fraction, 0.1, 0.95))
    u = np.linspace(0, pf, max(3, points))
    v = 1 - (1 - u / pf) ** 2
    pts = [(round(float(x), 5), round(float(y), 5)) for x, y in zip(u, v)]
    pts[-1] = (round(pf, 5), 1.0)
    return pts + [(1.0, 1.0)]


def _avoid_bad(s0: float, s1: float, ctx: Context) -> tuple[float, float]:
    """Shrink [s0, s1] so it does not run into an excluded stretch."""
    for bad in ctx.video.bad_segments:
        if bad.start < s1 - EPS and bad.end > s0 + EPS:
            if bad.start <= s0 + EPS:
                s0 = min(bad.end, s1)
            else:
                s1 = bad.start
    return s0, max(s0, s1)


def _pieces(events: list[VisualEvent], T: np.ndarray, chosen: list[tuple[int, int]], ctx: Context) -> list[Piece]:
    cfg, music = ctx.cfg, ctx.music
    ec = cfg.events
    cuts = [float(T[j]) for _, j in chosen]
    pieces: list[Piece] = []

    def add(t0: float, t1: float, s0: float, s1: float, style: str, curve, energy: float | None, label: str | None):
        prev_end = pieces[-1].src_end if pieces else ctx.src_open
        s0 = max(s0, prev_end)
        s1 = max(s1, s0)
        if style in ("opening", "flow"):
            s0, s1 = _avoid_bad(s0, s1, ctx)
        pieces.append(Piece(t0, t1, s0, s1, style, energy=energy, curve=curve, event=label))

    first = events[chosen[0][0]]
    add(0.0, cuts[0], ctx.src_open, first.before, "opening", ease_curve(ec.ease, ec.curve_points),
        music.energy_between(0.0, cuts[0]), None)
    for k, (i, _) in enumerate(chosen):
        ev = events[i]
        t0 = cuts[k]
        t1 = cuts[k + 1] if k + 1 < len(cuts) else ctx.hold_start
        next_before = events[chosen[k + 1][0]].before if k + 1 < len(chosen) else ctx.src_final
        e = music.energy_between(t0, t1)
        label = f"{ev.kind} at {ev.time:.0f}s"
        if e >= ec.jump_energy_min and (t1 - t0) <= ec.jump_max_beats * music.beat_period + EPS:
            add(t0, t1, ev.before, ev.after, "jump", jump_curve(ec.play_fraction, ec.curve_points), e, label)
        elif e < ec.moment_energy_max:
            span = (t1 - t0) * ec.moment_speed
            s0 = max(ev.before, min(ev.time - span / 2, next_before - span))
            s1 = min(s0 + span, next_before)
            add(t0, t1, s0, s1, "moment", None, e, label)
        else:
            add(t0, t1, ev.before, next_before, "flow", ease_curve(ec.ease, ec.curve_points), e, label)
    _merge_static_opening(pieces, ctx)
    return pieces


def _merge_static_opening(pieces: list[Piece], ctx: Context) -> None:
    """An opening with (almost) nothing to show becomes the start of the first event's segment."""
    if len(pieces) < 2:
        return
    op, first = pieces[0], pieces[1]
    if op.src_end - op.src_start >= ctx.cfg.events.min_opening_speed * (op.out_end - op.out_start):
        return
    ease = ease_curve(ctx.cfg.events.ease, ctx.cfg.events.curve_points)
    # flow all the way to where the next segment starts (stopping before excluded footage), so nothing early is lost
    end = pieces[2].src_start if len(pieces) > 2 else first.src_end
    s0, s1 = _avoid_bad(op.src_start, max(end, first.src_end), ctx)
    pieces[:2] = [Piece(op.out_start, first.out_end, s0, s1, "flow",
                        energy=ctx.music.energy_between(op.out_start, first.out_end), curve=ease, event=first.event)]
