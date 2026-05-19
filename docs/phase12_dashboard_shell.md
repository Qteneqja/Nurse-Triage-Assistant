# Phase 12 Dashboard Shell

## Purpose

Phase 12 adds a basic multi-vertical dashboard/admin shell for the Voice
Decision Support Platform. It is intentionally lightweight: typed admin API
endpoints plus a FastAPI-served static dashboard. It supports healthcare,
property management, future verticals, and a placeholder proposed-action
approval area.

## Dashboard Pages

- `/dashboard` - overview summary and recent calls.
- `/dashboard/sessions` - recent sessions table with organization, vertical,
  workflow, decision,
  and status filters.
- `/dashboard/sessions/{session_id}` - session detail with workflow result,
  extraction, transcript/turns, rules, safety, and audit metadata.
- `/dashboard/actions` - placeholder proposed-actions page for internal
  approval workflow.

## API Endpoints

- `GET /admin/summary`
- `GET /admin/organizations`
- `GET /admin/workflows`
- `GET /admin/sessions`
- `GET /admin/sessions/{session_id}`
- `GET /admin/sessions/{session_id}/turns`
- `GET /admin/sessions/{session_id}/actions`
- `POST /admin/sessions/{session_id}/actions/{action_id}/approve`
- `POST /admin/sessions/{session_id}/actions/{action_id}/reject`
- `POST /admin/sessions/{session_id}/actions/{action_id}/complete`

The action mutation routes change only internal placeholder status. They do not
execute external actions.

## Running Locally

Start the FastAPI app as usual:

```powershell
python run.py
```

If local Postgres is not running, use the memory backend:

```powershell
$env:STORAGE_BACKEND="memory"
python run.py
```

Then open:

```text
http://localhost:8000/dashboard
```

In `APP_ENV=development` or `APP_ENV=test`, dashboard API calls and the local
HTML shell do not require an admin token. In staging and production, set
`DASHBOARD_ADMIN_TOKEN` and send it as `X-Dashboard-Token` or
`Authorization: Bearer <token>`. The production shell route and API routes use
the same token gate. If no approved admin access path exists, set
`DASHBOARD_ENABLED=false`.

## Privacy And Masking

Dashboard responses mask sensitive display values by default:

- Phone numbers are returned as `***-***-1234`.
- Caller and patient names are reduced to initials.
- DOB-like fields are redacted.
- Transcript text is passed through the existing PHI masking utility.
- Raw extraction model output is omitted.

This shell is meant for operational review without casually exposing PHI.

## Current Limitations

- The dashboard is a static vanilla JavaScript shell, not a full React app.
- Actions are deterministic placeholders only.
- There is no persistent proposed-action table yet; status is process-local.
- Auth is token-based and intentionally minimal; production should move toward
  an Entra-authenticated front door or private admin surface.
- Summary counts are computed from recent sessions, not optimized aggregate
  tables.

## Next Phase

The next phase should evaluate OpenClaw locally, define a sandbox action
adapter, add persistent action/audit tables, and wire human approval workflows
without changing clinical or workflow dispositions.
