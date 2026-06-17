"""Constants for ORCA's Birchwood automotive collision intake demo."""

AUTOMOTIVE_COLLISION_VERTICAL = "automotive_collision"
BIRCHWOOD_COLLISION_WORKFLOW_ID = "birchwood_collision_intake_v1"
BIRCHWOOD_COLLISION_WORKFLOW_VERSION = "v1"
BIRCHWOOD_COLLISION_DISPLAY_NAME = "Birchwood Collision Intake"
BIRCHWOOD_COLLISION_POWERED_BY = "ORCA"
BIRCHWOOD_COLLISION_CLIENT_TARGET = "Birchwood Automotive Group"
BIRCHWOOD_COLLISION_STATUS = "demo/pilot"
BIRCHWOOD_COLLISION_PHONE_PLACEHOLDER = "+15555550140"

BIRCHWOOD_COLLISION_OUTPUT_TYPE = "AUTOMOTIVE_COLLISION_INTAKE"

# PR 2: required = what the shop needs before a callback is useful, asked
# via targeted gap-fill only when the caller's story didn't already answer
# it. Email/address moved to optional (captured when offered, never
# interrogated); incident datetime/location and the injury state are now
# first-class requirements.
BIRCHWOOD_COLLISION_REQUIRED_FIELDS = [
    "caller_name",
    "phone",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "is_drivable",
    "damage_type",
    "incident_description",
    "incident_datetime",
    "incident_location",
    "injuries_state",
    "filing_insurance_claim",
]

# Optional fields: captured opportunistically (narrative extraction or
# volunteered), never asked one-by-one during gap-fill.
BIRCHWOOD_COLLISION_OPTIONAL_FIELDS = [
    "email",
    "address",
    "license_plate",
    "other_parties",
    "police_report_filed",
    "photos_available",
    "insurance_provider",
    "claim_number",
    "preferred_collision_center",
    "preferred_timing",
    "urgency",
]

BIRCHWOOD_COLLISION_OUTCOMES = [
    "COMPLETED_INTAKE",
    "INCOMPLETE_CALLBACK_NEEDED",
    "TRANSFER_COLLISION_CENTER",
    "TRANSFER_GLASS_DEPARTMENT",
    "DECLINED_VEHICLE_YEAR",
    "DECLINED_REBUILT_SALVAGE",
    # Safety hardening: deterministic hazard/scene/legal escalation (additive,
    # over-escalation preferred). Routed to an immediate human handoff with
    # safety guidance. See safety_escalation.py + ADR/skills.
    "ESCALATE_SAFETY",
    "HUMAN_REVIEW",
]

# PROVISIONAL — confirm with Birchwood (July discovery call). Categories the
# intake bot must never advise on; enforced deterministically by
# advice_boundaries.py (post-check on assistant text + caller-request routing).
BIRCHWOOD_COLLISION_RESTRICTED_ADVICE = [
    "coverage",  # insurance coverage / claim approval
    "fault",  # fault / liability assignment
    "cost",  # repair cost / estimate / pricing
    "legal",  # legal advice (sue / lawyer / charges)
    "medical",  # medical diagnosis / advice
]

BIRCHWOOD_COLLISION_DEFAULT_LOCATIONS = [
    "BIRCHWOOD_COLLISION_LOCATION_1",
    "BIRCHWOOD_COLLISION_LOCATION_2",
    "BIRCHWOOD_COLLISION_LOCATION_3",
]
BIRCHWOOD_COLLISION_DEFAULT_LUXURY_LOCATION = "BIRCHWOOD_COLLISION_LUXURY_LOCATION"

BIRCHWOOD_COLLISION_DEFAULT_LUXURY_BRANDS = [
    "Audi",
    "Porsche",
    "BMW",
    "Mercedes-Benz",
    "Lexus",
    "Land Rover",
    "Jaguar",
    "Genesis",
    "Acura",
    "Cadillac",
    "Infiniti",
    "Volvo",
]

BIRCHWOOD_COLLISION_DISCLAIMERS = [
    "ORCA does not provide repair estimates.",
    "ORCA does not provide insurance advice or coverage decisions.",
    "Staff must confirm appointments and repair acceptance.",
]
