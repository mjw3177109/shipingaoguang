# BasketCut V1.7 — Browser resumable upload and controlled load gates

V1.7 addresses a production workflow gap: a dropped connection no longer requires
re-uploading a long match from the beginning. V1.2–V1.6 remain otherwise intact.

## Production workflow

The dashboard now creates a private authenticated upload session and sends binary
chunks of at most 8 MiB. Before sending each chunk, the browser computes
SHA-256 with WebCrypto for that chunk ONLY (never buffers the whole 4GiB file).
After each successfully stored chunk, Django commits the confirmed offset to the
DB. If the TCP connection breaks, the browser checks the current confirmed
server offset and resumes from there. The session UUID is the only information
stored in browser localStorage for matching the selected file and venue/title.

Endpoints:
- POST /games/resumable/ with title, filename, byte_size, venue_id and optional
  full-file sha256 (empty for browser clients). Authentication and CSRF required.
- GET /games/resumable/{uuid}/ checks acknowledged offset and expiry.
- POST /games/resumable/{uuid}/chunk/ with application/octet-stream,
  X-Upload-Offset and X-Chunk-SHA256. Out-of-order chunks are rejected HTTP 409.
- POST /games/resumable/{uuid}/finish/ checks full byte size, computed SHA-256,
  FFprobe duration and owner quota; publishes a Game via same-filesystem rename,
  not a redundant 4GiB media copy. Returns final server SHA-256.
- DELETE /games/resumable/{uuid}/ cancels uncompleted session and staging data.

If the browser supplies a full-file SHA-256, the server verifies it at finish.
When omitted, server verifies EVERY chunk digest and returns a final computed
whole-file SHA; this is not equivalent to independent full-source checksum
verification, but detects transport errors for each chunk.

The client keeps no video in persistent browser storage. Each session expires
24 hours after its last committed chunk. Clean old sessions with:

    python manage.py cleanup_resumable
    python manage.py cleanup_resumable --apply

The first invocation previews the count; the latter destroys expired staging
sessions, never completed Game videos. Schedule hourly or daily in operations.

## Isolation and safety

Sessions require authenticated ownership; an unrelated user cannot query
offset, write, cancel or finalize. PostgreSQL SELECT FOR UPDATE serializes
per-session appends and per-account reservations. Every new session reserves
its declared byte_size against the account's logical quota; legacy uploads
also include active incomplete reservations.

The quota is **not physical disk reservation**; simultaneous accounts can
still oversubscribe the filesystem. Run MEDIA_ROOT on a monitored, quota-
limited volume, and limit simultaneous ingress per reverse proxy. Media
ownership and rights continue to be deployment/operator responsibilities.

Resume is compatible with repeated retries, interrupted/chunk-tail recovery,
cancellation and idempotent finish. Database transactions cannot make all
filesystem rename+DB crash windows perfectly atomic: backup, periodic orphan
inspection, disk alarms and staging policies remain necessary for production.

## GitHub evidence

Workflow v17-resumable-acceptance.yml runs:
1. Real HTTP TCP to Gunicorn: generated synthetic decodable MP4 padded to 14MiB,
   confirmed 8MiB chunk, actual socket abort half-way through second chunk,
   reconnect and upload remaining bytes, full-source SHA-256 match, same Game
   id on repeated finish.
2. Django unit tests for one account, cross-tenant 404, bad chunk SHA,
   offset races/replay, DB-versus-file crash reconciliation and expiry.
3. Real PostgreSQL 16, two independent connections simultaneously reserving
   the same owner's quota, ensuring exactly one reservation succeeds.
4. Dashboard JavaScript syntax check and changed-only source patch.

**No 2GiB / 4GiB real file has yet been uploaded** and no 3 authorized
full-match recordings have been assessed. Successful 14MiB TCP tests cannot
demonstrate 4GiB throughput, CDN/Nginx request limits, browser tab eviction,
disk reservation, mobile radio reconnect, true browser E2E, or event recall.
A private separate multi-GB test is the next operational requirement. Source
footage MUST NOT be uploaded as public CI artifacts.

## Deployment

    python manage.py migrate --noinput
    python manage.py check
    python manage.py cleanup_resumable

Nginx/Caddy should permit the 8MiB chunk size with appropriate timeouts,
Content-Length checks and CSRF protection. Retain V1.6 reverse proxy guards
and V1.5 HTTP/Worker recovery acceptance suites.
