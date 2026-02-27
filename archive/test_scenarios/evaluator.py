from triage_b.engine import triage_step
from scenarios_b.demo_scenarios import SCENARIOS

EXPECTED = {
    "A_non_urgent": "SAFE",
    "B_red_flag": "HUMAN_REVIEW",
    "C_ambiguous": "HUMAN_REVIEW",
}

def main():
    print("=== Triage Engine Smoke Test ===")
    all_ok = True

    for name, state in SCENARIOS.items():
        decision = triage_step(state)
        expected = EXPECTED[name]
        got = decision.triage_disposition

        ok = (got == expected)
        all_ok = all_ok and ok

        print(f"\nScenario: {name}")
        print(f"  expected: {expected}")
        print(f"  got     : {got}")
        print(f"  confidence: {decision.confidence}")
        print(f"  stop_intake: {decision.stop_intake}")
        print(f"  red_flags: {decision.red_flags}")
        print(f"  next_question: {decision.next_question}")
        print(f"  rationale: {decision.rationale_bullets}")
        print(f"  PASS: {ok}")

    print("\n=== RESULT ===")
    print("PASS" if all_ok else "FAIL (one or more scenarios mismatched)")

if __name__ == "__main__":
    main()
