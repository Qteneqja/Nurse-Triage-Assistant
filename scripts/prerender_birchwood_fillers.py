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
from src.utils.voice_fillers import fillers_dir, render_fillers
from src.verticals.automotive_collision.voice_naturalness import FILLER_POOL


async def main() -> int:
    count = await render_fillers()
    out = fillers_dir()
    print(f"Wrote {count}/{len(FILLER_POOL)} fillers + manifest to {out}")
    if not count:
        print(
            "No audio was rendered — check AZURE_SPEECH_KEY / AZURE_SPEECH_REGION. "
            "The /thinking loop will keep using typing.wav until this succeeds."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
