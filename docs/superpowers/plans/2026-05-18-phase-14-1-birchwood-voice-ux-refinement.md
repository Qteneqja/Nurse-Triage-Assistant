# Phase 14.1 Birchwood Voice UX Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine Birchwood Collision voice prompts, narrative capture timing, and workflow-specific Azure TTS behavior without changing healthcare, insurance, or deterministic routing outcomes.

**Architecture:** Keep the deterministic Birchwood workflow intact and layer voice UX refinement at the scripted-stage and Twilio/TTS adapter boundaries. Add Birchwood-specific speech and TTS profile resolution so only the automotive collision workflow gets the longer narrative capture and warmer synthesized voice while all other workflows continue using existing defaults.

**Tech Stack:** FastAPI, Twilio voice webhooks/TwiML, Pydantic workflow schemas, Azure Speech TTS, pytest, deepeval, Ruff.

---

### Task 1: Lock the desired Birchwood voice profile in tests

**Files:**
- Modify: `tests/test_automotive_collision_workflow.py`
- Create: `tests/test_birchwood_voice_ux.py`

- [ ] Add failing tests that assert Birchwood scripted stages mark narrative questions with longer timeout/speech settings while non-narrative stages retain the short defaults.
- [ ] Add failing tests that assert Birchwood prompts use warmer dealership-specific wording and avoid clinical language.
- [ ] Add failing tests around Twilio/Azure profile selection so Birchwood routes request the Birchwood voice profile while default workflows continue using the existing global profile.

### Task 2: Implement Birchwood speech profile resolution

**Files:**
- Modify: `src/verticals/automotive_collision/workflow.py`
- Modify: `src/platform/workflows/schemas.py`
- Modify: `src/twilio/routes.py`

- [ ] Extend scripted stage metadata so a stage can declare a voice interaction profile without affecting existing workflows.
- [ ] Update the Birchwood workflow stages so the narrative questions opt into a Birchwood narrative profile and the remaining questions keep their current short-turn behavior.
- [ ] Persist the applied speech profile in Twilio session metadata/audit metadata when a Birchwood scripted stage is prompted.

### Task 3: Implement workflow-specific Azure TTS options with fallback

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`
- Modify: `src/utils/azure_tts.py`
- Modify: `src/twilio/routes.py`

- [ ] Add Birchwood-specific TTS config values for voice, rate, pitch, style, and break length.
- [ ] Refactor Azure TTS SSML generation so callers can request a specific voice profile while the default behavior remains unchanged.
- [ ] Gracefully fall back to the global/default Azure voice settings when Birchwood-specific style or voice synthesis fails.

### Task 4: Refresh Birchwood prompts and spoken messaging

**Files:**
- Modify: `src/verticals/automotive_collision/prompts.py`
- Modify: `src/verticals/automotive_collision/workflow.py`

- [ ] Rewrite the Birchwood intro and scripted prompts to sound warmer, dealership-specific, and one-question-at-a-time.
- [ ] Keep the gate order and deterministic business rules unchanged.
- [ ] Update any Birchwood final spoken responses that still sound robotic or overly generic while preserving the same routing outcomes.

### Task 5: Update demo and documentation surfaces

**Files:**
- Modify: `demo/birchwood_collision/BIRCHWOOD_DEMO_SCRIPT.md`
- Modify: `demo/birchwood_collision/BIRCHWOOD_COLLISION_ONE_PAGER.md`
- Modify: `demo/birchwood_collision/transcripts/*.md`
- Modify: `docs/AUTOMOTIVE_COLLISION_VERTICAL.md`
- Create: `docs/BIRCHWOOD_CALL_TEST_CHECKLIST.md`
- Modify: `README.md` (only if wording needs a short note)

- [ ] Align the demo script/transcripts with the warmer Birchwood language and narrative capture behavior.
- [ ] Document the Birchwood-specific speech/TTS settings and manual call-test expectations.
- [ ] Keep all demo data fake and preserve ORCA/Birchwood branding boundaries.

### Task 6: Run focused and regression validation

**Files:**
- Test only

- [ ] Run `python -m pytest tests/test_birchwood_voice_ux.py`.
- [ ] Run `python -m pytest tests/test_automotive_collision_workflow.py`.
- [ ] Run `python -m pytest tests/test_birchwood_collision_demo_pack.py`.
- [ ] Run `python -m pytest tests/evals`.
- [ ] Run `deepeval test run tests/evals`.
- [ ] Run `python -m pytest tests/test_insurance_claims_workflow.py`.
- [ ] Run `python -m pytest tests/test_phase11_5_healthcare_dynamic_intake.py`.
- [ ] Run `python -m pytest tests/test_red_flags.py`.
- [ ] Run `ruff check src tests scripts`.
- [ ] Run `ruff format --check src tests scripts`.

### Task 7: Finalize branch output

**Files:**
- VCS only

- [ ] Commit with `Refine Birchwood voice UX and narrative capture`.
- [ ] Push `phase-14-1-birchwood-voice-ux-refinement`.
- [ ] Open the Phase 14.1 pull request with the exact title/body requested by the user.