"""Private move-in photo storage using S3-compatible or local development storage."""

from __future__ import annotations

import uuid
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app import config

MAX_PHOTO_SIZE = 10 * 1024 * 1024
MEDIA_TYPES = {
    "image/jpeg": (".jpg", (b"\xff\xd8\xff",)),
    "image/png": (".png", (b"\x89PNG\r\n\x1a\n",)),
    "image/webp": (".webp", (b"RIFF",)),
}


class InvalidPhoto(ValueError):
    """The uploaded file is not a supported image within the size limit."""


def _s3_client():
    configured = (
        config.PHOTO_STORAGE_BUCKET,
        config.PHOTO_STORAGE_ACCESS_KEY,
        config.PHOTO_STORAGE_SECRET_KEY,
    )
    if any(configured) and not all(configured):
        raise RuntimeError("Photo object storage is only partially configured.")
    if not all(configured):
        return None
    return boto3.client(
        "s3",
        endpoint_url=config.PHOTO_STORAGE_ENDPOINT,
        region_name=config.PHOTO_STORAGE_REGION,
        aws_access_key_id=config.PHOTO_STORAGE_ACCESS_KEY,
        aws_secret_access_key=config.PHOTO_STORAGE_SECRET_KEY,
    )


def store_photo(content: bytes, media_type: str) -> tuple[str, str]:
    if len(content) > MAX_PHOTO_SIZE:
        raise InvalidPhoto("Photos must be 10 MB or smaller.")
    extension_and_signatures = MEDIA_TYPES.get(media_type)
    if not content or not extension_and_signatures:
        raise InvalidPhoto("Upload a JPEG, PNG or WebP image.")
    extension, signatures = extension_and_signatures
    if not any(content.startswith(signature) for signature in signatures):
        raise InvalidPhoto("The uploaded file content does not match its image type.")
    if media_type == "image/webp" and content[8:12] != b"WEBP":
        raise InvalidPhoto("The uploaded file content does not match its image type.")
    key = f"move-in/{uuid.uuid4().hex}{extension}"
    client = _s3_client()
    if client:
        try:
            client.put_object(
                Bucket=config.PHOTO_STORAGE_BUCKET,
                Key=key,
                Body=content,
                ContentType=media_type,
                ServerSideEncryption="AES256",
            )
        except (BotoCoreError, ClientError) as error:
            raise RuntimeError("Could not save the move-in photo to object storage.") from error
    else:
        destination = (config.PHOTO_STORAGE_DIR / key).resolve()
        root = config.PHOTO_STORAGE_DIR.resolve()
        if root not in destination.parents:
            raise RuntimeError("Invalid photo storage path.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    return key, media_type


def read_photo(key: str) -> bytes:
    if Path(key).is_absolute() or ".." in Path(key).parts:
        raise RuntimeError("Invalid photo storage key.")
    client = _s3_client()
    if client:
        try:
            response = client.get_object(Bucket=config.PHOTO_STORAGE_BUCKET, Key=key)
            return response["Body"].read(MAX_PHOTO_SIZE + 1)
        except (BotoCoreError, ClientError) as error:
            raise RuntimeError("Could not read the stored move-in photo.") from error
    path = (config.PHOTO_STORAGE_DIR / key).resolve()
    root = config.PHOTO_STORAGE_DIR.resolve()
    if root not in path.parents:
        raise RuntimeError("Invalid photo storage key.")
    return path.read_bytes()


def delete_photo(key: str) -> None:
    client = _s3_client()
    if client:
        try:
            client.delete_object(Bucket=config.PHOTO_STORAGE_BUCKET, Key=key)
        except (BotoCoreError, ClientError) as error:
            raise RuntimeError("Could not remove an unlinked move-in photo.") from error
        return
    path = (config.PHOTO_STORAGE_DIR / key).resolve()
    if config.PHOTO_STORAGE_DIR.resolve() not in path.parents:
        raise RuntimeError("Invalid photo storage key.")
    path.unlink(missing_ok=True)
