"""Diagnostic plots (matplotlib, headless)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .models import EDL, SegmentAudio, VideoAnalysis  # noqa: E402

plt.style.use("dark_background")
STYLE_COLORS = {"opening": "#9aa3b5", "jump": "#ff8fab", "flow": "#4fa3ff", "moment": "#5fd38d",
                "timelapse": "#4fa3ff", "hold": "#ffb347"}
EVENT_COLORS = {"wash": "#4fa3ff", "new_color": "#ff8fab", "detail": "#ffd166", "bloom": "#5fd38d",
                "change": "#c792ea"}


def _shade_bad(ax, va: VideoAnalysis, scale: float = 1.0) -> None:
    for b in va.bad_segments:
        ax.axvspan(b.start / scale, b.end / scale, color="#ff5c5c", alpha=0.18, lw=0)


def plot_analysis(va: VideoAnalysis, out: Path) -> Path:
    scale = 60.0 if va.duration > 600 else 1.0
    unit = "min" if scale > 1 else "s"
    t = np.array(va.times) / scale
    fig, axes = plt.subplots(4, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(t, va.progress_raw, color="#888", lw=0.8, label="raw")
    axes[0].plot(t, va.progress, color="#4fa3ff", lw=1.6, label="progress")
    axes[0].axvline(va.final_time / scale, color="#ffb347", ls="--", lw=1, label="final frame")
    axes[0].axvline(va.start_time / scale, color="#5fd38d", ls="--", lw=1, label="start")
    axes[0].set_ylabel("progress")
    axes[0].legend(loc="lower right", fontsize=8)
    axes[1].plot(t, va.visual_change, color="#c792ea", lw=1, label="visual change")
    for e in va.events:
        axes[1].axvspan(e.start / scale, e.end / scale, color=EVENT_COLORS[e.kind], alpha=0.25, lw=0)
        axes[1].plot([e.time / scale], [e.strength], "o", color=EVENT_COLORS[e.kind], ms=4)
    for kind, c in EVENT_COLORS.items():
        axes[1].plot([], [], "o", color=c, label=kind.replace("_", " "))
    axes[1].set_ylabel("events")
    axes[1].legend(loc="upper right", fontsize=7, ncol=5)
    axes[2].plot(t, va.detail, color="#ffd166", lw=1, label="detail")
    axes[2].plot(t, va.colorfulness, color="#ff8fab", lw=1, label="colour")
    axes[2].plot(t, va.activity, color="#5fd38d", lw=0.8, alpha=0.7, label="activity")
    axes[2].legend(loc="upper left", fontsize=7)
    axes[3].plot(t, va.occlusion, color="#ff8fab", lw=1)
    axes[3].set_ylabel("occlusion")
    axes[3].set_xlabel(f"source time ({unit})")
    for ax in axes:
        _shade_bad(ax, va, scale)
        ax.grid(alpha=0.15)
    reasons = sorted({r.strip() for b in va.bad_segments for r in b.reason.split(",")})
    axes[0].set_title(f"Video analysis: {len(va.events)} events" + (f" (red = excluded: {', '.join(reasons)})"
                                                                     if reasons else ""), fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def plot_mapping(edl: EDL, out: Path, va: VideoAnalysis | None = None, audio: SegmentAudio | None = None) -> Path:
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    scale = 60.0 if edl.video.duration > 600 else 1.0
    for s in edl.segments:
        u = np.linspace(0, 1, 24)
        color = STYLE_COLORS.get(s.style or "", STYLE_COLORS["timelapse"] if s.mode != "hold" else "#ffb347")
        ax.plot(s.out_start + u * s.out_duration, s.source_at(u) / scale, color=color, lw=2.2)
        if s.transition_in.type == "xfade":
            ax.axvspan(s.out_start, s.out_start + s.transition_in.duration, color="#c792ea", alpha=0.35, lw=0)
    if va is not None:
        for b in va.bad_segments:
            ax.axhspan(b.start / scale, b.end / scale, color="#ff5c5c", alpha=0.15, lw=0)
        for e in va.events:
            ax.plot([0], [e.time / scale], ">", color=EVENT_COLORS[e.kind], ms=4, clip_on=False)
    for b in edl.beats:
        ax.axvline(b, color="#ffffff", alpha=0.10, lw=0.6)
        ax2.axvline(b, color="#ffffff", alpha=0.10, lw=0.6)
    for b in edl.downbeats:
        ax.axvline(b, color="#ffffff", alpha=0.25, lw=0.8)
    used = {s.style for s in edl.segments if s.style}
    for style, c in STYLE_COLORS.items():
        if style in used:
            ax.plot([], [], color=c, lw=3, label=style)
    ax.plot([], [], color="#c792ea", lw=6, alpha=0.5, label="xfade")
    ax.legend(loc="upper left", fontsize=8)
    ax.set_ylabel(f"source time ({'min' if scale > 1 else 's'})")
    ax.set_title(f"Output → source mapping ({len(edl.segments)} segments, {edl.meta.get('strategy', '')})",
                 fontsize=10)
    ax.grid(alpha=0.15)
    if audio is not None:
        ax2.plot(audio.energy_times, audio.energy, color="#ffb347", lw=1.2, label="energy")
        ax2.plot(audio.onset_times, audio.onset, color="#888", lw=0.5, alpha=0.6, label="onset")
        if audio.drop is not None:
            ax2.axvline(audio.drop, color="#ff5c5c", lw=1.2, label="drop")
        ax2.legend(loc="upper left", fontsize=7)
    speeds = [s.speed for s in edl.segments]
    ax3 = ax2.twinx()
    ax3.step([s.out_start for s in edl.segments], np.maximum(speeds, 1e-2), where="post", color="#4fa3ff", lw=1)
    ax3.set_yscale("log")
    ax3.set_ylabel("avg speed ×", color="#4fa3ff")
    ax2.set_ylabel("energy")
    ax2.set_xlabel("output time (s)")
    ax2.set_xlim(0, edl.duration)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out
