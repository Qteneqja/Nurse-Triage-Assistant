# Insurance FNOL Vertical Foundation

Phase 13 adds the first insurance workflow foundation:

- Vertical: `insurance`
- Workflow ID: `insurance_claims_fnol_v1`
- Workflow version: `v1`
- Output type: `CLAIM_INTAKE`
- Placeholder phone route key: `INSURANCE_FNOL_PHONE_NUMBER`
- Placeholder phone number: `+15555550130`

This is a deterministic first notice of loss (FNOL) claims intake workflow. It is
not a coverage engine, payment engine, legal advice workflow, or adjuster
replacement.

## What It Collects

The scripted intake gathers:

- caller name
- callback number
- policy number
- claim type
- loss date/time
- loss location
- incident summary

Supported claim type values:

- `auto accident`
- `property damage`
- `water damage`
- `theft/loss`
- `glass/window damage`
- `liability/business claim`
- `other`

## Deterministic Routing Outcomes

The workflow can route to:

- `EMERGENCY_SERVICES_NOW`
- `URGENT_ADJUSTER_REVIEW`
- `STANDARD_CLAIM_INTAKE`
- `DOCUMENTS_NEEDED`
- `INFORMATION_ONLY`
- `HUMAN_REVIEW`

The routing rules are deterministic and offline. They inspect the scripted FNOL
fields plus the caller's dynamic follow-up answer. They do not call DeepSeek,
OpenAI, Twilio, or any external service.

## Safety And Compliance Boundaries

The insurance workflow must not promise:

- coverage
- approval
- payout
- legal advice

The assistant can collect facts and route the intake record. A licensed broker,
adjuster, or insurer representative must confirm coverage and next steps.

This insurance vertical does not alter healthcare behavior. Healthcare red flags,
SBAR logic, completeness gates, finalization rules, and clinical dispositions
remain unchanged.

## Phone Route Placeholder

For local and development routing, `.env.example` includes:

```env
INSURANCE_FNOL_PHONE_NUMBER=+15555550130
```

When a call is made to that number and no database route is available, the route
resolver maps it to:

```text
vertical=insurance
workflow_id=insurance_claims_fnol_v1
workflow_version=v1
```

Production should use seeded `phone_numbers` routing rows instead of relying on
this placeholder.

## Structured Output

The workflow final result contains:

- `structured_output.output_type`
- `structured_output.claim_record`
- `structured_output.intake`
- `structured_output.disposition_taxonomy`

The claim record includes dashboard-ready fields such as:

- claim type
- policy number
- loss date/time
- loss location
- incident summary
- emergency or safety issue
- injuries mentioned
- emergency services involved
- police or fire report
- property secure
- mitigation needed
- documents available
- missing information
- recommended routing
- confidence
- human review required
- disclaimers given

## Post-Call Extraction

`InsuranceClaimsExtractionAgent` is a read-only deterministic extractor for
analytics and dashboard use. It does not mutate final disposition. The extraction
schema version is:

```text
insurance_claims_fnol_extraction_v1
```

## Demo Pack

Phase 13.1 adds a repeatable insurance FNOL demo pack in:

```text
demo/insurance_fnol/
```

It includes:

- `scenarios.json` - seven fake scripted FNOL scenarios for auto, property
  danger, water mitigation, theft/loss, glass/window damage, information-only,
  and missing-information demos.
- `expected_outputs/` - one expected extracted JSON example per scenario,
  shaped to the implemented `InsuranceClaimRecord` schema and extraction entity
  list.
- `transcripts/` - readable Markdown demo transcripts for common sales-call
  walk-throughs.
- `BROKER_DEMO_SCRIPT.md` - broker/agency demo guidance with positioning,
  setup, talk track, and pilot success metrics.
- `INSURANCE_FNOL_ONE_PAGER.md` - concise client-facing overview.

Run the offline demo reader without API keys:

```bash
python scripts/run_insurance_demo.py
python scripts/run_insurance_demo.py --scenario water_damage_mitigation
python scripts/run_insurance_demo.py --all
```

The runner is intentionally static and deterministic. It reads the demo files,
prints expected routing, extracted fields, dashboard summary, and disclaimer
checks. It does not call Twilio, DeepSeek, OpenAI, or any external service.

Use the broker demo script when preparing a sales or stakeholder call. Use the
one-pager as a short client-facing leave-behind. The expected outputs are useful
for showing how dashboard or CRM data could look after the call.

To replace the local placeholder phone number later, keep
`INSURANCE_FNOL_PHONE_NUMBER=+15555550130` for local/demo routing and configure
production numbers through seeded `phone_numbers` routing rows or
organization-specific deployment configuration.

## Evals

Insurance FNOL deterministic evals live in:

```text
tests/evals/test_insurance_fnol_eval.py
```

They exercise:

- active fire routing to emergency services
- standard property damage routing
- missing theft/loss information routing
- information-only caller routing

Run:

```bash
python -m pytest tests/evals
deepeval test run tests/evals
```

Healthcare eval CI remains blocking. Insurance evals are deterministic and live
in the same offline eval tree so they do not require external API keys.
