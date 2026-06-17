"""Smoke tests for the offline Birchwood conversation simulator.

The Birchwood workflow is a MINIMAL pure-intake flow: it only collects what
a collision specialist needs and hands off. Outcomes are only
COMPLETED_INTAKE or INCOMPLETE_CALLBACK_NEEDED, and flags are descriptive
(needs_tow / private_pay / mpi_claim_in_hand / mpi_claim_open_no_number) —
there is no injury question, narrative capture, transfer, decline, or
readback in this flow."""

from scripts.simulate_birchwood_call import SCENARIOS, simulate


def test_completed_drivable_claim_scenario_completes():
    result = simulate("completed_drivable_claim")
    assert result["outcome"] == "COMPLETED_INTAKE"
    assert "mpi_claim_in_hand" in result["flags"]
    assert "SITUATION:" in result["shop_summary"]


def test_needs_tow_scenario_completes_and_flags():
    result = simulate("needs_tow")
    assert result["outcome"] == "COMPLETED_INTAKE"
    assert "needs_tow" in result["flags"]


def test_private_pay_scenario_completes_and_flags():
    result = simulate("completed_private_pay")
    assert result["outcome"] == "COMPLETED_INTAKE"
    assert "private_pay" in result["flags"]


def test_incomplete_missing_required_field_needs_callback():
    result = simulate("incomplete_missing_year")
    assert result["outcome"] == "INCOMPLETE_CALLBACK_NEEDED"


def test_all_scenarios_run_clean():
    for name in SCENARIOS:
        result = simulate(name)
        expected = SCENARIOS[name].get("expect_outcome")
        if expected:
            assert result["outcome"] == expected, name
        for flag in SCENARIOS[name].get("expect_flags", []):
            assert flag in result["flags"], (name, flag)
