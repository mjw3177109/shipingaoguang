# BasketCut V1.6 — Nginx reverse proxy upload and full-match evidence gate

## This release DOES NOT mean full commercial acceptance

V1.6 adds two **new** independent validation capabilities without rewriting
the V1.2–V1.5 storage, CPU suggestion, approval and FFmpeg export pipelines.

### 1. Real Nginx transport smoke

The GitHub workflow installs **actual Nginx**, starts Gunicorn and Django on
localhost and sends a **40 MiB HTTP multipart body** through Nginx to Django.

- A playable 25-second **synthetic** MP4 core receives explicit trailing
  padding to 40 MiB. This is an upload-byte-volume fixture, NOT a 40 MiB
  authentic basketball recording.
- Verify upload redirects, stored size and **source-to-storage SHA-256**.
- Verify independent Django 413 and Nginx 413 on oversized Content-Length,
  before either request persists a Game.
- Configure \`proxy_request_buffering off\` in this smoke and sample both
  Django upload temp and Nginx request-body temp directories at 50 ms intervals.
- Record real elapsed time, sampled temp peaks and transport flags as
  \`v16_proxy_report.json\`.

A reference reverse proxy snippet is at \`deploy/nginx/basketcut_upload.example.conf\`.
It is NOT automatically installed. It assumes a separate HTTPS terminator and
must be reviewed for TLS, host allowlists, CSRF, timeouts, ingress policy,
DoS controls, and disk capacity. **Disabling Nginx buffering does NOT stop
Django's upload handler from writing its own temporary media file.**

The CI upload is **40 MiB**; neither a 2 GiB nor a 4 GiB upload, low-bandwidth
network, public TLS, resumable upload, or simultaneous multiple large users
have been tested. For a manual 2 GiB/4 GiB trial, use a private isolated
staging environment, adjust MAX_UPLOAD_BYTES and the proxy size limit, and
invoke the probe with a real approved media file. Do not upload user videos
into public GitHub Actions, nor turn DEBUG on in production.

### 2. Independent full-match admission before accuracy claims

Use \`python -m highlights.vision.real_match_gate_v16 --manifest
/path/private/matches.json --out /path/private/report.json --strict\` only on
a machine holding media that the user has the necessary processing rights for.

Manifest structure (one entry per match, at least three separate entries):

\`\`\`json
{
  "schema_version": 1,
  "games": [
    {
      "id": "court_A_match_01",
      "video_file": "court_A_match_01.mp4",
      "video_sha256": "<real SHA-256>",
      "truth_file": "court_A_match_01_truth.json",
      "proposals_file": "court_A_match_01_proposals.json",
      "synthetic": false,
      "looped": false,
      "continuous_unedited_full_match": true,
      "rights_to_process_and_store": true,
      "license_evidence_reference": "private-operator-consent-record",
      "independent_of_training_and_tuning": true,
      "human_reviewers": [
        {"reviewer_id": "reviewer_A", "verified_complete_shot_attempt_labels": true, "review_evidence_reference": "private-human-review-1"},
        {"reviewer_id": "reviewer_B", "verified_complete_shot_attempt_labels": true, "review_evidence_reference": "private-human-review-2"}
      ]
    }
  ]
}
\`\`\`

The actual manifest must have **at least three distinct 60–120 minute
matches**, >/=20 labeled shot attempts per match, independent reviewers,
source file hashes, rights attestations, full CPU scan and matching
\`game_id\` in truth/proposals. \`truth_file\` and \`proposals_file\` use
the already supported \`highlights.vision.evaluate\` JSON schemas.

- Looped/synthetic, incomplete, unverifiable, duplicate, truncated,
  unconsented or missing-evidence recordings are **BLOCKED**.
- For eligible source evidence, output is only
  \`ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW\`, NOT production-certified.
- Metrics measure **shot_attempt retrieval** rather than made-basket
  classification; no actual made-shot precision is asserted.
- Rights, consent, reviewer independence and continuous footage are
  operator **attestations**, not independently verified by the script.
- Final real-match accuracy remains **not measured** until real external
  files and human-approved labels are provided and tested on a sealed
  holdout set.

CI intentionally runs a **BLOCKED** short repeated-source manifest and unit
checks. Green CI means the admission gate rejects insufficient evidence;
it is not a substitute for supplying real basketball game footage.

### Further production work

Next stage should validate real end-to-end authorized matches, 2–4 GiB
ingress over TLS, multiple concurrent clients, upload interruption/restart,
disk-reservation race, and gunicorn slow-upload behavior. Always keep
actual match media in restricted private test storage, never public logs.
