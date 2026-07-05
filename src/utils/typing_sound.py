"""
Typing Sound Generator — Faint keyboard clicking audio for hold music.

Generates a short WAV audio clip of realistic keyboard typing sounds.
Used as auditory feedback while the LLM is processing, so the caller
doesn't sit in silence.
"""

from __future__ import annotations

import io
import math
import random
import struct
import wave

# Pre-generated audio bytes (lazily initialized, keyed by duration)
_typing_wav_cache: dict[float, bytes] = {}

# Audio parameters
_SAMPLE_RATE = 8000  # 8kHz — Twilio telephony standard
_DURATION_S = 3.5  # seconds per loop iteration
# Short bed for the hold loop's later cycles: the <Redirect> back to /thinking
# only fires when the <Play> finishes, so bed length sets how quickly a
# finished LLM turn can actually be delivered to the caller.
_SHORT_DURATION_S = 1.6
_CHANNELS = 1
_SAMPLE_WIDTH = 2  # 16-bit


def _generate_click(length: int, amplitude: float) -> list[int]:
    """Generate a single key-click: a short burst of filtered noise with decay."""
    samples = []
    for i in range(length):
        # Exponential decay envelope
        env = math.exp(-6.0 * i / length)
        # Band-limited noise: blend two sine components for a "tock" sound
        t = i / _SAMPLE_RATE
        tone = (
            0.5 * math.sin(2 * math.pi * 3500 * t)  # high tick
            + 0.3 * math.sin(2 * math.pi * 1800 * t)  # mid body
            + 0.2 * (random.random() * 2 - 1)  # noise texture
        )
        samples.append(int(tone * env * amplitude))
    return samples


def _generate_typing_wav(duration_s: float = _DURATION_S) -> bytes:
    """Generate a WAV file with faint keyboard typing sounds.

    Returns raw WAV bytes suitable for Twilio <Play>.
    """
    total_samples = int(duration_s * _SAMPLE_RATE)
    audio: list[int] = [0] * total_samples

    # Seed for reproducibility across restarts (same sound each time)
    rng = random.Random(42)

    pos = 0
    while pos < total_samples - 200:
        # Click duration: 4-10ms (32-80 samples at 8kHz)
        click_len = int(rng.uniform(0.004, 0.010) * _SAMPLE_RATE)
        # Amplitude: faint (600-1200 out of 32767)
        amplitude = rng.uniform(600, 1200)
        # Gap between clicks: 80-220ms (simulates typing rhythm)
        gap = int(rng.uniform(0.08, 0.22) * _SAMPLE_RATE)

        # Occasional longer pause (word boundary / thinking)
        if rng.random() < 0.15:
            gap = int(rng.uniform(0.3, 0.6) * _SAMPLE_RATE)

        click = _generate_click(min(click_len, total_samples - pos), amplitude)
        for i, s in enumerate(click):
            if pos + i < total_samples:
                audio[pos + i] = max(-32767, min(32767, audio[pos + i] + s))

        pos += click_len + gap

    # Encode as WAV
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(_CHANNELS)
        wav.setsampwidth(_SAMPLE_WIDTH)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(struct.pack(f"<{len(audio)}h", *audio))

    return buf.getvalue()


def get_typing_sound_wav() -> bytes:
    """Get the typing sound WAV bytes (cached after first generation)."""
    return _get_typing_wav(_DURATION_S)


def get_short_typing_sound_wav() -> bytes:
    """Short (~1.6s) typing bed used by the hold loop's later cycles."""
    return _get_typing_wav(_SHORT_DURATION_S)


def _get_typing_wav(duration_s: float) -> bytes:
    cached = _typing_wav_cache.get(duration_s)
    if cached is None:
        cached = _generate_typing_wav(duration_s)
        _typing_wav_cache[duration_s] = cached
    return cached
