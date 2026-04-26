"""Healthcare workflow constants."""

HEALTHCARE_VERTICAL = "healthcare"
HEALTHCARE_TRIAGE_WORKFLOW_ID = "healthcare_triage_v1"
HEALTHCARE_TRIAGE_VERSION = "v1"
HEALTHCARE_TRIAGE_DISPLAY_NAME = "Healthcare Triage"

HEALTHCARE_DISPOSITIONS = [
    "ER_NOW",
    "URGENT",
    "SCHEDULE",
    "SELF_CARE",
    "HUMAN_REVIEW",
]

HEALTHCARE_OUTPUT_TYPES = ["SBAR"]

HEALTHCARE_REQUIRED_FIELDS = [
    "caller_name",
    "caller_age",
    "caller_sex",
    "chief_complaint",
]

