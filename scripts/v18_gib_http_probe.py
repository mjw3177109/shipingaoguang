#!/usr/bin/env python3
"""V1.8: actually transmit multi-GiB bytes over localhost TCP HTTP.

Unlike a sparse-file stat-only check, ALL input bytes cross the HTTP socket.
A playable synthetic 20-second MP4 header and zero-filled trailing padding
form the fixture. Never claim it is a multi-hour basketball recording.

Must run against disposable local SQLite and dedicated MEDIA_ROOT.
"""
import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import secrets
import signal
import socket
import subprocess
import sys
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
from django.db import close_old_connections
from highlights.models import Game, Venue, ResumableUpload
from highlights.resumable import hash_file
from highlights.services import probe_duration

HOST = "127.0.0.1"
PORT = 18898
NGINX_PORT = 18899
REQUEST_PORT = PORT
CHUNK = 8 * 1024 * 1024


def request(method, path, *, body=None, cookie="", csrf="", headers=None, timeout=120):
    conn = http.client.HTTPConnection(HOST, REQUEST_PORT, timeout=timeout)
    h = {"Cookie": cookie, "X-CSRFToken": csrf, **(headers or {})}
    if body is not None:
        h["Content-Length"] = str(len(body))
    conn.request(method, path, body=body, headers=h)
    response = conn.getresponse()
    data = response.read(4096)
    code = response.status
    conn.close()
    try:
        return code, json.loads(data)
    except (ValueError, TypeError):
        return code, {}


def broken_upload(path, *, offset, total, digest, cookie, csrf):
    """Connect, announce whole chunk, send 1 MiB only, deliberately close socket."""
    conn = http.client.HTTPConnection(HOST, REQUEST_PORT, timeout=15)
    conn.putrequest("POST", path)
    for name, value in {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(total),
        "X-Upload-Offset": str(offset),
        "X-Chunk-SHA256": digest,
        "Cookie": cookie, "X-CSRFToken": csrf,
    }.items():
        conn.putheader(name, value)
    conn.endheaders()
    conn.send(b"\x00" * 1024 * 1024)
    if conn.sock is not None:
        conn.sock.shutdown(socket.SHUT_RDWR)
    conn.close()


def vmrss_bytes(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, IndexError, ValueError):
        pass
    return 0


class ResourceMonitor:
    """Best-effort sampled RSS and filesystem free space."""
    def __init__(self, pid, folder):
        self.pid = pid
        self.folder = folder
        self.peak_workers_rss = 0
        self.min_free = os.statvfs(folder).f_bavail * os.statvfs(folder).f_frsize
        self.samples = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self.stop.is_set():
            try:
                children = Path(f"/proc/{self.pid}/task/{self.pid}/children")
                descendants = ([int(x) for x in children.read_text().split()]
                               if children.exists() else [])
                rss = sum(vmrss_bytes(pid) for pid in [self.pid, *descendants])
                self.peak_workers_rss = max(self.peak_workers_rss, rss)
                st = os.statvfs(self.folder)
                self.min_free = min(self.min_free, st.f_bavail * st.f_frsize)
                self.samples += 1
            except (OSError, ValueError):
                pass
            self.stop.wait(0.2)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=5)


def run(args):
    global REQUEST_PORT
    use_nginx = bool(getattr(args, "nginx", False))
    isolated_flag = ("BASKETCUT_V19_ISOLATED" if use_nginx
                     else "BASKETCUT_V18_ISOLATED")
    if os.getenv(isolated_flag) != "YES" or not settings.DEBUG:
        raise RuntimeError("Isolated disposable GiB environment required")
    if use_nginx and not shutil.which("nginx"):
        raise RuntimeError("Actual Nginx binary required for V1.9")
    database = settings.DATABASES["default"]
    if (database["ENGINE"] != "django.db.backends.sqlite3"
             or ("basketcut-v19" if use_nginx else "basketcut-v18") not in str(database.get("NAME"))):
        raise RuntimeError("Never run multi-GiB test on a production database")
    path = Path(args.video).resolve()
    out = Path(args.out).resolve()
    media = Path(settings.MEDIA_ROOT).resolve()
    out.mkdir(exist_ok=True, parents=True)
    if media == out or media in out.parents or media == path or media in path.parents:
        raise RuntimeError("The input, audit and media paths must be isolated")
    if not media.is_dir() or not path.is_file():
        raise RuntimeError("Missing dedicated MEDIA_ROOT or source file")
    length = path.stat().st_size
    if length < 2 * 1024**3 or length > 4 * 1024**3:
        raise RuntimeError("This test requires a real 2-4 GiB file")
    if length > settings.MAX_UPLOAD_BYTES or length > settings.OWNER_MAX_RAW_BYTES:
        raise RuntimeError("Configured per-user quota is smaller than test media")
    duration = probe_duration(path)
    checksum = hash_file(path)

    owner = get_user_model().objects.create_user(
        username="v18_http_" + uuid.uuid4().hex[:12],
        password=secrets.token_hex(16))
    venue = Venue.objects.create(owner=owner, name="V18 isolated multi-GiB test")
    session = SessionStore()
    session[SESSION_KEY] = str(owner.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = owner.get_session_auth_hash()
    session.save()
    csrf = secrets.token_hex(16)
    cookie = "sessionid=" + session.session_key + "; csrftoken=" + csrf

    log = (out / "v18_gunicorn.log").open("w")
    # Actual Nginx listens on a distinct local TCP port. 8MiB chunks may
    # pass, but a raw >10MiB single upload request is rejected at the proxy.
    nginx_proc = None
    nginx_out = None
    nginx_root = out / "nginx_runtime"
    if use_nginx:
        (nginx_root / "logs").mkdir(parents=True, exist_ok=True)
        (nginx_root / "body_tmp").mkdir(parents=True, exist_ok=True)
        config = nginx_root / "nginx.conf"
        config.write_text(f"""
worker_processes 1;
pid {nginx_root}/nginx.pid;
error_log {nginx_root}/logs/error.log notice;
events {{ worker_connections 512; }}
http {{
  access_log {nginx_root}/logs/access.log;
  client_body_temp_path {nginx_root}/body_tmp;
  server {{
    listen {HOST}:{NGINX_PORT};
    server_name localhost;
    client_max_body_size 10m;
    location / {{
      proxy_pass http://{HOST}:{PORT};
      proxy_set_header Host $host;
      proxy_http_version 1.1;
      proxy_request_buffering off;
      proxy_buffering off;
      proxy_read_timeout 240s;
      proxy_send_timeout 240s;
    }}
  }}
}}
""")
        subprocess.run(["nginx", "-t", "-c", str(config), "-p", str(nginx_root)],
                       check=True, text=True, capture_output=True, timeout=15)
        REQUEST_PORT = NGINX_PORT
    else:
        REQUEST_PORT = PORT
    proc = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "basketcut.wsgi:application",
         "--bind", HOST + ":" + str(PORT), "--workers", "2", "--timeout", "240"],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        if use_nginx:
            nginx_out = (out / "v19_nginx.log").open("w")
            nginx_proc = subprocess.Popen(
                ["nginx", "-c", str(config), "-p", str(nginx_root),
                 "-g", "daemon off;"], stdout=nginx_out, stderr=subprocess.STDOUT,
                start_new_session=True)
        for _ in range(150):
            if proc.poll() is not None or (nginx_proc and nginx_proc.poll() is not None):
                raise RuntimeError("HTTP Gunicorn worker exited during startup")
            try:
                code, _ = request("GET", "/health/live", timeout=1)
                if code == 204:
                    break
            except (OSError, http.client.HTTPException):
                time.sleep(.1)
        else:
            raise RuntimeError("Gunicorn did not become healthy")
        code, start = request("POST", "/games/resumable/",
            body=json.dumps({
                "venue_id": venue.pk, "title": "V18 GiB Test",
                "filename": path.name,
                "byte_size": length, "sha256": checksum,
            }).encode(), csrf=csrf, cookie=cookie,
            headers={"Content-Type": "application/json"})
        if code != 201 or "id" not in start:
            raise RuntimeError("Could not initiate real GiB session: " + str(start))
        upload_id = start["id"]
        base = "/games/resumable/" + upload_id + "/"
        observed_abort_offset = False
        read_limit = 0
        transferred_bytes = 0
        requests_count = 0
        begun = time.monotonic()
        with ResourceMonitor(proc.pid, media) as monitor:
            with path.open("rb") as source:
                offset = 0
                while offset < length:
                    buffer = source.read(min(CHUNK, length - offset))
                    read_limit = max(read_limit, len(buffer))
                    digest = hashlib.sha256(buffer).hexdigest()
                    if not observed_abort_offset and offset >= CHUNK:
                        # First chunk already committed; crash the *network*
                        # in the middle of the next chunk, then resume.
                        broken_upload(base + "chunk/", offset=offset,
                                      total=len(buffer), digest=digest,
                                      cookie=cookie, csrf=csrf)
                        time.sleep(.35)
                        status, state = request("GET", base, cookie=cookie, csrf=csrf)
                        if status != 200 or state.get("offset") != offset:
                            raise RuntimeError("Server committed interrupted chunk")
                        observed_abort_offset = True

                    attempt = 0
                    while True:
                        try:
                            status, ack = request("POST", base + "chunk/",
                                body=buffer, csrf=csrf, cookie=cookie,
                                headers={"Content-Type": "application/octet-stream",
                                         "X-Upload-Offset": str(offset),
                                         "X-Chunk-SHA256": digest})
                            requests_count += 1
                            if status == 200 and ack.get("offset") == offset + len(buffer):
                                offset += len(buffer)
                                transferred_bytes += len(buffer)
                                break
                            if status == 409 and isinstance(ack.get("offset"), int):
                                # Confirm server state rather than assuming failed POST
                                # did not commit. All chunks are SHA protected.
                                offset = ack["offset"]
                                source.seek(offset)
                                break
                            raise RuntimeError(f"Unexpected HTTP chunk status {status}: {ack}")
                        except (OSError, http.client.HTTPException):
                            attempt += 1
                            if attempt > 3:
                                raise
                            time.sleep(.3 * attempt)
                            state_status, state = request("GET", base,
                                                          csrf=csrf, cookie=cookie)
                            if state_status != 200:
                                raise RuntimeError("Cannot confirm resumed offset")
                            offset = state["offset"]
                            source.seek(offset)
                            break
                    if requests_count and requests_count % 32 == 0:
                        print(f"confirmed_chunks={requests_count}, server_offset={offset}", flush=True)

            if transferred_bytes != length:
                raise RuntimeError("Not all input bytes crossed the HTTP socket as committed chunks")
            final_start = time.monotonic()
            status, done = request("POST", base + "finish/", body=b"",
                                    csrf=csrf, cookie=cookie, timeout=240)
            if status != 200 or done.get("sha256") != checksum:
                raise RuntimeError(f"GiB server finalized wrong hash: {status}, {done}")
            finalize_seconds = round(time.monotonic() - final_start, 3)
        close_old_connections()
        game = Game.objects.get(pk=done["game_id"])
        if game.video.size != length or hash_file(game.video.path) != checksum:
            raise RuntimeError("Final media mismatch: bytes did not reach stored Game")
        if ResumableUpload.objects.get(pk=upload_id).received_bytes != length:
            raise RuntimeError("Committed offset differs from expected byte count")
        code, again = request("POST", base + "finish/", body=b"",
                              csrf=csrf, cookie=cookie, timeout=30)
        if code != 200 or again.get("game_id") != game.pk:
            raise RuntimeError("Final upload idempotency failed")
        report = {
            "schema_version": 1, "version": ("1.9" if use_nginx else "1.8"), "result": "PASS",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "short_synthetic_playable_mp4_with_sparse_zero_tail",
            "actual_nginx_reverse_proxy": use_nginx,
            "nginx_request_buffering": "off" if use_nginx else "not_tested",
            "reverse_proxy_tls_tested": False,
            "is_real_basketball_full_match": False,
            "actual_total_file_bytes": length,
            "actual_http_confirmed_chunk_bytes": transferred_bytes,
            "chunks_confirmed": requests_count,
            "maximum_client_memory_chunk_bytes": read_limit,
            "real_socket_aborted_in_second_chunk": observed_abort_offset,
            "returned_offset_after_network_abort": CHUNK,
            "final_server_offset": length,
            "server_and_client_full_sha256_equal": True,
            "stored_file_sha256_verified_again": True,
            "double_finish_idempotent": True,
            "duration_of_playable_synthetic_core_seconds": duration,
            "http_upload_seconds": round(time.monotonic() - begun, 3),
            "finalization_sha_and_ffprobe_seconds": finalize_seconds,
            "peak_observed_gunicorn_master_plus_direct_workers_rss_bytes":
                monitor.peak_workers_rss or None,
            "observed_min_media_free_bytes": monitor.min_free,
            "media_free_sample_count": monitor.samples,
            "limits": [
                "Actual 2-4GiB HTTP byte transport on localhost Gunicorn (not production TLS/Nginx)",
                "Sparse source file but entire padded payload physically sent and stored",
                "Synthetic short MP4 does not establish complete game decoder or recall",
                "Memory and disk observations are sampled estimates, not guaranteed peaks",
            ],
        }
        if use_nginx:
            access = (nginx_root / "logs" / "access.log").read_text()
            # Only actual 200 acknowledgements passing Nginx are counted.
            count_200 = sum(
                'POST /games/resumable/' in line and '/chunk/ ' in line and
                re.search(r'" 200 ', line) is not None
                for line in access.splitlines())
            if count_200 < length // CHUNK:
                raise RuntimeError("Nginx log does not independently show all confirmed chunks")
            report["nginx_successful_chunk_post_count"] = count_200
            report["nginx_access_log_cross_checked"] = True
            report["limits"].append(
                "This test checks 4GiB through localhost Nginx, not public TLS/CDN")
        filename = "v19_4gib_nginx_audit.json" if use_nginx else "v18_gib_http_audit.json"
        (out / filename).write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        if nginx_proc is not None and nginx_proc.poll() is None:
            os.killpg(nginx_proc.pid, signal.SIGTERM)
            nginx_proc.wait(timeout=15)
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=15)
        if nginx_out is not None:
            nginx_out.close()
        log.close()
        REQUEST_PORT = PORT


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--nginx", action="store_true", help="V1.9 real Nginx reverse proxy")
    run(parser.parse_args())
