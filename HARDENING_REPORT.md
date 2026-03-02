# Production Hardening — Technical Report

**Repository:** Nurse-Triage-Assistant  
**Date:** 2026-03-02  
**Author:** GitHub Copilot (automated hardening)  
**Scope:** Controlled production hardening — pilot-ready. No clinical logic, safety thresholds, or orchestrator architecture modified.

---

## 1. Summary of Changes by Step

### Step 1 — Security Cleanup
- Hardened `.gitignore` with `*.env`, `test_secret*`, `secrets/` globs
- Removed hardcoded DeepSeek API key from `src/llm/config.py` (default now `""`)
- Updated `tests/test_llm_client.py` to use `os.getenv()` instead of hardcoded key
- Deleted `test_secret.txt` and ran `git rm --cached`
- Created `SECURITY_CLEANUP.md` documenting all findings
- Added "Secrets & Security" section to `README.md`

### Step 2 — Twilio Signature Enforcement & Health Endpoints
- Enhanced `src/security/twilio_signature.py` with security logging (source IP, endpoint), JSON error body, production enforcement
- Updated `/health` endpoint: returns `{"status": "ok", "timestamp": "<ISO8601>"}`
- Updated `/ready` endpoint: hides exception class names in production mode
- Added `twilio>=8.0.0` to `requirements.txt`
- Created `tests/test_step2_twilio_health.py` (7 tests)

### Step 3 — Golden-Call Regression Framework
- Created `tests/golden_calls/` framework with JSON Schema validation
- 5 golden-call case files covering: insufficient info, chest pain escalation, breathing difficulty, mild cough, confused caller
- Deterministic-only runner with LLM blocking (`install_llm_block()`)
- Parametrized pytest test file with severity-based assertions
- Created `tests/golden_calls/README.md`

### Step 4 — Sentry Error Monitoring
- Created `src/observability/sentry_integration.py` with:
  - `init_sentry()` — conditional init (no-op without `SENTRY_DSN`)
  - `_scrub_phi()` — before_send hook with 3-layer PHI defense
  - `set_sentry_context()` — safe tag attachment
  - `capture_llm_failure()`, `capture_json_validation_failure()`, `capture_db_failure()`, `add_safety_gate_breadcrumb()`
- Added capture points in `src/llm/client.py`, `src/storage/factory.py`, `src/orchestrator/orchestrator.py`
- Hooked `init_sentry()` into FastAPI lifespan in `src/main.py`
- Added `sentry-sdk>=1.40.0` to `requirements.txt`
- Created `MONITORING.md`
- Created `tests/test_step4_sentry.py` (8 tests)

### Step 5 — CI Pipeline
- Created `.github/workflows/ci.yml` with 3 jobs: lint (ruff), test (pytest + coverage), security-scan (bandit + safety)
- Added CI badge to `README.md`
- Added CI/CD + Monitoring sections to `README.md`

### Step 6 — Docker & Deployment Artifacts
- Updated `.dockerignore` with `test_secret*`, `secrets/`, `docs/`, `patch_result.txt`, `patch.json`, `.github/`
- Added `SENTRY_DSN` environment variable to `docker-compose.yml` and `docker-compose.prod.yml`
- Created `DEPLOYMENT.md` with full deployment guide

---

## 2. Files Modified

| File | Change Type | Step |
|------|-------------|------|
| `.gitignore` | Modified | 1 |
| `src/llm/config.py` | Modified (removed hardcoded key) | 1 |
| `tests/test_llm_client.py` | Modified (env var lookup) | 1 |
| `src/security/twilio_signature.py` | Modified (security logging, JSON error) | 2 |
| `src/main.py` | Modified (health/ready endpoints, Sentry init) | 2, 4 |
| `requirements.txt` | Modified (added twilio, sentry-sdk) | 2, 4 |
| `src/llm/client.py` | Modified (Sentry capture points) | 4 |
| `src/storage/factory.py` | Modified (Sentry DB failure capture) | 4 |
| `src/orchestrator/orchestrator.py` | Modified (Sentry safety breadcrumb) | 4 |
| `README.md` | Modified (security section, CI badge, CI/CD section, monitoring section) | 1, 5 |
| `tests/test_phase3_observability.py` | Modified (health status `"ok"` alignment) | 2* |
| `tests/test_phase5_infrastructure.py` | Modified (health status `"ok"` alignment) | 2* |
| `.dockerignore` | Modified (additional exclusions) | 6 |
| `docker-compose.yml` | Modified (SENTRY_DSN env var) | 6 |
| `docker-compose.prod.yml` | Modified (SENTRY_DSN env var) | 6 |

\* Pre-existing tests updated to match new `/health` response format.

---

## 3. Files Created

| File | Purpose | Step |
|------|---------|------|
| `SECURITY_CLEANUP.md` | Security audit report | 1 |
| `tests/test_step2_twilio_health.py` | Twilio sig + health endpoint tests | 2 |
| `tests/golden_calls/__init__.py` | Package init | 3 |
| `tests/golden_calls/schema.json` | JSON Schema for case validation | 3 |
| `tests/golden_calls/runner.py` | Deterministic test runner | 3 |
| `tests/golden_calls/test_golden_calls.py` | Parametrized golden-call tests | 3 |
| `tests/golden_calls/README.md` | Framework documentation | 3 |
| `tests/golden_calls/cases/case_001_insufficient_info.json` | Golden case | 3 |
| `tests/golden_calls/cases/case_002_chest_pain_escalation.json` | Golden case | 3 |
| `tests/golden_calls/cases/case_003_breathing_difficulty.json` | Golden case | 3 |
| `tests/golden_calls/cases/case_004_mild_cough_low_acuity.json` | Golden case | 3 |
| `tests/golden_calls/cases/case_005_confused_caller.json` | Golden case | 3 |
| `src/observability/sentry_integration.py` | Sentry integration + PHI scrubbing | 4 |
| `tests/test_step4_sentry.py` | Sentry integration tests | 4 |
| `MONITORING.md` | Monitoring & error tracking guide | 4 |
| `.github/workflows/ci.yml` | CI pipeline (lint, test, security) | 5 |
| `DEPLOYMENT.md` | Deployment guide | 6 |

---

## 4. Test Results

**Final test run: 531 passed, 0 failed.**

| Test File | Tests | Status |
|-----------|-------|--------|
| `tests/test_step2_twilio_health.py` | 7 | ✅ All pass |
| `tests/golden_calls/test_golden_calls.py` | 7 | ✅ All pass |
| `tests/test_step4_sentry.py` | 8 | ✅ All pass |
| All other existing tests | 509 | ✅ All pass |

---

## 5. Security Findings

| Finding | Severity | Status |
|---------|----------|--------|
| Hardcoded DeepSeek API key in `src/llm/config.py` | HIGH | ✅ FIXED — default changed to `""` |
| Hardcoded API key in `tests/test_llm_client.py` | MEDIUM | ✅ FIXED — uses `os.getenv()` |
| `test_secret.txt` with test API key | MEDIUM | ✅ DELETED + git rm --cached |
| API key in `SECURITY.md` documentation | LOW | ⚠️ Documented (safe: documentation, rotated key) |
| Secrets in git history (commit `a43cf3f`) | **BLOCKING** | ⛔ REQUIRES git history rewrite or rotation |

### BLOCKING ISSUE

The hardcoded DeepSeek API key (`sk-8749...a0e9`) and `test_secret.txt` content exist in git history. **Do NOT tag `v1.0.0-hardened` until one of:**
1. `git filter-repo` removes the commits containing secrets, OR
2. The API key is confirmed rotated/invalidated

See `SECURITY_CLEANUP.md` for full details.

---

## 6. What Was NOT Changed

Per the hardening scope rules:
- ❌ No clinical logic modified (red flags, scoring, thresholds)
- ❌ No orchestrator architecture changes (except adding 1 Sentry breadcrumb import)
- ❌ No safety thresholds modified (`CONFIDENCE_MIN_THRESHOLD`, `REDFLAG_SCORE_THRESHOLD`)
- ❌ No API route signatures changed
- ❌ No database schema changes
- ❌ No patient-facing messages modified

---

## 7. Known Limitations

1. **Git history contains secrets** — see Section 5 blocking issue
2. **Golden-call tests are deterministic-only** — LLM-backed mode not yet implemented (by design; requires `DISABLE_EXTERNAL_CALLS=0`)
3. **Ruff not yet installed** — CI lint job will fail until `ruff` config (`pyproject.toml` `[tool.ruff]` section) is added
4. **Sentry PHI scrubbing is defense-in-depth** — the primary safeguard is that capture functions never include PHI fields; `_scrub_phi` is a safety net

---

## 8. Deployment Checklist

- [ ] Rotate DeepSeek API key `sk-8749...a0e9` (BLOCKING)
- [ ] Set `SENTRY_DSN` in production environment
- [ ] Verify CI pipeline passes on `main` branch push
- [ ] Run `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` for production
- [ ] Verify `/health` returns `{"status": "ok", "timestamp": "..."}`
- [ ] Verify `/ready` returns `{"status": "ready", "database": "connected"}`

---

## 9. Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| Sentry disabled by default | Zero overhead when `SENTRY_DSN` unset; opt-in only |
| 3-layer PHI scrubbing | HIPAA defense-in-depth; any single layer failure is not catastrophic |
| Deterministic-only golden calls | LLM non-determinism makes assertion-based tests unreliable; deterministic mode tests the safety-critical path |
| Conservative escalation for UNDECIDED | In deterministic mode, any case without red flags defaults to `HUMAN_REVIEW` with `escalation_required=True` — fails safe |
| CI lint before test | Fast feedback; lint failures caught in seconds before the slower test suite |
| Security scan as parallel job | Non-blocking; reports uploaded as artifacts for review |

---

## 10. Recommended Next Steps

1. **Remediate git history** — `git filter-repo` to remove `sk-8749...a0e9` and `test_secret.txt` from history
2. **Add `pyproject.toml` ruff config** — lint rules for CI pipeline
3. **Expand golden-call suite** — add cases for allergic reaction, pediatric fever, pregnancy complications
4. **Enable Sentry alerting** — configure alert rules for LLM failure rate > 5%, DB connection failures
5. **Add load testing to CI** — `tests/load/` suite for pre-deployment validation
6. **Tag release** — `v1.0.0-hardened` after git history remediation
