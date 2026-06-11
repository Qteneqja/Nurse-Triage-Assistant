# Changelog

## v5.1.0 — Birchwood Pilot Lock (2026-06-11)

The pilot-readiness sprint: five PRs taking ORCA from demo to a
pilot-ready Birchwood Collision Intake deployment.

### PR 0 — Security close-out (#31, #32)
- Gitleaks blocking in CI + all-branch/full-history secret scanning.
- Git history rewrite **executed**: `test_secret.txt` and the rotated
  DeepSeek key purged from all branches; verified clean from a fresh clone.
- `.env.example` completed and guarded by a sync test.

### PR 1 — Runtime stability gate (#34)
- Multi-segment narrative capture: long collision stories are never cut
  off ("go on, I'm listening").
- Idempotent webhooks (duplicate /incoming and /gather), consecutive-
  silence fail-safe close, vertical-aware fail-closed errors (apology +
  callback promise + flagged record).
- Deterministic injury safety branch for non-clinical verticals
  (Invariant 3): 9-1-1 advisory once per call, record flagged, human
  review forced — on every routing outcome.
- Vertical golden calls (10 Birchwood + 3 insurance); red-flag scoring
  proven length-invariant.

### PR 2 — Birchwood conversation experience (#35)
- Narrative-first intake: deterministic extraction prefills fields from
  the story; targeted gap-fill of required fields only (a rich story needs
  ~4 follow-ups); direct injury question when unresolved.
- Dynamic readback confirmation with a correction path; "here's what
  happens next" close; caller plain summary + shop-facing summary on
  every record.
- Offline conversation simulator (`scripts/simulate_birchwood_call.py`).

### PR 3 — Workflow engine (#36)
- Declarative `WorkflowSpec` + `SpecDrivenWorkflow`: a new workflow is one
  JSON definition file + config (`WORKFLOW_PHONE_ROUTES`,
  `EXTRA_WORKFLOW_DEFINITIONS_DIR`).
- Safety hard-wired beneath the workflow layer: the engine forces the
  injury branch on every non-clinical result; healthcare ids/vertical are
  reserved and unclaimable by specs.
- Birchwood runs as the first complete spec-defined workflow,
  byte-identical behavior.

### PR 4 — Dashboard MVP (#37, #38, #39)
- Intake records list (injury/urgent pinned, filterable) + detail view
  with shop summary, narrative, transcript, and collision data.
- Status workflow new → contacted → scheduled → completed (+ escalated
  derived for flagged records) with an immutable audit trail
  (`record_status_events`, Alembic 004).
- Browser sign-in page (CSP-compliant) + shell cookie; contact fields
  shown on non-healthcare records behind auth (maskable); healthcare
  always fully masked.

### PR 5 — Pilot lock (this release)
- Staging posture verified (postgres, signature validation, auth, rate
  limiting); rollback procedure for Azure Container Apps.
- Final 35-call validation pack (Birchwood-weighted) + results template.
- Pilot document suite: runbook, escalation workflow, limitations,
  success metrics (computable via `scripts/pilot_metrics.py`), failure-
  mode response plan, client one-pager, pricing assumptions.

Healthcare safety suites (Phase 1 + red flags + evals) green and the
healthcare flow diff-clean across the entire sprint. Test suite:
756 → 865.
