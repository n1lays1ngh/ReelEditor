from __future__ import annotations

import time

import numpy as np
import pytest

import synth
from conftest import SEG_END, SEG_START, ffprobe_streams, video_md5
from paintreel import pipeline, video_analysis
from paintreel.config import controls_to_overrides, resolve
from paintreel.models import EDL, Crop, JobParams, RenderEdlParams, validate_edl
from paintreel.render import Occlusion, plan_frames, target_times
from paintreel.tools import iter_frames

FPS = 30
PRESETS = ["energetic", "calm", "reveal", "smooth"]


@pytest.fixture(scope="session")
def analysis(synth_files, settings):
    return pipeline.run_analysis(synth_files["video"], resolve(), settings)


def _params(synth_files, preset: str, **kw) -> JobParams:
    return JobParams(video=str(synth_files["video"]), song=str(synth_files["song"]), start=SEG_START, end=SEG_END,
                     preset=preset, **kw)


def _iou(a: Crop, b: tuple[int, int, int, int]) -> float:
    bx, by, bw, bh = b
    ix = max(0, min(a.x + a.w, bx + bw) - max(a.x, bx))
    iy = max(0, min(a.y + a.h, by + bh) - max(a.y, by))
    inter = ix * iy
    return inter / (a.w * a.h + bw * bh - inter)


def _covered(bad, t0: float, t1: float, min_frac: float = 0.8) -> bool:
    for b in bad:
        overlap = max(0.0, min(b.end, t1) - max(b.start, t0))
        if overlap >= min_frac * (t1 - t0):
            return True
    return False


def _in_bad(bad, t: float) -> bool:
    return any(b.start <= t <= b.end for b in bad)


# ------------------------------------------------------------------ analysis


def test_analysis_curves(analysis):
    assert _iou(analysis.canvas, synth.CANVAS) > 0.85
    assert analysis.canvas_detected
    bad = analysis.bad_segments
    assert _covered(bad, *synth.IDLE), f"idle stretch not excluded: {bad}"
    assert _covered(bad, *synth.BUMP), f"camera bump not excluded: {bad}"
    assert _covered(bad, *synth.LIGHT_DIP), f"lighting dip not excluded: {bad}"
    assert synth.CLEAN_TAIL[0] <= analysis.final_time <= synth.CLEAN_TAIL[1]
    assert analysis.complete_time <= analysis.final_time
    prog = np.array(analysis.progress)
    assert np.all(np.diff(prog) >= 0), "progress must be monotonic"
    assert prog[int(analysis.final_time)] == pytest.approx(1.0)
    assert analysis.start_time < 10, "painting starts ~5s"
    occ = np.array(analysis.occlusion)
    times = np.array(analysis.times)
    hand_on = (times > 10) & (times < 38)
    hand_off = (times > 41) & (times < 49)
    assert occ[hand_on].mean() > 3 * occ[hand_off].mean() + 0.01
    for curve in (analysis.detail, analysis.colorfulness, analysis.visual_change):
        assert len(curve) == len(times) and 0 <= min(curve) and max(curve) <= 1


def test_visual_events(analysis):
    events = analysis.events
    assert events, "no visual events detected"
    # most blobs painted outside excluded stretches are found as events near the end of their growth
    visible = [b for b in synth.blobs() if not _in_bad(analysis.bad_segments, b.start + b.dur)]
    found = sum(any(b.start <= e.time <= b.start + b.dur + 4 for e in events) for b in visible)
    assert found >= 0.6 * len(visible), f"only {found}/{len(visible)} painted blobs detected as events"
    prev_after = -1.0
    for e in events:
        assert e.kind in ("wash", "new_color", "detail", "bloom", "change")
        assert 0 < e.strength <= 1
        assert e.before <= e.time <= e.after, e
        assert e.before >= prev_after, "events must not overlap in time"
        assert not _in_bad(analysis.bad_segments, e.time), f"event inside an excluded stretch: {e}"
        prev_after = e.after
    # the hand-free frames framing events are cleaner than the events themselves on average
    occ_at = lambda t: float(np.interp(t, analysis.times, analysis.occlusion))  # noqa: E731
    assert np.mean([occ_at(e.after) for e in events]) <= np.mean([occ_at(e.time) for e in events]) + 1e-9


def test_analysis_cache_hit(synth_files, settings, analysis, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("video was decoded again despite the cache")

    monkeypatch.setattr(video_analysis, "_decode_pass", boom)
    t0 = time.time()
    again = pipeline.run_analysis(synth_files["video"], resolve("calm", {"planner": {"speed": {"max": 50}}}), settings)
    assert time.time() - t0 < 2.0
    assert again.cache_key == analysis.cache_key  # planner/preset changes don't invalidate the video analysis


# ------------------------------------------------------------------ audio


def test_beats_match_click_track(synth_files, settings):
    _, seg = pipeline.song_segment(synth_files["song"], SEG_START, SEG_END, resolve(), settings)
    truth = synth.true_beats(SEG_START, SEG_END)
    beats = np.array(seg.beats)
    assert len(beats) >= len(truth) - 2
    err = np.array([np.min(np.abs(truth - b)) for b in beats])
    assert err.max() <= 2.0 / FPS, f"beats off by up to {err.max() * 1000:.0f} ms"
    assert seg.drop == pytest.approx(synth.DROP_AT - SEG_START, abs=0.6)


# ------------------------------------------------------------------ config


def test_simple_controls_are_relative_to_the_preset():
    assert controls_to_overrides("energetic", {}) == {}
    mid = resolve("energetic", None, {"pace": 0.5, "cuts": 0.5, "transitions": 0.5})
    base = resolve("energetic")
    assert mid.planner == base.planner, "controls at their defaults must not change the preset"
    calm_pace = resolve("energetic", None, {"pace": 0.0}).planner
    fast_pace = resolve("energetic", None, {"pace": 1.0}).planner
    assert calm_pace.speed.max < base.planner.speed.max < fast_pace.speed.max
    assert calm_pace.events.jump_energy_min > fast_pace.events.jump_energy_min
    few, many = resolve("energetic", None, {"cuts": 0.0}).planner, resolve("energetic", None, {"cuts": 1.0}).planner
    assert few.cuts.max_beats_per_cut > many.cuts.max_beats_per_cut >= 1
    assert resolve("energetic", None, {"transitions": 0.0}).planner.transitions.xfade_energy_max == 0.0
    assert resolve("calm", None, {"hold": 4.2}).planner.ending.hold_seconds == pytest.approx(4.2)
    with pytest.raises(ValueError, match="unknown control"):
        controls_to_overrides("energetic", {"nope": 1.0})


# ------------------------------------------------------------------ planner


@pytest.mark.parametrize("preset", PRESETS + ["uniform"])
def test_plan_constraints(preset, synth_files, settings, analysis):
    overrides = {"planner": {"strategy": "uniform"}} if preset == "uniform" else {}
    name = "smooth" if preset == "uniform" else preset
    params = _params(synth_files, name, overrides=overrides)
    cfg = pipeline.job_config(params)
    edl, va, seg = pipeline.plan_job(params, cfg, settings)
    validate_edl(edl)
    segs = edl.segments
    # chronological, contiguous, exact length; curves never go backwards
    assert all(b.src_start >= a.src_end - 1e-6 for a, b in zip(segs, segs[1:]))
    for s in segs:
        src = s.source_at(np.linspace(0, 1, 50))
        assert np.all(np.diff(src) >= -1e-9), f"segment {s.index} goes back in time"
    assert segs[-1].out_end == pytest.approx(SEG_END - SEG_START, abs=0.5 / FPS)
    # every cut lands within one frame of a detected beat, on the frame grid
    beats = np.array(seg.beats)
    for s in segs[1:]:
        assert np.min(np.abs(beats - s.out_start)) <= 1.0 / FPS + 1e-6, f"cut at {s.out_start} not on a beat"
        assert abs(s.out_start * FPS - round(s.out_start * FPS)) < 1e-3, "cuts must be on the frame grid"
    # the end hold uses a clean final frame
    hold = segs[-1]
    assert hold.mode == "hold" and hold.style == "hold"
    assert synth.CLEAN_TAIL[0] <= hold.src_start <= synth.CLEAN_TAIL[1]
    assert np.interp(hold.src_start, va.times, va.occlusion) < cfg.analysis.clean_threshold
    assert 2.0 <= hold.out_duration <= 3.5
    # opening shows an early state; excluded stretches are never shown
    assert segs[0].progress_start is not None and segs[0].progress_start < 0.1
    for s in segs:
        if s.mode == "hold":
            continue
        for b in va.bad_segments:
            inside = max(0.0, min(b.end, s.src_end) - max(b.start, s.src_start))
            assert inside <= 1.0 + 1e-6, f"segment {s.index} shows {inside:.1f}s of excluded '{b.reason}'"


@pytest.mark.parametrize("preset", ["energetic", "calm", "reveal"])
def test_events_land_on_beats(preset, synth_files, settings, analysis):
    params = _params(synth_files, preset)
    edl, va, seg = pipeline.plan_job(params, pipeline.job_config(params), settings)
    assert edl.meta["strategy"] == "events"
    shown = edl.meta["events_shown"]
    assert len(shown) >= 3
    beats = np.array(seg.beats)
    starts = np.array([s.out_start for s in edl.segments])
    for ev in shown:
        assert np.min(np.abs(beats - ev["out"])) <= 1.0 / FPS + 1e-6
    # each event's segment starts on its cut (except one merged into the opening)
    on_cut = sum(np.min(np.abs(starts - ev["out"])) < 1e-6 for ev in shown)
    assert on_cut >= len(shown) - 1
    styles = {s.style for s in edl.segments}
    assert styles <= {"opening", "jump", "flow", "moment", "hold"}
    # the drop is a cut
    assert seg.drop is not None and np.min(np.abs(starts - seg.drop)) <= 1.0 / FPS + 1e-6


def test_energy_drives_segment_style(synth_files, settings):
    params = _params(synth_files, "energetic")
    edl, _, seg = pipeline.plan_job(params, pipeline.job_config(params), settings)
    loud = [s for s in edl.segments if s.out_start >= seg.drop - 1.0 / FPS and s.style != "hold"]
    quiet = [s for s in edl.segments if s.out_end <= seg.drop + 1.0 / FPS]
    assert loud and quiet
    assert any(s.style == "jump" for s in loud), "loud passages should cut stop-motion style"
    assert all(s.style != "jump" for s in quiet), "quiet passages should not jump"
    cut_rate = lambda segs: len(segs) / max(sum(s.out_duration for s in segs), 1e-6)  # noqa: E731
    assert cut_rate(loud) > cut_rate(quiet), "louder music should get more cuts"


def test_reveal_holds_back_finished_painting(synth_files, settings):
    params = _params(synth_files, "reveal")
    cfg = pipeline.job_config(params)
    edl, _, seg = pipeline.plan_job(params, cfg, settings)
    assert seg.drop is not None
    before = [s for s in edl.segments if s.out_end <= seg.drop + 1.0 / FPS]  # the drop is snapped to the frame grid
    assert before and max(s.progress_end or 0 for s in before) <= cfg.planner.reveal.pre_drop_progress + 0.1


# ------------------------------------------------------------------ frame selection


def test_plan_frames_prefers_clean_frames():
    cfg = resolve().render
    tau = np.linspace(100.0, 400.0, 31)[:-1]  # 10 s per output frame: deep timelapse
    t = np.arange(0, 500, 1.0)
    occ = np.where((t.astype(int) % 10) < 7, 0.2, 0.0)  # the hand is away 3 s out of every 10
    fp = plan_frames(tau, 30.0, 1.0, cfg, Occlusion(t, occ))
    assert fp.clean and np.all(np.diff(fp.picks) >= 0)
    shown = tau[0] + fp.picks / fp.rate
    nearest = tau
    assert np.interp(shown, t, occ).mean() < 0.5 * np.interp(nearest, t, occ).mean()
    assert np.all(np.abs(shown - tau) <= 5.0 + 1.0 / fp.rate), "picks stay inside each frame's time slot"
    fp2 = plan_frames(np.linspace(10.0, 12.0, 60), 30.0, 1.0, cfg, Occlusion(t, occ))
    assert not fp2.clean  # slow segments just take the nearest frame
    assert fp2.count >= 60 * 2.0 / 2.0


# ------------------------------------------------------------------ render


@pytest.fixture(scope="session")
def rendered(synth_files, settings, tmp_path_factory):
    out = tmp_path_factory.mktemp("render")
    res = pipeline.run_render_job(_params(synth_files, "energetic", debug=True), out, settings)
    return out, res


def test_render_outputs(rendered):
    out, res = rendered
    for key in ("reel", "silent", "edl", "debug", "cover", "mapping_plot", "analysis_plot"):
        assert (out / res.outputs[key]).is_file(), key
    n = round((SEG_END - SEG_START) * FPS)
    streams = {s["codec_type"]: s for s in ffprobe_streams(out / res.outputs["reel"])}
    v, a = streams["video"], streams["audio"]
    assert (v["codec_name"], v["width"], v["height"], v["r_frame_rate"]) == ("h264", 1080, 1920, "30/1")
    assert int(v["nb_read_frames"]) == n
    assert (a["codec_name"], a["sample_rate"]) == ("aac", "48000")
    assert abs(float(a["duration"]) - (SEG_END - SEG_START)) <= 1.0 / FPS
    silent = ffprobe_streams(out / res.outputs["silent"])
    assert [s["codec_type"] for s in silent] == ["video"]
    assert video_md5(out / res.outputs["reel"]) == video_md5(out / res.outputs["silent"])
    cover = ffprobe_streams(out / res.outputs["cover"])[0]
    assert (cover["width"], cover["height"]) == (1080, 1920)
    assert res.summary["events_shown"] >= 3 and res.summary["strategy"] == "events"


def test_render_from_edited_edl(rendered, settings, tmp_path):
    out, _ = rendered
    edl = EDL.model_validate_json((out / "edl.json").read_text())
    edl.segments[1].transition_in.type = "cut"  # an edit: no planning involved
    edl.segments[1].transition_in.duration = 0.0
    r2 = pipeline.run_render_edl(RenderEdlParams(edl=edl), tmp_path, settings)
    streams = {s["codec_type"]: s for s in ffprobe_streams(tmp_path / r2.outputs["reel"])}
    assert int(streams["video"]["nb_read_frames"]) == round((SEG_END - SEG_START) * FPS)

    bad = edl.model_copy(deep=True)
    bad.segments[3].src_start = bad.segments[1].src_start  # goes back in time
    with pytest.raises(ValueError, match="chronolog"):
        pipeline.run_render_edl(RenderEdlParams(edl=bad), tmp_path / "bad", settings)
    curved = edl.model_copy(deep=True)
    curved.segments[2].curve = [(0.0, 0.0), (0.5, 0.8), (0.7, 0.6), (1.0, 1.0)]  # a curve that reverses
    with pytest.raises(ValueError, match="back in time"):
        validate_edl(curved)


@pytest.mark.parametrize("preset", ["energetic", "smooth"])
def test_frame_accurate_via_code_strip(preset, synth_files, settings, tmp_path):
    """Render the full frame (code strip visible) with hard cuts and read back the source frame of every frame."""
    overrides = {"planner": {"transitions": {"xfade_enabled": False}, "ending": {"transition": "cut", "zoom_out": False}},
                 "render": {"clean_sampling": False}}
    params = _params(synth_files, preset, overrides=overrides, crop=Crop(x=0, y=0, w=640, h=360, mode="pad"))
    res = pipeline.run_render_job(params, tmp_path, settings)
    edl = EDL.model_validate_json((tmp_path / "edl.json").read_text())
    scale = 1080 / synth.W
    y0 = (1920 - round(synth.H * scale / 2) * 2) // 2
    codes = np.array([synth.decode_code(f, 0, y0, scale) / synth.FPS
                      for f in iter_frames(tmp_path / res.outputs["silent"], FPS, 1080, 1920)])
    assert len(codes) == round((SEG_END - SEG_START) * FPS)
    shown = []
    for s in edl.segments:
        f0, f1 = round(s.out_start * FPS), round(s.out_end * FPS)
        tau = target_times(s, FPS)
        if s.mode != "hold":
            step = np.max(np.abs(np.diff(tau))) if len(tau) > 1 else 0.0
            tol = step / 2 + 1.5 / synth.FPS
            err = np.abs(codes[f0:f1] - tau)
            assert err.max() <= tol, (f"segment {s.index} ({s.style}): frame shows {codes[f0 + err.argmax()]:.2f}s, "
                                      f"planned {tau[err.argmax()]:.2f}s")
        else:
            assert abs(codes[f0] - s.src_start) <= 1.5 / synth.FPS
        shown.extend(codes[f0:f1])
    assert np.all(np.diff(shown) >= -1.0 / synth.FPS), "source order must never go backwards"
