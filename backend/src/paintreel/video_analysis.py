"""Video analysis: one streaming decode pass at low resolution, then curves computed from on-disk thumbnails.

Per sample (default 1 fps), measured on the hand-free "clean canvas":
  progress toward the finished painting, activity, occlusion (hand/brush coverage), camera shift, lighting,
  detail (Laplacian variance), edge density, colourfulness, colour-histogram change and changed area.
From those: visual events (washes, new colours, detail work, blooms) with clean before/after frames,
bad segments, the canvas bbox and the clean frame of the finished painting.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .cache import cache_key, fingerprint, load_model, save_model
from .config import AnalysisConfig
from .models import BadSegment, Crop, VideoAnalysis, VisualEvent
from .tools import even, iter_frames, keyframe_interval, probe_video

log = logging.getLogger(__name__)
ANALYSIS_VERSION = 6  # bump when the algorithm changes so cached results are recomputed
Progress = Callable[[float, str], None]


def _noop(frac: float, msg: str) -> None:
    pass


def cache_dir_for(path: Path, cfg: AnalysisConfig, cache_root: Path) -> Path:
    return cache_root / "video" / cache_key(fingerprint(path), {"v": ANALYSIS_VERSION, **cfg.model_dump(mode="json")})


def load_cached(path: Path, cfg: AnalysisConfig, cache_root: Path) -> VideoAnalysis | None:
    va = load_model(cache_dir_for(path, cfg, cache_root) / "analysis.json", VideoAnalysis)
    return va.model_copy(update={"path": str(path)}) if va else None


@dataclass
class _Signals:
    occlusion: np.ndarray
    activity: np.ndarray
    lighting: np.ndarray
    area: np.ndarray  # fraction of the canvas that changed (per sample of lag)
    hue: np.ndarray  # colour-histogram change (per sample of lag)
    detail: np.ndarray  # Laplacian variance level
    edges: np.ndarray  # edge density level
    colorfulness: np.ndarray  # colourfulness level


def analyze_video(path: Path, cfg: AnalysisConfig, cache_root: Path, progress: Progress = _noop,
                  force: bool = False) -> VideoAnalysis:
    out_dir = cache_dir_for(path, cfg, cache_root)
    if not force and (cached := load_cached(path, cfg, cache_root)):
        log.info("video analysis cache hit: %s", out_dir.name)
        return cached
    t_start = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    progress(0.0, "Opening the video")
    meta = probe_video(path)
    kf = keyframe_interval(path)
    use_kf = cfg.keyframe_decode == "always" or (
        cfg.keyframe_decode == "auto" and kf <= cfg.keyframe_auto_factor / cfg.sample_fps)
    log.info("analyzing %s: %.0fs %dx%d @%.2f fps, keyframe interval %.2fs (keyframe decode: %s)",
             path.name, meta.duration, meta.width, meta.height, meta.fps, kf, use_kf)

    thumbs, shifts = _decode_pass(path, meta.duration, meta.width, meta.height, cfg, use_kf, out_dir,
                                  lambda f, m: progress(0.65 * f, m))
    n = len(shifts)
    if n < 3:
        raise ValueError(f"video too short to analyze ({n} samples)")
    th_h, th_w = thumbs.shape[1:3]
    sx, sy = meta.width / th_w, meta.height / th_h
    shifts_src = shifts * meta.width / even(cfg.decode_width)

    progress(0.67, "Finding the paper")
    rect, detected = detect_canvas(thumbs, n, cfg)
    cx, cy, cw, ch = rect

    sig = _clean_canvas_pass(thumbs, n, rect, cfg, out_dir, lambda f, m: progress(0.68 + 0.22 * f, m))
    times = np.arange(n) / cfg.sample_fps

    bad_mask = np.zeros(n, bool)
    bad: list[BadSegment] = []
    pad = int(round(cfg.bad_pad_seconds * cfg.sample_fps))
    for i in np.where(shifts_src > cfg.bump_threshold_px)[0]:
        bad.append(BadSegment(start=times[max(0, i - pad)], end=times[min(n - 1, i + pad)], reason="camera bump"))
    for i in np.where(np.abs(sig.lighting) > cfg.lighting_jump)[0]:
        bad.append(BadSegment(start=times[max(0, i - pad)], end=times[min(n - 1, i + pad)], reason="lighting jump"))
    bad = _merge(bad, cfg.merge_gap_seconds)
    for b in bad:
        bad_mask[(times >= b.start) & (times <= b.end)] = True

    progress(0.92, "Measuring progress")
    final_idx = _final_index(sig.occlusion, bad_mask, cfg)
    progress_raw = _progress_raw(out_dir, n, final_idx, cfg)
    similar = _monotonic_progress(progress_raw, bad_mask, final_idx, cfg)
    work = _work_progress(sig.area, bad_mask, final_idx)
    # layered watercolour: underpainting can look *less* like the final piece, so also count work done
    w = cfg.progress_work_weight
    prog = np.maximum.accumulate((1 - w) * similar + w * work)
    complete_idx, hold_idx = _hold_index(prog, sig.occlusion, bad_mask, final_idx, cfg)

    idle = _idle_segments(prog, sig.activity, sig.occlusion, times, hold_idx, cfg)
    bad = _merge(bad + idle, cfg.merge_gap_seconds)
    for b in bad:
        bad_mask[(times >= b.start) & (times <= b.end)] = True
    started = np.where(prog > cfg.start_progress)[0]
    start_idx = max(0, int(started[0]) - 1) if len(started) else 0

    progress(0.96, "Detecting painting moments")
    score, events = _detect_events(times, sig, prog, bad_mask, start_idx, hold_idx, cfg)

    canvas = Crop(x=int(cx * sx), y=int(cy * sy), w=even(cw * sx), h=even(ch * sy))
    canvas.w = min(canvas.w, meta.width - canvas.x)
    canvas.h = min(canvas.h, meta.height - canvas.y)
    va = VideoAnalysis(
        path=str(path), fingerprint=fingerprint(path), cache_key=out_dir.name, duration=meta.duration,
        fps=meta.fps, width=meta.width, height=meta.height, keyframe_interval=kf, sample_fps=cfg.sample_fps,
        keyframe_decode=use_kf, canvas=canvas, canvas_detected=detected, times=_r(times, 3),
        progress=_r(prog), progress_raw=_r(progress_raw), activity=_r(sig.activity), occlusion=_r(sig.occlusion),
        shift=_r(shifts_src, 2), lighting=_r(sig.lighting, 4), detail=_r(_level(sig.detail)),
        colorfulness=_r(_level(sig.colorfulness)), visual_change=_r(score), events=events, bad_segments=bad,
        final_time=float(times[hold_idx]), complete_time=float(times[complete_idx]),
        start_time=float(times[start_idx]), analysis_seconds=round(time.time() - t_start, 2),
    )
    save_model(out_dir / "analysis.json", va)
    progress(1.0, "Analysis done")
    log.info("analysis done in %.1fs: %d events, final frame %.1fs, %d bad segments, canvas %s",
             va.analysis_seconds, len(events), va.final_time, len(bad), canvas.model_dump())
    return va


def _r(a: np.ndarray, nd: int = 4) -> list[float]:
    return np.round(np.nan_to_num(np.asarray(a, float)), nd).tolist()


def _level(x: np.ndarray) -> np.ndarray:
    """Scale a level curve to 0..1 by its 99th percentile (for display)."""
    ref = np.percentile(x, 99) if len(x) else 1.0
    return np.clip(x / max(ref, 1e-9), 0, 1)


def sample_image(path: Path, cfg: AnalysisConfig, cache_root: Path, t: float, clean: bool) -> np.ndarray:
    """Analysis thumbnail at source time t: the hand-free canvas crop (clean) or the full frame."""
    d = cache_dir_for(path, cfg, cache_root)
    cleans = np.load(d / "cleans.npy", mmap_mode="r")
    n = len(cleans)
    idx = int(np.clip(round(t * cfg.sample_fps), 0, n - 1))
    if clean:
        return np.asarray(cleans[idx])
    return np.asarray(np.load(d / "thumbs.npy", mmap_mode="r")[idx])


# ------------------------------------------------------------------ decode pass


def _decode_pass(path: Path, duration: float, width: int, height: int, cfg: AnalysisConfig, use_kf: bool,
                 out_dir: Path, progress: Progress) -> tuple[np.ndarray, np.ndarray]:
    dw = even(cfg.decode_width)
    dh = even(dw * height / width)
    tw = even(cfg.thumb_width)
    th = even(tw * height / width)
    expected = int(duration * cfg.sample_fps) + 3
    thumbs = np.lib.format.open_memmap(out_dir / "thumbs.npy", mode="w+", dtype=np.uint8, shape=(expected, th, tw, 3))
    shifts = np.zeros(expected, np.float32)
    window = cv2.createHanningWindow((dw, dh), cv2.CV_32F)
    prev = None
    n = 0
    total = max(1, expected - 3)
    report_every = max(1, expected // 100)
    for frame in iter_frames(path, cfg.sample_fps, dw, dh, keyframes_only=use_kf):
        if n >= expected:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            shifts[n] = _global_shift(prev, gray, window, cfg.bump_min_quadrants)
        prev = gray
        thumbs[n] = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
        n += 1
        if n % report_every == 0:
            progress(min(1.0, n / total), f"Reading frames ({n}/{total})")
    thumbs.flush()
    return thumbs[:n], shifts[:n]


def _global_shift(prev: np.ndarray, cur: np.ndarray, window: np.ndarray, min_quadrants: int) -> float:
    """Whole-frame translation (decode px), or 0 unless the frame quadrants agree on it.

    Phase correlation alone can lock onto a large moving hand; a camera bump moves every quadrant.
    """
    (dx, dy), _ = cv2.phaseCorrelate(prev, cur, window)
    mag = float(np.hypot(dx, dy))
    if mag < 1.0:
        return mag
    h, w = prev.shape
    qwin = cv2.createHanningWindow((w // 2, h // 2), cv2.CV_32F)
    agree = 0
    for y0 in (0, h // 2):
        for x0 in (0, w // 2):
            a = np.ascontiguousarray(prev[y0:y0 + h // 2, x0:x0 + w // 2])
            b = np.ascontiguousarray(cur[y0:y0 + h // 2, x0:x0 + w // 2])
            (qx, qy), _ = cv2.phaseCorrelate(a, b, qwin)
            agree += np.hypot(qx - dx, qy - dy) <= max(1.5, 0.25 * mag)
    return mag if agree >= min_quadrants else 0.0


# ------------------------------------------------------------------ canvas


def detect_canvas(thumbs: np.ndarray, n: int, cfg: AnalysisConfig) -> tuple[tuple[int, int, int, int], bool]:
    """Largest bright, roughly rectangular region in the median of early frames (paper on a desk)."""
    h, w = thumbs.shape[1:3]
    last = max(1, int(n * cfg.canvas_sample_portion))
    idx = np.unique(np.linspace(0, last - 1, min(cfg.canvas_sample_count, last)).astype(int))
    med = np.median(thumbs[idx], axis=0).astype(np.uint8)
    gray = cv2.GaussianBlur(cv2.cvtColor(med, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = max(3, int(round(5 * w / 160)) | 1)  # ~ the same physical size at any thumbnail width
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)  # drop small bright clutter first
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    full = (0, 0, w, h)
    if not contours:
        return full, False
    c = max(contours, key=cv2.contourArea)
    x, y, bw, bh = cv2.boundingRect(c)
    area = bw * bh / (w * h)
    rectangularity = cv2.contourArea(c) / max(bw * bh, 1)
    if not (cfg.canvas_min_area <= area <= cfg.canvas_max_area) or rectangularity < cfg.canvas_min_rectangularity:
        log.warning("canvas detection unsure (area %.2f, rectangularity %.2f): using full frame", area, rectangularity)
        return full, False
    mx, my = int(round(cfg.canvas_margin * w)), int(round(cfg.canvas_margin * h))
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(w, x + bw + mx), min(h, y + bh + my)
    return (x0, y0, x1 - x0, y1 - y0), True


# ------------------------------------------------------------------ clean canvas and per-sample signals


def _skin_mask(bgr: np.ndarray, cfg: AnalysisConfig) -> np.ndarray:
    ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    cr, cb = ycrcb[..., 1], ycrcb[..., 2]
    return (cr >= cfg.skin_cr[0]) & (cr <= cfg.skin_cr[1]) & (cb >= cfg.skin_cb[0]) & (cb <= cfg.skin_cb[1])


def _colorfulness(bgr: np.ndarray) -> float:
    """Hasler & Suesstrunk colourfulness."""
    b, g, r = (bgr[..., i].astype(np.float32) for i in range(3))
    rg = r - g
    yb = 0.5 * (r + g) - b
    return float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))


def _hue_hist(bgr: np.ndarray, cfg: AnalysisConfig) -> np.ndarray:
    """Hue x saturation histogram of painted (saturated) pixels, as a fraction of the whole canvas."""
    v = cfg.visual
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = (hsv[..., 1] >= v.hue_min_saturation).astype(np.uint8)
    hist = cv2.calcHist([hsv], [0, 1], mask, [v.hue_bins, v.sat_bins], [0, 180, v.hue_min_saturation, 256])
    return hist.ravel() / float(bgr.shape[0] * bgr.shape[1])


def _clean_canvas_pass(thumbs: np.ndarray, n: int, rect: tuple[int, int, int, int], cfg: AnalysisConfig,
                       out_dir: Path, progress: Progress) -> _Signals:
    """Centered running median = clean canvas (hand removed), saved to cleans.npy; all per-sample signals."""
    x, y, w, h = rect
    canv = thumbs[:n, y:y + h, x:x + w]
    cleans = np.lib.format.open_memmap(out_dir / "cleans.npy", mode="w+", dtype=np.uint8, shape=(n, h, w, 3))
    half = cfg.clean_window // 2
    thr = cfg.occlusion_diff_threshold
    lag = cfg.activity_lag
    vlag = _visual_lag(cfg)  # visual change uses a shorter lag so neighbouring strokes stay apart
    vis = cfg.visual
    occlusion, activity, lighting = np.zeros(n), np.zeros(n), np.zeros(n)
    area, hue = np.zeros(n), np.zeros(n)
    detail, edges, colorful = np.zeros(n), np.zeros(n), np.zeros(n)
    hists = np.zeros((n, vis.hue_bins * vis.sat_bins), np.float32)
    report_every = max(1, n // 50)
    for i in range(n):
        clean = np.median(canv[max(0, i - half):i + half + 1], axis=0).astype(np.uint8)
        cleans[i] = clean
        frame = canv[i]
        diff = np.abs(frame.astype(np.int16) - clean.astype(np.int16)).max(axis=2)
        occluded = (diff > thr) | (_skin_mask(frame, cfg) & (diff > thr * cfg.skin_diff_factor))
        occlusion[i] = occluded.mean()
        gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
        detail[i] = float(cv2.Laplacian(gray, cv2.CV_32F, ksize=3).var())
        edges[i] = float((cv2.Canny(gray, vis.canny_low, vis.canny_high) > 0).mean())
        colorful[i] = _colorfulness(clean)
        hists[i] = _hue_hist(clean, cfg)
        if i >= lag:
            activity[i] = np.abs(clean.astype(np.int16) - cleans[i - lag].astype(np.int16)).mean() / lag
        if i >= vlag:
            prev = cleans[i - vlag].astype(np.int16)
            area[i] = (np.abs(clean.astype(np.int16) - prev).max(axis=2) > vis.change_threshold).mean() / vlag
            hue[i] = 0.5 * float(np.abs(hists[i] - hists[i - vlag]).sum()) / vlag
        if i > 0:  # global exposure change: most pixels move together; paint only touches a few
            ratio = np.log1p(thumbs[i].astype(np.float32)) - np.log1p(thumbs[i - 1].astype(np.float32))
            lighting[i] = float(np.expm1(np.median(ratio)))
        if i % report_every == 0:
            progress(i / n, "Removing the hand, measuring colour and detail")
    cleans.flush()
    activity = np.convolve(activity, np.ones(cfg.activity_smooth) / cfg.activity_smooth, mode="same")
    scale = np.percentile(activity, 95) or 1.0
    # change is measured between i - vlag and i: centre it on the middle of that interval
    area, hue = _shift_back(area, vlag // 2), _shift_back(hue, vlag // 2)
    return _Signals(occlusion=occlusion, activity=np.clip(activity / scale, 0, 1), lighting=lighting,
                    area=area, hue=hue, detail=detail, edges=edges, colorfulness=colorful)


def _visual_lag(cfg: AnalysisConfig) -> int:
    return max(1, int(round(cfg.activity_lag / 2)))


def _shift_back(x: np.ndarray, k: int) -> np.ndarray:
    """x[i + k] -> x[i] (zero-filled at the end)."""
    if k <= 0:
        return x
    return np.concatenate([x[k:], np.zeros(k)])


def _final_index(occlusion: np.ndarray, bad: np.ndarray, cfg: AnalysisConfig) -> int:
    """Last sample that ends a run of clean, non-bad samples (reference for progress)."""
    n = len(occlusion)
    ok = (occlusion < cfg.clean_threshold) & ~bad
    k = cfg.final_stable_samples
    for i in range(n - 1, k - 2, -1):
        if ok[i - k + 1:i + 1].all():
            return i
    tail = max(1, int(n * cfg.final_search_portion))
    return int(n - tail + np.argmin(occlusion[-tail:]))


def _hold_index(prog: np.ndarray, occlusion: np.ndarray, bad: np.ndarray, final_idx: int,
                cfg: AnalysisConfig) -> tuple[int, int]:
    """(complete, hold): when the painting stopped changing, and the first clean stable frame from then on.

    Using the first clean frame after completion (not the last one in the file) avoids clean-up footage.
    """
    reached = np.where(prog >= cfg.final_completion)[0]
    complete = int(reached[0]) if len(reached) else final_idx
    ok = (occlusion < cfg.clean_threshold) & ~bad
    k = cfg.final_stable_samples
    for i in range(complete, min(final_idx, len(ok) - k) + 1):
        if ok[i:i + k].all():
            j = i
            while j + 1 < len(ok) and ok[j + 1]:
                j += 1
            # the middle of the clean run: sample times are approximate (keyframe decoding), so stay off its edges
            return complete, (i + j) // 2
    return min(complete, final_idx), final_idx


def _progress_raw(out_dir: Path, n: int, final_idx: int, cfg: AnalysisConfig) -> np.ndarray:
    cleans = np.load(out_dir / "cleans.npy", mmap_mode="r")
    k = cfg.progress_blur | 1
    final = cv2.GaussianBlur(np.asarray(cleans[final_idx]), (k, k), 0).astype(np.float32)
    dist = np.zeros(n)
    for i in range(n):
        img = cv2.GaussianBlur(np.asarray(cleans[i]), (k, k), 0).astype(np.float32)
        dist[i] = np.abs(img - final).mean()
    ref = np.percentile(dist[:final_idx + 1], 99) if final_idx > 0 else dist.max()
    return np.clip(1.0 - dist / max(ref, 1e-6), 0, 1)


def _monotonic_progress(raw: np.ndarray, bad: np.ndarray, final_idx: int, cfg: AnalysisConfig) -> np.ndarray:
    p = raw.astype(float).copy()
    p[bad] = np.nan
    if np.isnan(p[0]):
        p[0] = 0.0
    for i in range(1, len(p)):  # forward fill over bad samples
        if np.isnan(p[i]):
            p[i] = p[i - 1]
    w = cfg.progress_median_window | 1
    padded = np.pad(p, w // 2, mode="edge")
    p = np.median(np.lib.stride_tricks.sliding_window_view(padded, w), axis=1)
    p = np.maximum.accumulate(p)
    p = p - p[0]
    top = p[final_idx] if p[final_idx] > 0 else (p.max() or 1.0)
    p = np.clip(p / top, 0, 1)
    p[final_idx:] = 1.0
    return p


def _work_progress(area: np.ndarray, bad: np.ndarray, final_idx: int) -> np.ndarray:
    """Cumulative hand-free paint change, 0..1 at the final frame (monotonic by construction)."""
    a = np.where(bad, 0.0, np.clip(area, 0, None))
    a[final_idx + 1:] = 0.0
    cum = np.cumsum(a)
    total = cum[final_idx] if cum[final_idx] > 0 else 1.0
    return np.clip(cum / total, 0, 1)


def _idle_segments(prog: np.ndarray, activity: np.ndarray, occlusion: np.ndarray, times: np.ndarray,
                   final_idx: int, cfg: AnalysisConfig) -> list[BadSegment]:
    n = len(prog)
    hw = max(1, int(cfg.idle_window_seconds * cfg.sample_fps / 2))
    idx = np.arange(n)
    gain = prog[np.minimum(n - 1, idx + hw)] - prog[np.maximum(0, idx - hw)]
    core = (gain < cfg.idle_progress_eps) & (activity < cfg.idle_activity_max)
    # the centered window shrinks a flat stretch by hw on each side: grow it back
    idle = np.convolve(core.astype(int), np.ones(2 * hw + 1, int), mode="same") > 0
    idle[final_idx:] = False  # the tail after the final frame is never shown anyway
    started = np.where(prog > cfg.start_progress)[0]
    if len(started):
        idle[:started[0]] = False  # before the first stroke: handled by the opening
    out = []
    min_len = cfg.idle_min_seconds * cfg.sample_fps
    i = 0
    while i < n:
        if idle[i]:
            j = i
            while j + 1 < n and idle[j + 1]:
                j += 1
            if j - i + 1 >= min_len:
                reason = "painter absent" if occlusion[i:j + 1].mean() < cfg.clean_threshold else "idle"
                out.append(BadSegment(start=float(times[i]), end=float(times[j]), reason=reason))
            i = j + 1
        else:
            i += 1
    return out


def _merge(segs: list[BadSegment], gap: float) -> list[BadSegment]:
    out: list[BadSegment] = []
    for s in sorted(segs, key=lambda s: s.start):
        if out and s.start - out[-1].end <= gap:
            last = out[-1]
            reasons = last.reason if s.reason in last.reason else f"{last.reason}, {s.reason}"
            out[-1] = BadSegment(start=last.start, end=max(last.end, s.end), reason=reasons)
        else:
            out.append(s)
    return out


# ------------------------------------------------------------------ visual events


def _detect_events(times: np.ndarray, sig: _Signals, prog: np.ndarray, bad: np.ndarray, start_idx: int,
                   hold_idx: int, cfg: AnalysisConfig) -> tuple[np.ndarray, list[VisualEvent]]:
    """Combine the visual-change signals into one score, pick its peaks and describe each as an event."""
    ec = cfg.events
    n = len(times)
    fps = cfg.sample_fps
    usable = ~bad
    usable[:start_idx] = False
    usable[hold_idx + 1:] = False
    if usable.sum() < 3:
        return np.zeros(n), []
    vlag = _visual_lag(cfg)

    def positive_change(level: np.ndarray) -> np.ndarray:
        d = np.zeros(n)
        d[vlag:] = np.maximum(0.0, level[vlag:] - level[:-vlag]) / vlag
        return _shift_back(d, vlag // 2)

    def norm(x: np.ndarray) -> np.ndarray:
        vals = x[usable]
        ref = float(np.percentile(vals, 95))
        if ref <= 1e-12:
            ref = float(vals.max()) if vals.max() > 0 else 1.0
        return np.clip(x / ref, 0, 1.5)

    area_n, hue_n = norm(sig.area), norm(sig.hue)
    detail_n, edges_n = norm(positive_change(sig.detail)), norm(positive_change(sig.edges))
    w = ec.weights
    raw = (w.area * area_n + w.hue * hue_n + w.detail * detail_n + w.edges * edges_n)
    raw = raw / max(w.area + w.hue + w.detail + w.edges, 1e-9)
    raw[~usable] = 0.0
    win = max(1, int(round(ec.smooth_seconds * fps)))
    smooth = np.convolve(raw, np.ones(win) / win, mode="same")
    ref = float(np.percentile(smooth[usable], 98))
    score = np.clip(smooth / max(ref, 1e-9), 0, 1)
    score[~usable] = 0.0

    # peaks: local maxima above the threshold, greedily kept by height with a minimum spacing
    cand = [i for i in range(n) if score[i] >= ec.min_score
            and (i == 0 or score[i] >= score[i - 1]) and (i == n - 1 or score[i] > score[i + 1])]
    min_gap = max(1, int(round(ec.min_gap_seconds * fps)))
    chosen: list[int] = []
    for i in sorted(cand, key=lambda i: -score[i]):
        if all(abs(i - j) >= min_gap for j in chosen):
            chosen.append(i)
    chosen.sort()

    occ = sig.occlusion
    clean_ok = (occ < cfg.clean_threshold) & ~bad
    max_ext = int(round(ec.max_extent_seconds * fps))
    search = max(1, int(round(ec.clean_search_seconds * fps)))
    events: list[VisualEvent] = []
    prev_after = start_idx
    for k, p in enumerate(chosen):
        lo_bound = (chosen[k - 1] + p) // 2 + 1 if k > 0 else start_idx
        hi_bound = (p + chosen[k + 1]) // 2 if k + 1 < len(chosen) else hold_idx
        thr = ec.extent_fraction * score[p]
        lo = p
        while lo - 1 >= max(lo_bound, p - max_ext) and score[lo - 1] > thr:
            lo -= 1
        hi = p
        while hi + 1 <= min(hi_bound, p + max_ext) and score[hi + 1] > thr:
            hi += 1
        span = slice(lo, hi + 1)
        a, h = float(area_n[span].mean()), float(hue_n[span].mean())
        d = float(np.maximum(detail_n[span], edges_n[span]).mean())
        if occ[span].mean() <= ec.bloom_max_occlusion and a >= ec.bloom_min_area:
            kind = "bloom"
        elif a >= ec.wash_area:
            kind = "wash"
        elif h > 0 and h >= ec.hue_dominance * a:
            kind = "new_color"
        elif d >= ec.detail_dominance * max(a, h):
            kind = "detail"
        else:
            kind = "change"
        # hand-free frames framing the event; strictly ordered across events
        before = _clean_sample(clean_ok, usable, occ, max(prev_after, lo - search), lo, prefer_late=True)
        before = min(max(before, prev_after), p)
        next_lo = (hi + chosen[k + 1]) // 2 if k + 1 < len(chosen) else hold_idx
        after = _clean_sample(clean_ok, usable, occ, hi, min(max(hi, next_lo), hi + search, hold_idx),
                              prefer_late=False)
        after = max(after, p, before)
        prev_after = after
        events.append(VisualEvent(
            time=float(times[p]), start=float(times[lo]), end=float(times[hi]), before=float(times[before]),
            after=float(times[after]), strength=round(float(score[p]), 4), kind=kind,  # type: ignore[arg-type]
            progress=round(float(prog[after]), 4)))
    return score, events


def _clean_sample(clean_ok: np.ndarray, usable: np.ndarray, occ: np.ndarray, lo: int, hi: int,
                  prefer_late: bool) -> int:
    """Hand-free sample in [lo, hi]: the clean one nearest the event, else the least occluded usable one.

    Falls back to the window edge nearest the event when the whole window is excluded footage.
    """
    lo = max(0, lo)
    hi = max(lo, min(hi, len(occ) - 1))
    idx = np.arange(lo, hi + 1)
    # prefer samples whose neighbours are clean too: sample times are approximate (keyframe decoding)
    stable = clean_ok & np.concatenate([[True], clean_ok[:-1]]) & np.concatenate([clean_ok[1:], [True]])
    for mask in (stable, clean_ok):
        ok = idx[mask[lo:hi + 1]]
        if len(ok):
            return int(ok[-1] if prefer_late else ok[0])
    good = idx[usable[lo:hi + 1]]
    if len(good):
        return int(good[np.argmin(occ[good])])
    return hi if prefer_late else lo
