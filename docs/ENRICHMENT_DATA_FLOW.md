# Enrichment Data Flow — Privacy Review Artifact

**Status: PENDING PRIVACY/LEGAL REVIEW.** This document describes exactly
what data the post-call enrichment layer sends outside the system, to which
provider, in which mode. It exists so a qualified privacy/legal
professional can review it; it is not legal advice, and `raw` mode must not
be used in the pilot until that review is complete and recorded here.

- Reviewer: ____________  Date: ____________  Verdict: ____________

## What the enrichment layer is

An optional, **shadow-mode** pipeline that processes Birchwood collision
call records AFTER a call is finalized — never during the call. Master
switch `ENRICHMENT_ENABLED` (default **false**: zero new code paths, zero
tokens). The live call flow is deterministic and uses no LLM regardless of
this layer.

## Data flow, step by step

1. A Birchwood call finalizes → a background task is scheduled (in-process
   FastAPI BackgroundTasks for the pilot; a durable queue is the production
   upgrade).
2. The pipeline loads the finalized record + transcript from our database
   (Azure Database for PostgreSQL, Canada Central).
3. **Redaction** (`ENRICHMENT_PII_MODE=redact`, the default): direct
   identifiers are replaced with tokens before any text leaves the system —
   caller name → `[CUSTOMER_NAME]`, phone(s) → `[PHONE_n]`, email →
   `[EMAIL_n]`, address → `[ADDRESS]`, plate → `[PLATE]`, claim reference →
   `[CLAIM_REF]`. Two passes: exact values from the structured record, then
   a regex sweep for phone/email shapes the record didn't know about.
4. The redacted text + non-identifying structured fields are sent to the
   configured LLM provider over HTTPS.
5. Outputs are validated, post-processed deterministically, and stored in
   our own `enrichment_results` table — keyed by session/CallSid, separate
   from the core record. The token→value map is stored ONLY in our
   database; the provider never receives it. Token restoration (e.g.
   rendering a follow-up draft with the real name) happens locally, behind
   the authenticated admin view.

## Exactly what leaves the system

| Mode | Sent to provider | NOT sent |
|---|---|---|
| `redact` (default) | Tokenized transcript + narrative; vehicle year/make/model; damage description; incident date/time and location *as spoken*; drivability; insurance provider name; routing outcome and flags | Caller name, phone numbers, email, mailing address, license plate, claim number, the token map, dashboard data, healthcare anything |
| `raw` (**requires sign-off above**) | Everything in redact mode PLUS the raw transcript and the structured contact/claim fields | Healthcare anything |

**Deliberate scope decisions for the reviewer:**
- Incident locations ("Pembina and Stafford") and vehicle details are NOT
  redacted in `redact` mode — they are the working material of the
  features and are not direct identifiers, but combined with other data
  they could narrow identity. Flag if this requires tokenization too.
- Injury MENTIONS may appear in the narrative text ("my neck hurts").
  These are sensitive; they survive redaction because they are the safety
  signal the QA feature checks. Flag if this requires a stricter mode.

## Provider

- Selected by `LLM_PROVIDER` (default `deepseek` — DeepSeek API,
  api.deepseek.com). The code is provider-agnostic; switching providers is
  a config change plus an adapter.
- Per DeepSeek's API terms, prompts may be processed outside Canada.
  **This is the central question for the privacy review**, and the reason
  `redact` is the default and `raw` is blocked behind this document.
- A startup WARNING is logged whenever `raw` mode is enabled.

## Controls summary

| Control | Value |
|---|---|
| Master switch | `ENRICHMENT_ENABLED=false` by default |
| Per-feature switches | `ENRICH_NORMALIZE/SUMMARY/QA/FOLLOWUP/ROUTING/INSIGHTS` |
| PII mode | `ENRICHMENT_PII_MODE=redact` by default; `raw` logs a startup warning |
| Logs/metrics | Enrichment logs carry session id + feature + error type only — never caller PII |
| Failure behavior | Fail closed: errors stored as failed/needs_review rows; the source record is never modified |
| Insights | Aggregate statistics only are sent for commentary — never call content; below `ENRICH_INSIGHTS_MIN_CALLS` (30) the view reports "insufficient data" |
| Drafts | Follow-up drafts are never auto-sent; `requires_human_approval` is forced true deterministically |
| Verticals | Birchwood (automotive_collision) only; healthcare is excluded at the trigger and untouched |

## Retention

`enrichment_results` rows cascade-delete with their session. Deleting the
table loses only enrichment output — the core pilot record is independent.
