"""Minimal Birchwood collision-intake vertical (pure intake -> specialist handoff).

A deliberately MINIMAL, separate workflow (birchwood_collision_intake_min_v1) that
collects only what a collision specialist needs and hands off to a human. It does
NOT estimate, decide, advise, triage, or adjudicate. It is independent of the
richer live pilot (automotive_collision / birchwood_collision_intake_v1), which is
left fully intact. The two are intentionally NOT converged; the canonical v1 will
be chosen after the Birchwood discovery call (July).
"""
