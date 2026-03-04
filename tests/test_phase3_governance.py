"""
Phase 3 — Governance Tests

Tests for protocol status gating, schema validation, and governance enforcement.
"""

import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.governance.protocol_status import (
    VALID_STATUSES,
    filter_protocols_by_governance,
    validate_approved_protocols_exist,
    validate_protocol_schema,
)


# ---------------------------------------------------------------------------
# Protocol Schema Validation
# ---------------------------------------------------------------------------


class TestProtocolSchemaValidation:
    """Test protocol JSON governance schema validation."""

    def test_valid_protocol(self):
        """A fully valid protocol passes validation."""
        data = {
            "id": "PROTO-001",
            "title": "Test Protocol",
            "keywords": ["test"],
            "body": "Test body",
            "version": "1.0",
            "status": "approved",
            "effective_date": "2026-01-15",
            "reviewed_by": "Test Reviewer",
            "reviewed_at": "2026-01-14T10:00:00",
            "owner": "Test Department",
        }
        issues = validate_protocol_schema(data)
        assert len(issues) == 0

    def test_missing_required_fields(self):
        """Missing required fields are reported."""
        data = {"id": "PROTO-001"}
        issues = validate_protocol_schema(data)
        assert len(issues) >= 3  # Missing title, keywords, body, version

    def test_invalid_status(self):
        """Invalid status value is reported."""
        data = {
            "id": "PROTO-001",
            "title": "Test",
            "keywords": [],
            "body": "test",
            "version": "1.0",
            "status": "invalid_status",
        }
        issues = validate_protocol_schema(data)
        assert any("Invalid status" in i for i in issues)

    def test_invalid_effective_date(self):
        """Invalid date format is reported."""
        data = {
            "id": "PROTO-001",
            "title": "Test",
            "keywords": [],
            "body": "test",
            "version": "1.0",
            "effective_date": "not-a-date",
        }
        issues = validate_protocol_schema(data)
        assert any("effective_date" in i for i in issues)

    def test_valid_statuses(self):
        """All valid statuses pass validation."""
        for status in VALID_STATUSES:
            data = {
                "id": "PROTO-001",
                "title": "Test",
                "keywords": [],
                "body": "test",
                "version": "1.0",
                "status": status,
            }
            issues = validate_protocol_schema(data)
            assert not any("status" in i.lower() for i in issues)


# ---------------------------------------------------------------------------
# Protocol Status Gating
# ---------------------------------------------------------------------------


class TestProtocolStatusGating:
    """Test governance-based protocol filtering."""

    def test_production_only_approved(self):
        """In production mode, only approved protocols pass."""
        protocols = [
            {"id": "P1", "status": "approved"},
            {"id": "P2", "status": "draft"},
            {"id": "P3", "status": "deprecated"},
            {"id": "P4", "status": "approved"},
        ]
        result = filter_protocols_by_governance(protocols, environment="production")
        assert len(result) == 2
        assert all(p["status"] == "approved" for p in result)

    def test_development_loads_all(self):
        """In development mode, all protocols load."""
        protocols = [
            {"id": "P1", "status": "approved"},
            {"id": "P2", "status": "draft"},
            {"id": "P3", "status": "deprecated"},
        ]
        result = filter_protocols_by_governance(protocols, environment="development")
        assert len(result) == 3

    def test_missing_status_treated_as_approved(self):
        """Protocols without status field are treated as approved."""
        protocols = [
            {"id": "P1"},  # No status field
            {"id": "P2", "status": "draft"},
        ]
        result = filter_protocols_by_governance(protocols, environment="production")
        assert len(result) == 1
        assert result[0]["id"] == "P1"

    def test_empty_list(self):
        """Empty protocol list returns empty."""
        result = filter_protocols_by_governance([], environment="production")
        assert result == []


# ---------------------------------------------------------------------------
# Approved Protocols Existence Check
# ---------------------------------------------------------------------------


class TestApprovedProtocolsExist:
    """Test startup validation of approved protocols."""

    def test_production_no_approved_raises(self):
        """Production mode with no approved protocols raises RuntimeError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create one draft protocol
            proto = {
                "id": "P1",
                "title": "Draft",
                "keywords": [],
                "body": "test",
                "version": "1.0",
                "status": "draft",
            }
            with open(Path(tmpdir) / "test.json", "w") as f:
                json.dump(proto, f)

            with pytest.raises(RuntimeError, match="No approved protocols"):
                validate_approved_protocols_exist(
                    Path(tmpdir), environment="production"
                )

    def test_production_with_approved_passes(self):
        """Production mode with approved protocols passes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proto = {
                "id": "P1",
                "title": "Approved",
                "keywords": [],
                "body": "test",
                "version": "1.0",
                "status": "approved",
            }
            with open(Path(tmpdir) / "test.json", "w") as f:
                json.dump(proto, f)

            result = validate_approved_protocols_exist(
                Path(tmpdir), environment="production"
            )
            assert result is True

    def test_development_no_approved_ok(self):
        """Development mode with no approved protocols does NOT raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proto = {
                "id": "P1",
                "title": "Draft",
                "keywords": [],
                "body": "test",
                "version": "1.0",
                "status": "draft",
            }
            with open(Path(tmpdir) / "test.json", "w") as f:
                json.dump(proto, f)

            result = validate_approved_protocols_exist(
                Path(tmpdir), environment="development"
            )
            assert result is False

    def test_missing_directory_production_raises(self):
        """Missing protocol directory in production raises."""
        with pytest.raises(RuntimeError, match="Protocol directory not found"):
            validate_approved_protocols_exist(
                Path("/nonexistent/dir"), environment="production"
            )

    def test_missing_directory_development_ok(self):
        """Missing protocol directory in development returns False."""
        result = validate_approved_protocols_exist(
            Path("/nonexistent/dir"), environment="development"
        )
        assert result is False


# ---------------------------------------------------------------------------
# Integration with Retriever
# ---------------------------------------------------------------------------


class TestGovernanceRetrieverIntegration:
    """Test that the retriever respects governance filtering."""

    def test_retriever_loads_approved_protocols(self):
        """ProtocolRetriever loading respects approved status filter."""
        from src.protocols.retriever import load_protocols

        protocols = load_protocols()
        # All default protocols have status=approved, so all should load
        assert len(protocols) > 0

    def test_retriever_filters_in_production(self):
        """In production, only approved protocols load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Approved protocol
            p1 = {
                "id": "PROTO-APPROVED",
                "title": "Approved Protocol",
                "keywords": ["test"],
                "body": "test body",
                "disposition_notes": "test notes",
                "last_updated": "2026-01-15",
                "version": "1.0",
                "status": "approved",
            }
            # Draft protocol
            p2 = {
                "id": "PROTO-DRAFT",
                "title": "Draft Protocol",
                "keywords": ["draft"],
                "body": "draft body",
                "disposition_notes": "draft notes",
                "last_updated": "2026-01-15",
                "version": "1.0",
                "status": "draft",
            }
            with open(Path(tmpdir) / "approved.json", "w") as f:
                json.dump(p1, f)
            with open(Path(tmpdir) / "draft.json", "w") as f:
                json.dump(p2, f)

            from src.protocols.retriever import load_protocols

            with patch("src.governance.protocol_status.ENVIRONMENT", "production"):
                protocols = load_protocols(protocol_dir=tmpdir, apply_governance=True)

            ids = [p.id for p in protocols]
            assert "PROTO-APPROVED" in ids
            assert "PROTO-DRAFT" not in ids


# ---------------------------------------------------------------------------
# Config Validation Tests
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """Test centralized configuration validation."""

    def test_valid_config(self):
        """Valid config passes validation."""
        from src.config import validate_config

        with (
            patch("src.config.STORAGE_BACKEND", "memory"),
            patch("src.config.CONFIDENCE_MIN_THRESHOLD", 0.60),
            patch("src.config.REDFLAG_SCORE_THRESHOLD", 10),
            patch("src.config.ENVIRONMENT", "development"),
        ):
            errors = validate_config()
            assert len(errors) == 0

    def test_invalid_storage_backend(self):
        """Invalid STORAGE_BACKEND generates error."""
        from src.config import validate_config

        with (
            patch("src.config.STORAGE_BACKEND", "redis"),
            patch("src.config.CONFIDENCE_MIN_THRESHOLD", 0.60),
            patch("src.config.REDFLAG_SCORE_THRESHOLD", 10),
            patch("src.config.ENVIRONMENT", "development"),
        ):
            errors = validate_config()
            assert any("STORAGE_BACKEND" in e for e in errors)

    def test_postgres_without_url(self):
        """STORAGE_BACKEND=postgres without DATABASE_URL generates error."""
        from src.config import validate_config

        with (
            patch("src.config.STORAGE_BACKEND", "postgres"),
            patch("src.config.DATABASE_URL", None),
            patch("src.config.CONFIDENCE_MIN_THRESHOLD", 0.60),
            patch("src.config.REDFLAG_SCORE_THRESHOLD", 10),
            patch("src.config.ENVIRONMENT", "development"),
        ):
            errors = validate_config()
            assert any("DATABASE_URL" in e for e in errors)

    def test_old_env_var_backward_compat(self):
        """Old CONFIDENCE_THRESHOLD env var is accepted with deprecation."""
        import warnings

        old_env = {
            "CONFIDENCE_THRESHOLD": "0.70",
        }
        with patch.dict("os.environ", old_env, clear=False):
            # Remove new-name var if present
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("CONFIDENCE_MIN_THRESHOLD", None)
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    import src.config as cfg

                    val = cfg._env_with_deprecation(
                        "CONFIDENCE_MIN_THRESHOLD", "CONFIDENCE_THRESHOLD", "0.60"
                    )
                    assert float(val) == 0.70
                    assert len(w) >= 1
                    assert "deprecated" in str(w[0].message).lower()
