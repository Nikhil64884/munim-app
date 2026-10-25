from app.config import config
from app.services.reminder import generate_upi_link


def test_upi_link_not_generated_without_merchant_configuration(monkeypatch):
    monkeypatch.setattr(config, "MERCHANT_UPI_ID", "")
    assert generate_upi_link("owner", "Ramu", 500.0) is None


def test_upi_link_uses_configured_merchant(monkeypatch):
    monkeypatch.setattr(config, "MERCHANT_UPI_ID", "shop@bank")
    monkeypatch.setattr(config, "MERCHANT_NAME", "My Shop")

    link = generate_upi_link("owner", "Ramu", 500.0)
    assert link.startswith("upi://pay?")
    assert "pa=shop%40bank" in link
    assert "pn=My+Shop" in link
    assert "yourshop%40upi" not in link
