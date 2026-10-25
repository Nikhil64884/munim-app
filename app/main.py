"""Munim Ji AI FastAPI application entrypoint."""

import asyncio
import contextlib
import datetime
import logging
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import config
from app.database import init_db, migrate_legacy_data
from app.logging_config import setup_logging
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.customers import router as customers_router
from app.routes.health import router as health_router
from app.routes.mobile_api import router as mobile_router

logger = logging.getLogger("MunimJi.main")

TZ = ZoneInfo(config.TIMEZONE)
BACKUP_HOUR_LOCAL = 2


def _init_sentry() -> None:
    """Initialise Sentry once logging exists (called from lifespan)."""
    if not config.SENTRY_DSN:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=config.SENTRY_DSN,
            traces_sample_rate=0.1,
            environment=config.ENV,
        )
        logger.info("Sentry monitoring initialized successfully.")
    except Exception as _se:
        logger.warning(f"Sentry init error: {_se}")


def _seconds_until_next_backup() -> float:
    """Seconds until the next 02:00 in the configured timezone."""
    now = datetime.datetime.now(TZ)
    target = now.replace(hour=BACKUP_HOUR_LOCAL, minute=0, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


async def _daily_backup_worker() -> None:
    """Run the (idempotent) daily backup and retention cleanup at startup and then daily."""
    from app.database import cleanup_expired_deleted_accounts
    from app.services.backup import run_daily_backup

    while True:
        try:
            await asyncio.to_thread(run_daily_backup)
            await asyncio.to_thread(cleanup_expired_deleted_accounts)
            logger.info("Daily backup check and retention cleanup completed.")
        except asyncio.CancelledError:
            raise
        except Exception as _be:
            logger.error(f"Scheduled worker failed: {_be}")
        try:
            await asyncio.sleep(_seconds_until_next_backup())
        except asyncio.CancelledError:
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise logging and the database before serving requests."""
    setup_logging()
    _init_sentry()
    init_db()
    migrate_legacy_data()
    # Keep a reference: an unreferenced task may be garbage-collected.
    app.state.backup_task = asyncio.create_task(_daily_backup_worker())
    try:
        yield
    finally:
        task = getattr(app.state, "backup_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Munim Ji AI API",
    version=__version__,
    lifespan=lifespan,
    docs_url=None if config.IS_PROD else "/docs",
    redoc_url=None if config.IS_PROD else "/redoc",
    openapi_url=None if config.IS_PROD else "/openapi.json",
)

# Wildcard origins cannot be combined with credentials. In production the
# config validator requires an explicit allow-list.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=not config.CORS_ALLOW_ALL,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Public health endpoints.
app.include_router(health_router)

# Authenticated mobile API.
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(customers_router, prefix="/api")
app.include_router(mobile_router, prefix="/api")
