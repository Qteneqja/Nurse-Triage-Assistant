# BC_001 Completed Toyota Intake

Narrative-first flow: the caller tells the whole story, deterministic
extraction prefills the fields it answered, and ORCA only asks the required
gaps before the readback and booking-oriented close. Regenerated from the
live engine (`python -m scripts.simulate_birchwood_call --scenario completed_toyota`).

ORCA: Thank you for calling Birchwood Automotive Group. This call may be recorded for training and quality purposes. I'm here to get your vehicle booked in and taken care of after your accident - we'll get you back on the road. And if you'd rather speak with one of our team right away, just say transfer or press 0. I'm sorry you're dealing with this - let's get your vehicle looked after. Whenever you're ready, take your time and walk me through what happened, from the beginning, in your own words. I'll listen first, and then ask a few quick follow-ups.

Caller: Another driver backed into me in a parking lot yesterday afternoon and crunched the front bumper and grille. It's my 2020 Toyota Camry, it's still safe to drive, and nobody was hurt. I'm going through insurance - the claim number is CLM-DEMO-1001.

ORCA: Got it - go on, I'm listening. When you're finished, just say, that's everything.

Caller: that's everything

ORCA: Got it. I noted that. Almost done with the vehicle questions. Has it ever been written off and rebuilt? It would show as rebuilt or salvage on the title.

Caller: no, clean title

ORCA: If your insurance has given you a claim number, what is it? Totally fine if you don't have one yet - it just helps us coordinate your repair with them.

Caller: CLM-DEMO-1001

ORCA: Now just a couple of details so we can take care of you. What's your full name?

Caller: John Smith

ORCA: And what's the best phone number to reach you at?

Caller: 204 555 0101

ORCA: Wonderful - let me just make sure I've got everything right: a 2020 Toyota Camry; still safe to drive; damage to the bumper, grille; it happened yesterday afternoon; around a parking lot; going through insurance with claim number CLM-DEMO-1001; and your advisor should call John Smith +12045550101. Did I get all of that right?

Caller: yes that's right

ORCA: Perfect - you're all set, and we'll get you booked in. Here's what happens next: one of our service advisors will give you a call back to confirm timing and the Birchwood location that works best for you. Just so you know, this doesn't confirm coverage, pricing, or an appointment yet - your advisor will take care of those details with you. Thanks so much for calling Birchwood, and take care.

Demo note: Completed Birchwood collision intake powered by ORCA. Outcome COMPLETED_INTAKE, flag injuries_denied. Fake demo data only.
