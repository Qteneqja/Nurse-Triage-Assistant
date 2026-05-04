"""Voice scripted prompts for property maintenance intake."""

PROPERTY_MAINTENANCE_PROMPTS = {
    "caller_name": "Thanks for calling. Can I get your name?",
    "property_address": "What property address is this about?",
    "unit_number": "What unit or apartment number is this for?",
    "issue_type": (
        "What type of issue are you calling about? For example plumbing, heat, "
        "electrical, lockout, appliance, noise, or something else."
    ),
    "issue_description": "Can you briefly describe what is happening?",
    "access_permission": (
        "Do we have permission to enter the unit if maintenance needs access?"
    ),
    "callback_phone": "What is the best phone number for a callback?",
}
