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


async def render_fillers() -> int:
    """Render every FILLER_POOL phrase to filler_<i>.mp3 + manifest.json.

    Uses the Birchwood Bree voice profile. Returns the count rendered. Shared by
    the startup warm-up and scripts/prerender_birchwood_fillers.py.
    """
    import json

    import src.config as config
    from src.utils.azure_tts import synthesize_speech
    from src.verticals.automotive_collision.voice_naturalness import FILLER_POOL

    _FILLER_DIR.mkdir(parents=True, exist_ok=True)
    voice = getattr(
        config, "BIRCHWOOD_AZURE_TTS_VOICE", "en-US-Bree:DragonHDLatestNeural"
    )
    rate = getattr(config, "BIRCHWOOD_AZURE_TTS_RATE", "+3%")
    pitch = getattr(config, "BIRCHWOOD_AZURE_TTS_PITCH", "+0%")
    break_ms = int(getattr(config, "BIRCHWOOD_AZURE_TTS_BREAK_MS", 250))
    expressive = bool(getattr(config, "BIRCHWOOD_TTS_EXPRESSIVE", True))

    rendered: list[str] = []
    for index, phrase in enumerate(FILLER_POOL):
        audio = await synthesize_speech(
            phrase,
            voice,
            rate=rate,
            pitch=pitch,
            break_ms=break_ms,
            expressive=expressive,
        )
        if not audio:
            continue
        name = f"filler_{index}.mp3"
        (_FILLER_DIR / name).write_bytes(audio)
        rendered.append(name)

    (_FILLER_DIR / "manifest.json").write_text(
        json.dumps(
            {"voice": voice, "files": rendered, "phrases": list(FILLER_POOL)}, indent=2
        ),
        encoding="utf-8",
    )
    available_filler_files(refresh=True)
    return len(rendered)


async def warm_up_fillers() -> None:
    """Pre-render the verbal fillers on startup if absent and TTS is configured.

    This makes the /thinking verbal filler "just work" in any environment with
    an Azure Speech key (the rendered MP3s are environment-specific and not in
    the container image), without a manual pre-render step. Non-fatal: if there's
    no key or rendering fails, the loop simply keeps using typing.wav.
    """
    import logging

    import src.config as config

    logger = logging.getLogger(__name__)
    try:
        if available_filler_files(refresh=True):
            return
        if not getattr(config, "AZURE_SPEECH_KEY", ""):
            return
        count = await render_fillers()
        logger.info(
            "[Fillers] Pre-rendered %s Birchwood verbal fillers on startup", count
        )
    except Exception:
        logger.warning("[Fillers] startup pre-render failed (non-fatal)", exc_info=True)
