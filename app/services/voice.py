"""
Voice service — client uploads audio, we transcribe via Groq Whisper.

Validates extension, MIME type, and size before transcription.
"""

import asyncio
import logging
import os
import tempfile

from fastapi import UploadFile

from app.brain import transcribe_audio
from app.config import config

logger = logging.getLogger("MunimJi.services.voice")

ALLOWED_EXTENSIONS = {".m4a", ".mp3", ".ogg", ".wav", ".webm", ".aac", ".opus", ".flac"}

ALLOWED_MIME_PREFIXES = (
    "audio/",
    "video/mp4",
    "video/webm",
    "application/ogg",
)

_CHUNK_SIZE = 64 * 1024  # 64 KB


def _validate_extension(filename: str) -> tuple[bool, str]:
    if not filename:
        return False, "Missing filename."
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"File extension not allowed: {ext}"
    return True, ""


def _validate_mime(content_type: str | None) -> tuple[bool, str]:
    if not content_type:
        logger.warning("Audio upload missing Content-Type header.")
        return True, ""
    ct = content_type.lower().split(";")[0].strip()
    if not any(ct.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES):
        return False, f"Unsupported content type: {ct}"
    return True, ""


async def transcribe_upload(file: UploadFile) -> tuple[str | None, str | None]:
    """
    Save uploaded audio to temp file, transcribe, cleanup.

    Returns:
        (transcript, error_message)
    """
    max_bytes = config.MAX_AUDIO_MB * 1024 * 1024

    # Extension
    ok, err = _validate_extension(file.filename or "")
    if not ok:
        logger.warning(f"Upload rejected: {err}")
        return None, err

    # MIME
    ok, err = _validate_mime(file.content_type)
    if not ok:
        logger.warning(f"Upload rejected: {err}")
        return None, err

    # Size guard
    try:
        file.file.seek(0, os.SEEK_END)
        size = file.file.tell()
        file.file.seek(0)
    except Exception:
        size = -1

    if size == 0:
        return None, "Empty audio file."
    if size > max_bytes:
        logger.warning(f"Audio too big: {size} bytes (max {max_bytes})")
        return None, f"Audio too large (max {config.MAX_AUDIO_MB} MB)."

    # Stream to temp file
    ext = os.path.splitext(file.filename or "")[1].lower()
    fd, temp_path = tempfile.mkstemp(suffix=ext, prefix="munim_voice_")

    try:
        written = 0
        with os.fdopen(fd, "wb") as f:
            while chunk := await file.read(_CHUNK_SIZE):
                written += len(chunk)
                if written > max_bytes:
                    logger.warning(f"Upload exceeded limit while streaming: {written}")
                    return None, f"Audio too large (max {config.MAX_AUDIO_MB} MB)."
                f.write(chunk)

        transcript = await asyncio.to_thread(transcribe_audio, temp_path)
        if not transcript:
            return None, "Transcription failed."
        return transcript.strip() if isinstance(transcript, str) else transcript, None
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass
