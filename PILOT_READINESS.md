# Pilot Readiness Checklist
<!-- Purpose: Track pilot-blocking items and readiness status -->
<!-- Date Created: 2026-03-02 -->
<!-- Task: 1 — Git History Verification + Pilot Gate -->

## Git History Assessment

### Findings

| Item | Status | Detail |
|------|--------|--------|
| `test_secret.txt` in HEAD | ✅ Removed | File deleted, entry in `.gitignore` |
| `test_secret.txt` in git history | ⚠️ Present | Commit `a43cf3f` ("test secret") contains `sk-12345678901234567890` |
| `.env` files in history | ✅ Clean | No `.env` files found in any historical commit |
| `.pem` / `.key` files in history | ✅ Clean | None found |
| API keys rotated | ✅ Done | All keys rotated by repo owner (2026-03-02) |
| Remote pushed | Yes | `origin https://github.com/Qteneqja/Nurse-Triage-Assistant.git` |

### Risk Assessment

The value found in history (`sk-12345678901234567890`) appears to be a **test/placeholder key** (sequential digits), not a production credential. The repo owner has confirmed all real keys have been rotated.

**However**, because the repo was pushed to GitHub with this commit:
- GitHub may have cached the blob
- Anyone with prior clone access could recover it from reflog
- GitHub Secret Scanning may have flagged it (check GitHub UI)

### Remediation Commands (DO NOT RUN AUTOMATICALLY)

If you determine history rewrite is needed, run these commands **after coordinating with all collaborators**:

```bash
# Option A: git filter-repo (preferred)
pip install git-filter-repo
git filter-repo --invert-paths --path test_secret.txt

# Force push all branches and tags
git push --force --all
git push --force --tags

# Clean local reflog
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

After running:
- **All collaborators must delete their local clone and re-clone**
- `git pull` will NOT work after history rewrite
- GitHub cached data will be purged after force-push + GitHub support request (if private repo)

### Blocking Status

| Condition | Blocking? | Rationale |
|-----------|-----------|-----------|
| Repo was pushed to GitHub | ⚠️ RECOMMENDED | History rewrite recommended before pilot onboarding new collaborators |
| Key was a test/placeholder value | Mitigating | Low actual risk since key is not a real credential |
| All real keys rotated | Mitigating | Even if discovered, no valid credentials are exposed |

**Decision:** History rewrite is **recommended but not blocking** for pilot, given:
1. The exposed value appears to be a placeholder, not a real credential
2. All real keys have been rotated
3. The repo is under single-owner control

**If the repo will be shared with new collaborators or made public before pilot:** Run the remediation commands first.

---

## Pilot Gate Checklist

### Security (Must Pass)
- [x] All API keys rotated
- [x] `test_secret.txt` removed from HEAD
- [x] `.gitignore` covers `.env`, `test_secret*`, `*.pem`, `*.key`
- [x] `.gitleaks.toml` configured with custom rules
- [ ] Gitleaks added to CI pipeline (Task 2)
- [ ] GitHub Secret Scanning enabled (Task 3 — manual UI step)
- [ ] Git history rewrite completed (recommended, not blocking)

### Infrastructure (Must Pass)
- [x] Azure Container Apps staging deployed
- [x] Managed Postgres provisioned and connected
- [x] Environment variables configured in Azure
- [ ] Staging smoke test completed (see `STAGING_MANUAL_TEST_PACK.md`)
- [ ] Twilio signature verification validated in staging

### Testing (Must Pass)
- [x] Unit tests passing
- [x] Golden call framework operational (5 cases)
- [ ] Golden calls expanded to 20+ cases (Task 6)
- [ ] Manual 10-call test pack executed on staging (Task 7)

### Documentation (Must Pass)
- [ ] Escalation workflow documented (`PILOT_ESCALATION_WORKFLOW.md`)
- [ ] System limitations documented (`PILOT_SYSTEM_LIMITATIONS.md`)
- [ ] Success metrics defined (`PILOT_SUCCESS_METRICS.md`)
- [ ] Staging runbook created (`STAGING_RUNBOOK.md`)

### Monitoring (Should Pass)
- [x] Sentry integration in code
- [ ] Sentry DSN configured in staging
- [ ] Test event received in Sentry dashboard
- [ ] Alert rules configured (LLM timeout, JSON failure, DB error)
