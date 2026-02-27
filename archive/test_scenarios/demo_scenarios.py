from triage_b.schemas import IntakeState

# 3 hackathon demo scenarios:
# A) Non-urgent → routed away from ER
# B) Red-flag → immediate escalation
# C) Ambiguous → HUMAN_REVIEW

SCENARIOS = {
    "A_non_urgent": IntakeState(
        age_group="adult",
        chief_complaint="Mild sore throat and runny nose",
        symptom_category="other",
        onset="gradual",
        severity="mild",
        fainted_or_altered=False,
        fever_present=False,
        dehydration_signs=False,
    ),
    "B_red_flag": IntakeState(
        age_group="older_adult",
        chief_complaint="Having trouble breathing",
        symptom_category="breathing",
        onset="sudden",
        severity="severe",
        can_speak_full_sentences=False,  # red flag
    ),
    "C_ambiguous": IntakeState(
        age_group="adult",
        chief_complaint="Not feeling well, not sure what’s wrong",
        symptom_category="other",
        onset="unknown",
        severity="unknown",
    ),
}
