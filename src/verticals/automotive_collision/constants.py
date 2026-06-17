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

# Minimal pure-intake: only what a collision specialist needs to start the file
# and call the customer back. The bot never decides/triages/adjudicates and never
# asks about injuries (the reactive emergency reflex is the shared platform
# overlay). Everything else (claim number, datetime, location, etc.) is optional
# data captured when offered. PROVISIONAL — confirm with Birchwood (July call).
BIRCHWOOD_COLLISION_REQUIRED_FIELDS = [
    "caller_name",
    "phone",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "damage_type",
    "is_drivable",
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
    "HUMAN_REVIEW",
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
