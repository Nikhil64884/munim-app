"""Small privacy helpers for log output."""


def mask_phone(value: str | None) -> str:
    """Hide a phone number / owner id for logs, keeping only the last 4 characters.

    "+919876543210" -> "*********3210". Short or empty values are fully hidden.
    """
    if not value:
        return "-"
    text = str(value)
    if len(text) <= 6:
        return "***"
    return "*" * (len(text) - 4) + text[-4:]
