"""Voice prompts for ORCA's Birchwood collision intake workflow."""

BIRCHWOOD_COLLISION_INTRO = (
    "Thanks for calling Birchwood Collision. This is ORCA, a voice assistant "
    "that can help collect the details for your collision repair intake and "
    "make sure it gets routed to the right team. If this is an emergency or "
    "your vehicle is not safe to drive, I'll get you to the collision team "
    "right away."
)

BIRCHWOOD_COLLISION_PROMPTS = {
    "is_drivable": "Is your vehicle safe to drive right now?",
    "damage_type": "What part of the vehicle is damaged?",
    "vehicle_year": "What year is your vehicle?",
    "rebuilt_salvage_status": (
        "Has this vehicle ever been written off by insurance and rebuilt? "
        "This would show as rebuilt or salvage on your title."
    ),
    "caller_name": "Can I get your full name?",
    "phone": "What is the best phone number for a callback?",
    "email": "What email address should the team use for follow-up?",
    "address": "What is your mailing address?",
    "vehicle_make": "What is the vehicle make?",
    "vehicle_model": "What is the vehicle model?",
    "license_plate": "What is the license plate, if you have it available?",
    "incident_description": "Please briefly describe what happened.",
    "incident_datetime": "When did this happen, if you know?",
    "filing_insurance_claim": "Are you filing an insurance claim for this repair?",
    "claim_number": (
        "If you are filing a claim and have the claim number, what is it?"
    ),
    "preferred_collision_center": ("Which Birchwood collision center would you like?"),
}
