from pathlib import Path


ASCII_ONLY_FILES = [
    Path("scripts/rename_reports.py"),
    Path("scripts/simulate_calls.py"),
]


def test_phase12_scripts_use_ascii_only_source_text():
    offenders: dict[str, list[str]] = {}

    for path in ASCII_ONLY_FILES:
        text = path.read_text(encoding="utf-8")
        codepoints = sorted({f"U+{ord(ch):04X}" for ch in text if ord(ch) > 127})
        if codepoints:
            offenders[str(path)] = codepoints

    assert offenders == {}
