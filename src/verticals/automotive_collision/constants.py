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

BIRCHWOOD_COLLISION_REQUIRED_FIELDS = [
    "caller_name",
    "phone",
    "email",
    "address",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "is_drivable",
    "damage_type",
    "incident_description",
    "filing_insurance_claim",
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
