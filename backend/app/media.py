"""Move-in photo checks. The image bytes are stored in the database (tenancy_photos.data)."""

from __future__ import annotations

MAX_PHOTO_SIZE = 10 * 1024 * 1024
MEDIA_TYPES = {
    "image/jpeg": (".jpg", (b"\xff\xd8\xff",)),
    "image/png": (".png", (b"\x89PNG\r\n\x1a\n",)),
    "image/webp": (".webp", (b"RIFF",)),
}


class InvalidPhoto(ValueError):
    """The uploaded file is not a supported image within the size limit."""


def validate_photo(content: bytes, media_type: str) -> str:
    if len(content) > MAX_PHOTO_SIZE:
        raise InvalidPhoto("Photos must be 10 MB or smaller.")
    extension_and_signatures = MEDIA_TYPES.get(media_type)
    if not content or not extension_and_signatures:
        raise InvalidPhoto("Upload a JPEG, PNG or WebP image.")
    _extension, signatures = extension_and_signatures
    if not any(content.startswith(signature) for signature in signatures):
        raise InvalidPhoto("The uploaded file content does not match its image type.")
    if media_type == "image/webp" and content[8:12] != b"WEBP":
        raise InvalidPhoto("The uploaded file content does not match its image type.")
    return media_type
