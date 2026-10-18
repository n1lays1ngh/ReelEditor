from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import synth

SYNTH_DIR = Path(__file__).parent / ".synth"  # generated once, reused across runs (gitignored)
SEG_START, SEG_END = 20.0, 40.0


@pytest.fixture(scope="session")
def synth_files() -> dict[str, Path]:
    SYNTH_DIR.mkdir(exist_ok=True)
    video, song = SYNTH_DIR / "painting.mp4", SYNTH_DIR / "song.wav"
    stamp = SYNTH_DIR / "version.txt"
    version = "3"  # bump when synth.py changes
    if not (video.exists() and song.exists() and stamp.exists() and stamp.read_text() == version):
        synth.make_video(video)
        synth.make_song(song)
        stamp.write_text(version)
    return {"video": video, "song": song}


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    os.environ["PAINTREEL_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    from paintreel.config import app_settings

    s = app_settings()
    s.media_dirs = [SYNTH_DIR]
    return s


def ffprobe_streams(path: Path) -> list[dict]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-show_entries",
         "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,duration,nb_read_frames",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"]


def video_md5(path: Path) -> str:
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-c", "copy", "-f", "md5", "-"],
                          capture_output=True, text=True, check=True).stdout.strip()
