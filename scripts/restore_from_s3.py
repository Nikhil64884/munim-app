"""
Utility script to download and decrypt a backup from S3.
"""

import base64
import os
import sys

from app.config import config


def decrypt_file(encrypted_data: bytes, key_b64: str) -> bytes:
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64decode(key_b64)
    f = Fernet(base64.urlsafe_b64encode(key))
    return f.decrypt(encrypted_data)


def restore_backup(s3_key: str, output_path: str):
    """Download encrypted backup from S3 and write decrypted DB to output_path."""
    if not config.S3_BUCKET or not config.BACKUP_ENCRYPTION_KEY:
        print("❌ S3_BUCKET or BACKUP_ENCRYPTION_KEY not configured in .env")
        sys.exit(1)

    import boto3

    s3 = boto3.client(
        "s3",
        region_name=config.S3_REGION,
        aws_access_key_id=config.S3_ACCESS_KEY,
        aws_secret_access_key=config.S3_SECRET_KEY,
    )

    print(f"Downloading s3://{config.S3_BUCKET}/{s3_key}...")
    resp = s3.get_object(Bucket=config.S3_BUCKET, Key=s3_key)
    encrypted_data = resp["Body"].read()

    print("Decrypting backup...")
    decrypted_data = decrypt_file(encrypted_data, config.BACKUP_ENCRYPTION_KEY)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(decrypted_data)

    print(f"✅ Backup restored successfully to: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/restore_from_s3.py <s3_key> <output_sqlite_db_path>")
        sys.exit(1)
    restore_backup(sys.argv[1], sys.argv[2])
