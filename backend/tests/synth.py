"""Synthetic test data: a fake overhead watercolor video and a click-track song with known structure.

Video timeline (seconds), 640x360 @ 30 fps, keyframe every 30 frames:
  0-5      blank paper, no hand
  5-58     blobs painted one after another, moving skin-coloured hand (absent 40-50)
  60-75    idle: nothing changes, painter absent
  75-103   more blobs, hand present until 105
  85-86.5  camera bump (frame shifted 12 px)
  95-98    lighting dip (x0.75)
  105-120  finished painting, no hand (clean)
A 12-bit binary frame index is drawn above the paper (outside the canvas) for decoding in tests.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 640, 360, 30
DURATION = 120.0
CANVAS = (120, 40, 400, 300)  # x, y, w, h
CODE_BITS, CODE_BLOCK, CODE_X, CODE_Y = 12, 16, 8, 8
HAND_ABSENT = [(0, 5), (40, 50), (58, 75), (105, 120)]
IDLE = (60, 75)
BUMP = (85, 86.5)
LIGHT_DIP = (95, 98)
CLEAN_TAIL = (105, 120)
BPM = 120.0
SONG_DURATION = 60.0
DROP_AT = 30.0

COLORS = [(180, 80, 20), (60, 140, 40), (150, 40, 120), (200, 160, 40), (40, 90, 160), (120, 120, 30),
          (190, 60, 90), (70, 50, 140)]  # BGR, deliberately not skin-like


@dataclass
class Blob:
    start: float
    dur: float
    cx: int
    cy: int
    r: int
    color: tuple[int, int, int]


def blobs() -> list[Blob]:
    rng = np.random.default_rng(7)
    starts = list(np.arange(5, 55, 6.0)) + list(np.arange(76, 100, 6.0))
    x, y, w, h = CANVAS
    out = []
    for i, s in enumerate(starts):
        out.append(Blob(float(s), 5.0, int(rng.integers(x + 50, x + w - 50)), int(rng.integers(y + 50, y + h - 50)),
                        int(rng.integers(30, 60)), COLORS[i % len(COLORS)]))
    return out


def in_any(t: float, spans: list[tuple[float, float]]) -> bool:
    return any(a <= t < b for a, b in spans)


def frame_at(f: int, bl: list[Blob]) -> np.ndarray:
    t = f / FPS
    img = np.full((H, W, 3), 70, np.uint8)  # desk
    x, y, w, h = CANVAS
    img[y:y + h, x:x + w] = (238, 240, 242)  # paper
    for b in bl:
        k = np.clip((t - b.start) / b.dur, 0, 1)
        if k > 0:
            cv2.circle(img, (b.cx, b.cy), max(1, int(b.r * k)), b.color, -1, cv2.LINE_AA)
    if not in_any(t, HAND_ABSENT):
        active = min(bl, key=lambda b: abs(t - (b.start + b.dur / 2)))
        hx = int(active.cx + 40 * np.sin(t * 2.1))
        hy = int(active.cy + 25 * np.cos(t * 1.7))
        cv2.rectangle(img, (hx - 25, hy), (hx + 25, hy + 70), (120, 160, 210), -1)  # hand
        cv2.rectangle(img, (hx - 18, hy + 70), (hx + 18, H), (110, 150, 200), -1)  # arm
    for i in range(CODE_BITS):
        bit = (f >> (CODE_BITS - 1 - i)) & 1
        x0 = CODE_X + i * CODE_BLOCK
        img[CODE_Y:CODE_Y + CODE_BLOCK, x0:x0 + CODE_BLOCK] = 255 if bit else 0
    if in_any(t, [LIGHT_DIP]):
        img = (img.astype(np.float32) * 0.75).astype(np.uint8)
    if in_any(t, [BUMP]):
        img = np.roll(img, (12, 12), axis=(0, 1))
    return img


def make_video(path: Path) -> Path:
    bl = blobs()
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
           "-g", "30", "-pix_fmt", "yuv420p", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for f in range(int(DURATION * FPS)):
        proc.stdin.write(frame_at(f, bl).tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return path


def decode_code(img: np.ndarray, x0: float, y0: float, scale: float) -> int:
    """Read the frame-index strip from an image where source pixel (u, v) maps to (x0 + u*scale, y0 + v*scale)."""
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    val = 0
    for i in range(CODE_BITS):
        cx = x0 + (CODE_X + (i + 0.5) * CODE_BLOCK) * scale
        cy = y0 + (CODE_Y + 0.5 * CODE_BLOCK) * scale
        r = max(1, int(CODE_BLOCK * scale * 0.25))
        patch = gray[int(cy) - r:int(cy) + r, int(cx) - r:int(cx) + r]
        val = (val << 1) | int(patch.mean() > 128)
    return val


def make_song(path: Path) -> Path:
    import librosa
    import soundfile as sf

    sr = 44100
    n = int(SONG_DURATION * sr)
    t = np.arange(n) / sr
    beat_times = np.arange(0, SONG_DURATION, 60.0 / BPM)
    y = np.zeros(n, np.float32)
    for k, bt in enumerate(beat_times):
        loud = bt >= DROP_AT
        amp = (0.9 if loud else 0.3) * (1.0 if k % 4 == 0 else 0.6)
        freq = 1500.0 if k % 4 == 0 else 1000.0
        y += amp * librosa.clicks(times=[bt], sr=sr, click_freq=freq, click_duration=0.06, length=n)
    rng = np.random.default_rng(1)
    pad = 0.05 * np.sin(2 * np.pi * 220 * t)
    bass = 0.25 * np.sin(2 * np.pi * 55 * t) * (t >= DROP_AT)  # sustained: raises energy, no onsets
    noise = rng.normal(0, 0.01, n)
    y = (y + pad + bass + noise).astype(np.float32)
    y /= max(1.0, np.abs(y).max() / 0.95)
    sf.write(str(path), y, sr)
    return path


def true_beats(start: float, end: float) -> np.ndarray:
    beats = np.arange(0, SONG_DURATION, 60.0 / BPM)
    return beats[(beats >= start) & (beats <= end)] - start
