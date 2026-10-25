"""
OTP-based phone login.

POST /api/auth/send-otp
POST /api/auth/verify-otp
"""

import datetime
import hmac
import logging
import time

import uuid

import jwt
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import config
from app.database import AccountDeletion, get_db
from app.privacy import mask_phone
from app.security import is_token_revoked, revoke_token
from app.services.otp import send_otp, verify_firebase_id_token, verify_otp

logger = logging.getLogger("MunimJi.routes.auth")

router = APIRouter(tags=["Auth"])

# In-memory rate-limit state (use shared storage in multi-worker deployments).
_verify_ip_failures: dict[str, list[float]] = {}
_send_phone_attempts: dict[str, list[float]] = {}
_send_ip_attempts: dict[str, list[float]] = {}
_MAX_RATE_LIMIT_BUCKETS = 10_000
_last_rate_limit_gc = time.time()


class SendOtpReq(BaseModel):
    phone: str = Field(min_length=8, max_length=20)


class VerifyOtpReq(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    otp: str | None = Field(default=None, min_length=4, max_length=8)
    firebase_id_token: str | None = Field(default=None, min_length=1, max_length=8192)


def _normalize_phone(phone: str) -> str:
    """Normalize to +91 format if 10 digits."""
    p = phone.strip().replace(" ", "").replace("-", "")
    if not p.startswith("+"):
        p = "+91" + p if (len(p) == 10 and p.isdigit()) else "+" + p
    return p


def _prune_rate_limits(now: float) -> None:
    """Expire old buckets and cap memory use for attacker-controlled keys."""
    global _last_rate_limit_gc
    if now - _last_rate_limit_gc < 60:
        return
    windows = (
        (_verify_ip_failures, config.OTP_WINDOW_MINUTES * 60),
        (_send_phone_attempts, config.OTP_SEND_WINDOW_MINUTES * 60),
        (_send_ip_attempts, config.OTP_SEND_WINDOW_MINUTES * 60),
    )
    for buckets, window in windows:
        for key, times in list(buckets.items()):
            recent = [t for t in times if now - t < window]
            if recent:
                buckets[key] = recent
            else:
                del buckets[key]
    _last_rate_limit_gc = now


def _remember_attempt(buckets: dict[str, list[float]], key: str, now: float) -> None:
    """Record an attempt while bounding attacker-controlled bucket growth."""
    if key not in buckets and len(buckets) >= _MAX_RATE_LIMIT_BUCKETS:
        oldest_key = min(buckets, key=lambda item: max(buckets[item], default=0))
        del buckets[oldest_key]
    buckets.setdefault(key, []).append(now)


def _check_verify_limit(client_ip: str) -> None:
    """Limit failed OTP checks per IP without letting attackers lock a phone out."""
    now = time.time()
    _prune_rate_limits(now)
    window = config.OTP_WINDOW_MINUTES * 60
    recent = [t for t in _verify_ip_failures.get(client_ip, []) if now - t < window]
    limit = max(config.OTP_MAX_ATTEMPTS * 5, 20)
    if len(recent) >= limit:
        raise HTTPException(429, "Too many attempts. Please try again later.")


def _record_verify_failure(client_ip: str) -> None:
    now = time.time()
    _remember_attempt(_verify_ip_failures, client_ip, now)


def _check_send_limit(phone: str, client_ip: str | None) -> None:
    """Limit OTP sends per phone and direct client IP."""
    now = time.time()
    _prune_rate_limits(now)
    window = config.OTP_SEND_WINDOW_MINUTES * 60
    phone_recent = [t for t in _send_phone_attempts.get(phone, []) if now - t < window]
    ip_recent = []
    if client_ip:
        ip_recent = [t for t in _send_ip_attempts.get(client_ip, []) if now - t < window]

    if len(phone_recent) >= config.OTP_SEND_MAX_ATTEMPTS:
        raise HTTPException(429, "Too many OTP requests. Please try again later.")
    if client_ip and len(ip_recent) >= config.OTP_SEND_MAX_ATTEMPTS * 5:
        raise HTTPException(429, "Too many OTP requests. Please try again later.")

    _remember_attempt(_send_phone_attempts, phone, now)
    if client_ip:
        _remember_attempt(_send_ip_attempts, client_ip, now)


def _issue_token(phone: str) -> str:
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "sub": phone,
        "jti": uuid.uuid4().hex,
        "iat": now.timestamp(),
        "exp": int((now + datetime.timedelta(days=config.JWT_EXPIRY_DAYS)).timestamp()),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


@router.post("/auth/send-otp")
def send(req: SendOtpReq, request: Request):
    phone = _normalize_phone(req.phone)
    client_ip = request.client.host if request.client else None
    _check_send_limit(phone, client_ip)
    if not send_otp(phone):
        raise HTTPException(500, "Could not send OTP.")
    resp: dict = {"status": "sent", "phone": phone}
    # In dev mode, return the OTP so the Flutter UI can show it to the user
    if config.OTP_PROVIDER == "dev" and not config.IS_PROD:
        resp["otp"] = config.DEV_OTP
        resp["test_otp"] = config.DEV_OTP
    return resp


@router.post("/auth/verify-otp")
def verify(req: VerifyOtpReq, request: Request):
    phone = _normalize_phone(req.phone)
    client_ip = request.client.host if request.client else "unknown"
    _check_verify_limit(client_ip)

    # Play Store review account bypass
    if (
        config.REVIEW_PHONE
        and config.REVIEW_OTP
        and phone == _normalize_phone(config.REVIEW_PHONE)
    ):
        if req.otp is not None and hmac.compare_digest(req.otp.encode(), config.REVIEW_OTP.encode()):
            logger.warning(f"Review login: {mask_phone(phone)}")
            return {"token": _issue_token(phone), "phone": phone}
        _record_verify_failure(client_ip)
        raise HTTPException(401, "Invalid OTP.")

    if config.OTP_PROVIDER == "firebase":
        if not req.firebase_id_token or not verify_firebase_id_token(
            req.firebase_id_token, phone
        ):
            _record_verify_failure(client_ip)
            raise HTTPException(401, "Invalid Firebase authentication token.")
    if not req.otp or not verify_otp(phone, req.otp):
        _record_verify_failure(client_ip)
        raise HTTPException(401, "Invalid OTP.")

    return {"token": _issue_token(phone), "phone": phone}


@router.post("/auth/logout")
def logout(authorization: str = Header(None)):
    """Revoke the current bearer token."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        jti = payload.get("jti")
        if jti:
            revoke_token(jti)
    except Exception:
        pass
    revoke_token(token)
    return {"status": "logged_out"}


# ---------------------------------------------------------------------------
# Auth dependency — extracts owner_id from Bearer JWT
# ---------------------------------------------------------------------------
def current_owner(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token.")
    token = authorization.split(" ", 1)[1].strip()
    if is_token_revoked(token):
        raise HTTPException(401, "Token has been revoked.")

    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired.") from None
    except Exception:
        raise HTTPException(401, "Invalid token.") from None

    jti = payload.get("jti")
    if jti and is_token_revoked(jti):
        raise HTTPException(401, "Token has been revoked.")

    phone = payload.get("sub")
    issued_at = payload.get("iat")
    if not phone or isinstance(issued_at, bool) or not isinstance(issued_at, int | float):
        raise HTTPException(401, "Invalid token payload.")

    with get_db() as db:
        deletion = (
            db.query(AccountDeletion)
            .filter_by(owner_id=phone)
            .order_by(AccountDeletion.requested_at.desc())
            .first()
        )
    if deletion and issued_at <= deletion.requested_at.replace(tzinfo=datetime.UTC).timestamp():
        raise HTTPException(401, "Account was deleted.")
    return phone
