"""
Integration tests for FastAPI endpoints (test_api.py).
"""


def test_health_check(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Munim Ji AI is running! 🚀"
    assert data["database"] == "ok"


def test_auth_flow(client):
    # Send OTP
    res = client.post("/api/auth/send-otp", json={"phone": "9876543210"})
    assert res.status_code == 200
    assert res.json()["status"] == "sent"

    # Verify OTP (Dev OTP is 123456)
    res = client.post("/api/auth/verify-otp", json={"phone": "9876543210", "otp": "123456"})
    assert res.status_code == 200
    token = res.json().get("token")
    assert token is not None

    # Invalid OTP should not lock the phone number out.
    res = client.post("/api/auth/verify-otp", json={"phone": "9876543210", "otp": "000000"})
    assert res.status_code == 401
    res = client.post("/api/auth/verify-otp", json={"phone": "9876543210", "otp": "123456"})
    assert res.status_code == 200


def test_unauthorized_access(client):
    res = client.get("/api/customers")
    assert res.status_code == 401


def test_logout_and_token_revocation(client, auth_headers):
    # Valid before logout
    res = client.get("/api/customers", headers=auth_headers)
    assert res.status_code == 200

    # Logout
    res_logout = client.post("/api/auth/logout", headers=auth_headers)
    assert res_logout.status_code == 200
    assert res_logout.json()["status"] == "logged_out"

    # Token is now revoked
    res_after = client.get("/api/customers", headers=auth_headers)
    assert res_after.status_code == 401
    assert "revoked" in res_after.json()["detail"].lower()


def test_otp_send_is_rate_limited(client):
    from app.routes import auth

    auth._send_phone_attempts.clear()
    auth._send_ip_attempts.clear()
    for _ in range(auth.config.OTP_SEND_MAX_ATTEMPTS):
        response = client.post("/api/auth/send-otp", json={"phone": "9876543210"})
        assert response.status_code == 200

    response = client.post("/api/auth/send-otp", json={"phone": "9876543210"})
    assert response.status_code == 429


def test_firebase_auth_uses_verified_id_token(client, monkeypatch):
    from app.config import config
    from app.routes import auth

    monkeypatch.setattr(config, "OTP_PROVIDER", "firebase")
    monkeypatch.setattr(
        auth, "verify_firebase_id_token", lambda token, phone: token == "valid-token"
    )
    auth._verify_ip_failures.clear()

    response = client.post(
        "/api/auth/verify-otp",
        json={"phone": "9876543210", "firebase_id_token": "valid-token"},
    )
    assert response.status_code == 200
    assert response.json()["token"]

    response = client.post(
        "/api/auth/verify-otp",
        json={"phone": "9876543211", "firebase_id_token": "invalid-token"},
    )
    assert response.status_code == 401


def test_customer_list_and_summary(client, auth_headers):
    res = client.get("/api/customers", headers=auth_headers)
    assert res.status_code == 200
    assert "customers" in res.json()

    res = client.get("/api/summary?period=today", headers=auth_headers)
    assert res.status_code == 200
    assert "udhaar_paise" in res.json()


def test_export_idor_prevention(client, auth_headers):
    # 1. Owner 1 generates export
    res = client.get("/api/export", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    filename = data["filename"]

    # 2. Owner 1 can download it
    res = client.get(f"/api/export/{filename}", headers=auth_headers)
    assert res.status_code == 200

    # 3. Owner 2 (different phone/token) is denied with 403 Forbidden
    from app.routes.auth import _issue_token

    owner2_token = _issue_token("+919999988888")
    owner2_headers = {"Authorization": f"Bearer {owner2_token}"}
    res2 = client.get(f"/api/export/{filename}", headers=owner2_headers)
    assert res2.status_code == 403
    assert "Access denied" in res2.json()["detail"]

    # 4. Invalid filename / path traversal attempt is rejected with 400 Bad Request
    res3 = client.get("/api/export/invalid_file.xlsx", headers=auth_headers)
    assert res3.status_code == 400
    assert "Invalid filename" in res3.json()["detail"]

    res4 = client.get("/api/export/..secret.xlsx", headers=auth_headers)
    assert res4.status_code == 400
    assert "Invalid filename" in res4.json()["detail"]


def test_account_deletion_clears_customer_links_and_inventory(client, auth_headers):
    from app.database import (
        Customer,
        Inventory,
        Transaction,
        add_or_update_inventory_item,
        add_transaction,
        get_db,
        get_or_create_customer,
    )

    owner_id = "+919876543210"
    customer_id = get_or_create_customer(owner_id, "Ramu")
    add_transaction(owner_id, customer_id, "Ramu", 50000, "Milk", "Debit", "Ramu 500")
    add_or_update_inventory_item(owner_id, "Milk", 10, 2, "litre")

    response = client.delete("/api/account", headers=auth_headers)
    assert response.status_code == 200
    assert client.get("/api/customers", headers=auth_headers).status_code == 401

    with get_db() as db:
        transaction = db.query(Transaction).filter_by(owner_id=owner_id).one()
        assert transaction.is_deleted == 1
        assert transaction.customer_id is None
        assert db.query(Customer).filter_by(owner_id=owner_id).count() == 0
        assert db.query(Inventory).filter_by(owner_id=owner_id).count() == 0


def test_transaction_edit_enforces_amount_limit(client, auth_headers):
    from app.database import add_transaction, get_or_create_customer

    owner_id = "+919876543210"
    customer_id = get_or_create_customer(owner_id, "Sita")
    tx_id = add_transaction(owner_id, customer_id, "Sita", 50000, "Milk", "Debit", "Sita 500")

    response = client.patch(
        f"/api/transactions/{tx_id}",
        headers=auth_headers,
        json={"amount_paise": 1_000_000_001},
    )
    assert response.status_code == 400


def test_delete_transaction_twice_returns_404(client, auth_headers):
    from app.database import add_transaction, get_or_create_customer

    owner_id = "+919876543210"
    customer_id = get_or_create_customer(owner_id, "Geeta")
    tx_id = add_transaction(owner_id, customer_id, "Geeta", 25000, "Rice", "Debit", "Geeta 250")

    res1 = client.delete(f"/api/transactions/{tx_id}", headers=auth_headers)
    assert res1.status_code == 200

    res2 = client.delete(f"/api/transactions/{tx_id}", headers=auth_headers)
    assert res2.status_code == 404


def test_create_customer_returns_actual_balance(client, auth_headers):
    from app.database import add_transaction, get_or_create_customer

    owner_id = "+919876543210"
    customer_id = get_or_create_customer(owner_id, "Aman")
    add_transaction(owner_id, customer_id, "Aman", 15000, "Snacks", "Debit", "Aman 150")

    res = client.post("/api/customers", headers=auth_headers, json={"name": "Aman"})
    assert res.status_code == 200
    assert res.json()["balance_paise"] == 15000
    assert "₹150" in res.json()["balance_display"]


def test_failed_chat_clears_idempotency_marker(client, auth_headers, monkeypatch):
    import pytest

    from app import database
    from app.routes import chat

    def fail_processing(_owner_id, _message):
        raise RuntimeError("simulated processing error")

    monkeypatch.setattr(chat, "handle_message", fail_processing)
    message_id = "retry-after-failure"
    with pytest.raises(RuntimeError, match="simulated processing error"):
        client.post(
            "/api/chat",
            headers=auth_headers,
            json={"message": "Ramu ko 500 udhaar", "client_msg_id": message_id},
        )

    marker = f"app:+919876543210:{message_id}"
    assert database.mark_message_processed(marker) is True


def test_technical_chat_failure_clears_idempotency_marker(client, auth_headers, monkeypatch):
    from app import database
    from app.messages import msg
    from app.routes import chat

    monkeypatch.setattr(
        chat,
        "handle_message",
        lambda _owner_id, _message: {"reply": msg("TECHNICAL_ERROR"), "media_url": None},
    )
    message_id = "retry-after-technical-error"
    response = client.post(
        "/api/chat",
        headers=auth_headers,
        json={"message": "Sita ko 500 udhaar", "client_msg_id": message_id},
    )
    assert response.status_code == 200
    assert database.mark_message_processed(
        f"app:+919876543210:{message_id}"
    ) is True
