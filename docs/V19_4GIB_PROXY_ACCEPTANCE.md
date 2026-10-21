# BasketCut V1.9 – 4GiB Nginx TCP upload and real two-user HTTP

## Scope

BasketCut V1.9 tests two previously unverified operational boundaries,
without changing the V1.7 production upload API or image recognition workflow.

### Actual 4GiB byte transport

The isolated GitHub Actions job launches **actual Nginx + Gunicorn + Django**
with 8MiB resumable binary chunks. Its input file is a **short generated
20-second decodable MP4** with a declared sparse zero trailer, extended to
exactly **4,294,967,296 bytes (4GiB)**.

The client reads all 4GiB, sends every chunk over actual HTTP sockets to
Nginx, Gunicorn and Django, and the server writes every acknowledged chunk.
The test aborts the second network chunk, reconnects, verifies offset
consistency, recomputes end-to-end full-file SHA-256 on both client and
persisted Game media, and verifies idempotent completion.

Independent Nginx access log parsing must show >=512 successful chunk POSTs.
Request-body buffering is OFF in this test configuration. Gunicorn sampled
RSS and media volume free bytes are recorded as *sample observations*, not
strict memory peaks or physical quota certifications.

**No public TLS/CDN/real browser test**: this is localhost with two services.
**No genuine 4GiB complete basketball match**: content is a 20s synthetic
pattern plus padding, so decoding only covers the pattern and says nothing
about 60–120min match detection.

### Real two-account parallel upload (small fixture)

A separate job uses **real PostgreSQL 16** (not SQLite), actual Nginx and
Gunicorn, and **two independent authenticated HTTP upload clients**. Both
simultaneously transfer a 16MiB synthetic padded MP4 and create separate
Games. It checks final file SHA256, account ownership, distinct persisted
media rows, and Nginx successful chunk logs. This proves bounded two-client
concurrent transport and tenant separation only.

**It does not establish simultaneous 2x4GiB load** or disk quota fencing.
The previously introduced V1.8 global PostgreSQL pending-byte admission
uses a DB lock and a logical capacity ledger, *not filesystem reservation*.
For true production multi-tenant load testing, use measured real ingress
and an isolated storage volume with system-managed quotas.

## CI and reproducibility

See .github/workflows/v19-4gib-proxy-multiuser.yml; it must pass all 3 jobs
and the existing Backend CI before merging. Reports:

- v19_4gib_nginx_audit.json – actual HTTP bytes, SHA, Nginx successes
- v19_parallel_postgres_http.json – 2 independent users' uploads
- BasketCut_V1_9_PATCH.zip – only new/modified files

Neither input videos nor uploaded 4GiB media files are retained as artifacts.
The harness requires BASKETCUT_V19_ISOLATED=YES, a dedicated test DB and
temporary media root, and non-production DEBUG mode. Do not manually point
it at production. 4GiB test requires >7GiB free on the runner.

## Remaining commercial gates

1. Genuine licensed 60–120min continuous match footage from >=3 independent
   events, complete human labels and rights attestations; use existing
   real_match_gate_v16.py with a confidential holdout set.
2. Public TLS/CDN and browser E2E large-upload acceptance (Chrome/Edge),
   including tab suspend, internet interruptions and CORS/CSRF.
3. Multiple truly simultaneous 4GiB uploads with block-device hard quotas,
   real reverse proxy timeouts, FFmpeg shared-disk loads, and host metrics.

**No real game shot_attempt Precision/Recall/F1 or basket success accuracy
is verified in V1.9**. Synthetic proxy transport results must not be used
to advertise detection accuracy or production certification.
