"""
Munim Ji AI — Configuration.

Loads and validates all environment variables at startup.
Fails fast on misconfiguration — better to crash at boot than mid-request.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("MunimJi.config")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _env_str(name: str, default: str = "") -> str:
    """Read a string env var, stripping whitespace."""
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    """Read an integer env var with a clear error if malformed."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"❌ Environment variable '{name}' must be an integer, got: {raw!r}"
        ) from None


def _env_list(name: str, default: str = "*") -> list[str]:
    """Read a comma-separated list env var."""
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
class _Config:
    # Environments that require strict checks
    PROD_ENVS = {"prod", "production"}

    def __init__(self):
        # ---- Environment ----
        self.ENV: str = _env_str("ENV", "production").lower()
        self.IS_PROD: bool = self.ENV in self.PROD_ENVS

        # ---- Required ----
        self.GROQ_API_KEY: str = self._require("GROQ_API_KEY")
        self.JWT_SECRET: str = self._require_jwt()

        # ---- OTP ----
        self.OTP_PROVIDER: str = _env_str("OTP_PROVIDER", "dev").lower()
        self.DEV_OTP: str = _env_str("DEV_OTP", "123456")

        # ---- Twilio (conditional) ----
        self.TWILIO_ACCOUNT_SID: str = _env_str("TWILIO_ACCOUNT_SID")
        self.TWILIO_AUTH_TOKEN: str = _env_str("TWILIO_AUTH_TOKEN")
        self.TWILIO_VERIFY_SERVICE_SID: str = _env_str("TWILIO_VERIFY_SERVICE_SID")

        # ---- JWT ----
        self.JWT_ALGORITHM: str = "HS256"
        self.JWT_EXPIRY_DAYS: int = _env_int("JWT_EXPIRY_DAYS", 7)

        # ---- Database ----
        self.DATABASE_URL: str = _env_str(
            "DATABASE_URL", "sqlite:///./data/munim_ji.db"
        )

        # ---- Logging ----
        self.LOG_LEVEL: str = _env_str("LOG_LEVEL", "INFO").upper()
        self.LOG_DIR: str = _env_str("LOG_DIR", "logs")

        # ---- CORS ----
        # "*" = allow all (dev only). Production should set specific origins.
        self.CORS_ORIGINS: list[str] = _env_list("CORS_ORIGINS", "*")
        self.CORS_ALLOW_ALL: bool = self.CORS_ORIGINS == ["*"]

        # ---- Rate limits ----
        self.MAX_REQUESTS_PER_MINUTE: int = _env_int("MAX_REQUESTS_PER_MINUTE", 30)
        self.OTP_MAX_ATTEMPTS: int = _env_int("OTP_MAX_ATTEMPTS", 5)
        self.OTP_WINDOW_MINUTES: int = _env_int("OTP_WINDOW_MINUTES", 15)
        self.OTP_SEND_MAX_ATTEMPTS: int = _env_int("OTP_SEND_MAX_ATTEMPTS", 3)
        self.OTP_SEND_WINDOW_MINUTES: int = _env_int("OTP_SEND_WINDOW_MINUTES", 15)
        self.FIREBASE_PROJECT_ID: str = _env_str("FIREBASE_PROJECT_ID")

        # ---- Context / UX ----
        self.CONTEXT_EXPIRY_MINUTES: int = _env_int("CONTEXT_EXPIRY_MINUTES", 10)
        self.UNDO_WINDOW_MINUTES: int = _env_int("UNDO_WINDOW_MINUTES", 5)
        self.CONFIRM_AMOUNT_THRESHOLD: int = _env_int(
            "CONFIRM_AMOUNT_THRESHOLD", 5000
        )  # in RUPEES

        # ---- Money safety ----
        self.MAX_AMOUNT_RUPEES: int = _env_int("MAX_AMOUNT_RUPEES", 10_000_000)

        # ---- Models ----
        self.GROQ_MODEL: str = _env_str("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.WHISPER_MODEL: str = _env_str("WHISPER_MODEL", "whisper-large-v3")
        self.GROQ_TIMEOUT_SECONDS: int = _env_int("GROQ_TIMEOUT_SECONDS", 20)
        self.GROQ_MAX_RETRIES: int = _env_int("GROQ_MAX_RETRIES", 2)

        # ---- Exports ----
        self.EXPORTS_DIR: str = _env_str("EXPORTS_DIR", "exports")
        self.EXPORT_RETENTION_HOURS: int = _env_int("EXPORT_RETENTION_HOURS", 24)

        # ---- Backups ----
        self.BACKUP_DIR: str = _env_str("BACKUP_DIR", "data/backups")
        self.BACKUP_RETENTION_DAYS: int = _env_int("BACKUP_RETENTION_DAYS", 7)

        # ---- S3 ----
        self.S3_BUCKET: str = _env_str("S3_BUCKET")
        self.S3_REGION: str = _env_str("S3_REGION", "ap-south-1")
        self.S3_ACCESS_KEY: str = _env_str("S3_ACCESS_KEY")
        self.S3_SECRET_KEY: str = _env_str("S3_SECRET_KEY")
        self.BACKUP_ENCRYPTION_KEY: str = _env_str("BACKUP_ENCRYPTION_KEY")

        # ---- Sentry ----
        self.SENTRY_DSN: str = _env_str("SENTRY_DSN")

        # ---- Voice ----
        self.MAX_AUDIO_MB: int = _env_int("MAX_AUDIO_MB", 5)

        # ---- Payment reminders ----
        self.MERCHANT_UPI_ID: str = _env_str("MERCHANT_UPI_ID")
        self.MERCHANT_NAME: str = _env_str("MERCHANT_NAME", "Shop")

        # ---- Review account (Play Store) ----
        self.REVIEW_PHONE: str = _env_str("REVIEW_PHONE")
        self.REVIEW_OTP: str = _env_str("REVIEW_OTP")

        # ---- Timezone ----
        self.TIMEZONE: str = _env_str("TIMEZONE", "Asia/Kolkata")

        # ---- Final validation (fail fast) ----
        self._validate()

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------
    def _validate(self) -> None:
        """Fail fast if config is inconsistent or insecure for this ENV."""

        # 1) OTP provider must be one of the known values
        allowed_providers = {"twilio", "firebase", "dev"}
        if self.OTP_PROVIDER not in allowed_providers:
            raise ValueError(
                f"❌ OTP_PROVIDER must be one of {allowed_providers}, "
                f"got: {self.OTP_PROVIDER!r}"
            )

        # 2) Provider-specific settings
        if self.OTP_PROVIDER == "firebase" and not self.FIREBASE_PROJECT_ID:
            raise ValueError("❌ OTP_PROVIDER=firebase requires FIREBASE_PROJECT_ID.")

        if self.OTP_PROVIDER == "twilio":
            missing = [
                name
                for name, val in [
                    ("TWILIO_ACCOUNT_SID", self.TWILIO_ACCOUNT_SID),
                    ("TWILIO_AUTH_TOKEN", self.TWILIO_AUTH_TOKEN),
                    ("TWILIO_VERIFY_SERVICE_SID", self.TWILIO_VERIFY_SERVICE_SID),
                ]
                if not val
            ]
            if missing:
                raise ValueError(
                    f"❌ OTP_PROVIDER=twilio requires: {', '.join(missing)}"
                )

        # 3) In production, refuse insecure defaults.
        if self.IS_PROD:
            if self.OTP_PROVIDER == "dev":
                raise ValueError(
                    "❌ OTP_PROVIDER=dev is NOT allowed in production — it lets "
                    "anyone log in as any phone number with the fixed DEV_OTP. "
                    "Set OTP_PROVIDER=twilio or firebase."
                )
            if self.CORS_ALLOW_ALL:
                logger.warning(
                    "⚠️ CORS_ORIGINS='*' is active. Set specific origins for strict security."
                )
            if self.JWT_SECRET == "dev-only-insecure-secret-change-me":
                raise ValueError(
                    "❌ JWT_SECRET is the dev default. Set a real secret in production."
                )
            if len(self.JWT_SECRET) < 32:
                raise ValueError(
                    "❌ JWT_SECRET must be at least 32 characters in production."
                )
            if bool(self.REVIEW_PHONE) != bool(self.REVIEW_OTP):
                raise ValueError(
                    "❌ REVIEW_PHONE and REVIEW_OTP must both be set or both be empty."
                )
            if self.REVIEW_OTP and self._is_weak_review_otp(self.REVIEW_OTP):
                raise ValueError(
                    "❌ In production, REVIEW_OTP must be 6-8 characters and not "
                    "easy to guess (e.g. not 123456, 111111 or 654321)."
                )
        elif self.DEV_OTP == "123456":
            logger.info("DEV_OTP is the default '123456' (fine for local dev only).")

    @staticmethod
    def _is_weak_review_otp(otp: str) -> bool:
        """True if the review-account OTP is unusable or trivially guessable.

        The login API accepts at most 8 characters, so longer values can never match.
        """
        if len(otp) < 6 or len(otp) > 8:
            return True
        if len(set(otp)) == 1:  # 111111
            return True
        if otp.isascii() and otp.isdigit():
            steps = {int(b) - int(a) for a, b in zip(otp, otp[1:], strict=False)}
            if steps <= {1} or steps <= {-1}:  # 123456 / 654321
                return True
        return False

    # -----------------------------------------------------------------------
    # Required-var helpers
    # -----------------------------------------------------------------------
    @staticmethod
    def _require(var_name: str) -> str:
        value = os.environ.get(var_name, "").strip()
        if not value:
            raise ValueError(
                f"❌ Required environment variable '{var_name}' is missing."
            )
        return value

    def _require_jwt(self) -> str:
        """JWT_SECRET with strict check when in production."""
        secret = os.environ.get("JWT_SECRET", "").strip()
        if secret:
            return secret

        # If ENV was set to production, refuse to use dev fallback
        env = os.environ.get("ENV", "dev").lower()
        if env in self.PROD_ENVS:
            raise ValueError("❌ JWT_SECRET is required in production.")

        return "dev-only-insecure-secret-change-me"


config = _Config()
