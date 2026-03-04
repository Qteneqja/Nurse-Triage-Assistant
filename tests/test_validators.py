"""
Tests for JSON schema validation and repair utilities.

Covers:
- JSON extraction from raw LLM output
- Schema validation for IntakeTurnOutput and FinalizeOutput
- Default fallback objects
- Handling of malformed inputs
"""
import json

from src.orchestrator.validators import (
    extract_json,
    validate_against_schema,
    parse_and_validate,
    safe_intake_turn_default,
    safe_finalize_default,
)
from src.orchestrator.schemas import (
    IntakeTurnOutput,
    IntakeStatePatch,
    FinalizeOutput,
    DispositionCategory,
)


# -----------------------------------------------------------------------
# extract_json
# -----------------------------------------------------------------------

class TestExtractJson:
    def test_clean_json(self):
        raw = '{"key": "value", "number": 42}'
        result = extract_json(raw)
        assert result == {"key": "value", "number": 42}

    def test_json_with_markdown_fences(self):
        raw = '```json\n{"key": "value"}\n```'
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_json_with_surrounding_text(self):
        raw = 'Here is the result:\n{"key": "value"}\nDone!'
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_empty_input(self):
        assert extract_json("") is None
        assert extract_json(None) is None  # type: ignore
        assert extract_json("   ") is None

    def test_no_json(self):
        assert extract_json("This is just plain text.") is None

    def test_invalid_json(self):
        assert extract_json('{"key": broken}') is None

    def test_nested_json(self):
        raw = '{"outer": {"inner": [1, 2, 3]}, "flag": true}'
        result = extract_json(raw)
        assert result is not None
        assert result["outer"]["inner"] == [1, 2, 3]
        assert result["flag"] is True


# -----------------------------------------------------------------------
# validate_against_schema — IntakeTurnOutput
# -----------------------------------------------------------------------

class TestValidateIntakeTurnOutput:
    def test_valid_data(self):
        data = {
            "extracted_fields_update": {"chief_complaint": "headache"},
            "missing_fields_prioritized": ["onset_time", "symptom_severity"],
            "next_question": "How long have you had this headache?",
            "llm_safety_flags": [],
            "confidence": 0.3,
        }
        obj, err = validate_against_schema(data, IntakeTurnOutput)
        assert err is None
        assert obj is not None
        assert obj.next_question == "How long have you had this headache?"
        assert obj.confidence == 0.3

    def test_minimal_data_uses_defaults(self):
        data = {
            "next_question": "What happened?",
        }
        obj, err = validate_against_schema(data, IntakeTurnOutput)
        assert err is None
        assert obj is not None
        assert isinstance(obj.extracted_fields_update, IntakeStatePatch)
        assert obj.extracted_fields_update.model_dump(exclude_none=True) == {}
        assert obj.confidence == 0.0

    def test_missing_required_field(self):
        data = {
            "extracted_fields_update": {},
            "confidence": 0.5,
            # missing next_question
        }
        obj, err = validate_against_schema(data, IntakeTurnOutput)
        assert obj is None
        assert err is not None
        assert "next_question" in err

    def test_confidence_out_of_range(self):
        data = {
            "next_question": "How are you?",
            "confidence": 1.5,  # > 1.0
        }
        obj, err = validate_against_schema(data, IntakeTurnOutput)
        assert obj is None
        assert err is not None


# -----------------------------------------------------------------------
# validate_against_schema — FinalizeOutput
# -----------------------------------------------------------------------

class TestValidateFinalizeOutput:
    def test_valid_finalize(self):
        data = {
            "disposition": "ER_NOW",
            "disposition_reasoning": "Severe chest pain with radiation",
            "safety_net_instructions": "Call 911 if symptoms worsen",
            "sbar_report": "S: Patient presents with...\nB: ...\nA: ...\nR: ...",
            "patient_summary": "Please call 911 immediately.",
            "llm_safety_flags": ["chest pain with radiation to arm"],
        }
        obj, err = validate_against_schema(data, FinalizeOutput)
        assert err is None
        assert obj is not None
        assert obj.disposition == DispositionCategory.ER_NOW

    def test_invalid_disposition(self):
        data = {
            "disposition": "INVALID_VALUE",
            "disposition_reasoning": "test",
            "safety_net_instructions": "test",
            "sbar_report": "test",
            "patient_summary": "test",
        }
        obj, err = validate_against_schema(data, FinalizeOutput)
        assert obj is None
        assert err is not None


# -----------------------------------------------------------------------
# parse_and_validate (end-to-end)
# -----------------------------------------------------------------------

class TestParseAndValidate:
    def test_clean_json_string(self):
        raw = json.dumps({
            "next_question": "Tell me more",
            "extracted_fields_update": {},
            "missing_fields_prioritized": [],
            "llm_safety_flags": [],
            "confidence": 0.2,
        })
        obj, err = parse_and_validate(raw, IntakeTurnOutput)
        assert err is None
        assert obj is not None

    def test_markdown_wrapped(self):
        raw = "```json\n" + json.dumps({
            "next_question": "How bad is it?",
            "confidence": 0.4,
        }) + "\n```"
        obj, err = parse_and_validate(raw, IntakeTurnOutput)
        assert err is None
        assert obj is not None
        assert obj.next_question == "How bad is it?"

    def test_totally_invalid(self):
        obj, err = parse_and_validate("Not JSON at all", IntakeTurnOutput)
        assert obj is None
        assert err is not None
        assert "No valid JSON" in err


# -----------------------------------------------------------------------
# Safe defaults
# -----------------------------------------------------------------------

class TestSafeDefaults:
    def test_intake_default(self):
        result = safe_intake_turn_default()
        assert isinstance(result, IntakeTurnOutput)
        assert result.confidence == 0.0
        assert len(result.next_question) > 0

    def test_intake_default_custom_question(self):
        result = safe_intake_turn_default("Custom fallback question?")
        assert result.next_question == "Custom fallback question?"

    def test_finalize_default(self):
        result = safe_finalize_default()
        assert isinstance(result, FinalizeOutput)
        assert result.disposition == DispositionCategory.HUMAN_REVIEW
        assert "nurse" in result.patient_summary.lower() or "review" in result.patient_summary.lower()
