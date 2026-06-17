# Birchwood Collision Intake (Minimal) - Demo Script

ORCA is our voice AI intake platform; Birchwood Automotive Group is the target client.
This is the **minimal pure-intake** workflow (`birchwood_collision_intake_min_v1`): it
collects only what a collision specialist needs and hands off to a human. It does **not**
estimate, decide, advise, triage, or adjudicate anything. All demo data is fake.

Run it offline (no Twilio, no LLM, no network):

```
python -m scripts.simulate_collision_min_call            # all scenarios
python -m scripts.simulate_collision_min_call --scenario asks_for_estimate
```

## What it captures (only what the specialist needs)

- Caller name + callback number
- Vehicle year / make / model (plate, VIN, color optional)
- A brief damage description
- MPI claim status: opened? yes/no; claim number if in hand (captured as data)
- Drivable, or needs a tow? (plus vehicle location if a tow is needed)

## The four demo scenarios

1. **Drivable + MPI claim in hand** -> full intake captured -> warm handoff to a specialist.
2. **Not drivable** -> intake plus the vehicle's location captured, tow flagged -> handoff.
3. **No claim yet** -> intake captured, "no MPI claim" flagged as data -> handoff.
4. **Caller asks "how much / will MPI cover it?"** -> the agent does **not** estimate or
   answer; it says the specialist will go over cost/coverage/timing, finishes the intake,
   and hands off.

## Built-in safety baseline (shared, not a collision feature)

The agent never asks about injuries. If a caller spontaneously says they are hurt or signals
a medical emergency, the shared platform reflex adds a brief "get medical attention / call
9 1 1 if urgent" advisory, flags the record, and routes to a human - then a person takes
over. (Try the `emergency_reflex` simulation scenario.)

## What it deliberately does NOT do

No repair estimates. No cost, coverage, or fault answers. No repair-time promises. No
declines, transfers, or routing decisions. Those are all left to the Birchwood specialist.

See `PROVISIONAL.md` for every choice to confirm on the July Birchwood discovery call.
