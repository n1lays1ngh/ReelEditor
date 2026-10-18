"""Data models shared by the pipeline, CLI and API (mirrored in frontend/src/types.ts)."""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

# ---------------------------------------------------------------- EDL


class Crop(BaseModel):
    """Source-pixel rectangle. mode: how it is fitted into 9:16 (auto = decide from aspect)."""

    x: int
    y: int
    w: int
    h: int
    mode: Literal["auto", "crop", "pad"] = "auto"


class Transition(BaseModel):
    type: Literal["cut", "xfade"] = "cut"
    duration: float = 0.0


class Zoom(BaseModel):
    start: float
    end: float


SegmentStyle = Literal["opening", "jump", "flow", "moment", "timelapse", "hold"]


class EDLSegment(BaseModel):
    index: int
    out_start: float
    out_end: float
    src_start: float
    src_end: float
    mode: Literal["timelapse", "realtime", "hold"]
    transition_in: Transition = Field(default_factory=Transition)
    zoom: Zoom | None = None
    # Speed curve: points (u, v), u = fraction of the segment's output time, v = fraction of its source range.
    # Must start at (0, 0), end at (1, 1) and never decrease. None = constant speed.
    curve: list[tuple[float, float]] | None = None
    style: SegmentStyle | None = None
    # Informational (UI, debug overlay, plots); ignored by the renderer.
    energy: float | None = None
    progress_start: float | None = None
    progress_end: float | None = None
    event: str | None = None

    @property
    def out_duration(self) -> float:
        return self.out_end - self.out_start

    @property
    def speed(self) -> float:
        d = self.out_duration
        return (self.src_end - self.src_start) / d if d > 0 else 0.0

    def source_at(self, u: np.ndarray | float) -> np.ndarray:
        """Source time shown at output fraction u (0..1) of this segment."""
        u = np.clip(np.asarray(u, float), 0.0, 1.0)
        if self.curve:
            cu, cv = zip(*self.curve)
            v = np.interp(u, cu, cv)
        else:
            v = u
        return self.src_start + v * (self.src_end - self.src_start)


class SongRef(BaseModel):
    path: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


class VideoRef(BaseModel):
    path: str
    duration: float
    fps: float
    width: int
    height: int
    keyframe_interval: float


class EDL(BaseModel):
    version: int = 2
    fps: int
    width: int
    height: int
    song: SongRef
    video: VideoRef
    crop: Crop
    segments: list[EDLSegment]
    beats: list[float] = Field(default_factory=list)
    downbeats: list[float] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.song.duration


class EDLError(ValueError):
    pass


def _check_curve(seg: EDLSegment) -> None:
    pts = seg.curve
    if pts is None:
        return
    if len(pts) < 2:
        raise EDLError(f"segment {seg.index}: a curve needs at least 2 points")
    if abs(pts[0][0]) > 1e-6 or abs(pts[0][1]) > 1e-6 or abs(pts[-1][0] - 1) > 1e-6 or abs(pts[-1][1] - 1) > 1e-6:
        raise EDLError(f"segment {seg.index}: a curve must start at (0, 0) and end at (1, 1)")
    for (u0, v0), (u1, v1) in zip(pts, pts[1:]):
        if u1 <= u0:
            raise EDLError(f"segment {seg.index}: curve points must have increasing u")
        if v1 < v0 - 1e-9:
            raise EDLError(f"segment {seg.index}: the curve goes back in time (the painting must move forward)")


def validate_edl(edl: EDL) -> None:
    """Enforce the hard constraints: contiguous output on the frame grid, chronological source."""
    if not edl.segments:
        raise EDLError("EDL has no segments")
    half = 0.5 / edl.fps
    if abs(edl.segments[0].out_start) > half:
        raise EDLError("first segment must start at 0")
    if abs(edl.segments[-1].out_end - edl.duration) > half + 1e-6:
        raise EDLError(f"last segment ends at {edl.segments[-1].out_end:.3f}, segment length is {edl.duration:.3f}")
    prev = None
    for seg in edl.segments:
        if seg.out_end - seg.out_start < 1.0 / edl.fps - 1e-6:
            raise EDLError(f"segment {seg.index} is shorter than one frame")
        if seg.src_end < seg.src_start - 1e-6:
            raise EDLError(f"segment {seg.index} runs backwards in source time")
        if seg.src_start < -1e-6 or seg.src_end > edl.video.duration + 1e-3:
            raise EDLError(f"segment {seg.index} source range is outside the video")
        _check_curve(seg)
        if prev is not None:
            if abs(seg.out_start - prev.out_end) > 1e-6:
                raise EDLError(f"gap/overlap between segments {prev.index} and {seg.index}")
            if seg.src_start < prev.src_end - 1e-6:
                raise EDLError(
                    f"segment {seg.index} starts at src {seg.src_start:.2f}s, before segment {prev.index} "
                    f"ends ({prev.src_end:.2f}s): the painting must progress chronologically"
                )
        if seg.transition_in.type == "xfade":
            if prev is None:
                raise EDLError("first segment cannot crossfade in")
            if seg.transition_in.duration > min(seg.out_duration, prev.out_duration) + 1e-6:
                raise EDLError(f"segment {seg.index} crossfade is longer than its neighbours")
        prev = seg


# ---------------------------------------------------------------- analysis results


class BadSegment(BaseModel):
    start: float
    end: float
    reason: str


EventKind = Literal["wash", "new_color", "detail", "bloom", "change"]


class VisualEvent(BaseModel):
    """A visible step in the painting (a wash, a new colour, detail work...), in source seconds."""

    time: float  # peak of the visual change
    start: float
    end: float
    before: float  # hand-free sample just before the event
    after: float  # hand-free sample just after it
    strength: float  # 0..1
    kind: EventKind
    progress: float  # painting progress at `after`


class VideoAnalysis(BaseModel):
    path: str
    fingerprint: str
    cache_key: str
    duration: float
    fps: float
    width: int
    height: int
    keyframe_interval: float
    sample_fps: float
    keyframe_decode: bool
    canvas: Crop
    canvas_detected: bool
    times: list[float]
    progress: list[float]
    progress_raw: list[float]
    activity: list[float]
    occlusion: list[float]
    shift: list[float]
    lighting: list[float]  # relative global brightness change vs previous sample
    detail: list[float]  # Laplacian variance of the clean canvas (normalised 0..1)
    colorfulness: list[float]  # colourfulness of the clean canvas (normalised 0..1)
    visual_change: list[float]  # combined visual-change score used for event detection (0..1)
    events: list[VisualEvent]
    bad_segments: list[BadSegment]
    final_time: float  # hand-free frame of the finished painting (used for the end hold)
    complete_time: float  # when the painting stopped changing
    start_time: float  # first sample where painting has visibly begun
    analysis_seconds: float


class SongOverview(BaseModel):
    path: str
    fingerprint: str
    duration: float
    tempo: float
    peaks: list[float]
    beats: list[float]
    downbeats: list[float]
    energy_times: list[float]
    energy: list[float]
    rms_lo: float
    rms_hi: float
    suggested_start: float
    suggested_end: float


class SegmentAudio(BaseModel):
    path: str
    start: float
    end: float
    tempo: float
    beats: list[float]  # segment-local seconds
    downbeats: list[float]
    sections: list[float]
    drop: float | None
    energy_times: list[float]
    energy: list[float]  # 0..1
    onset_times: list[float]
    onset: list[float]

    @property
    def duration(self) -> float:
        return self.end - self.start


# ---------------------------------------------------------------- jobs

JobState = Literal["queued", "analyzing", "planning", "rendering", "done", "failed", "cancelled"]
JobKind = Literal["analyze", "render", "render_edl"]
FINAL_STATES = ("done", "failed", "cancelled")


class AnalyzeParams(BaseModel):
    video: str
    preset: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class JobParams(BaseModel):
    video: str
    song: str
    start: float | None = None
    end: float | None = None
    preset: str = "energetic"
    controls: dict[str, float] = Field(default_factory=dict)  # simple UI controls (pace, cuts, ...)
    overrides: dict[str, Any] = Field(default_factory=dict)  # raw config overrides, applied last
    crop: Crop | None = None
    debug: bool = False


class RenderEdlParams(BaseModel):
    edl: EDL
    preset: str = "energetic"
    overrides: dict[str, Any] = Field(default_factory=dict)
    debug: bool = False
    source_job: str | None = None


class Job(BaseModel):
    id: str
    kind: JobKind
    state: JobState = "queued"
    stage: str = ""
    progress: float = 0.0
    message: str = ""
    created: float
    updated: float
    params: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)  # name -> filename in the job dir
    error: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
