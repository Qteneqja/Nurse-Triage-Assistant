# BC_002 Non-Drivable Transfer

A damaged car that needs a tow. ORCA still takes the full intake, then the
deterministic drivability gate routes the caller to the collision team.
Regenerated from the live engine
(`python -m scripts.simulate_birchwood_call --scenario non_drivable_transfer`).

ORCA: Thank you for calling Birchwood Automotive Group. This call may be recorded for training and quality purposes. I'm here to get your vehicle booked in and taken care of after your accident - we'll get you back on the road. And if you'd rather speak with one of our team right away, just say transfer or press 0. I'm sorry you're dealing with this - let's get your vehicle looked after. Whenever you're ready, take your time and walk me through what happened, from the beginning, in your own words. I'll listen first, and then ask a few quick follow-ups.

Caller: I got hit hard and the whole front end is crushed in - it's not safe to drive.

ORCA: Got it - go on, I'm listening. When you're finished, just say, that's everything.

Caller: that's everything

ORCA: Got it. I noted that. Before anything else - was anyone hurt, even a little?

Caller: no, everyone's okay

ORCA: What year is the vehicle?

Caller: 2018

ORCA: And the make - like Toyota or Ford?

Caller: Ford

ORCA: What model is it?

Caller: Escape

ORCA: Almost done with the vehicle questions. Has it ever been written off and rebuilt? It would show as rebuilt or salvage on the title.

Caller: no, clean title

ORCA: When did this happen? Roughly is fine.

Caller: this afternoon

ORCA: And where did it happen? A street, intersection, or parking lot is perfect.

Caller: Portage and Main

ORCA: For the repair, are you planning to go through insurance, or pay out of pocket? Either way's absolutely fine - we'll take care of the car either way.

Caller: going through insurance

ORCA: If your insurance has given you a claim number, what is it? Totally fine if you don't have one yet - it just helps us coordinate your repair with them.

Caller: CLM-DEMO-2002

ORCA: Now just a couple of details so we can take care of you. What's your full name?

Caller: Avery Lee

ORCA: And what's the best phone number to reach you at?

Caller: 204 555 0102

ORCA: Wonderful - let me just make sure I've got everything right: a 2018 Ford Escape; not safe to drive; damage to the front end; it happened this afternoon; around Portage and Main; going through insurance with claim number CLM-DEMO-2002; and your advisor should call Avery Lee +12045550102. Did I get all of that right?

Caller: yes

ORCA: No problem at all. Since the vehicle may not be safe to drive - or you'd just rather talk with a person - let me get you straight through to our collision team.

Demo note: Deterministic transfer to the collision team for a non-drivable vehicle. Outcome TRANSFER_COLLISION_CENTER, flag non_drivable_transfer. Fake demo routing data only.
