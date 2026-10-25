"""
Chat & Voice endpoints for the mobile app.

POST /api/chat   { message, client_msg_id? }   → { reply, media_url? }
POST /api/voice  multipart/form-data           → { transcript, reply, media_url? }
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.database import forget_message_processed, mark_message_processed
from app.engine import handle_message
from app.export_utils import generate_excel
from app.messages import msg
from app.privacy import mask_phone
from app.routes.auth import current_owner
from app.security import is_rate_limited
from app.services.voice import transcribe_upload

logger = logging.getLogger("MunimJi.routes.chat")

router = APIRouter(tags=["Chat"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ChatReq(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    client_msg_id: str | None = Field(default=None, max_length=100)


class ChatResp(BaseModel):
    reply: str
    media_url: str | None = None


class VoiceResp(BaseModel):
    transcript: str
    reply: str
    media_url: str | None = None


# ---------------------------------------------------------------------------
# Internal helper — handles export generation for both endpoints
# ---------------------------------------------------------------------------
async def _maybe_generate_export(reply: str, owner_id: str) -> tuple[str, str | None]:
    """
    If reply is the __EXPORT__ sentinel, generate an Excel file.

    Returns:
        (final_reply, media_url_or_None)
    """
    if reply != "__EXPORT__":
        return reply, None

    filename = await asyncio.to_thread(generate_excel, owner_id)
    if not filename:
        logger.error(f"Export failed for owner={mask_phone(owner_id)}")
        return msg("EXPORT_FAIL"), None

    # Guard: sanity check filename
    if "/" in filename or ".." in filename:
        logger.error(f"Suspicious export filename: {filename}")
        return msg("EXPORT_FAIL"), None

    return msg("EXPORT_SUCCESS"), f"/api/export/{filename}"


# ---------------------------------------------------------------------------
# Text chat
# ---------------------------------------------------------------------------
@router.post("/chat", response_model=ChatResp)
async def chat(
    req: ChatReq,
    request: Request,
    owner_id: str = Depends(current_owner),
):
    if is_rate_limited(owner_id, request):
        raise HTTPException(429, msg("RATE_LIMITED"))

    # Idempotency
    message_sid = f"app:{owner_id}:{req.client_msg_id}" if req.client_msg_id else None
    if message_sid:
        first = await asyncio.to_thread(mark_message_processed, message_sid)
        if not first:
            raise HTTPException(409, "Duplicate message.")

    try:
        result = await asyncio.to_thread(handle_message, owner_id, req.message)
        if message_sid and result.get("reply") == msg("TECHNICAL_ERROR"):
            await asyncio.to_thread(forget_message_processed, message_sid)
        reply, media_url = await _maybe_generate_export(result["reply"], owner_id)
        return ChatResp(reply=reply, media_url=media_url or result.get("media_url"))
    except Exception:
        if message_sid:
            try:
                await asyncio.to_thread(forget_message_processed, message_sid)
            except Exception:
                logger.exception("Could not clear failed chat idempotency marker.")
        raise


# ---------------------------------------------------------------------------
# Voice chat
# ---------------------------------------------------------------------------
@router.post("/voice", response_model=VoiceResp)
async def voice(
    request: Request,
    file: UploadFile = File(...),  # noqa: B008
    client_msg_id: str | None = Form(None),
    owner_id: str = Depends(current_owner),
):
    if is_rate_limited(owner_id, request):
        raise HTTPException(429, msg("RATE_LIMITED"))

    # Idempotency check for voice retry
    message_sid = f"app:{owner_id}:{client_msg_id}" if client_msg_id else None
    if message_sid:
        first = await asyncio.to_thread(mark_message_processed, message_sid)
        if not first:
            raise HTTPException(409, "Duplicate voice message.")

    # Transcription (with size/MIME/extension validation)
    transcript, err = await transcribe_upload(file)
    if not transcript:
        logger.warning(f"Voice rejected for owner={mask_phone(owner_id)}: {err}")
        if message_sid:
            try:
                await asyncio.to_thread(forget_message_processed, message_sid)
            except Exception:
                logger.exception("Could not clear rejected voice idempotency marker.")
        raise HTTPException(400, err or msg("VOICE_ERROR"))

    # Process the transcribed text
    try:
        result = await asyncio.to_thread(handle_message, owner_id, transcript)
        if message_sid and result.get("reply") == msg("TECHNICAL_ERROR"):
            await asyncio.to_thread(forget_message_processed, message_sid)
        reply, media_url = await _maybe_generate_export(result["reply"], owner_id)
    except Exception:
        if message_sid:
            try:
                await asyncio.to_thread(forget_message_processed, message_sid)
            except Exception:
                logger.exception("Could not clear failed voice idempotency marker.")
        raise

    return VoiceResp(
        transcript=transcript,
        reply=reply,
        media_url=media_url or result.get("media_url"),
    )
