# Security Cleanup Report
# Purpose: Document all security findings and remediation from production hardening
# Date Created: 2026-03-02
# Step: Production Hardening â€” Step 1

**Date:** 2026-03-02

**May 2026 update:** production credential rotation has already been completed
by the operator. Do not rotate again for the current Azure remediation unless a
future compromise is discovered. Current work focuses on preventing future
plaintext exposure.

**Step:** Production Hardening â€” Step 1

## Findings

### Finding 1 â€” Hardcoded DeepSeek API Key in `src/llm/config.py`
- **File:** `src/llm/config.py`, line 12
- **Description:** `DEEPSEEK_API_KEY` had a hardcoded default value `sk-****...****` (redacted) as the fallback in `os.getenv()`. This means the key would be used if the environment variable was not set.
- **Severity:** HIGH
- **Action:** Replaced hardcoded default with empty string `""`. The key must now be provided via environment variable.

### Finding 2 â€” Hardcoded API Key in `tests/test_llm_client.py`
- **File:** `tests/test_llm_client.py`, line 8
- **Description:** The same DeepSeek API key was hardcoded as a literal string assignment `api_key = "sk-****...****"`.
- **Severity:** HIGH
- **Action:** Replaced with `os.getenv("DEEPSEEK_API_KEY", "")` with a guard that exits if not set.

### Finding 3 â€” `test_secret.txt` in Repository Root
- **File:** `test_secret.txt` (root)
- **Description:** File contained a fake string matching an API key pattern (`<fake-secret-pattern>`).
- **Severity:** MEDIUM (appeared to be a test/fake key, but presence normalizes committing secret-like files)
- **Action:** File deleted from working tree and removed from git tracking.

### Finding 4 â€” API Key Referenced in `SECURITY.md`
- **File:** `SECURITY.md`, line 41
- **Description:** The `git filter-repo` remediation command in SECURITY.md contains the actual API key value being replaced. This documents the key in plaintext.
- **Severity:** MEDIUM (already in git history; SECURITY.md is documenting remediation steps)
- **Action:** Documentation now uses placeholders for history cleanup. Production rotation has already been completed; future rotation is incident-driven.

### Finding 5 â€” `.env.example` Contains Placeholder Key Pattern
- **File:** `.env.example`, line 27
- **Description:** Contains `DEEPSEEK_API_KEY=sk-your-api-key-here`. This is a safe placeholder (not a real key).
- **Severity:** NONE â€” intentional example value.
- **Action:** No change needed.

## Git History Assessment

### `test_secret.txt`
- **Committed in:** `a43cf3f` ("test secret")
- **Status:** EXISTS in git history
- **Risk:** The file contained a fake secret-like value, not a real key.

### Hardcoded API key (DeepSeek, redacted)
- **Committed in:** Multiple commits (present in `src/llm/config.py`, `tests/test_llm_client.py`, `SECURITY.md`)
- **Status:** EXISTS in git history
- **Risk:** HIGH â€” this appears to be a real API key that was used for development

### Historical Cleanup Steps

> **Superseded (2026-06-10, PR 0):** the authoritative, verified procedure is
> now [docs/HISTORY_REWRITE_PROCEDURE.md](docs/HISTORY_REWRITE_PROCEDURE.md)
> (mirror-clone based, with pre-push verification and rollback). The steps
> below are kept for historical context only.

```bash
# 1. For a future incident, rotate the affected key first.
#    The May 2026 production rotation has already been completed.

# 2. Install git-filter-repo (if not already installed)
pip install git-filter-repo

# 3. From a FRESH clone of the repository:
git clone https://github.com/Qteneqja/Nurse-Triage-Assistant.git fresh-clone
cd fresh-clone

# 4. Remove test_secret.txt from all history
git filter-repo --invert-paths --path test_secret.txt --force

# 5. Replace the hardcoded API key in all history
#    Create a file called replacements.txt with:
#    <exposed-secret-value>==>REDACTED
git filter-repo --replace-text replacements.txt --force

# 6. Force-push to remote
git push --force --all
git push --force --tags

# 7. ALL collaborators must re-clone â€” their local copies still have old history
```

**WARNING:** All collaborators must delete their local clones and re-clone after history rewrite.

## Actions Taken

1. **`.gitignore` hardened:** Added `*.env` glob, `test_secret*` glob, `secrets/`, `node_modules/`
2. **`src/llm/config.py`:** Removed hardcoded API key default â€” now uses empty string fallback
3. **`tests/test_llm_client.py`:** Removed hardcoded API key â€” now reads from environment variable
4. **`test_secret.txt`:** Deleted from working tree and untracked from git
5. **Verified all other secrets** (`TWILIO_AUTH_TOKEN`, `DATABASE_URL`, `AZURE_STORAGE_CONNECTION_STRING`, etc.) are loaded from `os.getenv()` â€” no hardcoded values found

## Secrets Policy

The following must NEVER be committed to this repository:
- API keys (OpenAI, DeepSeek, Azure, Twilio, Sentry, any third-party)
- Database credentials or connection strings
- Private keys, certificates, or PEM files
- `.env` files
- Test files containing secrets
- Bearer tokens or SAS tokens

All secrets must be provided via environment variables or a `.env` file that is gitignored.

## Pre-commit Recommendation

A pre-commit hook for secret scanning (e.g., `detect-secrets` or `gitleaks`) should be enforced.
The `SECURITY.md` already documents pre-commit setup with gitleaks â€” this must be activated in the CI pipeline (Step 5).

## Blocking Issues

1. **HISTORY RISK - Secrets exist in git history.** A redacted DeepSeek API key reference is present in historical commits. Production credential rotation has already been completed by the operator; history rewrite or documented risk acceptance remains a governance task.

2. **BLOCKING â€” Git tag `v1.0.0-hardened` must NOT be applied** until history remediation is confirmed complete.
