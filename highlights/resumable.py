"""Internal safeguards for authenticated, offset-based uploads.

Stages data privately in MEDIA_ROOT/.resumable; never trust user-supplied paths.
Database row lock serializes concurrent writes in PostgreSQL. On crashed writes,
the next request truncates uncommitted tail bytes back to committed offset.
"""
import hashlib
import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .models import ResumableUpload

VALID_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
CHUNK_MAX = 8 * 1024**2


def session_path(session):
    return Path(settings.MEDIA_ROOT) / ".resumable" / (str(session.pk) + ".part")


def reserved_upload_bytes(owner, *, exclude_id=None):
    query = ResumableUpload.objects.filter(
        owner=owner, state=ResumableUpload.State.RECEIVING,
        expires_at__gt=timezone.now())
    if exclude_id:
        query = query.exclude(pk=exclude_id)
    return sum(max(0, s.byte_size - s.received_bytes) + s.received_bytes
               for s in query.only("byte_size", "received_bytes"))


def extend_expiry(session):
    session.expires_at = timezone.now() + timedelta(hours=24)


def hash_file(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024**2), b""):
            checksum.update(block)
    return checksum.hexdigest()


def prepare_append(session):
    path = session_path(session)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        current = path.stat().st_size
        if current < session.received_bytes:
            raise OSError("Upload staging file shorter than committed database offset")
        if current > session.received_bytes:
            # A worker may have died after disk append but before DB commit.
            # Roll back the uncommitted suffix before the client retries.
            with path.open("r+b") as f:
                f.truncate(session.received_bytes)
                f.flush()
                os.fsync(f.fileno())
    elif session.received_bytes:
        raise OSError("Upload session staging file is missing")
    return path


def safe_unlink(session):
    session_path(session).unlink(missing_ok=True)
