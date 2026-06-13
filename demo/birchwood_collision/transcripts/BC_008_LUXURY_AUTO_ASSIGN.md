# BC_008 Luxury Auto-Assign

A luxury brand (BMW) is auto-assigned to the luxury collision location for
staff review, then completes a clean intake. Regenerated from the live engine
(`python -m scripts.simulate_birchwood_call --scenario luxury_completed`).

ORCA: Thank you for calling Birchwood Automotive Group. This call may be recorded for training and quality purposes. I'm here to get your vehicle booked in and taken care of after your accident - we'll get you back on the road. And if you'd rather speak with one of our team right away, just say transfer or press 0. I'm sorry you're dealing with this - let's get your vehicle looked after. Whenever you're ready, take your time and walk me through what happened, from the beginning, in your own words. I'll listen first, and then ask a few quick follow-ups.

Caller: Someone clipped my front fender in a parking garage. It's my 2022 BMW X5, it still drives fine, and no one was hurt. I'll be going through insurance, claim number CLM-DEMO-8008.

ORCA: Got it - go on, I'm listening. When you're finished, just say, that's everything.

Caller: that's everything

ORCA: Got it. I noted that. Almost done with the vehicle questions. Has it ever been written off and rebuilt? It would show as rebuilt or salvage on the title.

Caller: no

ORCA: When did this happen? Roughly is fine.

Caller: this morning

ORCA: If your insurance has given you a claim number, what is it? Totally fine if you don't have one yet - it just helps us coordinate your repair with them.

Caller: CLM-DEMO-8008

ORCA: Now just a couple of details so we can take care of you. What's your full name?

Caller: Sam Rivera

ORCA: And what's the best phone number to reach you at?

Caller: 204 555 0108

ORCA: Wonderful - let me just make sure I've got everything right: a 2022 BMW X5; still safe to drive; damage to the fender; it happened this morning; around a parking garage; going through insurance with claim number CLM-DEMO-8008; and your advisor should call Sam Rivera +12045550108. Did I get all of that right?

Caller: yes

ORCA: Perfect - you're all set, and we'll get you booked in. Here's what happens next: one of our service advisors will give you a call back to confirm timing and the Birchwood location that works best for you. Just so you know, this doesn't confirm coverage, pricing, or an appointment yet - your advisor will take care of those details with you. Thanks so much for calling Birchwood, and take care.

Demo note: Luxury brand auto-assigned to the luxury collision location for staff review. Outcome COMPLETED_INTAKE, flag luxury_auto_assigned. The luxury brand list and location are configurable placeholders pending Birchwood sign-off. Fake demo data only.
