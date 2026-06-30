"""Voice prompts for the Birchwood collision intake workflow.

Voice pass (pr-birchwood-script-voice): every line is written the way a
Birchwood Automotive Group service advisor would say it on the phone —
warm, branded, dealership-grade. The caller may have just been in an
accident; lead with reassurance, use plain spoken language, and frame
insurance details as coordination for THEIR repair, never as claim
processing.

Repair-voice pass (pr-birchwood-repair-voice): pushed the copy further
toward a collision shop booking a damaged car in rather than a claims
intake — the greeting promises to get the vehicle "booked in" and "back
on the road", drivability is asked as "drive it in to us, or does it
need a tow", insurance is reinforced as "we'll take care of the car
either way", and the close opens with "we'll get you booked in".

Aurora pass (pr-birchwood-aurora-naturalness): the assistant now introduces
itself by name ("Aurora") and discloses up front that it is an automated
assistant. Per-stage phrasing variation, slot-echo, backchannels, and off-topic
redirection live in ``voice_naturalness.py`` (deterministic, transport-neutral);
this module keeps the canonical static copy used as the fallback.

Wording only: extraction fields, safety branches, and the engine are
unchanged.
"""

# Aurora naturalness pass: introduce by name AND disclose the automated
# assistant up front. The brand line and the recording-consent clause are
# preserved verbatim; the automated-assistant disclosure is deliberate and
# must not be removed or softened.
BIRCHWOOD_COLLISION_INTRO = (
    "Thank you for calling Birchwood Automotive Group. My name's Aurora — "
    "I'm an automated assistant, and I'm here to get your vehicle booked in "
    "and back on the road as quickly as possible. This call may be recorded "
    "for training and quality purposes. And if you'd rather speak with one of "
    "our team right away, just say transfer or press zero."
)

# Conversational tier greeting (BIRCHWOOD_CONVERSATIONAL_INTAKE). Same persona,
# disclosure, and recording clause, but it leads straight into the FIRST question
# (the caller's name) rather than "tell me what happened" — the damage comes later
# in the flow, after the vehicle details.
BIRCHWOOD_COLLISION_CONVERSATIONAL_INTRO = (
    "Thank you for calling Birchwood Collision. My name's Aurora — "
    "I'm an automated assistant, and I'm here to help get your vehicle booked "
    "in after a collision. This call may be recorded for training and quality "
    "purposes. If at any point you'd rather speak with our team, just say "
    "transfer. To get started, can I get your full name?"
)

BIRCHWOOD_COLLISION_PROMPTS = {
    "incident_description": (
        "I'm sorry you're dealing with this - let's get your vehicle "
        "looked after. Whenever you're ready, take your time and walk me "
        "through what happened, from the beginning, in your own words. "
        "I'll listen first, and then ask a few quick follow-ups."
    ),
    "injuries_state": ("Before anything else - was anyone hurt, even a little?"),
    "is_drivable": (
        "Thank you. Can you drive the vehicle in to us right now, or does "
        "it need a tow?"
    ),
    "damage_type": (
        "In a sentence or two, what's the damage to the vehicle? Take your time."
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
        "Have you opened an MPI claim for this yet? A yes or no is perfect - "
        "the specialist can help with it either way."
    ),
    "claim_number": (
        "If you have an MPI claim number, what is it? Totally fine if you "
        "don't have one yet."
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
    "Perfect - you're all set, and we'll get you booked in. Here's what "
    "happens next: one of our service advisors will give you a call back "
    "to confirm timing and the Birchwood location that works best for "
    "you. Just so you know, this doesn't confirm coverage, pricing, or an "
    "appointment yet - your advisor will take care of those details with "
    "you. Thanks so much for calling Birchwood, and take care."
)
