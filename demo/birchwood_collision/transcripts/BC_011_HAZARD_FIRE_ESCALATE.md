# BC_011_HAZARD_FIRE_ESCALATE

Smoke/fuel-leak hazard. ORCA escalates to a human with safety guidance instead of routine intake.
Regenerated from the live engine (`python -m scripts.simulate_birchwood_call --scenario hazard_fire_escalate`).

ORCA: Thank you for calling Birchwood Automotive Group. This call may be recorded for training and quality purposes. I'm here to get your vehicle booked in and taken care of after your accident - we'll get you back on the road. And if you'd rather speak with one of our team right away, just say transfer or press 0. I'm sorry you're dealing with this - let's get your vehicle looked after. Whenever you're ready, take your time and walk me through what happened, from the beginning, in your own words. I'll listen first, and then ask a few quick follow-ups.

Caller: I just had a crash and there's smoke coming from under the hood and I think I can smell gas leaking.

ORCA: Got it - go on, I'm listening. When you're finished, just say, that's everything.

Caller: that's everything

ORCA: Got it. I noted that. Before anything else - was anyone hurt, even a little?

Caller: no one is hurt

ORCA: Thank you. Can you drive the vehicle in to us right now, or does it need a tow?

Caller: no, it's not safe

ORCA: What year is the vehicle?

Caller: 2020

ORCA: And the make - like Toyota or Ford?

Caller: Toyota

ORCA: What model is it?

Caller: RAV4

ORCA: Almost done with the vehicle questions. Has it ever been written off and rebuilt? It would show as rebuilt or salvage on the title.

Caller: no

ORCA: When did this happen? Roughly is fine.

Caller: just now

ORCA: And where did it happen? A street, intersection, or parking lot is perfect.

Caller: Pembina Highway

ORCA: For the repair, are you planning to go through insurance, or pay out of pocket? Either way's absolutely fine - we'll take care of the car either way.

Caller: going through insurance

ORCA: If your insurance has given you a claim number, what is it? Totally fine if you don't have one yet - it just helps us coordinate your repair with them.

Caller: CLM-DEMO-1101

ORCA: Now just a couple of details so we can take care of you. What's your full name?

Caller: Dana Demo

ORCA: And what's the best phone number to reach you at?

Caller: 204 555 0111

ORCA: Wonderful - let me just make sure I've got everything right: a 2020 Toyota RAV4; not safe to drive; damage to the hood; it happened just now; around Pembina Highway; going through insurance with claim number CLM-DEMO-1101; and your advisor should call Dana Demo +12045550111. Did I get all of that right?

Caller: yes

ORCA: Your safety comes first. If anyone is in danger, get to a safe place and call 9 1 1 right away. I'm connecting you with a member of our team now. I've saved your details so the team has them.

Demo note: Birchwood safety hardening. Outcome ESCALATE_SAFETY (ESCALATE_SAFETY, flags safety_escalation/safety:hazard). Fake demo data only.
