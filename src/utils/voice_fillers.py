"""Pre-rendered Birchwood verbal-filler audio (Aurora naturalness pass).

The ``/thinking`` poll loop plays a short spoken filler ("Okay, one sec.")
instead of the keyboard-typing bed FOR BIRCHWOOD CALLS ONLY, when the audio has
been pre-rendered by ``scripts/prerender_birchwood_fillers.py`` (which needs
Azure Speech credentials). Until that operator step runs the directory is empty,
``available_filler_files()`` returns ``[]``, and the loop falls back to
``typing.wav`` — so healthcare and any un-rendered deploy are never affected.

Pre-rendering avoids per-use TTS latency: the fillers exist to mask the
STT-finalization gap, so they must play instantly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_FILLER_DIR = (
    Path(__file__).resolve().parent.parent / "voice_audio" / "birchwood_fillers"
)
_MANIFEST = _FILLER_DIR / "manifest.json"
_NAME_RE = re.compile(r"^filler_\d+\.mp3$")

# Cache the available-files list (refreshed explicitly). None = not yet loaded.
_cache: list[str] | None = None


def fillers_dir() -> Path:
    return _FILLER_DIR


def available_filler_files(refresh: bool = False) -> list[str]:
    """Filler filenames that are present on disk (from the manifest), or []."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    files: list[str] = []
    try:
        if _MANIFEST.exists():
            data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
            files = [
                name
                for name in data.get("files", [])
                if isinstance(name, str)
                and _NAME_RE.match(name)
                and (_FILLER_DIR / name).exists()
            ]
    except Exception:
        files = []
    _cache = files
    return files


def get_filler_audio(filename: str) -> bytes | None:
    """Read a filler MP3 by name (path-traversal-safe), or None if absent."""
    name = Path(filename).name
    if not _NAME_RE.match(name):
        return None
    path = _FILLER_DIR / name
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except Exception:
        return None
