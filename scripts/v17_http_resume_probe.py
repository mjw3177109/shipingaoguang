#!/usr/bin/env python3
"""Real Gunicorn HTTP disconnection/reconnect test for V1.7 staged uploads.

Synthetic or padded test fixture only; never runs on a production database.
The client transmits chunks; the server hashes and acknowledges each block.
"""
import argparse
import hashlib
import http.client
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
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
from highlights.models import Game, Venue, ResumableUpload
from highlights.resumable import hash_file
from highlights.services import probe_duration

PORT = 18897
CHUNK_SIZE = 8 * 1024**2


def call(method, path, *, data=None, headers=None, cookie=""):
    con = http.client.HTTPConnection("127.0.0.1", PORT, timeout=90)
    hdr = {"Cookie": cookie}
    hdr.update(headers or {})
    if data is not None:
        hdr["Content-Length"] = str(len(data))
    con.request(method, path, body=data, headers=hdr)
    response = con.getresponse()
    payload = response.read()
    status = response.status
    con.close()
    try:
        return status, json.loads(payload)
    except (ValueError, TypeError):
        return status, {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    db_name = str(settings.DATABASES["default"].get("NAME"))
    if (os.environ.get("BASKETCUT_V17_ISOLATED") != "YES"
            or not settings.DEBUG
            or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3"
            or "basketcut-v17" not in db_name):
        raise RuntimeError("Only run with a disposable isolated BasketCut V1.7 SQLite DB")
    video = Path(args.video).resolve()
    destination = Path(args.out).resolve()
    destination.mkdir(exist_ok=True, parents=True)
    size = video.stat().st_size
    if not CHUNK_SIZE < size < 48 * 1024**2:
        raise RuntimeError("CI fixture must be 8MiB..48MiB")
    duration = probe_duration(video)
    content_digest = hash_file(video)

    account = get_user_model().objects.create_user(
        username="v17_http_" + uuid.uuid4().hex[:10], password=secrets.token_hex(16))
    venue = Venue.objects.create(owner=account, name="V17 reconnect court")
    session = SessionStore()
    session[SESSION_KEY] = str(account.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = account.get_session_auth_hash()
    session.save()
    csrf = secrets.token_hex(16)
    cookie = "sessionid=" + session.session_key + "; csrftoken=" + csrf
    auth = {"X-CSRFToken": csrf}

    logfile = (destination / "v17_gunicorn.log").open("w")
    server = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "basketcut.wsgi:application",
         "--bind", "127.0.0.1:" + str(PORT), "--workers", "2", "--timeout", "40"],
        stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        for _ in range(120):
            try:
                status, _ = call("GET", "/health/live")
                if status == 204:
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError("Gunicorn did not start")

        status, result = call("POST", "/games/resumable/",
            data=json.dumps({
                "venue_id": venue.pk, "title": "Real HTTP resumed game",
                "filename": video.name, "byte_size": size, "sha256": content_digest,
            }).encode(), headers={**auth, "Content-Type": "application/json"},
            cookie=cookie)
        if status != 201:
            raise RuntimeError("Session initiation failed: " + str(result))
        identifier = result["id"]
        endpoint = "/games/resumable/" + identifier + "/"
        with video.open("rb") as source:
            first = source.read(CHUNK_SIZE)
            rest = source.read()
        status, r = call("POST", endpoint + "chunk/", data=first, cookie=cookie,
            headers={**auth, "X-Upload-Offset": "0",
                     "X-Chunk-SHA256": hashlib.sha256(first).hexdigest(),
                     "Content-Type": "application/octet-stream"})
        if status != 200 or r["offset"] != CHUNK_SIZE:
            raise RuntimeError("First real HTTP chunk not committed")
        # Break an actual TCP POST mid-chunk: no committed offset is added.
        con = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        con.putrequest("POST", endpoint + "chunk/")
        for header, value in {
            **auth, "Cookie": cookie, "Content-Type": "application/octet-stream",
            "X-Upload-Offset": str(CHUNK_SIZE),
            "X-Chunk-SHA256": hashlib.sha256(rest).hexdigest(),
            "Content-Length": str(len(rest)),
        }.items():
            con.putheader(header, value)
        con.endheaders()
        con.send(rest[:len(rest)//2])
        if con.sock is not None:
            con.sock.shutdown(socket.SHUT_RDWR)
        con.close()
        time.sleep(.3)

        status, stat = call("GET", endpoint, cookie=cookie)
        if status != 200 or stat["offset"] != CHUNK_SIZE:
            raise RuntimeError("Broken connection illegally advanced committed offset")
        status, r = call("POST", endpoint + "chunk/", data=rest, cookie=cookie,
            headers={**auth, "X-Upload-Offset": str(CHUNK_SIZE),
                     "X-Chunk-SHA256": hashlib.sha256(rest).hexdigest(),
                     "Content-Type": "application/octet-stream"})
        if status != 200 or r["offset"] != size:
            raise RuntimeError("Reconnect chunk did not resume at confirmed offset")
        status, finish = call("POST", endpoint + "finish/", data=b"",
                              headers=auth, cookie=cookie)
        if status != 200 or finish.get("sha256") != content_digest:
            raise RuntimeError("Final source hash failed on real reconnected HTTP upload")
        game = Game.objects.get(pk=finish["game_id"])
        if hash_file(game.video.path) != content_digest:
            raise RuntimeError("Final stored Game differs from HTTP client source")
        status, again = call("POST", endpoint + "finish/", data=b"",
                              headers=auth, cookie=cookie)
        if status != 200 or again.get("game_id") != game.pk:
            raise RuntimeError("Idempotent finalize failed after reconnect")
        report = {
            "schema_version": 1, "version": "1.7", "result": "PASS",
            "utc": datetime.now(timezone.utc).isoformat(),
            "real_tcp_gunicorn_multipart_chunk_transport": True,
            "real_connection_aborted_mid_chunk": True,
            "server_confirmed_offset_after_abort": CHUNK_SIZE,
            "resumed_and_finished_offset": size,
            "original_and_stored_sha256_match": True,
            "finalize_idempotent": True,
            "size_bytes": size, "fixture_video_duration": duration,
            "real_nginx_tested_in_v17": False, "actual_2_to_4_gib_tested": False,
            "limitations": ["Locally generated padded fixture", "No public TLS",
                "One account, not a many-client performance benchmark",
                "Nginx V1.6 already has a separate 40MiB unchunked acceptance"],
        }
        (destination / "v17_http_resume_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        if server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=12)
        logfile.close()


if __name__ == "__main__":
    main()
