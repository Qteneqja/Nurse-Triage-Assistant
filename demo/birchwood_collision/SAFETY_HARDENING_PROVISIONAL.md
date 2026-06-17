# Birchwood Collision - Safety Hardening (PROVISIONAL)

ORCA is the platform; Birchwood Automotive Group is the target client. This note
lists every PROVISIONAL choice in the collision safety-hardening layer so the team
can confirm or adjust them on the Birchwood discovery call (July). Everything here
is additive to the existing booking-forward intake - the live routing
(COMPLETED_INTAKE / transfers / declines) is unchanged.

## What was added (additive)

- A deterministic **ESCALATE_SAFETY** outcome for hazard / scene / legal
  situations - an immediate human handoff with safety guidance, ahead of the
  normal booking/transfer/decline gates. Over-escalation is preferred and the
  scan is fail-closed.
- A deterministic **restricted-advice boundary**: the bot never states insurance
  coverage, assigns fault/liability, quotes a repair cost/estimate, or gives
  legal/medical advice. A caller who asks for that advice is routed to a human.
- Demo scenarios BC_011-BC_015 and offline simulator scenarios
  (`python -m scripts.simulate_birchwood_call --scenario hazard_fire_escalate`, etc.).

## PROVISIONAL choices to confirm with Birchwood

1. **Hazard triggers** (route to ESCALATE_SAFETY): fire/smoke, fuel or gas leak,
   deployed airbags, unsafe position in live traffic. Confirm wording and whether
   anything should be added/removed. (`safety_escalation.py`)
2. **Scene triggers**: crash is active / "just happened", someone trapped, caller
   in acute distress. Confirm.
3. **Legal triggers**: disputed liability, legal threat/lawyer, fatality, police/
   criminal proceeding (incl. hit-and-run, impaired driving). Confirm.
4. **Injury handling is intentionally unchanged**: an injury mention keeps the
   existing advisory + record flag + human review on the base outcome (it does
   NOT become ESCALATE_SAFETY). Confirm whether Birchwood wants injuries promoted
   to ESCALATE_SAFETY too.
5. **Restricted-advice categories**: coverage, fault, cost, legal, medical.
   Confirm the list. (`advice_boundaries.py`)
6. **Coverage/cost QUESTIONS route to HUMAN_REVIEW.** Confirm whether a caller
   asking "will insurance cover this?" / "how much will it cost?" should be routed
   to a human, or simply get the standard disclaimer and continue the intake.
7. **Mid-call behavior**: like the existing injury branch, the safety escalation
   is applied at the intake outcome (the bot still completes the questions). If
   Birchwood wants the bot to stop intake and hand off the instant a hazard is
   mentioned, that is a larger change to confirm.
8. Pre-existing placeholders still pending sign-off: collision-center locations,
   luxury brand list, and the Birchwood phone number (see `.env.example`).

## Safety guardrail

This layer is additive and never weakens the platform safety gate or the
healthcare red-flag overlay. All triggers are deterministic (no LLM) and every
trigger, boundary, and scenario is covered by `tests/test_birchwood_safety_escalation.py`.
