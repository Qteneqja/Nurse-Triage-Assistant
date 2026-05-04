"""Constants for the property management maintenance workflow."""

PROPERTY_MANAGEMENT_VERTICAL = "property_management"
PROPERTY_MAINTENANCE_WORKFLOW_ID = "property_management_maintenance_v1"
PROPERTY_MAINTENANCE_WORKFLOW_VERSION = "v1"

PROPERTY_MAINTENANCE_DISPOSITIONS = [
    "EMERGENCY",
    "SAME_DAY",
    "SCHEDULED_REPAIR",
    "INFORMATION_ONLY",
    "HUMAN_REVIEW",
]

PROPERTY_MAINTENANCE_REQUIRED_FIELDS = [
    "caller_name",
    "property_address",
    "unit_number",
    "issue_type",
    "issue_description",
    "access_permission",
    "callback_phone",
]

PROPERTY_MAINTENANCE_OUTPUT_TYPE = "WORK_ORDER"
