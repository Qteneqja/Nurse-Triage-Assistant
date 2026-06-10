# Git History Rewrite Procedure — Purge Secrets from History

**Status: EXECUTED 2026-06-10.** The rewrite was performed and verified: all 10
branches force-updated, `test_secret.txt` absent from all history, and a
gitleaks full-history scan of a fresh clone of GitHub passes with zero
suppressions. A pre-rewrite backup bundle and the `phase-12-7` patch were kept
temporarily in `C:\Users\QTene\rewrite-work\` — delete that directory once
satisfied (it contains pre-rewrite history). Remaining optional step: ask
GitHub Support to drop cached/dangling pre-rewrite commits (see Step 7).
This document is retained as the procedure of record for any future rewrite.

## What gets purged, and why

A full-history gitleaks scan (gitleaks 8.30.1, custom `.gitleaks.toml`, 82
commits) found:

| Item | Where in history | Status |
|---|---|---|
| `test_secret.txt` (52-byte binary, secret-like content) | Added `a43cf3f`, deleted `4100708`; blob remains reachable | Fake/test value, but normalizes committing secrets |
| DeepSeek API key (real, **rotated May 2026**) | Historical versions of `src/llm/config.py`, `tests/test_llm_client.py`, `SECURITY.md` (commit `0c7e623`) and `SECURITY_CLEANUP.md` (commit `4100708`) | Rotated — purge is hygiene, not incident response |

The current working tree is clean; only historical blobs are affected.
`test_secret.txt` is a single blob (`678f40b46398fb722d3502f8f9a1422c5e14a475`)
reachable from the history of **every** branch (6 local, 27 remote, including
all `dependabot/*` and `fix/*` branches) — which is why this procedure uses a
mirror clone that rewrites all refs at once, not a single-branch rewrite. No
tags or stashes exist; no branch tip carries the file.

> **Scope note:** the original task was "purge `test_secret.txt`", but the
> scan shows the rotated DeepSeek key is also still in history. Rewriting
> history twice is worse than once, so this procedure removes both.

## Preconditions — check every box before starting

- [ ] The DeepSeek key in history is the rotated one (confirmed May 2026 per
      `SECURITY_CLEANUP.md`). If there is any doubt, rotate again first.
- [ ] No open PRs on GitHub (`gh pr list` — was empty as of 2026-06-10).
      Open PRs reference old SHAs and break after a rewrite.
- [ ] All local branches you care about are pushed to `origin`.
- [ ] Delete remote branches you no longer need (stale `dependabot/*`,
      merged `fix/*`, merged `phase-*`) — fewer refs to rewrite, less noise:
      `git push origin --delete <branch>` for each.
- [ ] Branch protection on `main` is **not yet enabled** (or temporarily
      allows force push). See ordering note in
      [GITHUB_SECURITY_SETUP.md](GITHUB_SECURITY_SETUP.md).
- [ ] You have ~15 minutes where nobody (including CI-triggering pushes)
      touches the repo.

## Step 1 — Install git-filter-repo

```powershell
pip install git-filter-repo
git filter-repo --version
```

## Step 2 — Build the replacement file (contains the secret — keep it OUTSIDE any repo)

From your existing working clone, recover the old key value without displaying
it longer than needed, and write the replacement rule. **Do this in a directory
that is not a git repo** (e.g. `C:\Users\QTene\rewrite-work\`):

```powershell
mkdir C:\Users\QTene\rewrite-work
cd C:\Users\QTene\Nurse-Triage-Assistant
# Extract the literal key from the historical file into the replacements file:
git show 0c7e623:src/llm/config.py | Select-String 'sk-[0-9a-f]{32,}' | ForEach-Object {
  ($_.Matches[0].Value) + '==>REDACTED-ROTATED-DEEPSEEK-KEY'
} | Out-File -Encoding ascii C:\Users\QTene\rewrite-work\replacements.txt
# Sanity check: exactly one line, ends with ==>REDACTED-ROTATED-DEEPSEEK-KEY
Get-Content C:\Users\QTene\rewrite-work\replacements.txt
```

Delete `replacements.txt` when the procedure is complete (Step 7).

## Step 3 — Mirror-clone and rewrite

Work on a **fresh mirror clone**, never your working copy:

```powershell
cd C:\Users\QTene\rewrite-work
git clone --mirror https://github.com/Qteneqja/Nurse-Triage-Assistant.git repo-mirror.git
cd repo-mirror.git

# One pass: drop test_secret.txt from every commit AND redact the key everywhere.
git filter-repo --invert-paths --path test_secret.txt --replace-text ..\replacements.txt
```

`--force` is not needed on a fresh mirror clone. Every commit SHA from
`a43cf3f` onward will change.

## Step 4 — Verify the rewrite BEFORE pushing

All three checks must pass:

```powershell
# 1. test_secret.txt gone from all history (expect: no output)
git log --all --oneline -- test_secret.txt

# 2. Key gone from all history (expect: no output; searches all blobs)
git grep "sk-" $(git rev-list --all) -- src/llm/config.py tests/test_llm_client.py SECURITY.md SECURITY_CLEANUP.md

# 3. Full gitleaks scan of the rewritten history (expect: leaks found: 0)
#    (copy .gitleaks.toml from your working clone first)
copy C:\Users\QTene\Nurse-Triage-Assistant\.gitleaks.toml .
& "$env:USERPROFILE\.tools\gitleaks\gitleaks.exe" detect --source . --config .gitleaks.toml --redact --no-banner
```

If any check fails, **stop** — do not push. Delete the mirror and re-run.

## Step 5 — Force-push the rewritten history

```powershell
# filter-repo removes the origin remote as a safety measure; re-add it:
git remote add origin https://github.com/Qteneqja/Nurse-Triage-Assistant.git

git push --force --all origin
git push --force --tags origin
```

Note: errors about `refs/pull/*` (if any) are expected and harmless — GitHub
PR refs are read-only.

## Step 6 — Re-establish your local working clone

Your existing working clone still has the old history. Replace it:

```powershell
cd C:\Users\QTene
Rename-Item Nurse-Triage-Assistant Nurse-Triage-Assistant.old
git clone https://github.com/Qteneqja/Nurse-Triage-Assistant.git
# Copy over untracked local files you need (.env, .tmp scratch, etc.):
#   .env  — DO copy (gitignored, contains your real config)
robocopy Nurse-Triage-Assistant.old Nurse-Triage-Assistant .env /NJH /NJS
# After confirming the new clone works (run the test suite), delete the old one.
```

If you had unpushed branches, export them from the old clone first
(`git format-patch`) and re-apply (`git am`) in the new clone.

## Step 7 — Post-rewrite cleanup

- [ ] Delete `C:\Users\QTene\rewrite-work\replacements.txt` and the mirror.
- [ ] Remove the fingerprint entries from `.gitleaksignore` (they reference
      pre-rewrite commit SHAs) and run the "Secret Scan (All Branches)"
      workflow manually (`workflow_dispatch`) — it must pass with the
      suppressions gone, proving the rewrite actually purged the findings.
- [ ] GitHub keeps unreachable commits accessible **by SHA** for a while
      (cached views, old PR diffs). For a fully clean purge, contact GitHub
      Support ("remove cached/dangling commits after sensitive-data rewrite").
      Given the key is rotated and the test secret is fake, this is optional —
      documented residual risk if skipped.
- [ ] Update `SECURITY_CLEANUP.md`: mark the "Blocking Issues" history-risk
      item resolved, and note that commit SHAs referenced in docs
      (`a43cf3f`, `4100708`, `0c7e623`) now refer to pre-rewrite history.
- [ ] Verify CI is green on the rewritten `main`.
- [ ] Now enable branch protection + secret scanning
      ([GITHUB_SECURITY_SETUP.md](GITHUB_SECURITY_SETUP.md)).

## Rollback

There is no in-place rollback after the force-push. The only safety net is the
pre-rewrite state: before Step 5, your original working clone and GitHub both
still hold the old history. If verification fails post-push, force-push the old
history back from your original working clone (`git push --force --all` from
`Nurse-Triage-Assistant.old`) — this is why Step 6 keeps the old clone until
the new one is confirmed working.
