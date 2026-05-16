# OpenClaw Dashboard Readiness

Phase 12 prepares the dashboard for future OpenClaw proposed actions without
integrating OpenClaw.

## What Exists Now

- `/dashboard/actions` exists as the future approval area.
- `GET /admin/sessions/{session_id}/actions` returns deterministic placeholder
  actions after workflow finalization.
- `POST /admin/sessions/{session_id}/actions/{action_id}/approve`,
  `/reject`, and `/complete` update only internal placeholder status.
- The API schema includes fields expected by a future action approval flow:
  action id, session id, organization id, vertical, workflow, action type,
  title, description, payload, status, source, and timestamps.
- No placeholder action executes external work.

## Future Integration Rules

- OpenClaw must not change clinical dispositions.
- OpenClaw must not change property urgency classifications.
- OpenClaw should propose post-call actions only after workflow finalization
  and extraction.
- Human approval is required before any action executes.
- Every proposal, approval, rejection, edit, completion, and failure must be
  audit logged.

## Proposed Statuses

- `proposed`
- `approved`
- `rejected`
- `completed`

## Expected Future Work

- Add a persistent proposed-actions table.
- Add a sandbox adapter before connecting any real external system.
- Add organization-level policy controls.
- Add audit reports for action decisions and execution outcomes.
