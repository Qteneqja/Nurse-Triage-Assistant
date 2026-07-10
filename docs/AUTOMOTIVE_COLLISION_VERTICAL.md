# Automotive Collision Vertical

## Overview

ORCA is the platform and company name. Birchwood Automotive Group is the target client/demo account for this workflow.

- Vertical: `automotive_collision`
- Workflow ID: `birchwood_collision_intake_v1`
- Display name: Birchwood Collision Intake
- Powered by: ORCA
- Client target: Birchwood Automotive Group
- Status: demo/pilot
- Phone placeholder: `BIRCHWOOD_COLLISION_PHONE_NUMBER=+15555550140`

The phone value above is fake demo data. Do not hard-code or commit a production Birchwood number.

## Purpose

The Birchwood Collision Intake workflow is a deterministic automotive-service intake flow powered by ORCA. It qualifies collision leads, captures structured customer and vehicle details, routes callers to the correct team, supports after-hours lead capture, and reduces repetitive call center intake work.

Phase 14.1 refines the live call experience for Birchwood without changing the deterministic routing logic. Birchwood now uses a warmer voice script, longer narrative capture for the incident-description field, and a workflow-specific Azure TTS profile with safe fallback to the global default voice.

Demo business targets:

- 70%+ call deflection target.
- 3-5 minute average intake target.
- 100-150 calls per day normal capacity.
- 300+ calls per day peak capacity.

## Gates

### Gate 1: Drivability

Question: "First, is your vehicle safe to drive right now?"

- No or unsure: `TRANSFER_COLLISION_CENTER`
- Yes: continue

### Gate 2: Damage Type

Question: "Is the damage only to the glass, like a windshield or window, or is there body damage as well?"

- Glass-only: `TRANSFER_GLASS_DEPARTMENT`
- Body damage: continue
- Glass plus body damage: continue collision intake

### Gate 3: Vehicle Year

Question: "What year is the vehicle?"

- 2011 or older: `DECLINED_VEHICLE_YEAR`
- 2012 or newer: continue
- Unknown or unclear: `HUMAN_REVIEW`

Decline message:

"I appreciate you calling. Unfortunately, our collision centers handle vehicles 2012 and newer. Thanks for thinking of Birchwood."

### Gate 4: Rebuilt/Salvage Status

Question: "Has the vehicle ever been written off by insurance and rebuilt? It may show as rebuilt or salvage on the title."

## Voice UX

Warm opening:

"Thank you for calling Birchwood Automotive Group. My name's Aurora — I'm an automated assistant, and I'm here to get your vehicle booked in and back on the road as quickly as possible. This call may be recorded for training and quality purposes. And if you'd rather speak with one of our team right away, just press zero."

(The spoken handoff instruction is press zero only; saying "transfer" still works as an unadvertised fallback.)

Narrative prompt:

"Can you tell me what happened and what parts of the vehicle were damaged? Take your time - you can describe it in your own words."

Narrative capture settings are workflow-specific so healthcare and insurance keep their existing speech timing:

- `BIRCHWOOD_SHORT_FIELD_TIMEOUT_SECONDS=5`
- `BIRCHWOOD_NARRATIVE_TIMEOUT_SECONDS=15`
- `BIRCHWOOD_NARRATIVE_SPEECH_TIMEOUT_SECONDS=auto`
- `BIRCHWOOD_ALLOW_LONG_INCIDENT_DESCRIPTION=true`

Azure TTS defaults for Birchwood remain configurable and currently target Bree Dragon HD Latest with graceful fallback to the global default voice:

- `AZURE_SPEECH_KEY=secretref:azure-speech-key` in production Container Apps
- `AZURE_SPEECH_REGION=canadacentral`
- `AZURE_TTS_VOICE=en-US-Bree:DragonHDLatestNeural`
- `BIRCHWOOD_AZURE_TTS_VOICE=en-US-Bree:DragonHDLatestNeural`
- `BIRCHWOOD_AZURE_TTS_RATE=+3%`
- `BIRCHWOOD_AZURE_TTS_PITCH=+0%`
- `BIRCHWOOD_AZURE_TTS_STYLE=` (blank by default for DragonHD compatibility)
- `BIRCHWOOD_AZURE_TTS_BREAK_MS=250`

- Yes: `DECLINED_REBUILT_SALVAGE`
- No: continue
- Unsure: continue with `staff_review_rebuilt_status`

Decline message:

"Thanks for letting me know. Our collision centers aren't able to service rebuilt or salvage title vehicles. I appreciate you calling."

## Required Fields

Customer:

- Name
- Phone
- Email
- Address

Vehicle:

- Year
- Make
- Model
- License plate if available
- Drivable status
- Rebuilt/salvage status

Incident:

- Damage type/location
- Short description
- Glass-only vs body damage
- Optional date/time

Insurance:

- Whether the caller is filing an insurance claim
- Claim number if available
- Private pay flag when no claim is being filed

ORCA does not ask for insurance company name in this workflow.

## Outcomes

- `COMPLETED_INTAKE`
- `INCOMPLETE_CALLBACK_NEEDED`
- `TRANSFER_COLLISION_CENTER`
- `TRANSFER_GLASS_DEPARTMENT`
- `DECLINED_VEHICLE_YEAR`
- `DECLINED_REBUILT_SALVAGE`
- `HUMAN_REVIEW`

## Flags

Supported deterministic flags include:

- `missing_claim_number`
- `missing_license_plate`
- `possible_duplicate`
- `private_pay`
- `staff_review_rebuilt_status`
- `glass_only_transfer`
- `non_drivable_transfer`
- `vehicle_year_declined`
- `rebuilt_salvage_declined`
- `luxury_auto_assigned`
- `vw_location_choice`
- `callback_needed`
- `multiple_vehicles`

## Location Routing

The ORCA/Birchwood rules indicate that exact collision center locations and the luxury brand list require stakeholder confirmation.

Demo placeholders:

- `BIRCHWOOD_COLLISION_LOCATION_1`
- `BIRCHWOOD_COLLISION_LOCATION_2`
- `BIRCHWOOD_COLLISION_LOCATION_3`
- `BIRCHWOOD_COLLISION_LUXURY_LOCATION`

Configurable env vars:

```text
BIRCHWOOD_COLLISION_LOCATION_1=BIRCHWOOD_COLLISION_LOCATION_1
BIRCHWOOD_COLLISION_LOCATION_2=BIRCHWOOD_COLLISION_LOCATION_2
BIRCHWOOD_COLLISION_LOCATION_3=BIRCHWOOD_COLLISION_LOCATION_3
BIRCHWOOD_COLLISION_LUXURY_LOCATION=BIRCHWOOD_COLLISION_LUXURY_LOCATION
BIRCHWOOD_COLLISION_LUXURY_BRANDS=Audi,Porsche,BMW,Mercedes-Benz,Lexus,Land Rover,Jaguar,Genesis,Acura,Cadillac,Infiniti,Volvo
```

Rules:

- VW/Volkswagen: offer all three placeholder locations.
- Luxury brands: auto-assign to the luxury placeholder location.
- Non-luxury, non-VW: offer all three placeholder locations.

## Dashboard Mapping

Admin/dashboard metadata should show:

- `vertical`: `automotive_collision`
- `workflow_id`: `birchwood_collision_intake_v1`
- `display_name`: Birchwood Collision Intake
- `powered_by`: ORCA
- `client_target`: Birchwood Automotive Group
- `status`: demo/pilot

Structured extraction includes customer, vehicle, incident, insurance, routing, missing information, callback, disclaimers, confidence, and human review fields.

## Boundaries

ORCA can capture intake details, answer basic procedural questions, route calls, flag callbacks, and produce structured output.

ORCA cannot provide repair estimates, answer specific insurance policy questions, give coverage advice, process payments, decide what insurance covers, promise an appointment without staff confirmation, or promise repair acceptance.

Completed-intake closing:

"Thanks, I have the main details noted. The Birchwood Collision team will be able to review this intake and follow up with you. Just a reminder, this doesn't confirm coverage, pricing, or an appointment yet - the team will confirm the next steps."

## Open Stakeholder Questions

1. What are the exact three collision center locations?
2. What is the final luxury brand list?
3. What DMS or CRM should receive intake records?
4. What is the glass department transfer process?
5. Are the decline messages acceptable?
6. What is the post-intake workflow for staff?
7. What data retention policy applies to collision intake records?
