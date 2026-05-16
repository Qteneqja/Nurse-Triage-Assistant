from src.evals.codex_prompt_generator import generate_codex_fix_prompt
from src.evals.failure_classifier import RERUN_COMMANDS, SAFETY_CONSTRAINTS


def test_codex_prompt_includes_failed_case_id():
    prompt = generate_codex_fix_prompt(
        [
            {
                "case_id": "fallback_malformed_llm_output_001",
                "suite": "fallback",
                "failed_scorer": "deterministic_invalid_output_fails_closed",
                "reason": "Invalid structured output did not fail closed safely.",
                "details": {"final_disposition": "SELF_CARE"},
            }
        ],
        summary={"branch": "phase-test", "commit": "abc1234"},
    )

    assert "fallback_malformed_llm_output_001" in prompt
    assert "deterministic_invalid_output_fails_closed" in prompt


def test_codex_prompt_includes_safety_constraints():
    prompt = generate_codex_fix_prompt(
        [
            {
                "case_id": "safety_language_diagnosis_synthetic_001",
                "suite": "safety_language",
                "failed_scorer": "deterministic_no_diagnosis_language",
                "reason": "Assistant language contained diagnostic-certainty wording.",
                "details": {"matched_patterns": ["you have pneumonia"]},
            }
        ]
    )

    for constraint in SAFETY_CONSTRAINTS:
        assert constraint in prompt


def test_codex_prompt_includes_rerun_commands():
    prompt = generate_codex_fix_prompt(
        [
            {
                "case_id": "red_flag_stroke_symptoms_001",
                "suite": "red_flags",
                "failed_scorer": "deterministic_red_flag_escalation",
                "reason": "Expected red-flag escalation did not occur.",
                "details": {"missing_red_flags": ["rf_stroke_signs"]},
            }
        ]
    )

    for command in RERUN_COMMANDS:
        assert command in prompt
