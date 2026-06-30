"""Pre-render the Birchwood verbal fillers to MP3 (Aurora naturalness pass).

Run ONCE on a machine with Azure Speech credentials to populate
``src/voice_audio/birchwood_fillers/`` with ``filler_<i>.mp3`` + ``manifest.json``.
The ``/thinking`` poll loop then plays a short spoken filler for Birchwood calls
instead of the keyboard-typing bed; without this step the loop falls back to
``typing.wav``.

    python -m scripts.prerender_birchwood_fillers

Requires AZURE_SPEECH_KEY + AZURE_SPEECH_REGION. Audio is rendered with the
Birchwood Bree DragonHD voice profile so the fillers match Aurora's voice.
"""

from __future__ import annotations

import asyncio
import json

import src.config as config
from src.utils.azure_tts import synthesize_speech
from src.utils.voice_fillers import fillers_dir
from src.verticals.automotive_collision.voice_naturalness import FILLER_POOL


async def main() -> int:
    out = fillers_dir()
    out.mkdir(parents=True, exist_ok=True)

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
            print(f"  FAILED to render filler {index}: {phrase!r}")
            continue
        name = f"filler_{index}.mp3"
        (out / name).write_bytes(audio)
        rendered.append(name)
        print(f"  rendered {name}: {phrase!r}")

    manifest = {"voice": voice, "files": rendered, "phrases": list(FILLER_POOL)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(rendered)}/{len(FILLER_POOL)} fillers + manifest to {out}")
    if not rendered:
        print(
            "No audio was rendered — check AZURE_SPEECH_KEY / AZURE_SPEECH_REGION. "
            "The /thinking loop will keep using typing.wav until this succeeds."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
