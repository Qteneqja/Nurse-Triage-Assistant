import json
import unicodedata
from pathlib import Path


DEMO_DIR = Path("demo/birchwood_collision")
EXPECTED_DIR = DEMO_DIR / "expected_outputs"
TRANSCRIPTS_DIR = DEMO_DIR / "transcripts"
CHECKLIST_PATH = Path("docs/BIRCHWOOD_CALL_TEST_CHECKLIST.md")

FORBIDDEN_PHRASES = [
    "ORCA is " + "Birchwood",
    "Birchwood owns " + "ORCA",
    "repair estimate " + "guaranteed",
    "coverage " + "approved",
    "claim " + "approved",
    "we will " + "pay",
    "definitely " + "covered",
]


def test_demo_folder_exists():
    assert DEMO_DIR.exists()
    assert EXPECTED_DIR.exists()
    assert TRANSCRIPTS_DIR.exists()


def test_at_least_8_scenarios_exist():
    scenarios = _scenarios()

    assert len(scenarios) >= 8
    for scenario in scenarios:
        assert scenario["workflow_id"] == "birchwood_collision_intake_v1"
        assert scenario["vertical"] == "automotive_collision"
        assert scenario["powered_by"] == "ORCA"
        assert scenario["client_target"] == "Birchwood Automotive Group"


def test_expected_output_files_exist():
    for scenario in _scenarios():
        expected_file = EXPECTED_DIR / f"{scenario['scenario_id']}.json"
        assert expected_file.exists(), scenario["scenario_id"]
        payload = json.loads(expected_file.read_text(encoding="utf-8"))
        for field_name in [
            "workflow_id",
            "vertical",
            "powered_by",
            "client_target",
            "caller_name",
            "phone",
            "vehicle_year",
            "vehicle_make",
            "vehicle_model",
            "is_drivable",
            "damage_type",
            "filing_insurance_claim",
            "flags",
            "missing_information",
            "recommended_routing",
            "callback_needed",
            "disclaimers_given",
            "confidence",
            "human_review_required",
        ]:
            assert field_name in payload


def test_transcripts_exist():
    transcripts = list(TRANSCRIPTS_DIR.glob("*.md"))

    assert len(transcripts) >= 5
    for transcript in transcripts:
        text = transcript.read_text(encoding="utf-8")
        assert "ORCA" in text
        assert "Birchwood" in text
        assert "diagnosis" not in text.lower()
        assert "patient" not in text.lower()
        assert "sbar" not in text.lower()


def test_no_real_production_phone_numbers_and_placeholder_preserved():
    text = _demo_text()

    assert "BIRCHWOOD_COLLISION_PHONE_NUMBER=+15555550140" in text
    assert "__BIRCHWOOD_COLLISION_PHONE_NUMBER_PLACEHOLDER__" not in text
    assert "204-661" not in text
    assert "204-831" not in text
    assert "204-837" not in text


def test_no_hidden_bidi_or_control_unicode():
    for path in _demo_files():
        text = path.read_text(encoding="utf-8")
        for char in text:
            category = unicodedata.category(char)
            assert category not in {"Cf", "Cc"} or char in {"\n", "\r", "\t"}, path


def test_plain_ascii_where_practical():
    for path in _demo_files():
        text = path.read_text(encoding="utf-8")
        assert text.isascii(), path


def test_orca_not_described_as_birchwood_owned():
    text = _demo_text()

    assert "ORCA is the platform" in text
    assert "Birchwood Automotive Group is the target client" in text
    assert "ORCA is " + "Birchwood" not in text
    assert "Birchwood owns " + "ORCA" not in text


def test_docs_use_powered_by_orca_or_equivalent():
    text = _demo_text()

    assert "powered by ORCA" in text or "Powered by: ORCA" in text
    assert "ORCA is our voice AI intake platform" in text


def test_demo_data_is_fake():
    text = _demo_text()

    assert "demo_only" in text
    assert "CLM-DEMO" in text
    assert "@example.com" in text


def test_forbidden_phrases_do_not_appear():
    text = _demo_text()

    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in text


def test_demo_materials_reflect_refined_birchwood_voice_ux():
    text = _demo_text().lower()

    assert "take your time" in text
    assert "press 0" in text
    assert "main details noted" in text
    assert "doesn't confirm coverage, pricing, or an appointment yet" in text


def test_manual_call_checklist_exists_and_covers_narrative_cases():
    assert CHECKLIST_PATH.exists()

    text = CHECKLIST_PATH.read_text(encoding="utf-8").lower()
    assert "20-30 second collision story" in text
    assert "pause briefly mid-story" in text
    assert "twilio call sid" in text
    assert "cut-off happened" in text
    assert "fix needed yes/no" in text


def _scenarios() -> list[dict]:
    return json.loads((DEMO_DIR / "scenarios.json").read_text(encoding="utf-8"))


def _demo_files() -> list[Path]:
    return [
        path
        for path in DEMO_DIR.rglob("*")
        if path.is_file() and path.suffix in {".json", ".md"}
    ]


def _demo_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in _demo_files())
