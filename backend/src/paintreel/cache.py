"""Disk cache keys: content fingerprint of (possibly multi-GB) files + parameter hash."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

CHUNK = 8 * 1024 * 1024
_memo: dict[tuple[str, int, float], str] = {}


def fingerprint(path: Path) -> str:
    """sha1 of size + first/middle/last 8 MB. Fast for huge files, robust to renames."""
    st = os.stat(path)
    memo_key = (str(Path(path).resolve()), st.st_size, st.st_mtime)
    if memo_key in _memo:
        return _memo[memo_key]
    h = hashlib.sha1(str(st.st_size).encode())
    with open(path, "rb") as f:
        for offset in sorted({0, max(0, st.st_size // 2 - CHUNK // 2), max(0, st.st_size - CHUNK)}):
            f.seek(offset)
            h.update(f.read(CHUNK))
    _memo[memo_key] = h.hexdigest()[:16]
    return _memo[memo_key]


def cache_key(fp: str, params: Any) -> str:
    if isinstance(params, BaseModel):
        params = params.model_dump(mode="json")
    blob = json.dumps(params, sort_keys=True, default=str)
    return f"{fp}-{hashlib.sha1(blob.encode()).hexdigest()[:12]}"


def load_model(path: Path, model: type[BaseModel]) -> Any | None:
    if not path.exists():
        return None
    try:
        return model.model_validate_json(path.read_text())
    except Exception:  # stale/corrupt cache entry: recompute
        return None


def save_model(path: Path, obj: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(obj.model_dump_json())
    tmp.replace(path)
