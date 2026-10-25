"""
OTP provider abstraction: twilio | firebase | dev.

- twilio   : sends OTP via Twilio Verify Service
- firebase : client SDK handles sending/verifying
- dev      : fixed OTP for local testing (DEV_OTP)
"""

import logging

from app.config import config
from app.privacy import mask_phone

logger = logging.getLogger("MunimJi.services.otp")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def send_otp(phone: str) -> bool:
    """Send OTP to phone. Returns True on success."""
    if config.OTP_PROVIDER == "twilio":
        return _twilio_send(phone)
    if config.OTP_PROVIDER == "dev":
        logger.warning(f"[DEV] OTP requested for {mask_phone(phone)}")
        return True
    if config.OTP_PROVIDER == "firebase":
        # Client SDK handles sending; backend does nothing
        return True
    logger.error(f"Unknown OTP_PROVIDER: {config.OTP_PROVIDER}")
    return False


def verify_otp(phone: str, otp: str) -> bool:
    """Verify OTP for phone. Returns True if valid."""
    if config.OTP_PROVIDER == "twilio":
        return _twilio_verify(phone, otp)
    if config.OTP_PROVIDER == "dev":
        return otp == config.DEV_OTP
    if config.OTP_PROVIDER == "firebase":
        return False
    logger.error(f"Unknown OTP_PROVIDER: {config.OTP_PROVIDER}")
    return False


def verify_firebase_id_token(id_token: str, expected_phone: str) -> bool:
    """Verify a Firebase ID token and ensure it belongs to the requested phone."""
    try:
        import firebase_admin
        from firebase_admin import auth, credentials

        try:
            app = firebase_admin.get_app()
        except ValueError:
            app = firebase_admin.initialize_app(
                credentials.ApplicationDefault(),
                {"projectId": config.FIREBASE_PROJECT_ID},
            )
        claims = auth.verify_id_token(id_token, app=app, check_revoked=True)
        token_phone = claims.get("phone_number", "")
        return _normalize_phone(token_phone) == _normalize_phone(expected_phone)
    except Exception as exc:
        logger.warning("Firebase ID token verification failed: %s", exc)
        return False


def _normalize_phone(phone: str) -> str:
    phone = phone.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        phone = "+91" + phone if len(phone) == 10 and phone.isdigit() else "+" + phone
    return phone


# ---------------------------------------------------------------------------
# Twilio helpers
# ---------------------------------------------------------------------------
def _twilio_send(phone: str) -> bool:
    try:
        from twilio.rest import Client

        client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        client.verify.v2.services(config.TWILIO_VERIFY_SERVICE_SID) \
            .verifications.create(to=phone, channel="sms")
        logger.info(f"OTP sent to {mask_phone(phone)} via Twilio")
        return True
    except Exception as e:
        logger.error(f"Twilio send OTP failed: {e}", exc_info=True)
        return False


def _twilio_verify(phone: str, otp: str) -> bool:
    try:
        from twilio.rest import Client

        client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        check = client.verify.v2.services(config.TWILIO_VERIFY_SERVICE_SID) \
            .verification_checks.create(to=phone, code=otp)
        return check.status == "approved"
    except Exception as e:
        logger.error(f"Twilio verify failed: {e}", exc_info=True)
        return False
