# Birchwood Collision Demo Script

## 60-Second Pitch

ORCA is our voice AI intake platform. For Birchwood, this demo shows an automotive collision intake workflow that answers overflow or after-hours calls, qualifies the repair lead, applies Birchwood-specific routing rules, and creates a structured intake record for staff follow-up.

The Birchwood call flow now uses a warmer voice profile, lets callers take their time on the collision-story question, and keeps press 0 available for a transfer.

## How the Conversation Works (PR 2 - narrative-first)

The flow is no longer a fixed questionnaire. The call opens by inviting the
whole story ("Take your time and walk me through what happened, from the
beginning"), and the caller can pause naturally - ORCA says "go on, I'm
listening" instead of cutting them off. Deterministic extraction then
prefills every field the story already answered (vehicle, when, where,
drivability, damage, police report, photos, insurance path, claim number,
injuries), and ORCA asks ONLY the required fields that are still missing.
A rich story gets about four follow-up questions; a one-line story gets the
full gap-fill. Every call ends with a readback confirmation ("Let me make
sure I got this right...") and a next-steps close. If anyone is hurt, ORCA
immediately advises seeking medical attention or 9 1 1, flags the record,
and forces human review - on every routing outcome, including declines.

Try it offline before a live demo:

```bash
python -m scripts.simulate_birchwood_call                      # all scenarios
python -m scripts.simulate_birchwood_call --scenario injury_branch
```

## Problem Statement

Collision calls can be repetitive, time-sensitive, and hard to triage after hours. Staff need enough information to route the customer, but callers should not wait for a live person just to provide basic intake details.

## Live Demo Setup

- Use workflow `birchwood_collision_intake_v1`.
- Use fake placeholder phone route `BIRCHWOOD_COLLISION_PHONE_NUMBER=+15555550140`.
- Use demo data only.
- State clearly that ORCA is the platform and Birchwood is the target client account.

## Recommended First Scenario

Start with `BC_001_COMPLETED_TOYOTA`.

This scenario demonstrates the cleanest path:

- Drivable 2020 Toyota Camry.
- Body damage, not glass-only.
- Clean title.
- Insurance claim number available.
- Structured dashboard-ready intake output.

## What To Point Out

- ORCA asks one phone-friendly question at a time.
- ORCA now says callers can press 0 anytime to speak with someone.
- The incident-description prompt explicitly says "take your time" so callers can tell the collision story in their own words.
- The workflow is automotive-service oriented, not clinical.
- Non-drivable and unsure vehicles transfer to the collision team.
- Glass-only damage routes separately.
- Older vehicles and rebuilt/salvage titles receive short, polite decline messages.
- Missing claim numbers and plates create callback flags.
- The assistant does not ask for insurance company name.

## Staff Dashboard Value

- See customer and vehicle fields in a consistent structure.
- Review missing information before calling back.
- Filter transfers, declines, completed intakes, and callback-needed records.
- Spot possible duplicate calls when a customer already spoke with someone.

## Management Dashboard Value

- Track deflection target against 70%+ goal.
- Review average intake time against 3-5 minute target.
- Compare normal capacity of 100-150 calls per day and peak capacity of 300+ calls per day.
- Identify common missing information and routing patterns.

## Safety And Compliance Boundaries

- ORCA does not provide repair estimates.
- ORCA does not give insurance advice.
- ORCA does not decide coverage.
- ORCA does not process payments.
- ORCA does not book appointments unless staff confirmation exists.
- ORCA does not promise repair acceptance.
- Completed-intake language says the main details are noted, but it doesn't confirm coverage, pricing, or an appointment yet.

## Pilot Ask

Ask Birchwood stakeholders to approve a limited pilot scope, confirm location and glass routing details, and choose where structured intake records should be delivered.

## Stakeholder Questions

1. What are the exact three collision center locations?
2. Which luxury brands should auto-route to the luxury location?
3. What DMS, CRM, or inbox should receive the intake output?
4. What is the preferred transfer process for glass-only calls?
5. Should the decline wording be adjusted?
6. What staff queue owns completed intake review?
7. What retention policy applies to demo and pilot call records?
