"""Private authenticated resumable upload endpoints (V1.7).

POST /games/resumable/                    small JSON metadata
GET,DELETE /games/resumable/<uuid>/       status or cancel
POST /games/resumable/<uuid>/chunk/       binary chunk + X-Upload-Offset
POST /games/resumable/<uuid>/finish/      verify SHA, duration and publish Game
"""
import json
import hashlib
import os
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .models import Game, Venue, ResumableUpload, video_path
from .operations import InsufficientStorage, owner_raw_media_bytes, require_media_space
from .resumable import (
    CHUNK_MAX, VALID_VIDEO_SUFFIXES, extend_expiry, hash_file,
    prepare_append, reserved_upload_bytes, safe_unlink, session_path,
)
from .services import MediaError, probe_duration


def reply(data, code=200):
    response = JsonResponse(data, status=code)
    response["Cache-Control"] = "no-store"
    return response


def _session(request, upload_id):
    return get_object_or_404(
        ResumableUpload.objects.select_related("venue"),
        pk=upload_id, owner=request.user)


def _available(session):
    return (session.state == ResumableUpload.State.RECEIVING and
            session.expires_at > timezone.now())


@login_required
@require_POST
def start_upload(request):
    if len(request.body) > 8192:
        return reply({"error": "metadata_too_large"}, 413)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError()
        title = data["title"]
        filename = data["filename"]
        size = data["byte_size"]
        digest = data["sha256"]
        venue_id = data["venue_id"]
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 180:
            raise ValueError()
        if not isinstance(filename, str) or not 1 <= len(filename) <= 180:
            raise ValueError()
        if Path(filename).name != filename or "\\" in filename:
            raise ValueError()
        if Path(filename).suffix.lower() not in VALID_VIDEO_SUFFIXES:
            raise ValueError()
        if type(size) is not int or not 1 <= size <= settings.MAX_UPLOAD_BYTES:
            raise ValueError()
        if not isinstance(digest, str) or (digest and not re.fullmatch("[0-9a-f]{64}", digest)):
            raise ValueError()
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return reply({"error": "invalid_upload_metadata"}, 400)

    venue = get_object_or_404(Venue, pk=venue_id, owner=request.user)
    try:
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if owner_raw_media_bytes(request.user) + reserved_upload_bytes(
                    request.user) + size > settings.OWNER_MAX_RAW_BYTES:
                raise InsufficientStorage("Owner storage budget would be exceeded")
            require_media_space(size)
            session = ResumableUpload(
                owner=request.user, venue=venue, title=title.strip(),
                filename=filename, byte_size=size, expected_sha256=digest)
            extend_expiry(session)
            session.save()
    except (InsufficientStorage, OSError, ValueError):
        return reply({"error": "insufficient_storage"}, 507)
    return reply({
        "id": str(session.pk), "offset": 0, "byte_size": size,
        "chunk_max_bytes": getattr(settings, "RESUMABLE_CHUNK_MAX_BYTES", CHUNK_MAX),
        "expires_at": session.expires_at.isoformat(),
    }, 201)


@login_required
@require_http_methods(["GET", "DELETE"])
def upload_status(request, upload_id):
    if request.method == "GET":
        session = _session(request, upload_id)
        return reply({
            "id": str(session.pk), "state": session.state,
            "offset": session.received_bytes, "byte_size": session.byte_size,
            "expires_at": session.expires_at.isoformat(),
            "expired": session.expires_at <= timezone.now(),
            "game_id": session.game_id if session.state == "complete" else None,
        })
    # Serializing DELETE on PG prevents racing chunk append/finalize.
    with transaction.atomic():
        session = get_object_or_404(
            ResumableUpload.objects.select_for_update(), pk=upload_id, owner=request.user)
        if session.state == ResumableUpload.State.COMPLETE:
            return reply({"error": "already_completed", "game_id": session.game_id}, 409)
        session.state = ResumableUpload.State.CANCELLED
        session.save(update_fields=["state", "updated_at"])
        safe_unlink(session)
    return HttpResponse(status=204)


@login_required
@require_POST
def upload_chunk(request, upload_id):
    declared_chunk_digest = request.headers.get("X-Chunk-SHA256", "")
    if not re.fullmatch("[0-9a-f]{64}", declared_chunk_digest):
        return reply({"error": "chunk_sha256_required"}, 400)
    raw_length = request.META.get("CONTENT_LENGTH", "")
    offset_raw = request.headers.get("X-Upload-Offset", "")
    if not raw_length.isdecimal() or not offset_raw.isdecimal():
        return reply({"error": "content_length_and_offset_required"}, 400)
    size, offset = int(raw_length), int(offset_raw)
    if size < 1 or size > min(CHUNK_MAX, getattr(settings, "RESUMABLE_CHUNK_MAX_BYTES", CHUNK_MAX)):
        return reply({"error": "invalid_chunk_size"}, 413)
    if not request.content_type.startswith("application/octet-stream"):
        return reply({"error": "binary_content_type_required"}, 415)

    with transaction.atomic():
        session = get_object_or_404(ResumableUpload.objects.select_for_update(),
                                    pk=upload_id, owner=request.user)
        if not _available(session):
            return reply({"error": "upload_closed_or_expired"}, 410)
        if offset != session.received_bytes:
            return reply({"error": "offset_mismatch", "offset": session.received_bytes}, 409)
        if offset + size > session.byte_size:
            return reply({"error": "declared_size_exceeded"}, 413)
        try:
            path = prepare_append(session)
            require_media_space(size)
            # A binary request never accesses request.body or multipart parsers.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                with os.fdopen(fd, "ab", buffering=0) as out:
                    remaining = size
                    chunk_digest = hashlib.sha256()
                    while remaining:
                        block = request.read(min(262144, remaining))
                        if not block:
                            raise OSError("Client disconnected before declared chunk length")
                        out.write(block)
                        chunk_digest.update(block)
                        remaining -= len(block)
                    out.flush()
                    os.fsync(out.fileno())
                    if chunk_digest.hexdigest() != declared_chunk_digest:
                        return reply({"error": "chunk_sha256_mismatch", "offset": offset}, 422)
            except BaseException:
                # Next attempt automatically truncates to committed DB offset.
                raise
        except (OSError, InsufficientStorage):
            return reply({"error": "chunk_write_failed_retry_from_status"}, 507)
        session.received_bytes += size
        extend_expiry(session)
        session.save(update_fields=["received_bytes", "expires_at", "updated_at"])
        return reply({"offset": session.received_bytes, "byte_size": session.byte_size})


@login_required
@require_POST
def finish_upload(request, upload_id):
    with transaction.atomic():
        session = get_object_or_404(ResumableUpload.objects.select_for_update(),
                                    pk=upload_id, owner=request.user)
        if session.state == ResumableUpload.State.COMPLETE and session.game_id:
            return reply({"state": "complete", "game_id": session.game_id})
        if not _available(session):
            return reply({"error": "upload_closed_or_expired"}, 410)
        if session.received_bytes != session.byte_size:
            return reply({"error": "upload_incomplete", "offset": session.received_bytes}, 409)
        path = session_path(session)
        if not path.exists() or path.stat().st_size != session.byte_size:
            return reply({"error": "file_size_mismatch"}, 409)
        final_digest = hash_file(path)
        if session.expected_sha256 and final_digest != session.expected_sha256:
            return reply({"error": "sha256_mismatch"}, 422)
        try:
            duration = probe_duration(path)
            require_media_space(0)
        except (MediaError, OSError, InsufficientStorage):
            return reply({"error": "invalid_media_or_disk"}, 422)
        owner = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if (owner_raw_media_bytes(owner)
                + reserved_upload_bytes(owner, exclude_id=session.pk)
                + session.byte_size > settings.OWNER_MAX_RAW_BYTES):
            return reply({"error": "owner_quota_conflict"}, 507)
        game = Game(venue=session.venue, title=session.title,
                    duration_seconds=duration)
        # A rename on the same filesystem avoids a second 4-GiB media copy.
        name = video_path(game, session.filename)
        target = Path(settings.MEDIA_ROOT) / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.replace(path, target)
            game.video.name = name
            game.save()
            session.game = game
            session.state = ResumableUpload.State.COMPLETE
            session.save(update_fields=["game", "state", "updated_at"])
        except Exception:
            # Filesystem moves and DB transactions are not atomic together.
            # Preserve the bytes for retry if the DB operation fails.
            if target.exists() and not path.exists():
                os.replace(target, path)
            raise
    return reply({"state": "complete", "game_id": game.pk,
                  "duration_seconds": duration, "sha256": final_digest})
