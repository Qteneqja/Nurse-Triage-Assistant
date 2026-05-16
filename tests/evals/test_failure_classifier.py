from src.evals.failure_classifier import classify_failure


def test_classifier_maps_premature_finalization_failure_correctly():
    classification = classify_failure(
        {
            "case_id": "abdominal_pain_premature_finalization_001",
            "suite": "premature_finalization",
            "failed_scorer": "deterministic_no_premature_finalization",
            "reason": "Routine finalization occurred before the minimum dynamic turn gate.",
            "details": {"finalized_at": 2, "min_turns": 4},
        }
    )

    assert classification.category == "premature_finalization"
    assert classification.severity == "critical"
    assert "src/verticals/healthcare/completeness.py" in classification.likely_files


def test_classifier_maps_red_flag_escalation_failure_correctly():
    classification = classify_failure(
        {
            "case_id": "red_flag_stroke_symptoms_001",
            "suite": "red_flags",
            "failed_scorer": "deterministic_red_flag_escalation",
            "reason": "Expected red-flag escalation did not occur.",
            "details": {"missing_red_flags": ["rf_stroke_signs"]},
        }
    )

    assert classification.category == "red_flag_escalation"
    assert classification.severity == "critical"
    assert "tests/test_red_flags.py" in classification.likely_files


def test_classifier_maps_sbar_failure_correctly():
    classification = classify_failure(
        {
            "case_id": "sbar_missing_fields_synthetic_001",
            "suite": "sbar",
            "failed_scorer": "deterministic_sbar_completeness",
            "reason": "Finalized healthcare session is missing SBAR fields.",
            "details": {"missing_sbar_fields": ["background"]},
        }
    )

    assert classification.category == "sbar_completeness"
    assert classification.severity == "high"
    assert "tests/evals/test_healthcare_sbar_eval.py" in classification.likely_files


def test_unknown_failure_gets_unknown_category_and_medium_severity():
    classification = classify_failure(
        {
            "case_id": "new_eval_case_001",
            "suite": "experimental",
            "failed_scorer": "deterministic_unmapped_metric",
            "reason": "An unmapped deterministic check failed.",
            "details": {"unexpected": True},
        }
    )

    assert classification.category == "unknown"
    assert classification.severity == "medium"
