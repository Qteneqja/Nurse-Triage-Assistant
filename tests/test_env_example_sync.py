"""
PR 0 — Security Close-Out: keep .env.example in sync with the code.

Guards the audit result that every environment variable the application reads
is documented in .env.example with a safe placeholder, and that .env.example
does not accumulate dead entries nothing reads. If either test fails, update
.env.example (or the exception sets below, with justification) in the same PR
that changed the env surface.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Set by the CI platform, never by a deployer — not part of .env.example.
CI_PROVIDED = {"GITHUB_HEAD_REF", "GITHUB_REF_NAME", "GITHUB_SHA"}

# Documented for docker-compose host substitution; not read by src/.
COMPOSE_ONLY = {"POSTGRES_PASSWORD"}

_READ_PATTERNS = [
    r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]+)["\']',
    r'os\.environ\.get\(\s*["\']([A-Z][A-Z0-9_]+)["\']',
    r'os\.environ\[\s*["\']([A-Z][A-Z0-9_]+)["\']\]',
    r'_env_flag\(\s*\n?\s*["\']([A-Z][A-Z0-9_]+)["\']',
]
# Both arguments name env vars: the current name and its deprecated alias.
_DEPRECATION_PATTERN = (
    r'_env_with_deprecation\(\s*["\']([A-Z][A-Z0-9_]+)["\'],'
    r'\s*["\']([A-Z][A-Z0-9_]+)["\']'
)


def _vars_read_by_src() -> set[str]:
    names: set[str] = set()
    for path in (REPO_ROOT / "src").rglob("*.py"):
        source = path.read_text(encoding="utf-8", errors="replace")
        for pattern in _READ_PATTERNS:
            names.update(re.findall(pattern, source))
        for pair in re.findall(_DEPRECATION_PATTERN, source):
            names.update(pair)
    return names


def _vars_documented_in_env_example() -> set[str]:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    # Matches both live entries (NAME=value) and deliberately commented-out
    # deprecated entries (# NAME=).
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", text, re.M))


def test_every_env_var_read_by_src_is_documented():
    missing = _vars_read_by_src() - _vars_documented_in_env_example() - CI_PROVIDED
    assert not missing, (
        f".env.example is missing env vars read by src/: {sorted(missing)}. "
        "Document each with a safe placeholder (never a real secret)."
    )


def test_every_documented_env_var_is_read_somewhere():
    dead = _vars_documented_in_env_example() - _vars_read_by_src() - COMPOSE_ONLY
    assert not dead, (
        f".env.example documents env vars nothing in src/ reads: {sorted(dead)}. "
        "Remove them or add them to COMPOSE_ONLY with justification."
    )
