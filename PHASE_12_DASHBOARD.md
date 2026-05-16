# Phase 12 Dashboard/Admin Shell

## What Was Added

- Typed admin API routes under `/admin`.
- A static dashboard at `/dashboard`.
- Organization, workflow, session, turn, audit metadata, and proposed-action read models.
- Deterministic placeholder post-call actions for finalized sessions.
- Healthcare session detail fields for intake completeness, finalization block reason, rules, red flags, confidence, escalation, and SBAR availability.
- Property-management session detail fields for work-order output, property address, unit, issue type, disposition, and required-field completeness.

## Intentionally Not Included

- No OpenClaw integration.
- No external action execution.
- No changes to healthcare disposition logic.
- No changes to clinical finalization or safety gates.
- No persistent action table yet; action statuses are process-local placeholders.

## OpenClaw Later

OpenClaw should plug in after workflow finalization as a sandboxed proposed-action layer. It may propose draft actions such as creating a ticket or notifying a queue, but it must not write back into healthcare disposition, SBAR, red-flag decisions, confidence, or finalization state.

## Why Approval Only

Healthcare remains safety-critical. Automation can help operators package follow-up work, but the clinical decision engine must stay deterministic, auditable, and protected from downstream automation. Human approval keeps proposed actions separate from clinical decisions and prevents accidental external side effects.

## Run Locally

```powershell
python run.py
```

If local Postgres is not running, use the memory backend for dashboard
development:

```powershell
$env:STORAGE_BACKEND="memory"
python run.py
```

Then open:

```text
http://localhost:8000/dashboard
```

In `APP_ENV=development` and `APP_ENV=test`, the dashboard API is available without a token. In staging and production, set `DASHBOARD_ADMIN_TOKEN` and send it as `X-Dashboard-Token` or `Authorization: Bearer <token>`.
