"""Configuration: YAML defaults + presets + simple controls + per-job overrides, validated by strict pydantic models.

The models deliberately have no defaults: every tuning number comes from config/paintreel.yaml.
"""
from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = BACKEND_DIR / "config" / "paintreel.yaml"
LOCAL_CONFIG_PATH = BACKEND_DIR / "config" / "config.local.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualConfig(_Strict):
    change_threshold: float
    hue_bins: int
    sat_bins: int
    hue_min_saturation: int
    canny_low: int
    canny_high: int


class EventWeights(_Strict):
    area: float
    hue: float
    detail: float
    edges: float


class EventDetectConfig(_Strict):
    weights: EventWeights
    smooth_seconds: float
    min_gap_seconds: float
    min_score: float
    extent_fraction: float
    max_extent_seconds: float
    clean_search_seconds: float
    wash_area: float
    hue_dominance: float
    detail_dominance: float
    bloom_max_occlusion: float
    bloom_min_area: float


class AnalysisConfig(_Strict):
    sample_fps: float
    decode_width: int
    thumb_width: int
    keyframe_decode: Literal["auto", "always", "never"]
    keyframe_auto_factor: float
    canvas_sample_count: int
    canvas_sample_portion: float
    canvas_min_area: float
    canvas_max_area: float
    canvas_min_rectangularity: float
    canvas_margin: float
    clean_window: int
    occlusion_diff_threshold: float
    skin_diff_factor: float
    skin_cr: tuple[int, int]
    skin_cb: tuple[int, int]
    clean_threshold: float
    activity_lag: int
    activity_smooth: int
    progress_blur: int
    progress_median_window: int
    bump_threshold_px: float
    bump_min_quadrants: int
    lighting_jump: float
    bad_pad_seconds: float
    merge_gap_seconds: float
    idle_window_seconds: float
    idle_min_seconds: float
    idle_progress_eps: float
    idle_activity_max: float
    final_search_portion: float
    final_stable_samples: int
    final_completion: float
    progress_work_weight: float
    start_progress: float
    visual: VisualConfig
    events: EventDetectConfig


class AudioConfig(_Strict):
    sr: int
    hop_length: int
    pad_seconds: float
    auto_segment_seconds: float
    energy_smooth_seconds: float
    energy_normalization: Literal["song", "segment"]
    energy_percentiles: tuple[float, float]
    beats_per_bar: int
    beat_refine_seconds: float
    waveform_bins: int
    energy_curve_hz: float
    section_seconds: float
    max_sections: int
    drop_window: tuple[float, float]


class SpeedConfig(_Strict):
    min: float
    max: float
    gamma: float
    floor: float
    ceiling: float


class ContentConfig(_Strict):
    base_weight: float
    activity_weight: float
    activity_power: float


class EventPlanConfig(_Strict):
    min_strength: float
    show_reward: float
    beat_match: float
    gap_penalty: float
    base_strength: float
    downbeat_strength: float
    section_strength: float
    onset_strength: float
    drop_bonus: float
    min_opening_speed: float
    coverage: float
    pace_weight: float
    pace_tolerance: float
    jump_energy_min: float
    jump_max_beats: float
    moment_energy_max: float
    moment_speed: float
    play_fraction: float
    ease: float
    curve_points: int


class CutsConfig(_Strict):
    min_beats_per_cut: int
    max_beats_per_cut: int
    prefer_downbeats: bool
    cut_on_sections: bool
    min_segment_seconds: float
    clean_search_seconds: float
    clean_search_fraction: float


class ModesConfig(_Strict):
    realtime_max_speed: float


class OpeningConfig(_Strict):
    start_progress: float
    lead_seconds: float
    hold_beats: int


class EndingConfig(_Strict):
    hold_seconds: float
    hold_min: float
    hold_max: float
    zoom_out: bool
    zoom_from: float
    transition: Literal["cut", "xfade"]
    transition_frames: int


class TransitionsConfig(_Strict):
    xfade_enabled: bool
    xfade_energy_max: float
    xfade_frames: int
    xfade_beat_fraction: float
    xfade_min_frames: int


class RevealConfig(_Strict):
    enabled: bool
    pre_drop_progress: float


class PlannerConfig(_Strict):
    strategy: Literal["events", "smooth", "uniform"]
    fps: int
    speed: SpeedConfig
    content: ContentConfig
    events: EventPlanConfig
    cuts: CutsConfig
    modes: ModesConfig
    opening: OpeningConfig
    ending: EndingConfig
    transitions: TransitionsConfig
    reveal: RevealConfig


class AudioRenderConfig(_Strict):
    fade_in: float
    fade_out: float
    lufs: float
    true_peak: float
    lra: float
    two_pass: bool
    bitrate: str
    sample_rate: int


class RenderConfig(_Strict):
    width: int
    height: int
    segment_preset: str
    final_preset: str
    crf: int
    crop_mode: Literal["auto", "crop", "pad"]
    max_crop_loss: float
    blur_radius: int
    parallel_segments: int
    keyframe_skip_factor: float
    seek_margin_seconds: float
    clean_sampling: bool
    clean_min_step: float
    candidates_per_frame: int
    clean_bias: float
    max_candidates_factor: float
    debug_width: int
    debug_height: int
    cover_quality: int
    audio: AudioRenderConfig


class PipelineConfig(_Strict):
    analysis: AnalysisConfig
    audio: AudioConfig
    planner: PlannerConfig
    render: RenderConfig


class AppSettings(_Strict):
    media_dirs: list[Path]
    max_scan_depth: int
    video_extensions: list[str]
    song_extensions: list[str]
    max_upload_mb: int
    data_dir: Path
    workers: int

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


class PresetInfo(_Strict):
    name: str = ""
    title: str
    icon: str
    description: str


class ControlTarget(_Strict):
    path: str
    mul: tuple[float, float] | None = None
    at: tuple[float, float] | None = None
    value: bool = False
    offset: float = 0.0
    round: bool = False
    min: float | None = None
    max: float | None = None


class Control(_Strict):
    key: str
    label: str
    left: str = ""
    right: str = ""
    unit: str = ""
    min: float
    max: float
    step: float
    default: float | None = None
    default_from: str | None = None
    targets: list[ControlTarget]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def _load_raw_cached(extra: str | None) -> dict[str, Any]:
    raw = _read_yaml(CONFIG_PATH)
    if LOCAL_CONFIG_PATH.exists():
        raw = deep_merge(raw, _read_yaml(LOCAL_CONFIG_PATH))
    if extra:
        raw = deep_merge(raw, _read_yaml(Path(extra)))
    return raw


def load_raw() -> dict[str, Any]:
    return copy.deepcopy(_load_raw_cached(os.environ.get("PAINTREEL_CONFIG")))


def preset_names() -> list[str]:
    return list(load_raw().get("presets", {}).keys())


def preset_info() -> list[PresetInfo]:
    raw = load_raw()
    info = raw.get("preset_info", {})
    out = []
    for name in raw.get("presets", {}):
        meta = info.get(name, {"title": name.title(), "icon": "brush", "description": ""})
        out.append(PresetInfo(name=name, **meta))
    return out


def controls() -> list[Control]:
    return [Control.model_validate(c) for c in load_raw().get("controls", [])]


def dotted_get(d: dict[str, Any], dotted: str) -> Any:
    cur: Any = d
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            raise KeyError(dotted)
        cur = cur[k]
    return cur


def dotted_set(d: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    """Return d with 'a.b.c' = value merged in (d itself is not modified)."""
    keys = dotted.split(".")
    node: dict[str, Any] = {}
    cur = node
    for k in keys[:-1]:
        cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value
    return deep_merge(d, node)


def resolve_dict(preset: str | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = load_raw()
    merged = raw["defaults"]
    if preset:
        presets = raw.get("presets", {})
        if preset not in presets:
            raise ValueError(f"Unknown preset '{preset}'. Available: {', '.join(presets)}")
        merged = deep_merge(merged, presets[preset])
    return deep_merge(merged, overrides or {})


def control_default(control: Control, base: dict[str, Any]) -> float:
    if control.default_from:
        return float(dotted_get(base, control.default_from))
    return float(control.default if control.default is not None else (control.min + control.max) / 2)


def _target_value(t: ControlTarget, x: float, default: float, lo: float, hi: float, base_value: float) -> float:
    if t.value:
        v = x + t.offset
    else:
        # position relative to the default: -1 (slider min) .. 0 (default) .. +1 (slider max)
        if x <= default:
            s = -(default - x) / max(default - lo, 1e-9)
        else:
            s = (x - default) / max(hi - default, 1e-9)
        if t.mul is not None:
            factor = t.mul[0] ** (-s) if s < 0 else t.mul[1] ** s
            v = base_value * factor
        elif t.at is not None:
            v = base_value + (base_value - t.at[0]) * s if s < 0 else base_value + (t.at[1] - base_value) * s
        else:
            v = base_value
    if t.min is not None:
        v = max(t.min, v)
    if t.max is not None:
        v = min(t.max, v)
    return float(round(v)) if t.round else float(v)


def controls_to_overrides(preset: str | None, values: dict[str, float] | None) -> dict[str, Any]:
    """Translate simple UI controls (pace, cuts, ...) into config overrides relative to the preset."""
    if not values:
        return {}
    base = resolve_dict(preset)
    known = {c.key: c for c in controls()}
    out: dict[str, Any] = {}
    for key, x in values.items():
        if key not in known:
            raise ValueError(f"unknown control '{key}'. Available: {', '.join(known)}")
        c = known[key]
        x = float(min(max(x, c.min), c.max))
        default = control_default(c, base)
        for t in c.targets:
            base_value = float(dotted_get(base, t.path))
            v = _target_value(t, x, default, c.min, c.max, base_value)
            original = dotted_get(base, t.path)
            out = dotted_set(out, t.path, int(v) if isinstance(original, int) and not isinstance(original, bool) else v)
    return out


def resolve(preset: str | None = None, overrides: dict[str, Any] | None = None,
            control_values: dict[str, float] | None = None) -> PipelineConfig:
    """Defaults <- preset <- simple controls <- explicit overrides, validated (unknown keys are errors)."""
    merged = deep_merge(controls_to_overrides(preset, control_values), overrides or {})
    return PipelineConfig.model_validate(resolve_dict(preset, merged))


def app_settings() -> AppSettings:
    app = dict(load_raw()["app"])
    app["media_dirs"] = [Path(os.path.expanduser(p)) for p in app["media_dirs"]]
    data_dir = Path(os.path.expanduser(os.environ.get("PAINTREEL_DATA_DIR", app["data_dir"])))
    app["data_dir"] = data_dir if data_dir.is_absolute() else (BACKEND_DIR / data_dir)
    settings = AppSettings.model_validate(app)
    for d in (settings.cache_dir, settings.outputs_dir, settings.uploads_dir / "video", settings.uploads_dir / "song"):
        d.mkdir(parents=True, exist_ok=True)
    return settings
