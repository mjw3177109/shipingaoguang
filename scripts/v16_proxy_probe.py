#!/usr/bin/env python3
"""V1.6: REAL localhost Nginx -> Gunicorn -> Django multipart integration.

Test transport / buffering behavior, NOT true basketball footage or public TLS.
Always use dedicated disposable SQLite and no production services.
"""
import argparse
import hashlib
import http.client
import json
import os
import secrets
import shutil
import signal
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
from scripts import v15_http_probe as http_probe

PROXY_PORT = 18888
APP_PORT = 18887


def size_under(root):
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


class TempMonitor:
    def __init__(self, paths):
        self.paths = paths
        self.stop = threading.Event()
        self.highest = {name: 0 for name in paths}
        self.sample_count = 0
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stop.is_set():
            for name, root in self.paths.items():
                try:
                    self.highest[name] = max(self.highest[name], size_under(root))
                except OSError:
                    pass
            self.sample_count += 1
            self.stop.wait(.05)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=3)


def request_status(port, length, *, cookie=""):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    connection.putrequest("POST", "/games/upload/")
    connection.putheader("Content-Type", "multipart/form-data; boundary=V16Check")
    connection.putheader("Content-Length", str(length))
    if cookie:
        connection.putheader("Cookie", cookie)
    connection.endheaders()
    # No body; both guards must reject by Content-Length alone.
    result = connection.getresponse()
    code = result.status
    result.read()
    connection.close()
    return code


def wait_http():
    for _ in range(120):
        try:
            connection = http.client.HTTPConnection("127.0.0.1", PROXY_PORT, timeout=1)
            connection.request("GET", "/health/live")
            response = connection.getresponse()
            response.read()
            connection.close()
            if response.status == 204:
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(.1)
    raise RuntimeError("Nginx -> Gunicorn /health/live did not become ready")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--minimum-bytes", type=int, default=32 * 1024**2)
    args = parser.parse_args()
    if os.environ.get("BASKETCUT_V16_ISOLATED") != "YES":
        raise RuntimeError("BASKETCUT_V16_ISOLATED=YES is mandatory")
    if not settings.DEBUG or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
        raise RuntimeError("Production database forbidden")
    if "basketcut-v16" not in str(settings.DATABASES["default"]["NAME"]):
        raise RuntimeError("Dedicated v16 SQLite path required")
    if not shutil.which("nginx"):
        raise RuntimeError("Actual Nginx executable required")

    path = Path(args.video).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.stat().st_size < args.minimum_bytes:
        raise RuntimeError("Input must meet the declared fixture byte floor")
    if path.stat().st_size + 1024**2 >= settings.MAX_UPLOAD_BYTES:
        raise RuntimeError("MAX_UPLOAD_BYTES insufficient for input and form headers")
    origin_duration = probe_duration(path)
    root = out / "nginx_runtime"
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "body_temp").mkdir(parents=True, exist_ok=True)
    (out / "django_temp").mkdir(exist_ok=True)
    if os.environ.get("TMPDIR") != str(out / "django_temp"):
        raise RuntimeError("TMPDIR must point at the dedicated test upload temporary directory")
    user = get_user_model().objects.create_user(
        username="v16_proxy_" + uuid.uuid4().hex[:12], password=secrets.token_hex(16))
    venue = Venue.objects.create(owner=user, name="V16 isolated HTTP proxy")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    csrf = secrets.token_hex(16)
    cookies = f"sessionid={session.session_key}; csrftoken={csrf}"
    # Nginx deliberately permits a little more than Django so each limit
    # can be independently exercised with a no-body Content-Length request.
    soft_limit = settings.MAX_UPLOAD_BYTES
    hard_limit = soft_limit + 8 * 1024**2
    nginx_config = f"""
worker_processes 1;
pid {root}/nginx.pid;
error_log {root}/logs/error.log notice;
events {{ worker_connections 512; }}
http {{
  access_log {root}/logs/access.log;
  client_body_temp_path {root}/body_temp;
  client_max_body_size {hard_limit};
  server {{
    listen 127.0.0.1:{PROXY_PORT};
    server_name localhost;
    location / {{
      proxy_pass http://127.0.0.1:{APP_PORT};
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-Proto http;
      proxy_http_version 1.1;
      proxy_request_buffering off;
      proxy_buffering off;
      proxy_read_timeout 300s;
      proxy_send_timeout 300s;
    }}
  }}
}}
"""
    config = root / "nginx.conf"
    config.write_text(nginx_config)
    subprocess.run(["nginx", "-t", "-c", str(config), "-p", str(root)],
                   capture_output=True, text=True, check=True, timeout=15)
    with (out / "gunicorn.log").open("w") as app_log, (out / "nginx.log").open("w") as proxy_log:
        gunicorn = subprocess.Popen([
            sys.executable, "-m", "gunicorn", "basketcut.wsgi:application",
            "--bind", f"127.0.0.1:{APP_PORT}", "--workers", "2",
            "--timeout", "300"], stdout=app_log, stderr=subprocess.STDOUT,
            start_new_session=True)
        nginx = None
        try:
            nginx = subprocess.Popen(["nginx", "-c", str(config), "-p", str(root),
                                      "-g", "daemon off;"],
                                     stdout=proxy_log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            wait_http()
            with TempMonitor({"nginx_body_temp": root / "body_temp",
                              "django_upload_temp": out / "django_temp"}) as monitor:
                started = time.monotonic()
                http_probe.PORT = PROXY_PORT
                status, location, body_bytes, sent, details = http_probe.multipart_post(
                    path, venue.pk, "V16 Nginx actual multipart", cookies, csrf)
                elapsed = time.monotonic() - started
            if status != 302 or not location.startswith("/games/"):
                raise RuntimeError(f"Proxy upload failed: {status}; {details}")
            stored = Game.objects.get(venue=venue, title="V16 Nginx actual multipart")
            actual_hash = http_probe.sha256(stored.video.path)
            if (actual_hash != http_probe.sha256(path)
                    or stored.video.size != path.stat().st_size):
                raise RuntimeError("End-to-end Nginx -> Django file hash/size mismatch")

            # Guard 1: Django's request-length middleware, below the proxy cap.
            django_limit_status = request_status(PROXY_PORT, soft_limit + 1024**2 + 2048)
            # Guard 2: Nginx itself, above its own cap.
            nginx_limit_status = request_status(PROXY_PORT, hard_limit + 1)
            if (django_limit_status != 413 or nginx_limit_status != 413
                    or Game.objects.filter(venue=venue).count() != 1):
                raise RuntimeError("Body limits failed or rejected requests persisted rows")
            report = {
                "schema_version": 1, "basketcut_version": "1.6",
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "result": "PASS",
                "transport": "real_localhost_nginx_to_gunicorn_to_django",
                "nginx_proxy_request_buffering_off": True,
                "input_type": "synthetic_decodable_mp4_with_declared_trailing_padding",
                "actual_video_core_duration_seconds": round(origin_duration, 3),
                "multipart_body_bytes": body_bytes,
                "bytes_streamed_from_file": sent,
                "source_and_stored_sha256_equal": True,
                "http_status": status,
                "upload_elapsed_seconds": round(elapsed, 3),
                "early_django_413": django_limit_status == 413,
                "early_nginx_413": nginx_limit_status == 413,
                "observed_temp_peak_bytes": monitor.highest,
                "temp_sample_count": monitor.sample_count,
                "input_bytes": path.stat().st_size,
                "media_stored_bytes": stored.video.size,
                "proxy_uses_tls": False,
                "large_2_to_4_gib_tested": path.stat().st_size >= 2 * 1024**3,
                "known_limitations": [
                    "Localhost Nginx transport only; no public network, TLS or browser test",
                    "Padded synthetic video tests transport, not long-match decoding",
                    "Observed temporary space sampled every 50ms, not guaranteed maximum",
                    "Proxy buffering off reduces disk staging, but cannot guarantee capacity",
                ],
            }
            (out / "v16_proxy_report.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
        finally:
            if nginx is not None and nginx.poll() is None:
                os.killpg(nginx.pid, signal.SIGTERM)
                nginx.wait(timeout=15)
            if gunicorn.poll() is None:
                os.killpg(gunicorn.pid, signal.SIGTERM)
                gunicorn.wait(timeout=15)


if __name__ == "__main__":
    main()
