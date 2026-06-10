# GitHub Security Setup — Manual Checklist (Operator Action Required)

These steps must be performed in the GitHub web UI by the repository owner.
They cannot be automated from this repo and **none of them have been performed
by the assistant** — check each box as you complete it.

Repository: `https://github.com/Qteneqja/Nurse-Triage-Assistant` (public, single owner)

## Ordering constraint

If you intend to run the git history rewrite
([HISTORY_REWRITE_PROCEDURE.md](HISTORY_REWRITE_PROCEDURE.md)), do it **before**
enabling branch protection on `main`, because the rewrite requires a force-push
that branch protection will (correctly) block. Recommended order:

1. History rewrite + force-push (separate procedure doc)
2. Secret scanning + push protection (this doc, section A)
3. Branch protection on `main` (this doc, section B)

## A. Secret Scanning + Push Protection

- [ ] Go to **Settings → Advanced Security** (older UI: **Settings → Code
      security and analysis**).
- [ ] Under **Secret scanning** (also labeled *Secret Protection*), click **Enable**.
      Free for public repositories.
- [ ] Under **Push protection**, click **Enable**. This blocks pushes that
      contain detectable secrets before they reach the repo.
- [ ] Optional but recommended: enable **Validity checks** if offered (GitHub
      checks whether detected tokens are still active).
- [ ] After enabling, open **Security → Secret scanning alerts** and review any
      alerts raised against existing history. Expected: alerts may fire on the
      historical DeepSeek key (already rotated May 2026 — see
      `SECURITY_CLEANUP.md`). Close those as **Revoked** with a note, or let the
      history rewrite remove them.

## B. Branch Protection on `main`

- [ ] Go to **Settings → Branches → Add branch protection rule** (or
      **Settings → Rules → Rulesets → New branch ruleset** in the newer UI).
- [ ] Branch name pattern: `main`.
- [ ] Enable **Require a pull request before merging** (approvals: 0 is fine
      for a single-owner repo; the point is forcing CI to run).
- [ ] Enable **Require status checks to pass before merging** and select:
  - [ ] `Secret Scan (Gitleaks)`
  - [ ] `Lint & Formatting`
  - [ ] `Test Suite`
  - [ ] `Healthcare Evals (DeepEval)`
  - [ ] `Security Scan`
  - [ ] (Checks only appear in the picker after they have run at least once on
        a PR — open the PR for this branch first if the list is empty.)
- [ ] Enable **Require branches to be up to date before merging**.
- [ ] Enable **Block force pushes** (default in rulesets) — only after the
      history rewrite is done.
- [ ] Do **not** check "Allow administrators to bypass" unless you accept that
      your own pushes skip CI.

## C. Repository secrets sanity check

- [ ] **Settings → Secrets and variables → Actions**: confirm no unused or
      stale secrets. `GITLEAKS_LICENSE` is **not required** for a public repo
      on a personal account — the CI reference to it is optional and harmless
      if unset.

## D. Pilot freeze (later — PR 5)

When the Birchwood pilot window starts, tighten the `main` ruleset: require
review, restrict who can push, and tag the release. Tracked in PR 5; listed
here only so this doc is the single place for GitHub UI actions.
