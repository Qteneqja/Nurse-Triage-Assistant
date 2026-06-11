# Dashboard Walkthrough — Intake Records (PR 4)

The **intake record** is what Birchwood works from: every completed call
becomes a reviewable record with the collision data, the caller narrative,
a shop-facing summary, a recommended action, and a status the shop advances
through its day: **new → contacted → scheduled → completed** (plus
**escalated**, the automatic default for injury-flagged or urgent records).

## Run it (Docker compose)

```bash
docker compose up -d postgres
docker compose --profile tools run --rm migrate   # alembic upgrade head (incl. 004_record_status_events)
docker compose up -d api
docker compose exec api python -m scripts.seed_dashboard_demo
```

Open <http://localhost:8000/dashboard/records>.
Local dev without Docker: `python -m scripts.seed_dashboard_demo` then
`uvicorn src.main:app` and open the same path (memory backend).

> **Screenshots:** capture after seeding — (1) the records list showing the
> red injury-flagged row pinned at top, (2) a record detail with the injury
> banner + status buttons, (3) the status history panel after clicking
> "contacted". Place them in `docs/img/dashboard/` and link here.
> *(Operator step — requires a browser.)*

## The records list — `/dashboard/records`

- **Injury-flagged and urgent records pin to the top**, highlighted red,
  newest first within each band. An `INJURY` badge means the caller
  mentioned injuries; the system already advised 9-1-1 on the call and
  forced human review.
- Filters: status, vertical, date range, "Injury only", "Urgent only".
- Each row: time, vehicle, customer + callback number, disposition,
  recommended action.

API: `GET /api/v1/dashboard/records?record_status=new&injury_flagged=true&date_from=2026-06-01`

## Record detail — `/dashboard/records/{session_id}`

- Injury/urgent banner, key facts (customer, callback, disposition,
  recommended action, missing info, flags).
- **Status buttons** — the dashboard's ONE write operation. Enter your name
  first; every change is audit-logged (status, actor, timestamp, optional
  note) and shown in the *Status history* panel. Statuses:
  `new`, `contacted`, `scheduled`, `completed`, `escalated`.
- Shop summary (SITUATION / VEHICLE / CUSTOMER / RECOMMENDED ACTION), the
  caller's narrative, full transcript, and the complete intake record JSON.

API:
```bash
curl -s -X POST http://localhost:8000/api/v1/dashboard/records/<id>/status \
  -H "Content-Type: application/json" \
  -H "X-Dashboard-Token: $DASHBOARD_ADMIN_TOKEN" \
  -d '{"status": "contacted", "actor": "front-desk", "note": "left voicemail"}'
```

## Auth, privacy, limits (pilot-grade — documented as such)

- **Auth:** single shared token (`DASHBOARD_ADMIN_TOKEN`, env); enforced in
  staging/production, open in local dev. **Browsers sign in at
  `/dashboard/login`** (unauthenticated static page — validates the token
  against the API, then sets a `SameSite=Strict` cookie for page loads and
  stores the token for the JS data calls; hitting any dashboard page
  without the cookie redirects there). Tools/curl use the
  `X-Dashboard-Token` header or `Bearer` auth — data endpoints accept
  headers only, never the cookie. No per-user accounts — the `actor` field
  on status changes is the accountability mechanism for the pilot.
- **Rate limiting:** all dashboard routes sit behind the global
  sliding-window limiter (`RATE_LIMIT`, default 60/minute).
- **No PII in URLs:** records are addressed by opaque session ids only.
- **PII display policy:** free text (narratives, transcripts) is always
  masked for stray digits/names. Structured **contact fields on
  non-healthcare records are shown** behind the auth gate — the shop must
  be able to call the customer back; set
  `DASHBOARD_RECORDS_SHOW_CONTACT=false` to mask them too. **Healthcare
  records are always fully masked** and render through the same generic
  view (no special handling, nothing breaks).
- When text storage is off (`STORE_PHI=false`), transcripts are stored
  pre-masked — the dashboard degrades gracefully to the masked text.

## Where the fields come from

The columns and detail fields follow each workflow's
`dashboard_display_fields` from its WorkflowSpec
([docs/WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md)) — Birchwood's spec defines
the collision set; a new spec-defined workflow gets a working records view
with zero dashboard changes.
