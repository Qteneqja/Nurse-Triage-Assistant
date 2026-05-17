"""Constants for the insurance FNOL claims workflow."""

INSURANCE_VERTICAL = "insurance"
INSURANCE_CLAIMS_FNOL_WORKFLOW_ID = "insurance_claims_fnol_v1"
INSURANCE_CLAIMS_FNOL_WORKFLOW_VERSION = "v1"
INSURANCE_FNOL_PHONE_PLACEHOLDER = "+15555550130"

INSURANCE_CLAIMS_FNOL_OUTPUT_TYPE = "CLAIM_INTAKE"

INSURANCE_CLAIMS_FNOL_REQUIRED_FIELDS = [
    "caller_name",
    "callback_number",
    "policy_number",
    "claim_type",
    "loss_datetime",
    "loss_location",
    "incident_summary",
]

INSURANCE_CLAIMS_FNOL_DISPOSITIONS = [
    "EMERGENCY_SERVICES_NOW",
    "URGENT_ADJUSTER_REVIEW",
    "STANDARD_CLAIM_INTAKE",
    "DOCUMENTS_NEEDED",
    "INFORMATION_ONLY",
    "HUMAN_REVIEW",
]

INSURANCE_CLAIM_TYPE_VALUES = [
    "auto accident",
    "property damage",
    "water damage",
    "theft/loss",
    "glass/window damage",
    "liability/business claim",
    "other",
]

INSURANCE_DISCLAIMERS = [
    "A licensed broker or adjuster can confirm coverage.",
    "This intake does not guarantee claim approval.",
]
