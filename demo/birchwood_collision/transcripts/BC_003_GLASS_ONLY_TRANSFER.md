# BC_003 Glass-Only Transfer

Windshield-only damage. The deterministic glass gate hands the caller to the
glass team after the intake. Regenerated from the live engine
(`python -m scripts.simulate_birchwood_call --scenario glass_only_transfer`).

ORCA: Thank you for calling Birchwood Automotive Group. This call may be recorded for training and quality purposes. I'm here to get your vehicle booked in and taken care of after your accident - we'll get you back on the road. And if you'd rather speak with one of our team right away, just say transfer or press 0. I'm sorry you're dealing with this - let's get your vehicle looked after. Whenever you're ready, take your time and walk me through what happened, from the beginning, in your own words. I'll listen first, and then ask a few quick follow-ups.

Caller: A rock flew up on the highway and cracked my windshield - it's just the glass, no other damage. The car still drives fine and nobody was hurt.

ORCA: Got it - go on, I'm listening. When you're finished, just say, that's everything.

Caller: that's everything

ORCA: Got it. I noted that. What year is the vehicle?

Caller: 2021

ORCA: And the make - like Toyota or Ford?

Caller: Honda

ORCA: What model is it?

Caller: Accord

ORCA: Almost done with the vehicle questions. Has it ever been written off and rebuilt? It would show as rebuilt or salvage on the title.

Caller: no

ORCA: When did this happen? Roughly is fine.

Caller: this morning

ORCA: For the repair, are you planning to go through insurance, or pay out of pocket? Either way's absolutely fine - we'll take care of the car either way.

Caller: paying out of pocket

ORCA: Now just a couple of details so we can take care of you. What's your full name?

Caller: Morgan Patel

ORCA: And what's the best phone number to reach you at?

Caller: 204 555 0103

ORCA: Wonderful - let me just make sure I've got everything right: a 2021 Honda Accord; still safe to drive; damage to the windshield, glass; it happened this morning; around the highway; paying privately; and your advisor should call Morgan Patel +12045550103. Did I get all of that right?

Caller: yes

ORCA: That sounds like glass-only damage, and our glass team takes care of those directly - let me get this over to them for you.

Demo note: Deterministic transfer to the glass department for glass-only damage. Outcome TRANSFER_GLASS_DEPARTMENT, flag glass_only_transfer. The exact glass department transfer process needs Birchwood stakeholder confirmation. Fake demo routing data only.
