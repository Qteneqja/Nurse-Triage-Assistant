# Security Policy — Nurse Triage Assistant

## 1. Secrets Management

| Rule | Detail |
|------|--------|
| **Never commit secrets** | All credentials go in `.env` (git-ignored). |
| **Rotate immediately** | If a secret is ever committed, rotate it at the provider **before** purging history. |
| **Environment files** | `.env`, `.env.staging`, `.env.production` are all in `.gitignore`. |
| **Private keys** | `*.pem`, `*.key`, `*.crt`, `*.p12`, `*.pfx` are in both `.gitignore` and `.dockerignore`. |

### Required Environment Variables

See [RUNBOOK.md](RUNBOOK.md) for the full list. At minimum:

```
DEEPSEEK_API_KEY=…
TWILIO_ACCOUNT_SID=…
TWILIO_AUTH_TOKEN=…
TWILIO_PHONE_NUMBER=…
API_AUTH_TOKEN=…
```

## 2. How to Rotate Compromised Secrets

1. **DeepSeek API key** — Generate a new key at <https://platform.deepseek.com/api_keys>, update `.env`.
2. **Twilio credentials** — Regenerate Auth Token in the Twilio Console → Account → Keys & Credentials, update `.env`.
3. **API_AUTH_TOKEN** — Generate a new random token (`python -c "import secrets; print(secrets.token_hex(32))"`), update `.env` and all clients.

## 3. Purging Secrets from Git History

If secrets were ever committed, they remain in `git log` even after deletion.

```bash
# Install git-filter-repo (pip install git-filter-repo)
# Then run from a FRESH clone:

git filter-repo --invert-paths --path .env --path reports/docs/credentials.md --path docs/credentials.md --force

# Also replace inline secrets in start_server.bat history:
git filter-repo --replace-text <(echo 'sk-8749f1c8dbe24ba096505d5ae758a0e9==>REDACTED') --force
```

After purging:
- Force-push to remote: `git push --force --all && git push --force --tags`
- All collaborators must re-clone (their local copies still have the old history).
- **Rotate every exposed secret** — purging history does NOT invalidate stolen keys.

## 4. Pre-commit Hooks

This repo uses [pre-commit](https://pre-commit.com) with [gitleaks](https://github.com/gitleaks/gitleaks) to prevent accidental secret commits.

```bash
pip install pre-commit
pre-commit install
```

Hooks will automatically scan staged files before each commit.

## 5. Safe Export

To share the codebase without secrets, run:

```powershell
.\scripts\safe_export.ps1
```

This creates a ZIP in `./export/` excluding `.env`, keys, `.git`, caches, and virtual environments.

## 6. PHI Protection

- **Logging**: All patient-identifiable data is masked via `mask_phi()` before logging.
- **Error responses**: Even in dev mode, exception details are filtered through `mask_phi()`.
- **Storage**: When `STORE_PHI=false` (default), PHI is masked before persistence.
- **Docker**: `.dockerignore` excludes secrets and key files from images.

## 7. Reporting a Vulnerability

If you discover a security issue, please email the project maintainer directly.
Do **not** open a public GitHub issue for security vulnerabilities.
