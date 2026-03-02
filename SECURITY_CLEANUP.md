# Security Cleanup Report
# Purpose: Document all security findings and remediation from production hardening
# Date Created: 2026-03-02
# Step: Production Hardening — Step 1

**Date:** 2026-03-02
**Step:** Production Hardening — Step 1

## Findings

### Finding 1 — Hardcoded DeepSeek API Key in `src/llm/config.py`
- **File:** `src/llm/config.py`, line 12
- **Description:** `DEEPSEEK_API_KEY` had a hardcoded default value `sk-****...****` (redacted) as the fallback in `os.getenv()`. This means the key would be used if the environment variable was not set.
- **Severity:** HIGH
- **Action:** Replaced hardcoded default with empty string `""`. The key must now be provided via environment variable.

### Finding 2 — Hardcoded API Key in `tests/test_llm_client.py`
- **File:** `tests/test_llm_client.py`, line 8
- **Description:** The same DeepSeek API key was hardcoded as a literal string assignment `api_key = "sk-****...****"`.
- **Severity:** HIGH
- **Action:** Replaced with `os.getenv("DEEPSEEK_API_KEY", "")` with a guard that exits if not set.

### Finding 3 — `test_secret.txt` in Repository Root
- **File:** `test_secret.txt` (root)
- **Description:** File contained a string matching an API key pattern (`sk-12345678901234567890`).
- **Severity:** MEDIUM (appeared to be a test/fake key, but presence normalizes committing secret-like files)
- **Action:** File deleted from working tree and removed from git tracking.

### Finding 4 — API Key Referenced in `SECURITY.md`
- **File:** `SECURITY.md`, line 41
- **Description:** The `git filter-repo` remediation command in SECURITY.md contains the actual API key value being replaced. This documents the key in plaintext.
- **Severity:** MEDIUM (already in git history; SECURITY.md is documenting remediation steps)
- **Action:** No change — this is a documentation reference for history cleanup. The key MUST be rotated regardless (see Blocking Issues).

### Finding 5 — `.env.example` Contains Placeholder Key Pattern
- **File:** `.env.example`, line 27
- **Description:** Contains `DEEPSEEK_API_KEY=sk-your-api-key-here`. This is a safe placeholder (not a real key).
- **Severity:** NONE — intentional example value.
- **Action:** No change needed.

## Git History Assessment

### `test_secret.txt`
- **Committed in:** `a43cf3f` ("test secret")
- **Status:** EXISTS in git history
- **Risk:** The file contained `sk-12345678901234567890` (likely a test value, not a real key)

### Hardcoded API key `sk-8749...a0e9` (DeepSeek)
- **Committed in:** Multiple commits (present in `src/llm/config.py`, `tests/test_llm_client.py`, `SECURITY.md`)
- **Status:** EXISTS in git history
- **Risk:** HIGH — this appears to be a real API key that was used for development

### Remediation Steps (MUST be performed before pilot)

```bash
# 1. ROTATE THE KEY FIRST — purging history does NOT invalidate stolen keys
#    Go to https://platform.deepseek.com/api_keys and regenerate the key

# 2. Install git-filter-repo (if not already installed)
pip install git-filter-repo

# 3. From a FRESH clone of the repository:
git clone https://github.com/Qteneqja/Nurse-Triage-Assistant.git fresh-clone
cd fresh-clone

# 4. Remove test_secret.txt from all history
git filter-repo --invert-paths --path test_secret.txt --force

# 5. Replace the hardcoded API key in all history
#    Create a file called replacements.txt with:
#    sk-8749f1c8dbe24ba096505d5ae758a0e9==>REDACTED
git filter-repo --replace-text replacements.txt --force

# 6. Force-push to remote
git push --force --all
git push --force --tags

# 7. ALL collaborators must re-clone — their local copies still have old history
```

**WARNING:** All collaborators must delete their local clones and re-clone after history rewrite.

## Actions Taken

1. **`.gitignore` hardened:** Added `*.env` glob, `test_secret*` glob, `secrets/`, `node_modules/`
2. **`src/llm/config.py`:** Removed hardcoded API key default — now uses empty string fallback
3. **`tests/test_llm_client.py`:** Removed hardcoded API key — now reads from environment variable
4. **`test_secret.txt`:** Deleted from working tree and untracked from git
5. **Verified all other secrets** (`TWILIO_AUTH_TOKEN`, `DATABASE_URL`, `AZURE_STORAGE_CONNECTION_STRING`, etc.) are loaded from `os.getenv()` — no hardcoded values found

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
The `SECURITY.md` already documents pre-commit setup with gitleaks — this must be activated in the CI pipeline (Step 5).

## Blocking Issues

1. **BLOCKING — Secrets exist in git history.** The DeepSeek API key `sk-8749...a0e9` is present in multiple commits across `src/llm/config.py`, `tests/test_llm_client.py`, and `SECURITY.md`. The file `test_secret.txt` was also committed in `a43cf3f`. History rewrite using `git filter-repo` is required before pilot deployment. The API key MUST be rotated at the provider immediately, regardless of history cleanup.

2. **BLOCKING — Git tag `v1.0.0-hardened` must NOT be applied** until history remediation is confirmed complete.
