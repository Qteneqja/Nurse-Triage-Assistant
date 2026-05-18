"""Voice prompts for ORCA's Birchwood collision intake workflow."""

BIRCHWOOD_COLLISION_INTRO = (
    "Thanks for calling Birchwood Collision. I'm ORCA, a voice assistant "
    "helping the team collect collision repair details. I'll ask a few quick "
    "questions so the right person can follow up. If your vehicle isn't safe "
    "to drive, or you'd rather speak with someone, just say transfer or press 0."
)

BIRCHWOOD_COLLISION_PROMPTS = {
    "is_drivable": "First, is your vehicle safe to drive right now?",
    "damage_type": (
        "Is the damage only to the glass, like a windshield or window, "
        "or is there body damage as well?"
    ),
    "vehicle_year": "What year is the vehicle?",
    "rebuilt_salvage_status": (
        "Has the vehicle ever been written off by insurance and rebuilt? "
        "It may show as rebuilt or salvage on the title."
    ),
    "caller_name": "Can I get your full name?",
    "phone": "What is the best phone number for a callback?",
    "email": "What email address should the team use for follow-up?",
    "address": "What is your mailing address?",
    "vehicle_make": "What is the vehicle make?",
    "vehicle_model": "What is the vehicle model?",
    "license_plate": "What is the license plate, if you have it available?",
    "incident_description": (
        "Can you tell me what happened and what parts of the vehicle were "
        "damaged? Take your time - you can describe it in your own words."
    ),
    "incident_datetime": "When did this happen, if you know?",
    "filing_insurance_claim": (
        "Are you going through insurance for this repair, or will this be private pay?"
    ),
    "claim_number": (
        "If you're going through insurance and already have the claim number, "
        "what is it?"
    ),
    "preferred_collision_center": (
        "Which Birchwood Collision location would you prefer? I have the "
        "available location options listed for the team, and they can confirm "
        "the best fit when they follow up."
    ),
}
