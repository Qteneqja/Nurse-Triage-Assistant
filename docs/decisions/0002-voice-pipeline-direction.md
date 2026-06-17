# ADR 0002 — Voice pipeline direction: Twilio ConversationRelay over gather/TwiML

- **Status:** Accepted — implemented behind the `VOICE_PIPELINE` flag (default stays
  `gather` until the latency gate passes on staging). Updated 2026-06-16.
- **Date:** 2026-06-16
- **Related:** [.claude/skills/voice-pipeline-operations/SKILL.md](../../.claude/skills/voice-pipeline-operations/SKILL.md)

## Implementation status (2026-06-16)

Built on branch `pr-voice-conversation-relay` ([src/twilio/conversation_relay.py](../../src/twilio/conversation_relay.py)),
additively, with **zero changes to `src/orchestrator/` or `src/safety/`** (a Step-0 audit
confirmed those layers are transport-agnostic):

- **Flag:** `VOICE_PIPELINE=gather|conversation_relay` (default `gather`). `/incoming`
  returns `<Connect><ConversationRelay>` under the flag, falling back to gather if no wss
  URL can be derived. Rollback = flip back to `gather`.
- **WS endpoint** `/api/v1/voice/relay` re-hosts the transport glue (scripted intake,
  narrative capture, injury advisory, finalize) by REUSING the gather path's
  transport-neutral helpers and calling the SAME workflow-engine entry point. The
  orchestrator stays turn-batched — token streaming is a transport concern only; it does
  NOT become a generator (that would bypass the safety gate).
- **Provider seam** `VOICE_OUTPUT_MODE=azure_play|cr_native`. CR's native TTS is
  ElevenLabs/Google/Amazon (STT Deepgram) — **Azure is not a CR TTS provider**, so
  `azure_play` (default) keeps the exact current voice via a CR `play` MP3 URL (no
  regression, no streaming TTS); `cr_native` streams text tokens for CR TTS (changes the
  voice, gains streaming + barge-in).
- **Nurse warm-transfer:** CR `end` carries a dial/hangup intent; the signature-validated
  `/api/v1/voice/relay-action` `<Connect action>` callback returns `<Dial>` or `<Hangup>`.
- **Deferred:** shared-number vertical menu (non-pilot; gather still serves it).
- **Latency gate (Step 4) — PENDING staging.** The true metric (caller-stops → audio
  starts) requires a live staged call with real Twilio CR + Deepgram + TTS and is NOT
  measurable offline; offline the app-side turn cost is identical for both paths (same
  orchestrator). See [scripts/measure_voice_latency.py](../../scripts/measure_voice_latency.py)
  and the voice-pipeline-operations skill. Do not flip the default to `conversation_relay`
  until p50/p95 on staging show CR is no worse than gather.

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

Direction (now realized — see Implementation status above):
- A pipeline-selection flag (`VOICE_PIPELINE`) branches the incoming handler between
  gather and ConversationRelay.
- Keep the orchestrator/safety contract unchanged: the voice layer still only transports
  text in and speech out; **all triage decisions stay behind the safety gate** (ADR 0001).
- Roll out non-clinical verticals (e.g. Birchwood) first; gate healthcare on full
  golden-call parity, and gate the default flip on the staging latency measurement.

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
