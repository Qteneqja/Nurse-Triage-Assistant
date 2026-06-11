"""Voice prompts for the Birchwood collision intake workflow.

Voice pass (pr-birchwood-script-voice): every line is written the way a
Birchwood Automotive Group service advisor would say it on the phone —
warm, branded, dealership-grade. The caller may have just been in an
accident; lead with reassurance, use plain spoken language, and frame
insurance details as coordination for THEIR repair, never as claim
processing. Wording only: extraction fields, safety branches, and the
engine are unchanged.
"""

BIRCHWOOD_COLLISION_INTRO = (
    "Thank you for calling Birchwood Automotive Group. This call may be "
    "recorded for training and quality purposes. I'm here to help get "
    "your vehicle taken care of after your accident. And if you'd rather "
    "speak with one of our team right away, just say transfer or press 0."
)

BIRCHWOOD_COLLISION_PROMPTS = {
    "incident_description": (
        "I'm sorry you're dealing with this - let's get your vehicle "
        "looked after. Whenever you're ready, take your time and walk me "
        "through what happened, from the beginning, in your own words. "
        "I'll listen first, and then ask a few quick follow-ups."
    ),
    "injuries_state": ("Before anything else - was anyone hurt, even a little?"),
    "is_drivable": "Thank you. And is your vehicle safe to drive right now?",
    "damage_type": (
        "Where's the damage on the vehicle? For example, is it just "
        "glass - like a windshield - or is there body damage too?"
    ),
    "vehicle_year": "What year is the vehicle?",
    "vehicle_make": "And the make - like Toyota or Ford?",
    "vehicle_model": "What model is it?",
    "rebuilt_salvage_status": (
        "Almost done with the vehicle questions. Has it ever been written "
        "off and rebuilt? It would show as rebuilt or salvage on the title."
    ),
    "incident_datetime": "When did this happen? Roughly is fine.",
    "incident_location": (
        "And where did it happen? A street, intersection, or parking lot is perfect."
    ),
    "filing_insurance_claim": (
        "Are you planning to go through insurance for the repair, or pay "
        "out of pocket? Either way is absolutely fine."
    ),
    "claim_number": (
        "If your insurance has given you a claim number, what is it? "
        "Totally fine if you don't have one yet - it just helps us "
        "coordinate your repair with them."
    ),
    "caller_name": (
        "Now just a couple of details so we can take care of you. What's "
        "your full name?"
    ),
    "phone": "And what's the best phone number to reach you at?",
    # Static fallback only — the readback is built dynamically from the
    # captured fields (see build_dynamic_prompt on the workflow).
    "confirmation_ack": "Did I get all of that right?",
    "correction_note": "No problem at all - what should I correct?",
    # Legacy keys kept for compatibility with stored sessions/demos.
    "email": "What email address should we use for your repair updates?",
    "address": "And what's your mailing address?",
    "license_plate": "What's the license plate, if you have it handy?",
    "preferred_collision_center": (
        "Which Birchwood location would be most convenient for you? Your "
        "advisor can confirm the best fit when they call you back."
    ),
}

# Spoken right after the caller confirms the readback.
BIRCHWOOD_COLLISION_NEXT_STEPS_CLOSE = (
    "Perfect - you're all set. Here's what happens next: one of our "
    "service advisors will give you a call back to confirm timing and "
    "the Birchwood location that works best for you. Just so you know, "
    "this doesn't confirm coverage, pricing, or an appointment yet - "
    "your advisor will take care of those details with you. Thanks so "
    "much for calling Birchwood, and take care."
)
