"""
Azure Speech TTS — Text-to-Speech via Azure AI Speech Service

Generates natural-sounding speech audio using Azure's neural voices,
uploads to blob storage, and returns a public URL for Twilio <Play>.

Flow:
  1. Text → Azure Speech REST API → MP3 audio bytes
  2. Audio bytes → Azure Blob Storage (tts-audio container, private + SAS)
  3. SAS URL returned → used in TwiML <Play> tag

Fully async — uses httpx.AsyncClient so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Optional
from xml.sax import saxutils

import httpx

logger = logging.getLogger(__name__)

# Cache: maps cache_key → (url, expiry_timestamp)
_audio_cache: dict[str, tuple[str, float]] = {}

# SAS token lifetime — 24 hours gives plenty of buffer
_SAS_LIFETIME_HOURS = 24
# Refresh cache entries when less than 1 hour remains
_SAS_REFRESH_THRESHOLD_SECONDS = 3600

# Reusable async client (created lazily)
_async_client: Optional[httpx.AsyncClient] = None


async def warm_up() -> None:
    """Pre-warm the TTS pipeline so the first real call is fast.

    Synthesizes a short phrase and uploads to blob.  Call this from
    the app lifespan startup handler.
    """
    try:
        from src.config import AZURE_SPEECH_KEY, AZURE_TTS_VOICE

        if not AZURE_SPEECH_KEY:
            return
        voice = AZURE_TTS_VOICE or "en-US-AvaMultilingualNeural"
        logger.info("[AzureTTS] Pre-warming TTS pipeline...")
        url = await text_to_speech_url("Hello.", voice)
        if url:
            logger.info("[AzureTTS] Pre-warm complete — pipeline ready")
        else:
            logger.warning(
                "[AzureTTS] Pre-warm returned None (will retry on first call)"
            )
    except Exception as exc:
        logger.warning(f"[AzureTTS] Pre-warm failed (non-fatal): {exc}")


def _get_client() -> httpx.AsyncClient:
    """Get or create a reusable async HTTP client."""
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(timeout=15.0)
    return _async_client


def _cache_key(
    text: str,
    voice: str,
    rate: str,
    pitch: str,
    style: str | None,
    break_ms: int,
) -> str:
    """Generate a deterministic cache key from the rendered speech profile."""
    cache_input = f"{voice}:{rate}:{pitch}:{style or ''}:{break_ms}:{text}"
    return hashlib.sha256(cache_input.encode()).hexdigest()[:16]


def _build_ssml(
    text: str,
    voice: str,
    *,
    rate: str,
    pitch: str,
    style: str | None,
    break_ms: int,
) -> str:
    escaped_text = saxutils.escape(text)
    sentence_pause = max(100, int(break_ms))
    comma_pause = max(75, sentence_pause // 2)
    prosody = (
        f"<prosody rate='{rate}' pitch='{pitch}' volume='+5%'>{escaped_text}</prosody>"
    )
    if style:
        prosody = f"<mstts:express-as style='{style}'>{prosody}</mstts:express-as>"

    return f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis'
    xmlns:mstts='https://www.w3.org/2001/mstts' xml:lang='en-US'>
    <voice name='{voice}'>
        <mstts:silence type='Sentenceboundary' value='{sentence_pause}ms'/>
        <mstts:silence type='Comma-Semicolon' value='{comma_pause}ms'/>
        {prosody}
    </voice>
</speak>"""


async def synthesize_speech(
    text: str,
    voice: str = "en-US-AvaMultilingualNeural",
    speech_key: Optional[str] = None,
    speech_region: Optional[str] = None,
    *,
    rate: str = "-3%",
    pitch: str = "+2%",
    style: str | None = None,
    break_ms: int = 250,
    fallback_voice: str | None = None,
) -> Optional[bytes]:
    """Synthesize speech using Azure Speech REST API (async).

    Args:
        text: The text to convert to speech.
        voice: Azure neural voice name.
        speech_key: Azure Speech API key (falls back to config).
        speech_region: Azure Speech region (falls back to config).

    Returns:
        Raw audio bytes (MP3 format) or None on failure.
    """
    from src.config import AZURE_SPEECH_KEY, AZURE_SPEECH_REGION

    key = speech_key or AZURE_SPEECH_KEY
    region = speech_region or AZURE_SPEECH_REGION

    if not key or not region:
        logger.warning(
            "[AzureTTS] AZURE_SPEECH_KEY or AZURE_SPEECH_REGION not configured"
        )
        return None

    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"

    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
        "User-Agent": "NurseTriageAssistant/5.0",
    }

    try:
        start = time.time()
        client = _get_client()
        attempts: list[tuple[str, str | None]] = [(voice, style or None)]
        if style:
            attempts.append((voice, None))
        fallback_candidate = (fallback_voice or "").strip()
        if fallback_candidate and fallback_candidate != voice:
            attempts.append((fallback_candidate, None))

        for attempt_voice, attempt_style in attempts:
            ssml = _build_ssml(
                text,
                attempt_voice,
                rate=rate,
                pitch=pitch,
                style=attempt_style,
                break_ms=break_ms,
            )
            response = await client.post(url, content=ssml, headers=headers)
            elapsed = time.time() - start

            if response.status_code == 200:
                logger.info(
                    "[AzureTTS] Synthesized %s chars in %.2fs (%s bytes) using voice=%s style=%s",
                    len(text),
                    elapsed,
                    len(response.content),
                    attempt_voice,
                    attempt_style or "none",
                )
                return response.content

            logger.warning(
                "[AzureTTS] Attempt failed: HTTP %s voice=%s style=%s - %s",
                response.status_code,
                attempt_voice,
                attempt_style or "none",
                response.text[:200],
            )

        return None

    except Exception as exc:
        logger.error(f"[AzureTTS] Request failed: {exc}")
        return None


def _upload_to_blob(audio_bytes: bytes, blob_name: str) -> Optional[str]:
    """Upload audio to blob storage and return a SAS URL (sync, run in thread)."""
    from src.config import AZURE_STORAGE_CONNECTION_STRING

    if not AZURE_STORAGE_CONNECTION_STRING:
        logger.warning("[AzureTTS] No blob storage configured")
        return None

    from azure.storage.blob import (
        BlobServiceClient,
        ContentSettings,
        generate_blob_sas,
        BlobSasPermissions,
    )
    from datetime import datetime, timedelta, timezone

    blob_service = BlobServiceClient.from_connection_string(
        AZURE_STORAGE_CONNECTION_STRING
    )
    container_name = "tts-audio"
    container_client = blob_service.get_container_client(container_name)

    if not container_client.exists():
        container_client.create_container()
        logger.info(f"[AzureTTS] Created blob container '{container_name}'")

    container_client.upload_blob(
        name=blob_name,
        data=audio_bytes,
        overwrite=True,
        content_settings=ContentSettings(content_type="audio/mpeg"),
    )

    account_name = blob_service.account_name
    account_key = blob_service.credential.account_key
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=_SAS_LIFETIME_HOURS),
    )
    url = f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}?{sas_token}"
    expiry_ts = time.time() + (_SAS_LIFETIME_HOURS * 3600)
    return url, expiry_ts


async def text_to_speech_url(
    text: str,
    voice: str = "en-US-AvaMultilingualNeural",
    *,
    rate: str = "-3%",
    pitch: str = "+2%",
    style: str | None = None,
    break_ms: int = 250,
    fallback_voice: str | None = None,
) -> Optional[str]:
    """Convert text to speech and return a publicly accessible audio URL (async).

    Overall pipeline timeout is 14 seconds — falls back to None (Polly) on failure.
    """
    from src.config import AZURE_SPEECH_KEY

    if not AZURE_SPEECH_KEY:
        return None

    cache_k = _cache_key(text, voice, rate, pitch, style, break_ms)

    if cache_k in _audio_cache:
        url, expiry_ts = _audio_cache[cache_k]
        if time.time() < (expiry_ts - _SAS_REFRESH_THRESHOLD_SECONDS):
            logger.debug(f"[AzureTTS] Cache hit: {cache_k}")
            return url
        else:
            logger.info(
                f"[AzureTTS] Cache entry expiring soon, regenerating: {cache_k}"
            )
            del _audio_cache[cache_k]

    try:
        # Synthesize (up to 10s — DragonHD voices are slower for long text)
        audio_bytes = await asyncio.wait_for(
            synthesize_speech(
                text,
                voice,
                rate=rate,
                pitch=pitch,
                style=style,
                break_ms=break_ms,
                fallback_voice=fallback_voice,
            ),
            timeout=10.0,
        )
        if not audio_bytes:
            return None

        # Blob upload in thread (up to 8s — first call creates container)
        blob_name = f"{cache_k}.mp3"
        result = await asyncio.wait_for(
            asyncio.to_thread(_upload_to_blob, audio_bytes, blob_name),
            timeout=8.0,
        )

        if result:
            blob_url, expiry_ts = result
            _audio_cache[cache_k] = (blob_url, expiry_ts)
            logger.info(
                f"[AzureTTS] Ready: {blob_name} (expires in {_SAS_LIFETIME_HOURS}h)"
            )
            return blob_url
        return None

    except asyncio.TimeoutError:
        logger.warning("[AzureTTS] Pipeline timed out — falling back to Polly")
        return None
    except Exception as exc:
        logger.error(f"[AzureTTS] Pipeline failed: {exc}")
        return None
