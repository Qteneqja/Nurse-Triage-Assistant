# Security Policy — Nurse Triage Assistant

## 1. Secrets Management

| Rule | Detail |
|------|--------|
| **Never commit secrets** | All credentials go in `.env` (git-ignored). |
| **Rotate for incidents** | If a future secret compromise is discovered, rotate it at the provider before or alongside containment. Production rotation for the May 2026 remediation has already been completed by the operator. |
| **Environment files** | `.env`, `.env.staging`, `.env.production` are all in `.gitignore`. |
| **Private keys** | `*.pem`, `*.key`, `*.crt`, `*.p12`, `*.pfx` are in both `.gitignore` and `.dockerignore`. |
| **Production secret storage** | Use Azure Key Vault references and Container App `secretRef`; do not store plaintext production secrets as Container App env values. |

### Required Environment Variables

See [RUNBOOK.md](RUNBOOK.md) for the full list. At minimum:

```
DEEPSEEK_API_KEY=…
TWILIO_ACCOUNT_SID=…
TWILIO_AUTH_TOKEN=…
TWILIO_PHONE_NUMBER=…
API_AUTH_TOKEN=…
```

## 2. Current Remediation Note

Production credential rotation has already been completed by the operator. Do
not rotate secrets again for this remediation unless new evidence indicates a
future compromise. Current repo work focuses on preventing future exposure:
managed identity, Key Vault references, protected dashboard shell, security
headers, safer Azure scripts, and enforceable security scans.

## 3. How to Rotate Compromised Secrets In A Future Incident

1. **DeepSeek API key** — Generate a new key at <https://platform.deepseek.com/api_keys>, update `.env`.
2. **Twilio credentials** — Regenerate Auth Token in the Twilio Console → Account → Keys & Credentials, update `.env`.
3. **API_AUTH_TOKEN** — Generate a new random token (`python -c "import secrets; print(secrets.token_hex(32))"`), update `.env` and all clients.

For production, update Key Vault or the approved Azure secret store instead of
printing or pasting values into shell history, docs, or tickets. Follow the
relevant runbook in `docs/incident-response/`.

## 4. Purging Secrets from Git History

If secrets were ever committed, they remain in `git log` even after deletion.

```bash
# Install git-filter-repo (pip install git-filter-repo)
# Then run from a FRESH clone:

git filter-repo --invert-paths --path .env --path reports/docs/credentials.md --path docs/credentials.md --force

# Also replace inline secrets in history using a local replacements file.
# Never paste the real secret value into docs or tickets.
git filter-repo --replace-text replacements.txt --force
```

After purging:
- Force-push to remote: `git push --force --all && git push --force --tags`
- All collaborators must re-clone (their local copies still have the old history).
- For a future incident, rotate every exposed secret. Purging history does not invalidate stolen keys.

## 5. Pre-commit Hooks

This repo uses [pre-commit](https://pre-commit.com) with [gitleaks](https://github.com/gitleaks/gitleaks) to prevent accidental secret commits.

```bash
pip install pre-commit
pre-commit install
```

Hooks will automatically scan staged files before each commit.

Run all local security checks:

```powershell
pre-commit run --all-files
bandit -r src -c pyproject.toml --severity-level high --confidence-level high
pip-audit -r requirements.txt --progress-spinner off
```

## 6. Safe Export

To share the codebase without secrets, run:

```powershell
.\scripts\safe_export.ps1
```

This creates a ZIP in `./export/` excluding `.env`, keys, `.git`, caches, and virtual environments.

## 7. PHI Protection

- **Logging**: All patient-identifiable data is masked via `mask_phi()` before logging.
- **Error responses**: Even in dev mode, exception details are filtered through `mask_phi()`.
- **Storage**: When `STORE_PHI=false` (default), PHI is masked before persistence.
- **Docker**: `.dockerignore` excludes secrets and key files from images.

## 8. Azure Hardening

Use `docs/AZURE_SECURITY_HARDENING.md` for the production Azure runbook and
`scripts/azure-security-verify.ps1` for sanitized posture evidence.

## 9. Reporting a Vulnerability

If you discover a security issue, please email the project maintainer directly.
Do **not** open a public GitHub issue for security vulnerabilities.
