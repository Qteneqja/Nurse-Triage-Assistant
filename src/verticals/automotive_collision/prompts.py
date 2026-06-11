"""Voice prompts for ORCA's Birchwood collision intake workflow.

PR 2 tone pass: callers may have just been in a collision — every prompt is
plain-language, unhurried, and empathetic. The conversation is
narrative-first: invite the whole story, then gap-fill only what the story
did not answer.
"""

BIRCHWOOD_COLLISION_INTRO = (
    "Thanks for calling Birchwood Collision. I'm ORCA, a voice assistant "
    "helping the team collect collision repair details. If your vehicle "
    "isn't safe to drive, or you'd rather speak with someone, just say "
    "transfer or press 0."
)

BIRCHWOOD_COLLISION_PROMPTS = {
    "incident_description": (
        "I'm sorry you're dealing with this - let's get the details so the "
        "right person can help. Take your time and walk me through what "
        "happened, from the beginning, in your own words. I'll listen "
        "first, and then ask a few quick follow-ups."
    ),
    "injuries_state": (
        "Before we talk about the vehicle - was anyone hurt, even a little?"
    ),
    "is_drivable": "Thanks. Is the vehicle safe to drive right now?",
    "damage_type": (
        "Which parts of the vehicle are damaged? For example, is it glass "
        "only, like a windshield, or is there body damage too?"
    ),
    "vehicle_year": "What year is the vehicle?",
    "vehicle_make": "And the make - like Toyota or Ford?",
    "vehicle_model": "What model is it?",
    "rebuilt_salvage_status": (
        "Almost done with the vehicle questions. Has it ever been written "
        "off by insurance and rebuilt? It would show as rebuilt or salvage "
        "on the title."
    ),
    "incident_datetime": "When did this happen? Roughly is fine.",
    "incident_location": (
        "And where did it happen? A street, intersection, or parking lot is perfect."
    ),
    "filing_insurance_claim": (
        "Are you going through insurance for the repair, or paying privately?"
    ),
    "claim_number": (
        "If you already have the claim number handy, what is it? It's "
        "completely fine if you don't have it yet."
    ),
    "caller_name": (
        "Now just a couple of details so the team can reach you. What's your full name?"
    ),
    "phone": "And the best phone number for a callback?",
    # Static fallback only — the readback is built dynamically from the
    # captured fields (see build_dynamic_prompt on the workflow).
    "confirmation_ack": "Did I get all of that right?",
    "correction_note": "No problem - what should I correct?",
    # Legacy keys kept for compatibility with stored sessions/demos.
    "email": "What email address should the team use for follow-up?",
    "address": "What is your mailing address?",
    "license_plate": "What is the license plate, if you have it available?",
    "preferred_collision_center": (
        "Which Birchwood Collision location would you prefer? The team can "
        "confirm the best fit when they follow up."
    ),
}

# Spoken right after the caller confirms the readback.
BIRCHWOOD_COLLISION_NEXT_STEPS_CLOSE = (
    "Perfect, you're all set. Here's what happens next: the Birchwood "
    "Collision team will review this and call you back to confirm timing "
    "and the best location. Just a reminder - this doesn't confirm "
    "coverage, pricing, or an appointment yet. Thanks for calling, and "
    "take care."
)
