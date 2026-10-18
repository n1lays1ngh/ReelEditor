"""Uploaded assets (videos, songs): listing, uploads, deletion and path access control."""
from __future__ import annotations

import os
import re
import shutil
import threading
from pathlib import Path

from fastapi import UploadFile

from ..cache import fingerprint
from ..config import AppSettings
from ..tools import probe_duration


class UploadTooLarge(ValueError):
    pass


_durations: dict[tuple[str, float, int], float | None] = {}
_dur_lock = threading.Lock()


def media_duration(p: Path) -> float | None:
    """Duration in seconds (cached per file version), None if ffprobe can't read it."""
    st = p.stat()
    key = (str(p), st.st_mtime, st.st_size)
    with _dur_lock:
        if key in _durations:
            return _durations[key]
    try:
        d: float | None = probe_duration(p)
    except Exception:
        d = None
    with _dur_lock:
        _durations[key] = d
    return d


def _iter_files(root: Path, exts: set[str], max_depth: int):
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - root_depth
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and depth < max_depth]
        for name in filenames:
            if not name.startswith(".") and Path(name).suffix.lower() in exts:
                yield Path(dirpath) / name


def list_media(settings: AppSettings, kind: str, limit: int = 300) -> list[dict]:
    """Uploaded files first, then files in the optional media folders; newest first."""
    exts = {e.lower() for e in (settings.video_extensions if kind == "video" else settings.song_extensions)}
    upload_root = settings.uploads_dir / kind
    roots = [upload_root] + [d for d in settings.media_dirs if d.is_dir()]
    seen: set[str] = set()
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in _iter_files(root, exts, settings.max_scan_depth):
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({"path": str(p), "name": p.name, "size": st.st_size, "mtime": st.st_mtime,
                        "uploaded": root == upload_root})
    out.sort(key=lambda f: f["mtime"], reverse=True)
    out = out[:limit]
    for f in out:
        f["duration"] = media_duration(Path(f["path"]))
    return out


def safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name).name).strip() or "upload"
    return stem[:150]


async def save_upload(settings: AppSettings, kind: str, file: UploadFile) -> Path:
    exts = settings.video_extensions if kind == "video" else settings.song_extensions
    name = safe_name(file.filename or "upload")
    if Path(name).suffix.lower() not in {e.lower() for e in exts}:
        raise ValueError(f"unsupported file type '{Path(name).suffix}'. Allowed: {', '.join(exts)}")
    dest_dir = settings.uploads_dir / kind
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    i = 1
    while dest.exists():
        dest = dest_dir / f"{Path(name).stem}-{i}{Path(name).suffix}"
        i += 1
    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    tmp = dest.with_name(dest.name + ".part")
    try:
        with open(tmp, "wb") as f:
            while chunk := await file.read(4 * 1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise UploadTooLarge(f"file is larger than the {settings.max_upload_mb} MB limit")
                f.write(chunk)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def delete_asset(settings: AppSettings, path: str) -> None:
    """Delete an uploaded file and everything cached for it. Files outside the uploads folder are never touched."""
    p = Path(path).expanduser().resolve()
    if not p.is_relative_to(settings.uploads_dir.resolve()):
        raise PermissionError("only uploaded files can be deleted")
    if not p.is_file():
        raise FileNotFoundError("file not found")
    fp = fingerprint(p)
    for d in (settings.cache_dir / "video").glob(f"{fp}-*"):
        shutil.rmtree(d, ignore_errors=True)
    for f in (settings.cache_dir / "audio").glob("*.json"):
        if fp in f.name:
            f.unlink(missing_ok=True)
    for f in (settings.cache_dir / "frames").glob(f"{fp}-*"):
        f.unlink(missing_ok=True)
    p.unlink()


class PathGuard:
    """Only serve files under the data folder, configured media folders, or paths used by earlier jobs."""

    def __init__(self, settings: AppSettings) -> None:
        self.roots = [settings.data_dir.resolve()] + [d.resolve() for d in settings.media_dirs if d.exists()]
        self.allowed: set[str] = set()

    def allow(self, path: str | Path) -> None:
        self.allowed.add(str(Path(path).expanduser().resolve()))

    def check(self, path: str | Path) -> Path:
        p = Path(path).expanduser().resolve()
        if str(p) in self.allowed or any(p.is_relative_to(r) for r in self.roots):
            return p
        raise PermissionError(f"path is outside the allowed folders: {p}")
