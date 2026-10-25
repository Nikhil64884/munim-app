"""
Idempotent daily SQLite backup. Optional S3 upload with encryption.

- Local backup: data/backups/munim_ji_YYYY-MM-DD.db
- Keeps last BACKUP_RETENTION_DAYS files
- Optional S3 upload with Fernet encryption
"""

import base64
import datetime
import logging
import os
import sqlite3

from app.config import config

logger = logging.getLogger("MunimJi.backup")


def _db_path() -> str | None:
    """Extract local file path from DATABASE_URL (SQLite only)."""
    url = config.DATABASE_URL
    if not url.startswith("sqlite"):
        return None
    return url.split("sqlite:///")[-1].split("sqlite://")[-1]


def run_daily_backup() -> str | None:
    """
    Create a local backup (once per calendar day), prune old ones,
    and optionally upload to S3.

    Returns the backup file path on success, None otherwise.
    """
    db_path = _db_path()
    if not db_path:
        logger.info("Backup skipped (non-SQLite).")
        return None
    if not os.path.exists(db_path):
        logger.warning(f"DB not found at {db_path}")
        return None

    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d")
    target = os.path.join(config.BACKUP_DIR, f"munim_ji_{stamp}.db")

    if os.path.exists(target):
        logger.info(f"Backup exists: {target}")
        return target

    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(target)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        logger.info(f"Backup created: {target}")
        _prune()
    except Exception as e:
        logger.error(f"Backup failed: {e}", exc_info=True)
        return None

    _upload_to_s3(target)
    return target


def _prune():
    """Keep only the last N backups."""
    try:
        files = sorted(
            f for f in os.listdir(config.BACKUP_DIR)
            if f.startswith("munim_ji_") and f.endswith(".db")
        )
    except OSError:
        return

    retention = max(1, config.BACKUP_RETENTION_DAYS)
    if len(files) > retention:
        for old in files[:-retention]:
            try:
                os.remove(os.path.join(config.BACKUP_DIR, old))
                logger.info(f"Pruned old backup: {old}")
            except OSError:
                pass


def _encrypt_file(filepath: str, key_b64: str) -> bytes:
    """Encrypt a file with Fernet (AES-128 + HMAC)."""
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64decode(key_b64)
    f = Fernet(base64.urlsafe_b64encode(key))
    with open(filepath, "rb") as fp:
        return f.encrypt(fp.read())


def _upload_to_s3(local_path: str):
    """Upload encrypted backup to S3. No-op if S3 not configured."""
    if not config.S3_BUCKET or not config.BACKUP_ENCRYPTION_KEY:
        return

    try:
        import boto3

        encrypted = _encrypt_file(local_path, config.BACKUP_ENCRYPTION_KEY)
        key_name = f"backups/{os.path.basename(local_path)}.enc"

        s3 = boto3.client(
            "s3",
            region_name=config.S3_REGION,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
        )
        s3.put_object(
            Bucket=config.S3_BUCKET,
            Key=key_name,
            Body=encrypted,
            ServerSideEncryption="AES256",
            StorageClass="STANDARD_IA",
        )
        logger.info(f"Uploaded encrypted backup to S3: {key_name}")
    except Exception as e:
        logger.error(f"S3 upload failed: {e}", exc_info=True)


def generate_encryption_key() -> str:
    """Generate a Fernet key for BACKUP_ENCRYPTION_KEY."""

    return base64.urlsafe_b64encode(os.urandom(32)).decode()
