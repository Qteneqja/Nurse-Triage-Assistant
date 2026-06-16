# Architecture Decision Records (ADRs)

Short, durable records of the *why* behind ORCA's key architectural choices. Each ADR is
immutable once accepted; to change a decision, add a new ADR that supersedes it.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-fail-closed-safety-hierarchy.md) | Fail-closed safety hierarchy and over-escalation default | Accepted |
| [0002](0002-voice-pipeline-direction.md) | Voice pipeline direction: Twilio ConversationRelay over gather/TwiML | Proposed |
| [0003](0003-llm-provider-abstraction-and-phi-governance.md) | LLM provider abstraction + DeepSeek/PHI governance and BAA seam | Accepted (+ proposed seam) |
| [0004](0004-multi-vertical-platform-pattern.md) | Multi-vertical platform pattern: one engine, config-per-vertical | Accepted |

Format: Context → Decision → Consequences → Alternatives rejected. Keep them short.

> Note: there is no `docs/design_notes.md` in this repo. Related design material lives in
> [docs/WORKFLOW_ENGINE.md](../WORKFLOW_ENGINE.md), [docs/ENRICHMENT_DATA_FLOW.md](../ENRICHMENT_DATA_FLOW.md),
> the `docs/pilot/` set, and the safety skill under
> [.claude/skills/](../../.claude/skills/).
