#!/usr/bin/env python3
"""V1.9: two ACTUAL concurrently streaming users through Nginx / Gunicorn / PostgreSQL.

Isolated only: 2 independent accounts, 2 concurrent HTTP clients, 2 valid
16 MiB outputs and full SHA equality. This is not a 2x4GiB stress test.
"""
import argparse
import hashlib
import http.client
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "basketcut.settings")
import django
django.setup()

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import close_old_connections
from highlights.models import Game, Venue
from highlights.resumable import hash_file

NGINX_PORT = 18879
APP_PORT = 18878
CHUNK_SIZE = 8 * 1024**2


def send(method, path, *, body=None, cookie="", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", NGINX_PORT, timeout=120)
    h = {"Cookie": cookie, **(headers or {})}
    if body is not None:
        h["Content-Length"] = str(len(body))
    conn.request(method, path, body=body, headers=h)
    res = conn.getresponse()
    status = res.status
    payload = res.read(8192)
    conn.close()
    try:
        return status, json.loads(payload)
    except (ValueError, TypeError):
        return status, {}


def main(args):
    if os.environ.get("BASKETCUT_V19_ISOLATED") != "YES" or not settings.DEBUG:
        raise RuntimeError("Dedicated isolated V1.9 environment required")
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        raise RuntimeError("Parallel real HTTP must use real PostgreSQL")
    if "basketcut" not in str(settings.DATABASES["default"].get("NAME")):
        raise RuntimeError("Dedicated PostgreSQL CI database required")
    source = Path(args.video).resolve()
    output = Path(args.out).resolve()
    media = Path(settings.MEDIA_ROOT).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not source.is_file() or source.stat().st_size < CHUNK_SIZE + 1:
        raise RuntimeError("Need at least two binary upload chunks")
    if not media.is_dir():
        raise RuntimeError("Dedicated MEDIA_ROOT must exist")
    digest = hash_file(source)
    users = []
    for i in range(2):
        user = get_user_model().objects.create_user(
            username="v19_http_" + uuid.uuid4().hex[:12],
            password=secrets.token_hex(16))
        venue = Venue.objects.create(owner=user, name=f"V19 parallel {i}")
        session = SessionStore()
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.save()
        users.append({
            "username": user.username, "venue_id": venue.pk, "user_id": user.pk,
            "cookie": "sessionid=" + session.session_key,
        })

    root = output / "nginx"
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "temp").mkdir(exist_ok=True)
    config = root / "nginx.conf"
    config.write_text(f"""
worker_processes 1;
pid {root}/nginx.pid;
error_log {root}/logs/error.log warn;
events {{ worker_connections 512; }}
http {{
    access_log {root}/logs/access.log;
    client_body_temp_path {root}/temp;
    server {{
        listen 127.0.0.1:{NGINX_PORT};
        server_name localhost;
        client_max_body_size 10m;
        location / {{
            proxy_pass http://127.0.0.1:{APP_PORT};
            proxy_request_buffering off;
            proxy_buffering off;
            proxy_http_version 1.1;
            proxy_read_timeout 180s;
        }}
    }}
}}
""")
    subprocess.run(["nginx", "-t", "-c", str(config), "-p", str(root)],
                   check=True, capture_output=True, text=True, timeout=20)
    app_log = (output / "gunicorn.log").open("w")
    proxy_log = (output / "nginx.log").open("w")
    app = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "basketcut.wsgi:application",
         "--bind", f"127.0.0.1:{APP_PORT}", "--workers", "4",
         "--timeout", "180"], stdout=app_log,
        stderr=subprocess.STDOUT, start_new_session=True)
    proxy = None
    try:
        proxy = subprocess.Popen(
            ["nginx", "-c", str(config), "-p", str(root), "-g", "daemon off;"],
            stdout=proxy_log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(160):
            if app.poll() is not None or proxy.poll() is not None:
                raise RuntimeError("Server failed early")
            try:
                status, _ = send("GET", "/health/live")
                if status == 204:
                    break
            except (OSError, http.client.HTTPException):
                time.sleep(.1)
        else:
            raise RuntimeError("No actual Nginx/Gunicorn readiness")

        concurrent = threading.Barrier(2)
        lock = threading.Lock()
        running = 0
        peak_client_concurrent = 0

        def upload(user):
            nonlocal running, peak_client_concurrent
            session_meta = json.dumps({
                "venue_id": user["venue_id"], "title": "V19 parallel ingestion",
                "filename": "valid_fixture.mp4",
                "byte_size": source.stat().st_size,
                "sha256": digest,
            }).encode()
            status, started = send("POST", "/games/resumable/",
                body=session_meta, cookie=user["cookie"],
                headers={"Content-Type": "application/json"})
            if status != 201:
                raise RuntimeError(f"Create upload session failed: {status} {started}")
            path = "/games/resumable/" + started["id"] + "/"
            concurrent.wait(timeout=30)
            with lock:
                running += 1
                peak_client_concurrent = max(peak_client_concurrent, running)
            try:
                offset = 0
                with source.open("rb") as media_in:
                    while block := media_in.read(CHUNK_SIZE):
                        status, ack = send("POST", path + "chunk/",
                            body=block, cookie=user["cookie"],
                            headers={
                                "Content-Type": "application/octet-stream",
                                "X-Upload-Offset": str(offset),
                                "X-Chunk-SHA256": hashlib.sha256(block).hexdigest(),
                            })
                        if status != 200 or ack.get("offset") != offset + len(block):
                            raise RuntimeError(f"Chunk failed HTTP {status}: {ack}")
                        offset = ack["offset"]
                status, finished = send("POST", path + "finish/", body=b"",
                                        cookie=user["cookie"])
                if status != 200 or finished.get("sha256") != digest:
                    raise RuntimeError(f"Finalize failed HTTP {status}: {finished}")
                close_old_connections()
                saved = Game.objects.get(pk=finished["game_id"])
                if saved.venue.owner_id != user["user_id"]:
                    raise RuntimeError("Cross-tenant ownership leak")
                if saved.video.size != source.stat().st_size or hash_file(saved.video.path) != digest:
                    raise RuntimeError("Stored media differs from client bytes")
                return {"game_id": saved.pk, "confirmed_bytes": offset,
                        "hash_matches": True}
            finally:
                with lock:
                    running -= 1
                close_old_connections()

        before = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            f = [pool.submit(upload, user) for user in users]
            results = [future.result(timeout=210) for future in f]
        elapsed = time.monotonic() - before
        if Game.objects.filter(title="V19 parallel ingestion").count() != 2:
            raise RuntimeError("Two unique Games not persisted")
        if peak_client_concurrent < 2:
            raise RuntimeError("Actual concurrent HTTP clients not observed")
        access = (root / "logs" / "access.log").read_text()
        successes = sum("/chunk/" in line and '" 200 ' in line
                        for line in access.splitlines())
        if successes < 4:
            raise RuntimeError("Proxy logs show fewer than 4 completed chunks")
        report = {
            "schema_version": 1, "version": "1.9", "result": "PASS",
            "transport": "actual_localhost_nginx_to_gunicorn_to_postgresql",
            "separate_authenticated_tenants": 2,
            "actual_concurrent_clients_peak": peak_client_concurrent,
            "actual_saved_games": len(results),
            "bytes_per_user": source.stat().st_size,
            "total_confirmed_http_bytes": sum(x["confirmed_bytes"] for x in results),
            "full_sha_and_ownership_verified_each_user": True,
            "nginx_chunk_successes": successes,
            "wall_clock_seconds": round(elapsed, 3),
            "video_content": "short_synthetic_mp4_padded_for_network_test",
            "limitations": [
                "Two simultaneous small 16MiB users, NOT two concurrent 4GiB users",
                "Localhost and no TLS; production public load not tested",
                "Database shared-volume capacity mocked separately in V1.8 tests",
                "No genuine basketball event detection measured",
            ],
        }
        (output / "v19_parallel_postgres_http.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        if proxy is not None and proxy.poll() is None:
            os.killpg(proxy.pid, signal.SIGTERM)
            proxy.wait(timeout=15)
        if app.poll() is None:
            os.killpg(app.pid, signal.SIGTERM)
            app.wait(timeout=15)
        app_log.close()
        proxy_log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    main(parser.parse_args())
