# Birchwood Collision Demo Script

## 60-Second Pitch

ORCA is our voice AI intake platform. For Birchwood, this demo shows an automotive collision intake workflow that answers overflow or after-hours calls, qualifies the repair lead, applies Birchwood-specific routing rules, and creates a structured intake record for staff follow-up.

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
