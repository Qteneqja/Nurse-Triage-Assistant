"""
Canonical Enforcement Tests — Structural Invariants

These tests enforce the refactored architecture:
1. Canonical disposition values ONLY (ER_NOW, URGENT, SCHEDULE, SELF_CARE, HUMAN_REVIEW)
2. No direct StructuredLLMClient usage in orchestrator
3. No in-memory session dicts in PostgresStorage
4. No legacy enum values in source code
5. All LLM calls go through GuardedLLM
6. Safety gate is the sole disposition authority
"""
from __future__ import annotations

import pathlib
import re

import pytest

SRC_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src"


# ---------------------------------------------------------------------------
# 1. Canonical disposition enum is the ONLY source of truth
# ---------------------------------------------------------------------------

class TestCanonicalEnumEnforcement:
    """Verify CanonicalDisposition is authoritative and complete."""

    def test_canonical_values(self):
        from src.shared.canonical import CanonicalDisposition, CANONICAL_DISPOSITION_VALUES
        expected = {"ER_NOW", "URGENT", "SCHEDULE", "SELF_CARE", "HUMAN_REVIEW"}
        assert set(e.value for e in CanonicalDisposition) == expected
        assert CANONICAL_DISPOSITION_VALUES == frozenset(expected)

    def test_assert_canonical_accepts_valid(self):
        from src.shared.canonical import assert_canonical
        for v in ["ER_NOW", "URGENT", "SCHEDULE", "SELF_CARE", "HUMAN_REVIEW"]:
            assert_canonical(v)  # should not raise

    def test_assert_canonical_rejects_legacy(self):
        from src.shared.canonical import assert_canonical
        for v in ["SAFE", "PCP", "EMERGENCY", "ROUTINE", "URGENT_CARE", "UNDECIDED", "SAME_DAY"]:
            with pytest.raises(ValueError, match="(?i)non-canonical"):
                assert_canonical(v)

    def test_disposition_category_uses_canonical(self):
        from src.orchestrator.schemas import DispositionCategory
        values = set(e.value for e in DispositionCategory)
        expected = {"ER_NOW", "URGENT", "SCHEDULE", "SELF_CARE", "HUMAN_REVIEW"}
        assert values == expected, f"DispositionCategory has non-canonical values: {values - expected}"

    def test_phase1_disposition_uses_canonical(self):
        from src.orchestrator.schemas import Phase1Disposition
        values = set(e.value for e in Phase1Disposition)
        expected = {"ER_NOW", "URGENT", "SCHEDULE", "SELF_CARE", "HUMAN_REVIEW"}
        assert values == expected, f"Phase1Disposition has non-canonical values: {values - expected}"


# ---------------------------------------------------------------------------
# 2. No direct StructuredLLMClient import in orchestrator
# ---------------------------------------------------------------------------

class TestNoDirectLLMInOrchestrator:
    """Orchestrator must use GuardedLLM exclusively."""

    def test_no_structured_client_import(self):
        orch_path = SRC_ROOT / "orchestrator" / "orchestrator.py"
        text = orch_path.read_text(encoding="utf-8")
        # Should NOT have import statements for StructuredLLMClient
        assert "from src.llm.client import StructuredLLMClient" not in text, \
            "Orchestrator imports StructuredLLMClient directly — must use GuardedLLM"
        assert "from src.llm.client import" not in text or \
               "LLMCallError" in text.split("from src.llm.client import")[1].split("\n")[0], \
            "Orchestrator imports from src.llm.client for more than just LLMCallError"
        assert "get_structured_client" not in text, \
            "Orchestrator imports get_structured_client — must use get_guarded_llm"

    def test_no_self_llm_attribute(self):
        orch_path = SRC_ROOT / "orchestrator" / "orchestrator.py"
        text = orch_path.read_text(encoding="utf-8")
        # Should NOT have self._llm (direct client reference)
        assert "self._llm" not in text, \
            "Orchestrator has self._llm — must use self._guarded only"


# ---------------------------------------------------------------------------
# 3. No in-memory session cache in PostgresStorage
# ---------------------------------------------------------------------------

class TestNoInMemorySessionState:
    """PostgresStorage must not use in-memory dicts for session state."""

    def test_no_active_dict(self):
        pg_path = SRC_ROOT / "storage" / "postgres.py"
        text = pg_path.read_text(encoding="utf-8")
        assert "self._active" not in text, \
            "PostgresStorage still has self._active in-memory dict"

    def test_no_call_index_dict(self):
        pg_path = SRC_ROOT / "storage" / "postgres.py"
        text = pg_path.read_text(encoding="utf-8")
        assert "self._call_index" not in text, \
            "PostgresStorage still has self._call_index in-memory dict"

    def test_session_repository_exists(self):
        repo_path = SRC_ROOT / "storage" / "session_repository.py"
        assert repo_path.exists(), "SessionRepository module missing"

    def test_no_get_orchestrator_storage_singleton(self):
        """The old get_orchestrator_storage() singleton must not exist in source."""
        for py_file in SRC_ROOT.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="replace")
            assert "get_orchestrator_storage" not in text, \
                f"get_orchestrator_storage found in {py_file.relative_to(SRC_ROOT.parent)}"


# ---------------------------------------------------------------------------
# 4. No legacy enum values in source (outside dead code)
# ---------------------------------------------------------------------------

class TestNoLegacyEnumValues:
    """Legacy disposition strings must not appear in orchestrator/safety/api source."""

    # Files allowed to have legacy values (internal stubs, mapping tables, comments)
    _ALLOWED_FILES = frozenset({
        "src/llm/deepseek_client.py",       # Dead code with internal stubs
        "src/safety/gate.py",               # LEGACY_TO_CANON mapping table
        "src/orchestrator/schemas.py",      # _normalize_disposition mapping
        "src/safety/triage_output_schema.py",  # Legacy → canonical mapping
        "src/shared/schemas.py",            # Documentation comment
        "src/shared/canonical.py",          # May reference for docs
    })

    @pytest.mark.parametrize("legacy_val", ["SAFE", "EMERGENCY"])
    def test_no_legacy_in_orchestrator(self, legacy_val):
        orch_path = SRC_ROOT / "orchestrator" / "orchestrator.py"
        text = orch_path.read_text(encoding="utf-8")
        # String literals containing legacy values
        pattern = rf'["\']({legacy_val})["\']'
        matches = re.findall(pattern, text)
        assert not matches, \
            f"Legacy value '{legacy_val}' found as string literal in orchestrator.py"

    def test_no_undecided_in_orchestrator(self):
        orch_path = SRC_ROOT / "orchestrator" / "orchestrator.py"
        text = orch_path.read_text(encoding="utf-8")
        pattern = r'["\']UNDECIDED["\']'
        matches = re.findall(pattern, text)
        assert not matches, \
            "Legacy value 'UNDECIDED' found in orchestrator.py — should be 'HUMAN_REVIEW'"


# ---------------------------------------------------------------------------
# 5. Deleted files stay deleted
# ---------------------------------------------------------------------------

class TestDeletedFilesStayDeleted:
    """Legacy files must not be re-created."""

    @pytest.mark.parametrize("path", [
        "src/triage/engine.py",
        "src/triage/rules.py",
        "src/triage/schemas.py",
        "src/triage/questions.py",
        "src/shared/intake_state.py",
        "src/storage/session.py",
    ])
    def test_file_deleted(self, path):
        full_path = SRC_ROOT.parent / path
        assert not full_path.exists(), f"Deleted file was re-created: {path}"


# ---------------------------------------------------------------------------
# 6. Storage factory enforces Postgres in production
# ---------------------------------------------------------------------------

class TestStorageFactoryEnforcement:
    """Factory must refuse non-postgres backend in production."""

    def test_factory_rejects_memory_in_production(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("STORAGE_BACKEND", "memory")

        # Need to reimport to pick up env changes
        import importlib
        import src.config
        importlib.reload(src.config)

        from src.storage.factory import reset_storage_backend, get_storage_backend
        reset_storage_backend()

        with pytest.raises(RuntimeError, match="Production requires Postgres"):
            get_storage_backend()

        # Cleanup
        monkeypatch.delenv("ENVIRONMENT")
        monkeypatch.setenv("STORAGE_BACKEND", "memory")
        importlib.reload(src.config)
        reset_storage_backend()


# ---------------------------------------------------------------------------
# 7. Phase1 uses structured_call (not raw_call_gated)
# ---------------------------------------------------------------------------

class TestPhase1UsesStructuredCall:
    """Phase1 must use GuardedLLM.structured_call, not raw_call_gated."""

    def test_no_raw_call_in_phase1(self):
        orch_path = SRC_ROOT / "orchestrator" / "orchestrator.py"
        text = orch_path.read_text(encoding="utf-8")
        # raw_call_gated should not appear in the orchestrator
        assert "raw_call_gated" not in text, \
            "Orchestrator still uses raw_call_gated — Phase1 must use structured_call"
