"""Command line interface: python -m paintreel analyze|render|render-edl|presets."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import yaml

from . import pipeline
from .config import app_settings, dotted_set, preset_names, resolve, resolve_dict
from .models import EDL, Crop, JobParams, RenderEdlParams
from .tools import FFmpegMissing, ffmpeg_status


def parse_time(s: str | None) -> float | None:
    """'42', '42.5', '0:42', '1:02:03.5' -> seconds."""
    if s is None or s == "":
        return None
    parts = [float(p) for p in s.split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def _overrides(args: argparse.Namespace) -> dict:
    ov: dict = {}
    if args.config:
        ov = yaml.safe_load(Path(args.config).read_text()) or {}
    for item in args.set or []:
        key, _, raw = item.partition("=")
        ov = dotted_set(ov, key.strip(), yaml.safe_load(raw))
    return ov


def _controls(args: argparse.Namespace) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in args.control or []:
        key, _, raw = item.partition("=")
        out[key.strip()] = float(raw)
    return out


def _reporter():
    last = {"t": 0.0}

    def report(stage: str, frac: float, msg: str) -> None:
        now = time.time()
        if now - last["t"] > 0.5 or frac >= 1.0:
            last["t"] = now
            print(f"  [{stage:<9}] {frac * 100:5.1f}%  {msg}", flush=True)

    return report


def cmd_analyze(args: argparse.Namespace) -> int:
    cfg = resolve(args.preset, _overrides(args))
    va = pipeline.run_analysis(args.video, cfg, app_settings(), _reporter(), force=args.force)
    plot = pipeline.analysis_plot_path(args.video, cfg, app_settings())
    print(json.dumps({
        "duration": va.duration, "canvas": va.canvas.model_dump(), "canvas_detected": va.canvas_detected,
        "final_time": va.final_time, "start_time": va.start_time, "keyframe_interval": va.keyframe_interval,
        "bad_segments": [b.model_dump() for b in va.bad_segments], "analysis_seconds": va.analysis_seconds,
        "plot": str(plot),
    }, indent=2))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    crop = None
    if args.crop:
        x, y, w, h = (int(v) for v in args.crop.split(","))
        crop = Crop(x=x, y=y, w=w, h=h, mode=args.crop_mode)
    params = JobParams(video=args.video, song=args.song, start=parse_time(args.start), end=parse_time(args.end),
                       preset=args.preset, controls=_controls(args), overrides=_overrides(args), crop=crop,
                       debug=args.debug)
    out = Path(args.out)
    res = pipeline.run_render_job(params, out, app_settings(), _reporter())
    (out / "params.json").write_text(params.model_dump_json(indent=2))
    print(json.dumps({"outputs": {k: str(out / v) for k, v in res.outputs.items()}, "summary": res.summary}, indent=2))
    return 0


def cmd_render_edl(args: argparse.Namespace) -> int:
    edl = EDL.model_validate_json(Path(args.edl).read_text())
    params = RenderEdlParams(edl=edl, preset=args.preset, overrides=_overrides(args), debug=args.debug)
    out = Path(args.out)
    res = pipeline.run_render_edl(params, out, app_settings(), _reporter())
    print(json.dumps({"outputs": {k: str(out / v) for k, v in res.outputs.items()}, "summary": res.summary}, indent=2))
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    if args.name:
        print(yaml.safe_dump(resolve_dict(args.name), sort_keys=False))
    else:
        print("\n".join(preset_names()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paintreel", description="Watercolor timelapse -> beat-synced Reel")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--preset", default="energetic", choices=preset_names())
        sp.add_argument("--config", help="YAML file with overrides (same structure as `defaults`)")
        sp.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="override one value, e.g. --set planner.speed.max=800 (repeatable)")

    a = sub.add_parser("analyze", help="analyze a painting video (cached)")
    a.add_argument("--video", required=True)
    a.add_argument("--force", action="store_true", help="ignore the cache")
    common(a)
    a.set_defaults(func=cmd_analyze)

    r = sub.add_parser("render", help="analyze + plan + render")
    r.add_argument("--video", required=True)
    r.add_argument("--song", required=True)
    r.add_argument("--start", help="segment start (e.g. 0:42); omit both for auto-pick")
    r.add_argument("--end", help="segment end (e.g. 1:12)")
    r.add_argument("--out", required=True)
    r.add_argument("--crop", help="manual canvas crop x,y,w,h in source pixels")
    r.add_argument("--crop-mode", default="auto", choices=["auto", "crop", "pad"])
    r.add_argument("--debug", action="store_true", help="also render the debug overlay version")
    r.add_argument("--control", action="append", metavar="NAME=VALUE",
                   help="simple control as in the UI: pace=0..1, cuts=0..1, transitions=0..1, hold=seconds")
    common(r)
    r.set_defaults(func=cmd_render)

    e = sub.add_parser("render-edl", help="render an existing (edited) EDL, skipping planning")
    e.add_argument("--edl", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--debug", action="store_true")
    common(e)
    e.set_defaults(func=cmd_render_edl)

    pr = sub.add_parser("presets", help="list presets or print one resolved")
    pr.add_argument("name", nargs="?")
    pr.set_defaults(func=cmd_presets)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    if args.cmd != "presets":
        status = ffmpeg_status()
        if not status["ok"]:
            print(f"error: {status['error']}", file=sys.stderr)
            return 2
    try:
        return args.func(args)
    except (FFmpegMissing, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
