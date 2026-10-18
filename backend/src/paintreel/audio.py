"""Audio analysis with librosa: song overview (waveform, beats, auto-pick) and per-segment analysis."""
from __future__ import annotations

import logging
from pathlib import Path

import librosa
import numpy as np

from .cache import cache_key, fingerprint, load_model, save_model
from .config import AudioConfig
from .models import SegmentAudio, SongOverview

log = logging.getLogger(__name__)
AUDIO_VERSION = 3  # bump when the algorithm changes so cached results are recomputed


def _smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(x) < 2:
        return x
    kernel = np.ones(n) / n
    padded = np.pad(x, (n // 2, n - 1 - n // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _tempo(t: np.ndarray | float) -> float:
    return float(np.atleast_1d(t)[0])


def _downbeats(beats: np.ndarray, beat_frames: np.ndarray, onset_env: np.ndarray, bpb: int) -> np.ndarray:
    """Every bpb-th beat, with the phase whose beats carry the strongest onsets."""
    if len(beats) < bpb:
        return beats[:1]
    frames = np.clip(beat_frames, 0, len(onset_env) - 1)
    scores = [onset_env[frames[p::bpb]].mean() for p in range(bpb)]
    return beats[int(np.argmax(scores))::bpb]


def _track_beats(onset_env: np.ndarray, sr: int, cfg: AudioConfig, start_bpm: float = 120.0):
    """librosa beat tracking, then each beat snapped to the strongest onset nearby (removes tempo-grid drift)."""
    tempo, frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=cfg.hop_length,
                                            start_bpm=start_bpm, trim=False)
    r = int(round(cfg.beat_refine_seconds * sr / cfg.hop_length))
    if r > 0 and len(frames):
        refined = []
        for f in frames:
            lo, hi = max(0, f - r), min(len(onset_env), f + r + 1)
            refined.append(lo + int(np.argmax(onset_env[lo:hi])))
        frames = np.unique(np.array(refined))
    times = librosa.frames_to_time(frames, sr=sr, hop_length=cfg.hop_length)
    if len(times) > 2:
        # mean of the typical intervals: the median would snap to the analysis frame grid (e.g. 117 for 120 BPM)
        d = np.diff(times)
        med = float(np.median(d))
        typical = d[np.abs(d - med) <= 0.25 * med]
        tempo = 60.0 / float(typical.mean() if len(typical) else med)
    return _tempo(tempo), frames, times


def _resample_curve(values: np.ndarray, frame_times: np.ndarray, hz: float, t0: float, t1: float):
    grid = np.arange(t0, t1 + 1e-9, 1.0 / hz)
    return grid, np.interp(grid, frame_times, values)


def _rms(y: np.ndarray, cfg: AudioConfig) -> tuple[np.ndarray, np.ndarray]:
    rms = librosa.feature.rms(y=y, hop_length=cfg.hop_length)[0]
    smooth_n = max(1, int(cfg.energy_smooth_seconds * cfg.sr / cfg.hop_length))
    rms = _smooth(rms, smooth_n)
    times = librosa.frames_to_time(np.arange(len(rms)), sr=cfg.sr, hop_length=cfg.hop_length)
    return times, rms


def _auto_pick(downbeats: np.ndarray, rms_t: np.ndarray, rms: np.ndarray, duration: float, length: float):
    length = min(length, duration)
    cands = [t for t in downbeats if t + length <= duration] or [0.0]
    csum = np.concatenate([[0.0], np.cumsum(rms)])

    def score(t0: float) -> float:
        a, b = np.searchsorted(rms_t, [t0, t0 + length])
        return (csum[b] - csum[a]) / max(1, b - a)

    best = max(cands, key=score)
    return float(best), float(best + length)


def song_overview(path: Path, cfg: AudioConfig, cache_dir: Path) -> SongOverview:
    fp = fingerprint(path)
    cache_file = cache_dir / "audio" / f"overview-{cache_key(fp, {'v': AUDIO_VERSION, **cfg.model_dump(mode='json')})}.json"
    cached = load_model(cache_file, SongOverview)
    if cached:
        return cached.model_copy(update={"path": str(path)})
    log.info("song overview: %s", path)
    y, sr = librosa.load(str(path), sr=cfg.sr, mono=True)
    duration = len(y) / sr
    bins = min(cfg.waveform_bins, max(1, len(y)))
    usable = len(y) // bins * bins
    peaks = np.abs(y[:usable]).reshape(bins, -1).max(axis=1)
    peaks = peaks / (peaks.max() or 1.0)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=cfg.hop_length)
    tempo, beat_frames, beats = _track_beats(onset_env, sr, cfg)
    downbeats = _downbeats(beats, beat_frames, onset_env, cfg.beats_per_bar)

    rms_t, rms = _rms(y, cfg)
    lo, hi = np.percentile(rms, cfg.energy_percentiles)
    et, ev = _resample_curve(rms, rms_t, cfg.energy_curve_hz, 0.0, duration)
    energy = np.clip((ev - lo) / max(hi - lo, 1e-9), 0, 1)
    s0, s1 = _auto_pick(downbeats, rms_t, rms, duration, cfg.auto_segment_seconds)

    ov = SongOverview(
        path=str(path), fingerprint=fp, duration=duration, tempo=tempo,
        peaks=np.round(peaks, 4).tolist(), beats=np.round(beats, 4).tolist(),
        downbeats=np.round(downbeats, 4).tolist(), energy_times=np.round(et, 3).tolist(),
        energy=np.round(energy, 4).tolist(), rms_lo=float(lo), rms_hi=float(hi),
        suggested_start=s0, suggested_end=s1,
    )
    save_model(cache_file, ov)
    return ov


def _sections(y: np.ndarray, sr: int, beat_frames: np.ndarray, beats_local: np.ndarray, length: float,
              cfg: AudioConfig) -> list[float]:
    k = int(np.clip(round(length / cfg.section_seconds), 2, cfg.max_sections))
    if len(beat_frames) < 2 * k:
        return []
    try:
        chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=cfg.hop_length)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=cfg.hop_length, n_mfcc=13)
        feats = np.vstack([librosa.util.normalize(chroma, axis=1), librosa.util.normalize(mfcc, axis=1)])
        synced = librosa.util.sync(feats, beat_frames, aggregate=np.median)
        bounds = librosa.segment.agglomerative(synced, k)
    except Exception as exc:  # segmentation is a nice-to-have
        log.warning("section segmentation failed: %s", exc)
        return []
    # synced column j covers beat_frames[j-1]..beat_frames[j]; column 0 starts at 0
    times = [beats_local[j - 1] for j in bounds if 1 <= j <= len(beats_local)]
    return sorted(t for t in times if 1.0 < t < length - 1.0)


def _drop(energy_t: np.ndarray, energy: np.ndarray, downbeats: np.ndarray, tempo: float, length: float,
          cfg: AudioConfig) -> float | None:
    bar = cfg.beats_per_bar * 60.0 / max(tempo, 1.0)
    n = max(1, int(bar * cfg.energy_curve_hz))
    best_t, best_rise = None, 0.1
    for i in range(n, len(energy) - n):
        t = energy_t[i]
        if not (cfg.drop_window[0] * length <= t <= cfg.drop_window[1] * length):
            continue
        rise = energy[i:i + n].mean() - energy[i - n:i].mean()
        if rise > best_rise:
            best_t, best_rise = t, rise
    if best_t is None:
        return None
    if len(downbeats):
        best_t = float(downbeats[np.argmin(np.abs(downbeats - best_t))])
    return float(best_t)


def analyze_segment(path: Path, start: float, end: float, cfg: AudioConfig, overview: SongOverview,
                    cache_dir: Path) -> SegmentAudio:
    if end <= start:
        raise ValueError(f"song segment end ({end}) must be after start ({start})")
    end = min(end, overview.duration)
    params = {"v": AUDIO_VERSION, "cfg": cfg.model_dump(mode="json"), "start": round(start, 3), "end": round(end, 3),
              "lo": overview.rms_lo, "hi": overview.rms_hi}
    cache_file = cache_dir / "audio" / f"segment-{cache_key(overview.fingerprint, params)}.json"
    cached = load_model(cache_file, SegmentAudio)
    if cached:
        return cached.model_copy(update={"path": str(path)})
    length = end - start
    offset = max(0.0, start - cfg.pad_seconds)
    y, sr = librosa.load(str(path), sr=cfg.sr, mono=True, offset=offset, duration=end - offset + cfg.pad_seconds)
    shift = offset - start  # add to file-local times to get segment-local times

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=cfg.hop_length)
    tempo, beat_frames, beats_all = _track_beats(onset_env, sr, cfg, overview.tempo or 120.0)
    beats_all = beats_all + shift
    down_all = _downbeats(beats_all, beat_frames, onset_env, cfg.beats_per_bar)
    in_seg = (beats_all >= 0) & (beats_all <= length)

    rms_t, rms = _rms(y, cfg)
    rms_t = rms_t + shift
    if cfg.energy_normalization == "song":
        lo, hi = overview.rms_lo, overview.rms_hi
    else:
        mask = (rms_t >= 0) & (rms_t <= length)
        lo, hi = np.percentile(rms[mask], cfg.energy_percentiles)
    et, ev = _resample_curve(rms, rms_t, cfg.energy_curve_hz, 0.0, length)
    energy = np.clip((ev - lo) / max(hi - lo, 1e-9), 0, 1)
    onset_t = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr, hop_length=cfg.hop_length) + shift
    ot, ov_ = _resample_curve(onset_env / (onset_env.max() or 1.0), onset_t, cfg.energy_curve_hz * 2, 0.0, length)

    beats = beats_all[in_seg]
    downbeats = down_all[(down_all >= 0) & (down_all <= length)]
    seg = SegmentAudio(
        path=str(path), start=start, end=end, tempo=tempo,
        beats=np.round(beats, 4).tolist(), downbeats=np.round(downbeats, 4).tolist(),
        sections=[round(t, 4) for t in _sections(y, sr, beat_frames, beats_all, length, cfg)],
        drop=_drop(et, energy, downbeats, tempo, length, cfg),
        energy_times=np.round(et, 3).tolist(), energy=np.round(energy, 4).tolist(),
        onset_times=np.round(ot, 3).tolist(), onset=np.round(ov_, 4).tolist(),
    )
    save_model(cache_file, seg)
    log.info("segment %.2f-%.2f: tempo %.1f, %d beats, drop %s", start, end, seg.tempo, len(beats), seg.drop)
    return seg
