# Birchwood Collision Intake (Minimal) - PROVISIONAL choices

Everything in `birchwood_collision_intake_min_v1` is PROVISIONAL pending the Birchwood
discovery call (July). It is kept as editable config so it can change without code surgery.
ORCA is the platform; Birchwood Automotive Group is the target client.

## Confirm with Birchwood

1. **Required fields** (`COLLISION_MIN_REQUIRED_FIELDS` in `constants.py`): caller name,
   callback number, vehicle year/make/model, brief damage description, drivable status.
   Confirm this is the right minimum set.
2. **Optional fields** (`COLLISION_MIN_OPTIONAL_FIELDS`): plate, VIN, color, MPI claim
   opened?, MPI claim number, vehicle location. `vehicle_location` becomes conditionally
   required only when the vehicle is not drivable (for the tow). Confirm.
3. **MPI claim status is captured as data only** - opened yes/no + number if in hand. The
   agent makes no coverage/claim decision. Confirm the wording of the claim question.
4. **Handoff modes**: `READY_FOR_SPECIALIST` (warm transfer when available) vs.
   `CALLBACK_NEEDED` (a required detail is missing -> capture + flag callback). Confirm
   whether warm transfer is available in the Birchwood environment or it's always callback.
5. **No estimates / no advice**: the agent never answers cost, coverage, fault, or repair
   time - it defers to the specialist and continues. Confirm the deflection wording
   (`COLLISION_MIN_DEFLECTION_REPLY`).
6. **Reactive emergency reflex** is the SHARED platform baseline (injury advisory + flag +
   human handoff). The agent never asks about injuries. Confirm this is the desired baseline
   behavior for collision calls.
7. **Placeholder phone routing**: this workflow is not bound to a real Birchwood number; it
   is routable via `WORKFLOW_PHONE_ROUTES` for testing. Confirm the routing approach.

## Relationship to the live pilot

This minimal workflow is **separate** from the live pilot `birchwood_collision_intake_v1`
(the richer `automotive_collision` vertical, which also declines old/rebuilt vehicles, routes
glass/non-drivable, and does luxury routing). The two are intentionally **not converged**.
After the July call we will decide which rule set is canonical, update v1, and retire the
unused one.

## Cut-over: make the Birchwood line use THIS minimal workflow

By default the Birchwood number routes to the live pilot (`birchwood_collision_intake_v1`).
To point it at the minimal pure-intake workflow instead, set one env var in the **deployed**
environment - no code change, reversible by unsetting it:

```
WORKFLOW_PHONE_ROUTES={"<the deployed BIRCHWOOD_COLLISION_PHONE_NUMBER>": "birchwood_collision_intake_min_v1"}
```

Use the SAME E.164 value already configured as `BIRCHWOOD_COLLISION_PHONE_NUMBER`.
`WORKFLOW_PHONE_ROUTES` is checked before the built-in Birchwood route, so it overrides it.
Verified by `tests/test_birchwood_collision_min_routing.py`.

Full sequence to change the live line:
1. Merge this PR (registers `birchwood_collision_intake_min_v1`).
2. Deploy to the environment the Birchwood number points at.
3. Set the `WORKFLOW_PHONE_ROUTES` env var above and restart/redeploy.
4. To revert: unset `WORKFLOW_PHONE_ROUTES` (the line returns to the live pilot).

Note: until steps 1-3 are done, calling the Birchwood line still runs the live pilot flow.
This wiring is intentionally NOT enabled by default (the live pilot stays the line's behavior
until you choose to cut over).
