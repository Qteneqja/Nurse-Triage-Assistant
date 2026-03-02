# Security Posture — Repository Safeguards
<!-- Purpose: Document GitHub security settings and incident response playbook -->
<!-- Date Created: 2026-03-02 -->
<!-- Task: 3 — Security Posture Documentation -->

## 1. GitHub Secret Scanning (Enable in UI)

### Steps to Enable
1. Go to **GitHub.com → Qteneqja/Nurse-Triage-Assistant → Settings**
2. Navigate to **Security → Code security and analysis**
3. Enable:
   - **Secret scanning** → Click "Enable"
   - **Push protection** → Click "Enable"
     - This blocks pushes that contain detected secrets (API keys, tokens, etc.)
     - Contributors see a clear error message explaining why the push was blocked
4. Optionally enable:
   - **Secret scanning alerts** → Notifies repo admins when a secret is detected in existing code
   - **Validity checks** → GitHub checks if detected secrets are still active with the provider

### What It Catches
- OpenAI / Azure API keys
- Twilio auth tokens and account SIDs
- AWS access keys
- GitHub tokens
- Generic high-entropy strings matching known provider patterns
- 200+ supported secret types

### Why It Complements CI Scanning
| Layer | Tool | When It Runs | What It Catches |
|-------|------|-------------|-----------------|
| Pre-push | GitHub Push Protection | Before code reaches remote | Known secret patterns in new commits |
| CI | Gitleaks Action | On PR / push to main | Secrets in full repo + custom patterns |
| CI | `.gitleaks.toml` custom rules | On PR / push to main | DeepSeek keys, Twilio-specific patterns |
| CI | Bandit | On PR / push to main | Python security anti-patterns |

Push Protection is the **first line of defense** — it prevents secrets from ever reaching the remote.
Gitleaks in CI is the **second line** — it catches anything Push Protection misses (custom patterns, edge cases).
Together they provide defense in depth.

---

## 2. Dependabot (Optional but Recommended)

### Steps to Enable
1. Go to **Settings → Security → Code security and analysis**
2. Enable **Dependabot alerts** — notifies on vulnerable dependencies
3. Enable **Dependabot security updates** — auto-creates PRs to fix vulnerable deps
4. Optionally enable **Dependabot version updates** — keeps deps current

### Configuration
Create `.github/dependabot.yml` if not present:
```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
```

---

## 3. Secret Leak Response Playbook

### If a Secret Is Detected (by GitHub, Gitleaks, or Manual Discovery)

**Immediate (within 1 hour):**
1. **Rotate the key** at the provider (DeepSeek, Twilio, Azure, etc.)
   - Generate a new key
   - Update the key in Azure Key Vault / Container App environment variables
   - Verify the application works with the new key
2. **Revoke the old key** at the provider
3. **Assess blast radius:**
   - When was the secret committed?
   - Was it pushed to remote?
   - Who has clone access?
   - Was the repo ever public?

**Within 24 hours:**
4. **Purge from git history** (if the commit was pushed):
   ```bash
   pip install git-filter-repo
   git filter-repo --invert-paths --path <file-with-secret>
   # OR for inline secrets:
   git filter-repo --replace-text <(echo 'LEAKED_VALUE==>REDACTED')
   git push --force --all
   git push --force --tags
   ```
5. **Notify collaborators** — they must delete local clones and re-clone
6. **Check provider audit logs** for unauthorized usage of the leaked key
7. **Document the incident** in `SECURITY_CLEANUP.md`

**Within 1 week:**
8. **Review how the secret entered the repo** — was it a config file, test fixture, copy-paste error?
9. **Add a targeted `.gitleaks.toml` rule** if the pattern isn't already covered
10. **Verify pre-commit hooks are active** for all contributors

### If GitHub Push Protection Blocks a Push
1. **Do not bypass** unless you've confirmed it's a false positive
2. If false positive: add to `.gitleaks.toml` allowlist with a comment explaining why
3. If real secret: remove it from the code, use environment variable instead

---

## 4. Current Safeguards Summary

| Safeguard | Status | Location |
|-----------|--------|----------|
| `.gitignore` covers secrets | ✅ Active | `.gitignore` |
| `.gitleaks.toml` custom rules | ✅ Active | `.gitleaks.toml` |
| Gitleaks CI step | ✅ Added | `.github/workflows/ci.yml` |
| Bandit security linter | ✅ Active | `.github/workflows/ci.yml` |
| GitHub Secret Scanning | ⬜ Enable manually | GitHub Settings UI |
| GitHub Push Protection | ⬜ Enable manually | GitHub Settings UI |
| Dependabot | ⬜ Optional | GitHub Settings UI |
| Pre-commit hook (gitleaks) | ⬜ Recommended | See instructions below |

### Setting Up Local Pre-commit Hook
```bash
pip install pre-commit
```

Create `.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.0
    hooks:
      - id: gitleaks
```

Activate:
```bash
pre-commit install
```

Now gitleaks runs on every local commit before it reaches the remote.
