# BC_006 Missing Claim Number Callback

Going through insurance but no claim number yet. ORCA captures everything,
notes the claim number as still to come, and flags a callback - never giving
insurance advice. Regenerated from the live engine
(`python -m scripts.simulate_birchwood_call --scenario missing_claim_callback`).

ORCA: Thank you for calling Birchwood Automotive Group. This call may be recorded for training and quality purposes. I'm here to get your vehicle booked in and taken care of after your accident - we'll get you back on the road. And if you'd rather speak with one of our team right away, just say transfer or press 0. I'm sorry you're dealing with this - let's get your vehicle looked after. Whenever you're ready, take your time and walk me through what happened, from the beginning, in your own words. I'll listen first, and then ask a few quick follow-ups.

Caller: I was sideswiped on the freeway and there's damage along the passenger side. The car drives okay and nobody was hurt. I'll be going through insurance but I don't have a claim number yet.

ORCA: Got it - go on, I'm listening. When you're finished, just say, that's everything.

Caller: that's everything

ORCA: Got it. I noted that. Thank you. Can you drive the vehicle in to us right now, or does it need a tow?

Caller: yes, it drives okay

ORCA: Where's the damage on the vehicle? For example, is it just glass - like a windshield - or is there body damage too?

Caller: passenger side panels

ORCA: What year is the vehicle?

Caller: 2019

ORCA: And the make - like Toyota or Ford?

Caller: Nissan

ORCA: What model is it?

Caller: Rogue

ORCA: Almost done with the vehicle questions. Has it ever been written off and rebuilt? It would show as rebuilt or salvage on the title.

Caller: no

ORCA: When did this happen? Roughly is fine.

Caller: last night

ORCA: And where did it happen? A street, intersection, or parking lot is perfect.

Caller: the freeway

ORCA: If your insurance has given you a claim number, what is it? Totally fine if you don't have one yet - it just helps us coordinate your repair with them.

Caller: I don't have it yet

ORCA: Now just a couple of details so we can take care of you. What's your full name?

Caller: Taylor Johnson

ORCA: And what's the best phone number to reach you at?

Caller: 204 555 0106

ORCA: Wonderful - let me just make sure I've got everything right: a 2019 Nissan Rogue; still safe to drive; damage to the passenger side panels; it happened last night; around the freeway; going through insurance, claim number to follow; and your advisor should call Taylor Johnson +12045550106. Did I get all of that right?

Caller: yes

ORCA: That's no problem at all - I've noted that the claim number is still to come, and your advisor will grab it when they call you back. Thanks so much for calling Birchwood, and take care.

Demo note: Callback flagged for the missing claim number. Outcome INCOMPLETE_CALLBACK_NEEDED, flags missing_claim_number and callback_needed. Fake demo data only.
