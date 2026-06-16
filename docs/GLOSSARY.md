# Glossary

Shared domain vocabulary for ORCA so terminology stays consistent across verticals.
Terms are grounded in the code; file references point to the source of truth.

### Vertical
A business domain served by the shared engine via config (healthcare, automotive
collision/Birchwood, insurance FNOL, property management). Lives under
`src/verticals/<name>/`. See [ADR 0004](decisions/0004-multi-vertical-platform-pattern.md).

### Workflow
The unit of vertical behavior: a `BaseWorkflow` (or `WorkflowSpec` + `SpecDrivenWorkflow`)
identified by a `workflow_id` (e.g. `birchwood_collision_intake_v1`,
`insurance_claims_fnol_v1`). Registered in `src/platform/workflows/registry.py`, resolved
by `src/platform/workflows/router.py`.

### Orchestrator
The stateless per-turn engine (`src/orchestrator/orchestrator.py`) that runs the intake
loop: pre-check → (protocol retrieval) → LLM → post-check → confidence → decision trace →
next action. All state lives in `OrchestratorSession`.

### Disposition
The outcome of triage. **Canonical set:** `ER_NOW`, `URGENT`, `SCHEDULE`, `SELF_CARE`,
`HUMAN_REVIEW` ([src/orchestrator/schemas.py:23-30](../src/orchestrator/schemas.py#L23-L30)).
Unknown values normalize to `HUMAN_REVIEW`. Verticals may define their own routing
dispositions (e.g. insurance `EMERGENCY_SERVICES_NOW`, `DOCUMENTS_NEEDED`) in their
`constants.py`.

### Red flag
A pattern indicating a potential emergency. **Critical** red flags force `ER_NOW`
immediately; **weighted** red flags accumulate a score (≥ 10 → `URGENT`). Defined in
`src/safety/red_flags.py` (`score_red_flags`) and `src/safety/red_flag_rules.py`
(`RED_FLAG_RULES`). False positives are acceptable; life-threatening false negatives are not.

### Escalation
Routing the caller to a higher tier of care or to a human — the fail-closed/over-escalation
default. Triggered by red flags, low confidence, confused-caller exhaustion, post-check
violations, or any system failure. `escalation_required=True` on the decision/trace.

### Safety gate
The single chokepoint through which all LLM output must pass: `gate_triage_output()`
(dispositions) and `gate_outbound_text()` (any caller/file/DB text), in
`src/safety/gate.py`. "The LLM is never the last word on safety."

### Pre-check / post-check
**Pre-check:** deterministic red-flag scoring *before* any LLM call (no LLM if it fires).
**Post-check:** scanning LLM output *after* the call for diagnoses, unsafe instructions,
role claims, PHI, and disposition downgrades ([src/orchestrator/validators.py:354-391](../src/orchestrator/validators.py#L354-L391)).

### Fail closed
On any error or uncertainty, resolve to the safe outcome (`HUMAN_REVIEW`/escalation with a
safe fallback message) rather than proceeding or reassuring. See
[ADR 0001](decisions/0001-fail-closed-safety-hierarchy.md).

### Confidence score
A 0–1 score computed deterministically from a 1.0 base minus deductions
(`ConfidenceBreakdown`, [src/orchestrator/schemas.py:737-752](../src/orchestrator/schemas.py#L737-L752)).
Below the floor (0.60) escalation is forced and non-emergency dispositions become
`HUMAN_REVIEW`.

### Confused-caller protocol
Handling for unclear/empty answers: a deterministic, non-repetitive retry ladder; the
third unclear answer escalates to a human
([src/orchestrator/orchestrator.py:730-801](../src/orchestrator/orchestrator.py#L730-L801)).

### Decision trace
The per-turn clinical audit log: a list of `DecisionTraceEntry` (user text, extracted
entities, red flags/rules triggered, confidence + breakdown, disposition, escalation,
system response, protocol hits/citations). The `AuditTrace` records every processing step.
Mandatory on every path.

### SBAR
Situation–Background–Assessment–Recommendation — the clinical handoff summary format
produced at healthcare finalization for nurse review (`FinalizeOutput.sbar_report`).

### FNOL (First Notice of Loss)
Insurance term for the first report of a claim/incident. The insurance vertical
(`insurance_claims_fnol_v1`) captures FNOL intake; "collision intake" is the automotive
analogue.

### Protocol
A versioned clinical reference (in `protocols/`) retrieved RAG-lite by
`src/protocols/retriever.py` to give the LLM context. **Supplementary only** — protocols
never override red flags or deterministic rules.

### Restricted-advice boundary
A vertical-specific limit on what the assistant may say (e.g. no coverage/approval
promises in insurance). Enforced deterministically post-turn; the LLM may not emit a
binding promise. See [adding-a-vertical](../.claude/skills/adding-a-vertical/SKILL.md).

### GuardedLLM
The mandatory wrapper around the raw LLM client (`src/llm/guarded_client.py`) — JSON
enforcement, repair, fallbacks, and routing all output through the safety gate. No code
calls a raw client directly.

### PHI (Protected Health Information)
Identifiable health data. Controlled by `STORE_PHI` (default off) + `mask_phi()` at the
gate and PII-off observability. See
[ADR 0003](decisions/0003-llm-provider-abstraction-and-phi-governance.md).

### Extraction
The post-call step that maps a finished session to structured analytics entities via a
vertical's `BaseExtractionAgent` (`src/verticals/<name>/extraction.py`).

### Demo pack
A vertical's `demo/<name>/` bundle: `scenarios.json`, `expected_outputs/*.json` (expected
extraction + dispositions), and `transcripts/*.md`. Verified by `tests/test_*_demo_pack.py`.

### Disposition normalization
Mapping any disposition string (including legacy values) to the canonical enum;
unrecognized → `HUMAN_REVIEW` ([src/safety/gate.py:65-75](../src/safety/gate.py#L65-L75)).
