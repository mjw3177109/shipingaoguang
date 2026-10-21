#!/usr/bin/env python3
"""Real TCP -> Gunicorn -> Django multipart upload, isolated CI database only.

Tests bounded client streaming > Django memory-upload threshold, successful
storage and header-level 413. Does NOT claim real 4 GiB throughput or nginx.
"""
import argparse
import hashlib
import http.client
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "basketcut.settings")
import django
django.setup()

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from highlights.models import Game, Venue
from highlights.services import probe_duration

PORT = 18887


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(262144), b""):
            h.update(chunk)
    return h.hexdigest()


def server_ready():
    try:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=1)
        conn.request("GET", "/health/live")
        res = conn.getresponse()
        res.read()
        conn.close()
        return res.status == 204
    except (OSError, http.client.HTTPException):
        return False


def multipart_post(path, venue_id, title, cookie, token):
    boundary = "BasketcutV15_" + uuid.uuid4().hex
    preamble = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"venue_id\"\r\n\r\n{venue_id}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\n{title}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"basketball.mp4\"\r\n"
        "Content-Type: video/mp4\r\n\r\n"
    ).encode()
    ending = f"\r\n--{boundary}--\r\n".encode()
    total = len(preamble) + path.stat().st_size + len(ending)
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=180)
    conn.putrequest("POST", "/games/upload/")
    for k, v in {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(total),
        "Cookie": cookie,
        "X-CSRFToken": token,
    }.items():
        conn.putheader(k, v)
    conn.endheaders()
    conn.send(preamble)
    sent = 0
    with path.open("rb") as fp:
        while data := fp.read(262144):
            conn.send(data)
            sent += len(data)
    conn.send(ending)
    res = conn.getresponse()
    status = res.status
    location = res.getheader("Location") or ""
    body = res.read(500)
    conn.close()
    return status, location, total, sent, body.decode(errors="replace")[:200]


def header_only_too_large(cookie, token):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    conn.putrequest("POST", "/games/upload/")
    conn.putheader("Content-Type", "multipart/form-data; boundary=Oversize")
    conn.putheader("Content-Length", str(settings.MAX_UPLOAD_BYTES + 1048577))
    conn.putheader("Cookie", cookie)
    conn.putheader("X-CSRFToken", token)
    conn.endheaders()
    # Header-level rejection deliberately sends NO media body.
    res = conn.getresponse()
    status = res.status
    res.read()
    conn.close()
    return status


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    if os.environ.get("BASKETCUT_V15_ISOLATED") != "YES":
        raise RuntimeError("Set BASKETCUT_V15_ISOLATED=YES in a disposable CI environment")
    if not settings.DEBUG or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
        raise RuntimeError("Refuses to use a production database")
    if "basketcut-v15" not in str(settings.DATABASES["default"]["NAME"]):
        raise RuntimeError("DB path does not identify the isolated V1.5 environment")
    video = Path(args.video).resolve()
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if video.stat().st_size <= settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
        raise RuntimeError("Fixture must be larger than Django's in-memory upload threshold")
    if video.stat().st_size + 1048576 >= settings.MAX_UPLOAD_BYTES:
        raise RuntimeError("Fixture must fit the configured HTTP body and account quotas")
    duration = probe_duration(video)
    username = "v15_http_" + uuid.uuid4().hex[:12]
    user = get_user_model().objects.create_user(username=username, password=secrets.token_hex(14))
    venue = Venue.objects.create(owner=user, name="V15 HTTP upload isolated")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    csrf = secrets.token_hex(16)
    cookies = f"sessionid={session.session_key}; csrftoken={csrf}"
    log = (output / "gunicorn_http_test.log").open("w")
    web = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "basketcut.wsgi:application",
         "--bind", f"127.0.0.1:{PORT}", "--workers", "2", "--timeout", "140",
         "--access-logfile", "-"],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        for _ in range(100):
            if server_ready():
                break
            if web.poll() is not None:
                raise RuntimeError("Gunicorn exited during startup")
            time.sleep(.1)
        else:
            raise RuntimeError("Gunicorn did not become ready")
        started = time.monotonic()
        status, location, length, streamed, excerpt = multipart_post(
            video, venue.pk, "V15 real HTTP multipart", cookies, csrf)
        elapsed = time.monotonic() - started
        if status != 302 or not location.startswith("/games/"):
            raise RuntimeError(f"Real multipart upload failed HTTP {status}: {excerpt}")
        stored = Game.objects.get(venue=venue, title="V15 real HTTP multipart")
        if stored.video.size != video.stat().st_size or stored.duration_seconds < 1:
            raise RuntimeError("Uploaded media differs in size or has no duration")
        stored_sha256 = sha256(stored.video.path)
        source_sha256 = sha256(video)
        if stored_sha256 != source_sha256:
            raise RuntimeError("HTTP streamed file SHA-256 mismatch")
        existing = Game.objects.filter(venue=venue).count()
        blocked_status = header_only_too_large(cookies, csrf)
        if blocked_status != 413 or Game.objects.filter(venue=venue).count() != existing:
            raise RuntimeError("Oversized HTTP body was not rejected before persistence")
        report = {
            "schema_version": 1, "basketcut_version": "1.5",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "result": "PASS",
            "transport": "real_localhost_tcp_http_to_gunicorn_to_django",
            "nginx_or_external_reverse_proxy_tested": False,
            "browser_client_tested": False,
            "source_duration_seconds": duration,
            "multipart_body_bytes": length,
            "file_bytes_sent_in_256kib_chunks": streamed,
            "actual_file_sha256_verified": stored_sha256 == source_sha256,
            "django_memory_upload_threshold_bytes": settings.FILE_UPLOAD_MAX_MEMORY_SIZE,
            "http_response_status": status,
            "upload_duration_seconds": round(elapsed, 3),
            "oversize_content_length_rejected_without_body": blocked_status == 413,
            "oversize_status": blocked_status,
            "max_upload_bytes": settings.MAX_UPLOAD_BYTES,
            "client_max_file_chunk_bytes": 262144,
            "notes": "No 4GiB test, no external proxy or interrupted-resume test.",
        }
        (output / "v15_http_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        import signal
        if web.poll() is None:
            os.killpg(web.pid, signal.SIGTERM)
            try:
                web.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(web.pid, signal.SIGKILL)
                web.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    main()
