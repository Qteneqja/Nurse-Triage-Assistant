"""Smoke tests for the offline Birchwood conversation simulator (PR 2 DoD:
the full flow — including the injury branch — runs end-to-end in the
simulation runner)."""

from scripts.simulate_birchwood_call import SCENARIOS, simulate


def test_rich_story_scenario_completes_with_minimal_questions():
    result = simulate("rich_story_completed")
    assert result["outcome"] == "COMPLETED_INTAKE"
    assert "injuries_denied" in result["flags"]
    # Story + cue + rebuilt + name + phone + readback — gap-fill stays lean.
    assert result["questions_asked"] <= 7
    prefilled = {e["field"] for e in result["narrative_extraction"]["prefilled"]}
    assert {"vehicle_make", "incident_location", "insurance_provider"} <= prefilled
    assert "SITUATION:" in result["shop_summary"]


def test_injury_branch_scenario_advises_and_flags():
    result = simulate("injury_branch")
    assert result["injuries_state"] == "reported"
    assert result["flags"][0] == "injuries_reported"
    spoken = " ".join(
        text for role, text in result["transcript"] if role == "assistant"
    )
    assert "9 1 1" in spoken
    # Advisory is spoken exactly once per call.
    assert spoken.count("Most importantly") == 1


def test_all_scenarios_run_clean():
    for name in SCENARIOS:
        result = simulate(name)
        expected = SCENARIOS[name].get("expect_outcome")
        if expected:
            assert result["outcome"] == expected, name
        for flag in SCENARIOS[name].get("expect_flags", []):
            assert flag in result["flags"], (name, flag)
