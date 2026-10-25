from starlette.requests import Request

from app.security import _client_ip


def test_client_ip_ignores_spoofed_forwarded_header():
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"x-forwarded-for", b"203.0.113.99")],
            "client": ("192.0.2.10", 12345),
            "server": ("testserver", 80),
        }
    )

    assert _client_ip(request) == "192.0.2.10"
