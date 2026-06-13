# BC_004 Vehicle Year Decline

A 2010 vehicle. After the intake, the deterministic vehicle-year gate gives a
short, warm decline. Regenerated from the live engine
(`python -m scripts.simulate_birchwood_call --scenario old_vehicle_decline`).

ORCA: Thank you for calling Birchwood Automotive Group. This call may be recorded for training and quality purposes. I'm here to get your vehicle booked in and taken care of after your accident - we'll get you back on the road. And if you'd rather speak with one of our team right away, just say transfer or press 0. I'm sorry you're dealing with this - let's get your vehicle looked after. Whenever you're ready, take your time and walk me through what happened, from the beginning, in your own words. I'll listen first, and then ask a few quick follow-ups.

Caller: Someone rear-ended me and dented the back bumper. The car still drives and no one was hurt.

ORCA: Got it - go on, I'm listening. When you're finished, just say, that's everything.

Caller: that's everything

ORCA: Got it. I noted that. What year is the vehicle?

Caller: 2010

ORCA: And the make - like Toyota or Ford?

Caller: Toyota

ORCA: What model is it?

Caller: Corolla

ORCA: Almost done with the vehicle questions. Has it ever been written off and rebuilt? It would show as rebuilt or salvage on the title.

Caller: no

ORCA: When did this happen? Roughly is fine.

Caller: yesterday

ORCA: And where did it happen? A street, intersection, or parking lot is perfect.

Caller: Main Street

ORCA: For the repair, are you planning to go through insurance, or pay out of pocket? Either way's absolutely fine - we'll take care of the car either way.

Caller: going through insurance

ORCA: If your insurance has given you a claim number, what is it? Totally fine if you don't have one yet - it just helps us coordinate your repair with them.

Caller: CLM-DEMO-4004

ORCA: Now just a couple of details so we can take care of you. What's your full name?

Caller: Casey Nguyen

ORCA: And what's the best phone number to reach you at?

Caller: 204 555 0104

ORCA: Wonderful - let me just make sure I've got everything right: a 2010 Toyota Corolla; still safe to drive; damage to the bumper; it happened yesterday; around Main Street; going through insurance with claim number CLM-DEMO-4004; and your advisor should call Casey Nguyen +12045550104. Did I get all of that right?

Caller: yes

ORCA: I really appreciate you calling. Unfortunately, our collision centers are only able to take vehicles from 2012 and newer. Thanks so much for thinking of Birchwood.

Demo note: Deterministic decline on the vehicle-year gate. Outcome DECLINED_VEHICLE_YEAR, flag vehicle_year_declined. The 2012-and-newer rule is a demo placeholder pending Birchwood confirmation. Fake demo data only.
