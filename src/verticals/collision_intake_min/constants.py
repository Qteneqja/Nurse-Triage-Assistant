"""Constants for the minimal Birchwood collision-intake workflow.

ALL values are PROVISIONAL - confirm with Birchwood (July discovery call).
Kept here as editable config so they can change without touching logic.
"""

COLLISION_MIN_VERTICAL = "automotive_collision_min"
COLLISION_MIN_WORKFLOW_ID = "birchwood_collision_intake_min_v1"
COLLISION_MIN_WORKFLOW_VERSION = "v1"
COLLISION_MIN_DISPLAY_NAME = "Birchwood Collision Intake (Minimal)"
COLLISION_MIN_OUTPUT_TYPE = "COLLISION_INTAKE_MIN"
COLLISION_MIN_POWERED_BY = "ORCA"
COLLISION_MIN_CLIENT_TARGET = "Birchwood Automotive Group"
COLLISION_MIN_STATUS = "demo/pilot"
# PROVISIONAL placeholder route; never a real Birchwood number until approved.
COLLISION_MIN_PHONE_PLACEHOLDER = "+15555550141"

# Only what a collision specialist needs to start the file. PROVISIONAL.
COLLISION_MIN_REQUIRED_FIELDS = [
    "caller_name",
    "callback_number",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "damage_description",
    "drivable_status",
]

# Captured as data when offered; never used to decide/triage. PROVISIONAL.
# vehicle_location becomes conditionally required when the vehicle is not
# drivable (so a tow can be coordinated) - see rules.py.
COLLISION_MIN_OPTIONAL_FIELDS = [
    "license_plate",
    "vin",
    "vehicle_color",
    "mpi_claim_opened",
    "mpi_claim_number",
    "vehicle_location",
]

# Intake-completeness states only - NOT triage/coverage/fault decisions.
#   READY_FOR_SPECIALIST - full intake captured -> hand off (warm transfer if
#                          available, otherwise capture + flag for callback).
#   CALLBACK_NEEDED      - a required detail is missing -> capture what we have
#                          and flag a callback.
COLLISION_MIN_DISPOSITIONS = [
    "READY_FOR_SPECIALIST",
    "CALLBACK_NEEDED",
]

# Plain hallucination-prevention (NOT a "restricted-advice subsystem"): the agent
# never estimates or answers coverage/fault/cost/repair-time; it defers to the
# specialist and continues. PROVISIONAL wording.
COLLISION_MIN_DISCLAIMER = (
    "ORCA collects your collision details for a Birchwood specialist; it does "
    "not estimate cost, coverage, fault, or repair time."
)
COLLISION_MIN_DEFLECTION_REPLY = (
    "A Birchwood collision specialist will go over cost, coverage, and timing "
    "with you directly. Let me make sure I have your details so they can help."
)
