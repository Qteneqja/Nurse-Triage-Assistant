import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from src.verticals.insurance.constants import INSURANCE_FNOL_PHONE_PLACEHOLDER
from src.verticals.insurance.schemas import InsuranceClaimRecord
from src.verticals.insurance.workflow import InsuranceClaimsFnolWorkflow


ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = ROOT / "demo" / "insurance_fnol"
SCENARIOS_PATH = DEMO_ROOT / "scenarios.json"
EXPECTED_OUTPUTS_ROOT = DEMO_ROOT / "expected_outputs"
TRANSCRIPTS_ROOT = DEMO_ROOT / "transcripts"

REQUIRED_SCENARIO_FIELDS = {
    "scenario_id",
    "title",
    "claim_type",
    "caller_profile",
    "scripted_turns",
    "caller_answers",
    "expected_routing",
    "expected_key_fields",
    "expected_disclaimers",
    "demo_notes",
    "dashboard_summary",
    "expected_output_file",
}

PHONE_PATTERN = re.compile(
    r"(?<![\w-])(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)"
    r"\d{3}[\s.-]?\d{4}(?![\w-])"
)
FORBIDDEN_PROMISE_PATTERN = re.compile(
    r"\bcovered\b|\bapproved\b|guaranteed\s+payout|we\s+will\s+pay|"
    r"definitely\s+covered",
    re.IGNORECASE,
)
ALLOWED_CONTROL_CHARACTERS = {"\n", "\r", "\t"}
BIDI_CONTROL_CODEPOINTS = {
    0x061C,
    0x200E,
    0x200F,
    0x202A,
    0x202B,
    0x202C,
    0x202D,
    0x202E,
    0x2066,
    0x2067,
    0x2068,
    0x2069,
}
PHASE_13_1_TEXT_PATHS = [
    ROOT / "README.md",
    ROOT / "docs" / "insurance_fnol_vertical.md",
    ROOT / "scripts" / "run_insurance_demo.py",
    ROOT / "tests" / "test_insurance_demo_pack.py",
]


def test_insurance_demo_scenarios_file_exists_and_has_required_fields():
    scenarios = _load_scenarios()

    assert SCENARIOS_PATH.exists()
    assert len(scenarios) >= 7

    seen_ids: set[str] = set()
    for scenario in scenarios:
        assert REQUIRED_SCENARIO_FIELDS.issubset(scenario)
        assert scenario["scenario_id"] not in seen_ids
        seen_ids.add(scenario["scenario_id"])
        assert scenario["expected_routing"]
        assert scenario["scripted_turns"]
        assert scenario["expected_key_fields"]
        assert scenario["dashboard_summary"]


def test_insurance_demo_scenarios_use_fake_demo_data_only():
    scenarios = _load_scenarios()

    for scenario in scenarios:
        profile = scenario["caller_profile"]
        answers = scenario["caller_answers"]

        callback_number = profile.get("callback_number", "")
        assert callback_number.startswith("555-")

        policy_number = answers.get("policy_number")
        if policy_number:
            assert "DEMO" in policy_number

        loss_location = answers.get("loss_location")
        if loss_location:
            assert "Demo" in loss_location

    text = "\n".join(path.read_text(encoding="utf-8") for path in _demo_text_files())
    for match in PHONE_PATTERN.findall(text):
        assert _normalize_phone(match) == _normalize_phone(
            INSURANCE_FNOL_PHONE_PLACEHOLDER
        )


def test_expected_output_files_exist_and_match_insurance_schema():
    scenarios = _load_scenarios()
    extraction_fields = set(
        InsuranceClaimsFnolWorkflow().get_extraction_schema()["entities"]
    )

    for scenario in scenarios:
        output = _load_expected_output(scenario)
        InsuranceClaimRecord.model_validate(output)

        assert scenario["expected_routing"] == output["recommended_routing"]
        assert extraction_fields.issubset(output)
        assert output["workflow_id"] == "insurance_claims_fnol_v1"
        assert output["vertical"] == "insurance"
        assert output["disclaimers_given"]


def test_transcript_files_exist_and_avoid_forbidden_promises():
    transcripts = sorted(TRANSCRIPTS_ROOT.glob("*.md"))

    assert len(transcripts) >= 5

    for transcript in transcripts:
        text = transcript.read_text(encoding="utf-8")
        assert "**Assistant:**" in text
        assert "**Caller:**" in text
        assert not FORBIDDEN_PROMISE_PATTERN.search(text)


def test_broker_demo_script_and_one_pager_exist():
    assert (DEMO_ROOT / "BROKER_DEMO_SCRIPT.md").exists()
    assert (DEMO_ROOT / "INSURANCE_FNOL_ONE_PAGER.md").exists()


def test_phase_13_1_files_have_no_hidden_unicode_controls():
    findings = []

    for path in [*PHASE_13_1_TEXT_PATHS, *_demo_text_files()]:
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(keepends=True), 1):
            for column_number, character in enumerate(line, 1):
                if _is_hidden_unicode_control(character):
                    findings.append(
                        (
                            str(path.relative_to(ROOT)),
                            line_number,
                            column_number,
                            f"U+{ord(character):04X}",
                            unicodedata.name(character, "<no name>"),
                        )
                    )

    assert findings == []


def test_demo_pack_files_are_plain_ascii():
    findings = []

    for path in _demo_text_files():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(keepends=True), 1):
            for column_number, character in enumerate(line, 1):
                if ord(character) > 0x7F:
                    findings.append(
                        (
                            str(path.relative_to(ROOT)),
                            line_number,
                            column_number,
                            f"U+{ord(character):04X}",
                            unicodedata.name(character, "<no name>"),
                        )
                    )

    assert findings == []


def test_offline_insurance_demo_runner_can_run_sample_without_api_keys():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_insurance_demo.py",
            "--scenario",
            "water_damage_mitigation",
        ],
        cwd=ROOT,
        env=_offline_env(),
        text=True,
        capture_output=True,
        check=True,
    )

    assert "water_damage_mitigation" in result.stdout
    assert "URGENT_ADJUSTER_REVIEW" in result.stdout
    assert "mitigation_needed: True" in result.stdout


def _load_scenarios() -> list[dict]:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def _load_expected_output(scenario: dict) -> dict:
    path = EXPECTED_OUTPUTS_ROOT / scenario["expected_output_file"]
    assert path.exists()
    return json.loads(path.read_text(encoding="utf-8"))


def _demo_text_files() -> list[Path]:
    return [
        path
        for path in DEMO_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".md", ".txt"}
    ]


def _normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def _is_hidden_unicode_control(character: str) -> bool:
    if character in ALLOWED_CONTROL_CHARACTERS:
        return False
    codepoint = ord(character)
    return (
        unicodedata.category(character) in {"Cc", "Cf"}
        or codepoint in BIDI_CONTROL_CODEPOINTS
    )


def _offline_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_ACCOUNT_SID",
    ]:
        env.pop(key, None)
    return env
