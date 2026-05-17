"""Voice scripted prompts for insurance FNOL intake."""

INSURANCE_FNOL_PROMPTS = {
    "caller_name": "Thanks for calling. Can I get your name?",
    "callback_number": "What is the best phone number for a callback?",
    "policy_number": "What is your policy number, if you have it handy?",
    "claim_type": (
        "What type of claim is this? For example auto accident, property damage, "
        "water damage, theft or loss, glass or window damage, liability or business "
        "claim, or other."
    ),
    "loss_datetime": "When did the loss or incident happen?",
    "loss_location": "Where did the loss or incident happen?",
    "incident_summary": "Please briefly describe what happened.",
}
