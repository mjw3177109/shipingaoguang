# BasketCut V1.8 — actual 2 GiB TCP transport and cross-tenant capacity admission

## What changed

V1.8 retains V1.7 web UI, authentication and resumable chunk transfer. No image
recognition, Worker, video codec or manual approval modes are switched.

**Logical admission ledger:** the singleton database row
\`MediaCapacityLock(pk=1)\` serializes PostgreSQL media admission across *all*
accounts. New uploads require:

    observed MEDIA_ROOT free >= MEDIA_MIN_FREE_BYTES
                             + remaining uncommitted bytes of ALL active sessions
                             + bytes of requested new upload

Legacy single-request upload now uses the same shared lock and capacity guard.
A successful chunk reduces pending uncommitted bytes while consuming disk space.
Cancellation and cleanup release pending capacity. This stops the race where
two users independently pass free-disk checks for overlapping available space.

**This is NOT a real filesystem reservation, disk quota, or guaranteed
multi-container capacity limit.** Unrelated processes, FFmpeg outputs, /tmp,
manual writes, crash leftovers, other mounts and external filesystem users can
consume bytes without using the ledger. Run MEDIA_ROOT on managed monitored
storage and configure actual per-volume quotas and ingress concurrency limits.
SQLite concurrency is not equivalent to PostgreSQL row locks.

## Real 2 GiB CI

\`.github/workflows/v18-gib-capacity.yml\` creates a SHORT 20-second synthetic
decodable MP4 and extends it with a *sparse zero-filled trailer* to exactly
**2,147,483,648 bytes (2 GiB)**. The source may occupy little physical disk
due to sparsity, but the HTTP test reads, sends, acknowledges and writes **all
2 GiB** across real localhost TCP sockets in bounded 8 MiB chunks.

The script:
- launches actual Gunicorn / Django against disposable SQLite;
- streams the source through 256 HTTP chunk requests;
- deliberately aborts the second chunk after sending a partial body and
  verifies the server kept the previous acknowledged offset;
- resumes and confirms all 2 GiB bytes, computes independent client-side
  full-source SHA-256 and checks server-computed full hash;
- hashes the physically written Game media file *again*, checks size and
  idempotent finalization;
- samples Gunicorn/child RSS and media volume free bytes for a best-effort
  stress report, **without** uploading the 2 GiB recording to GitHub.

This is an actual **GiB-scale byte-transport** test, but **NOT** proof that
a 2 GiB genuine full-match recording decodes correctly, works in public
browser/TLS/Nginx/CDN settings, meets GPU processing speed targets, or achieves
shot detection accuracy. The video header represents only a short test pattern.

The CI verifies **2 GiB**, not **4 GiB**. The harness accepts up to 4 GiB for
a separate private environment, subject to adequate disk space and quota.
Do not run it against production data or share customer matches in public CI.

## PostgreSQL cross-tenant check

\`highlights.test_v18_postgres\` spawns separate database connections for
two different owners, both requesting 600 bytes with a mock free-volume budget
of 1000 bytes and a 100 byte reserve. Exactly **one** request may succeed.
The mock determines simulated free capacity; the row-lock behavior is actual
PostgreSQL 16. Existing V1.7 per-account admission is independently tested.

## Operational steps

1. Back up your DB and MEDIA_ROOT before a deployment.
2. Run \`python manage.py migrate --noinput\`; migration 0014 creates
   and seeds \`MediaCapacityLock(pk=1)\`. Missing singleton fails closed.
3. Preserve \`MEDIA_MIN_FREE_BYTES\` (recommended nonzero), ingress user limits
   and periodic \`python manage.py cleanup_resumable --apply\`.
4. Examine generated JSON:
   \`v18_gib_http_audit.json\` and \`pg_report.json\`.
5. Test genuine **authorized**, continuous multi-hour source content on a
   separate private staging environment using the V1.6 real-match admission
   gate and independent human annotations.

No 3 independently reviewed 60–120 minute basketball matches are available in
this CI, so **real shot-attempt recall / made-basket accuracy remains unmeasured**.

## Remaining production blockers

- Actual HTTP 4 GiB in private staging (the harness supports 4 GiB but not CI).
- Public TLS proxy, CDN, browser tab suspend and throttle/packet-loss testing.
- Physical host or PVC quota fencing when FFmpeg and other apps share disks.
- Representative real-match ground truth and independent rights verification.
