"""
Tests for the security hardening changes:
  - weak REVIEW_OTP rejected in production
  - dev OTP never echoed back in production
  - review-account login
  - phone numbers masked for logs
  - input length limits
"""

import pytest

from app.config import _Config
from app.privacy import mask_phone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_PROD_ENV = {
    "ENV": "prod",
    "OTP_PROVIDER": "twilio",
    "TWILIO_ACCOUNT_SID": "sid",
    "TWILIO_AUTH_TOKEN": "token",
    "TWILIO_VERIFY_SERVICE_SID": "vsid",
    "CORS_ORIGINS": "https://app.example.com",
    "DEV_OTP": "987654",
    "JWT_SECRET": "a" * 40,
    "GROQ_API_KEY": "test-key",
}


def _set_env(monkeypatch, env: dict, review_phone=None, review_otp=None):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("REVIEW_PHONE", raising=False)
    monkeypatch.delenv("REVIEW_OTP", raising=False)
    if review_phone is not None:
        monkeypatch.setenv("REVIEW_PHONE", review_phone)
    if review_otp is not None:
        monkeypatch.setenv("REVIEW_OTP", review_otp)


# ---------------------------------------------------------------------------
# mask_phone
# ---------------------------------------------------------------------------
def test_mask_phone_keeps_only_last_four():
    assert mask_phone("+919876543210") == "*********3210"


def test_mask_phone_handles_empty_and_short_values():
    assert mask_phone(None) == "-"
    assert mask_phone("") == "-"
    assert mask_phone("12345") == "***"


# ---------------------------------------------------------------------------
# Review OTP strength
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "otp", ["123456", "111111", "000000", "654321", "234567", "12345", "123456789", "abc"]
)
def test_weak_review_otp_rejected_in_production(monkeypatch, otp):
    _set_env(monkeypatch, _PROD_ENV, review_phone="+919000000001", review_otp=otp)
    with pytest.raises(ValueError, match="REVIEW_OTP"):
        _Config()


@pytest.mark.parametrize("otp", ["K7m2Qx9p", "483920", "135790"])
def test_strong_review_otp_allowed_in_production(monkeypatch, otp):
    _set_env(monkeypatch, _PROD_ENV, review_phone="+919000000001", review_otp=otp)
    assert _Config().REVIEW_OTP == otp


def test_production_without_review_account_is_fine(monkeypatch):
    _set_env(monkeypatch, _PROD_ENV)
    assert _Config().IS_PROD is True


def test_mismatched_review_credentials_rejected(monkeypatch):
    _set_env(monkeypatch, _PROD_ENV, review_phone="+919000000001", review_otp=None)
    with pytest.raises(ValueError, match="REVIEW_PHONE and REVIEW_OTP"):
        _Config()


def test_weak_review_otp_still_allowed_in_dev(monkeypatch):
    env = {**_PROD_ENV, "ENV": "dev", "OTP_PROVIDER": "dev", "CORS_ORIGINS": "*"}
    _set_env(monkeypatch, env, review_phone="+919000000001", review_otp="123456")
    assert _Config().IS_PROD is False


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------
def test_dev_otp_is_not_returned_in_production(client, monkeypatch):
    from app.config import config
    from app.routes import auth

    auth._send_phone_attempts.clear()
    auth._send_ip_attempts.clear()
    monkeypatch.setattr(config, "IS_PROD", True)

    res = client.post("/api/auth/send-otp", json={"phone": "9123456780"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "sent"
    assert "otp" not in body
    assert "test_otp" not in body


def test_dev_otp_is_returned_in_dev(client):
    from app.routes import auth

    auth._send_phone_attempts.clear()
    auth._send_ip_attempts.clear()
    res = client.post("/api/auth/send-otp", json={"phone": "9123456781"})
    assert res.status_code == 200
    assert res.json()["otp"] == "123456"


def test_review_account_login(client, monkeypatch):
    from app.config import config
    from app.routes import auth

    auth._verify_ip_failures.clear()
    monkeypatch.setattr(config, "REVIEW_PHONE", "+919000000001")
    monkeypatch.setattr(config, "REVIEW_OTP", "K7m2Qx9p")

    ok = client.post("/api/auth/verify-otp", json={"phone": "9000000001", "otp": "K7m2Qx9p"})
    assert ok.status_code == 200
    assert ok.json()["token"]

    wrong = client.post("/api/auth/verify-otp", json={"phone": "9000000001", "otp": "K7m2Qx9q"})
    assert wrong.status_code == 401

    other_phone = client.post(
        "/api/auth/verify-otp", json={"phone": "9000000002", "otp": "K7m2Qx9p"}
    )
    assert other_phone.status_code == 401


# ---------------------------------------------------------------------------
# Input limits
# ---------------------------------------------------------------------------
def test_customer_name_length_is_limited(client, auth_headers):
    too_long = client.post("/api/customers", json={"name": "A" * 101}, headers=auth_headers)
    assert too_long.status_code == 422

    fine = client.post("/api/customers", json={"name": "Ramu"}, headers=auth_headers)
    assert fine.status_code == 200


def test_audit_limit_is_bounded(client, auth_headers):
    assert client.get("/api/audit?limit=0", headers=auth_headers).status_code == 422
    assert client.get("/api/audit?limit=501", headers=auth_headers).status_code == 422
    assert client.get("/api/audit?limit=5", headers=auth_headers).status_code == 200


def test_client_msg_id_length_is_limited(client, auth_headers):
    res = client.post(
        "/api/chat",
        json={"message": "Ramu ko 500 udhaar", "client_msg_id": "x" * 101},
        headers=auth_headers,
    )
    assert res.status_code == 422
