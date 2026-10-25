"""
Rate limiting — per-owner AND per-IP.

- Owner limit: authenticated user (from JWT)
- IP limit: unauthenticated/spam protection (higher threshold)

Both use an in-memory sliding window. For multi-server, swap for Redis.
"""

import logging
import time
from collections import defaultdict

from fastapi import Request

from app.config import config
from app.privacy import mask_phone

logger = logging.getLogger("MunimJi.security")

# ---- Buckets ----
_owner_buckets: dict[str, list[float]] = defaultdict(list)
_ip_buckets: dict[str, list[float]] = defaultdict(list)
_last_gc = time.time()
_GC_INTERVAL = 120  # seconds

# IP limit is higher because shared NATs (offices, cyber cafés) hit it
_IP_LIMIT_MULTIPLIER = 3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def is_rate_limited(
    owner_id: str | None = None,
    request: Request | None = None,
) -> bool:
    """
    Check both owner and IP limits.

    Returns True if the request should be blocked.
    """
    now = time.time()
    _maybe_gc(now)

    # ---- Owner check ----
    if owner_id:
        if _check_bucket(
            _owner_buckets,
            owner_id,
            now,
            limit=config.MAX_REQUESTS_PER_MINUTE,
        ):
            logger.warning(f"Rate limit hit for owner={mask_phone(owner_id)}")
            return True

    # ---- IP check ----
    if request is not None:
        ip = _client_ip(request)
        if ip and _check_bucket(
            _ip_buckets,
            ip,
            now,
            limit=config.MAX_REQUESTS_PER_MINUTE * _IP_LIMIT_MULTIPLIER,
        ):
            logger.warning(f"Rate limit hit for IP={ip}")
            return True

    return False


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------
def _check_bucket(
    buckets: dict[str, list[float]],
    key: str,
    now: float,
    limit: int,
    window: int = 60,
) -> bool:
    """Returns True if this request exceeds the limit."""
    bucket = [t for t in buckets[key] if now - t < window]
    if len(bucket) >= limit:
        buckets[key] = bucket
        return True
    bucket.append(now)
    buckets[key] = bucket
    return False


def _client_ip(request: Request) -> str | None:
    """Use the direct peer address; forwarded headers are client-controlled.

    When running behind a reverse proxy, let uvicorn's --proxy-headers /
    --forwarded-allow-ips handle X-Forwarded-For: it then rewrites
    request.client to the real peer address for trusted proxies only.
    """
    return request.client.host if request.client else None


def _maybe_gc(now: float):
    """Periodically clean stale buckets to avoid unbounded memory."""
    global _last_gc
    if now - _last_gc < _GC_INTERVAL:
        return
    _gc(_owner_buckets, now)
    _gc(_ip_buckets, now)
    _last_gc = now


def _gc(buckets: dict[str, list[float]], now: float, window: int = 60):
    stale = [k for k, ts in buckets.items() if not [t for t in ts if now - t < window]]
    for k in stale:
        del buckets[k]
    if stale:
        logger.info(f"Rate-limit GC pruned {len(stale)} buckets.")

REVOKED_TOKENS = set()

def revoke_token(token: str):
    if token:
        REVOKED_TOKENS.add(token.strip())

def is_token_revoked(token: str) -> bool:
    return token.strip() in REVOKED_TOKENS
