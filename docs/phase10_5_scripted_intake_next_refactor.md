# Phase 10.5 Scripted Intake Refactor Note

Phase 10.5 introduces `ScriptedIntakeDefinition` and `ScriptedStageDefinition`
on `BaseWorkflow`. `HealthcareTriageWorkflow` now exposes its current voice
intake fields through that interface:

- `caller_name`
- `caller_age`
- `caller_sex`
- `chief_complaint`

The Twilio route still executes the legacy healthcare-shaped scripted intake
for production compatibility. The next platformization step should move the
stage loop in `src/twilio/routes.py` behind the workflow-provided scripted
definition so a future vertical can supply its own ordered fields without
editing Twilio route control flow.

Recommended next refactor:

1. Resolve workflow route in `/incoming`.
2. Load `workflow.get_scripted_intake_definition()`.
3. Store current `stage_id` and collected field values in session metadata.
4. Dispatch each scripted answer through a generic stage handler.
5. Hand control to `workflow.handle_turn()` once scripted stages are complete.

Do this as a dedicated change because the existing healthcare route contains
telephony-specific STT normalization and name validation that should be kept
stable until covered by route-level regression tests.
