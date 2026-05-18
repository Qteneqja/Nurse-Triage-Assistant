# Birchwood Collision Intake Powered by ORCA

## Positioning

ORCA is the voice AI intake platform. Birchwood Automotive Group is the target client for this automotive collision intake demo workflow.

Workflow: `birchwood_collision_intake_v1`

Vertical: `automotive_collision`

Phone placeholder: `BIRCHWOOD_COLLISION_PHONE_NUMBER=+15555550140`

This placeholder is fake demo data. It is not a production Birchwood phone number.

## What ORCA Does

- Qualifies collision leads using deterministic intake gates.
- Captures customer, vehicle, incident, insurance claim, and location preference details.
- Routes non-drivable vehicles to the collision team.
- Routes glass-only damage to the glass department.
- Captures after-hours leads for staff follow-up.
- Produces structured intake records for dashboard review.

## What ORCA Does Not Do

- Does not provide repair estimates.
- Does not answer insurance policy questions.
- Does not give coverage advice.
- Does not process payments.
- Does not decide what insurance covers.
- Does not promise an appointment is booked without staff confirmation.
- Does not promise Birchwood will accept or complete a repair.

## Demo Targets

- 70%+ call deflection target.
- 3-5 minute average intake target.
- 100-150 calls per day normal capacity.
- 300+ calls per day peak capacity.

## Supported Gates

1. Drivability: unsafe or unsure vehicles transfer to the collision team.
2. Damage type: glass-only damage transfers to the glass department.
3. Vehicle year: 2011 or older vehicles receive a polite decline.
4. Rebuilt/salvage: rebuilt or salvage title vehicles receive a polite decline.

## Data Captured

- Customer: name, phone, email, address.
- Vehicle: year, make, model, license plate if available, drivable status, rebuilt/salvage status.
- Incident: damage type, description, optional incident date/time.
- Insurance: whether the caller is filing a claim and claim number if available.
- Location: preferred collision center or luxury placeholder auto-assignment.

## Routing Outcomes

- `COMPLETED_INTAKE`
- `INCOMPLETE_CALLBACK_NEEDED`
- `TRANSFER_COLLISION_CENTER`
- `TRANSFER_GLASS_DEPARTMENT`
- `DECLINED_VEHICLE_YEAR`
- `DECLINED_REBUILT_SALVAGE`
- `HUMAN_REVIEW`

## Dashboard Value

The staff dashboard can show the workflow ID, ORCA platform branding, Birchwood target account, routing result, missing information, callback flags, and intake summary. This gives staff a queue of structured collision leads instead of raw voicemail-style notes.

## Pilot Suggestion

Start with an after-hours or overflow pilot using the fake demo route in staging. Confirm routing behavior with Birchwood stakeholders, then seed approved phone-number routing through the normal platform routing table.

## Stakeholder Questions

1. What are the exact three collision center location names and addresses?
2. What is the final luxury brand list?
3. What DMS or CRM should receive structured intake records?
4. What is the glass department transfer process?
5. Are the decline messages acceptable as written?
6. What should happen after intake: callback, estimate request review, booking queue, or DMS task?
7. What data retention policy should apply to collision intake records?
