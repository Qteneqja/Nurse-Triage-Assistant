"""Scripted prompts for the minimal collision-intake workflow. PROVISIONAL wording."""

COLLISION_MIN_INTRO = (
    "Thank you for calling Birchwood Automotive Group. I can take down your "
    "collision details and pass them straight to a collision specialist. "
    "If you'd rather speak with someone now, just say transfer or press 0."
)

COLLISION_MIN_PROMPTS = {
    "caller_name": "First, who am I speaking with - your full name, please?",
    "callback_number": "What's the best phone number to reach you at?",
    "vehicle_year": "What year is the vehicle?",
    "vehicle_make": "And the make - like Toyota or Ford?",
    "vehicle_model": "What model is it?",
    "damage_description": ("In a sentence or two, what's the damage? Take your time."),
    "mpi_claim": (
        "Have you opened an MPI claim yet? If so, what's the claim number? "
        "It's totally fine if you haven't - the specialist can help with that."
    ),
    "drivable_status": ("Is the vehicle safe to drive, or does it need a tow?"),
    "vehicle_location": ("Since it needs a tow, where is the vehicle right now?"),
}

COLLISION_MIN_HANDOFF_CLOSE = (
    "Thank you - I've got your collision details. A Birchwood collision "
    "specialist will take it from here. Take care."
)

COLLISION_MIN_CALLBACK_CLOSE = (
    "Thank you - I've saved what you've told me, and a Birchwood collision "
    "specialist will call you back to finish up. Take care."
)
