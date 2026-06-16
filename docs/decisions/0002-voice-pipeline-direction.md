# ADR 0002 — Voice pipeline direction: Twilio ConversationRelay over gather/TwiML

- **Status:** Proposed (current implementation is gather/TwiML; ConversationRelay is the
  planned target, not yet built)
- **Date:** 2026-06-16
- **Related:** [.claude/skills/voice-pipeline-operations/SKILL.md](../../.claude/skills/voice-pipeline-operations/SKILL.md)

## Context

The voice layer today is request/response Twilio `<Gather>` (TwiML) with Twilio STT and
Azure TTS ([src/twilio/routes.py](../../src/twilio/routes.py),
[src/utils/azure_tts.py](../../src/utils/azure_tts.py)). Each turn is a full HTTP
round-trip; while the LLM thinks, a `/voice/thinking` poll loop plays typing sounds to
avoid dead air, and finalization is deferred to a background task to stay under Twilio's
~15s webhook timeout ([orchestrator.py:1347-1364](../../src/orchestrator/orchestrator.py#L1347-L1364)).

This works and is safe, but the architecture has inherent limits: noticeable per-turn
latency, no barge-in (caller can't interrupt), STT locked to Twilio's engine, and brittle
timeout management. There is currently **no streaming pipeline and no `ConversationRelay`
reference anywhere in the code**; Twilio voice is mocked in tests
(`docs/phase4/known_limitations.md`).

## Decision

Adopt **Twilio ConversationRelay** (streaming, bidirectional media with pluggable
STT/TTS) as the target voice architecture, migrating off gather/TwiML — **behind a
feature flag**, with healthcare-grade safety parity required before any cutover.

Direction:
- Introduce a pipeline-selection flag (e.g. `ENABLE_CONVERSATION_RELAY`) and branch TwiML
  generation in the incoming handler — there is no such flag today.
- Keep the orchestrator/safety contract unchanged: the voice layer still only transports
  text in and speech out; **all triage decisions stay behind the safety gate** (ADR 0001).
- Roll out non-clinical verticals (e.g. Birchwood) first; gate healthcare on full
  golden-call parity.

## Consequences

- **+** Lower latency, barge-in, provider-flexible STT/TTS, fewer timeout hacks.
- **−** WebSocket/streaming infra, new failure modes (partial transcripts, disconnects),
  and a second voice code path to maintain during migration.
- **Rollback:** because pipeline choice isn't stored per session and TwiML is generated
  per webhook, flipping the flag off routes all new calls back to gather/TwiML; in-flight
  calls finish on their current path. Keep gather/TwiML fully working until parity is
  proven.

## Alternatives rejected

- *Stay on gather/TwiML indefinitely:* rejected — latency/UX ceiling and STT lock-in.
- *Third-party voice-agent platform:* rejected for now — would move triage decisioning
  outside our audited safety gate, violating ADR 0001.
