import openpyxl

from app import export_utils
from app.database import add_transaction, get_or_create_customer
from app.export_utils import _safe_excel_text


def test_safe_excel_text_neutralizes_formula_prefixes():
    for value in ("=1+1", " +SUM(A1:A2)", "-1+1", "@SUM(A1:A2)"):
        assert _safe_excel_text(value).startswith("'")

    assert _safe_excel_text("Ramu") == "Ramu"
    assert _safe_excel_text(None) is None


def test_export_escapes_names_on_both_sheets(tmp_path, monkeypatch):
    owner_id = "export_formula_test"
    customer_id = get_or_create_customer(owner_id, "=1+1")
    add_transaction(owner_id, customer_id, "=1+1", 50000, "Milk", "Debit", "=2+2")
    monkeypatch.setattr(export_utils.config, "EXPORTS_DIR", str(tmp_path))

    filename = export_utils.generate_excel(owner_id)
    assert filename
    workbook = openpyxl.load_workbook(tmp_path / filename, data_only=False)

    transaction_name = workbook["Transactions"]["B2"]
    balance_name = workbook["Customer Balances"]["A2"]
    assert transaction_name.value == "'=1+1"
    assert balance_name.value == "'=1+1"
    assert transaction_name.data_type != "f"
    assert balance_name.data_type != "f"
