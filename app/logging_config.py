"""
Munim Ji AI — Logging Setup.

Configures the 'MunimJi' logger with:
  - Rotating file handler (5 MB × 3 backups)
  - Console handler
  - Idempotent setup (safe to call multiple times)
  - propagate=False (no duplicate logs through root logger)

Usage:
    from app.logging_config import setup_logging
    setup_logging()
    logger = logging.getLogger("MunimJi.main")
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from app.config import config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOGGER_NAME = "MunimJi"
LOG_FILENAME = "munim_ji.log"
LOG_MAX_BYTES = 5 * 1024 * 1024   # 5 MB per file
LOG_BACKUP_COUNT = 3              # keep 3 rotated files (munim_ji.log.1, .2, .3)
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Module-level flag so we never double-configure within a single process.
_configured: bool = False


def setup_logging(force: bool = False) -> logging.Logger:
    """
    Configure the MunimJi logger. Idempotent.

    Args:
        force: If True, tears down existing handlers and reconfigures.
               Useful in tests or when config changes at runtime.

    Returns:
        The configured logger.
    """
    global _configured

    logger = logging.getLogger(LOGGER_NAME)

    # Fast path — already configured and not forced
    if _configured and not force:
        return logger

    # Ensure log directory exists
    os.makedirs(config.LOG_DIR, exist_ok=True)

    # ---- Tear down any existing handlers (avoid duplicates on reload) ----
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)
    logger.handlers.clear()

    # ---- Formatter ----
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    # ---- File handler (rotating) ----
    file_path = os.path.join(config.LOG_DIR, LOG_FILENAME)
    try:
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,          # don't open the file until the first record
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
        logger.addHandler(file_handler)
    except OSError as e:
        # Filesystem issues shouldn't crash the app — fall back to console only.
        print(f"[logging] Could not open log file {file_path}: {e}")

    # ---- Console handler ----
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    logger.addHandler(console_handler)

    # ---- Logger-level settings ----
    logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

    # Prevent messages from bubbling up to the root logger (avoids duplicates
    # when e.g. uvicorn or pytest configures its own root handlers).
    logger.propagate = False

    _configured = True
    logger.debug(f"Logging configured: level={config.LOG_LEVEL}, dir={config.LOG_DIR}")
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """
    Convenience: get a child logger under 'MunimJi'.

    Usage:
        logger = get_logger("database")
        logger.info("...")   # → MunimJi.database
    """
    if not _configured:
        setup_logging()
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def shutdown_logging() -> None:
    """Close all handlers. Useful in tests."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)
    logger.handlers.clear()
    _configured = False
