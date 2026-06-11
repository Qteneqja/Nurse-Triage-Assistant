# Rollback Procedure — Azure Container Apps (Staging/Pilot)

Manual rollback for a bad deploy. Operator-executed; takes ~3 minutes.
The app deploys as ACA **revisions** — rolling back = shifting traffic to
the previous healthy revision. No rebuild needed.

## 1. Identify the bad and last-good revisions

```powershell
az containerapp revision list -n nurse-triage-api -g nurse-triage-rg `
  --query "[].{name:name, active:properties.active, created:properties.createdTime, healthy:properties.healthState, traffic:properties.trafficWeight}" -o table
```

The newest revision is the suspect; note the previous revision with
`healthy: Healthy`.

## 2. Shift traffic back

```powershell
# Single-revision mode (default): activate the last-good revision
az containerapp revision activate -n nurse-triage-api -g nurse-triage-rg `
  --revision <LAST_GOOD_REVISION>

# If multiple-revision mode is on, pin traffic explicitly:
az containerapp ingress traffic set -n nurse-triage-api -g nurse-triage-rg `
  --revision-weight <LAST_GOOD_REVISION>=100
```

## 3. Verify (same probes as the validation pre-flight)

```powershell
$BASE = "https://nurse-triage-api.livelymushroom-186460d5.canadacentral.azurecontainerapps.io"
curl "$BASE/health"      # 200 {"status":"ok"}
curl "$BASE/ready"       # 200 storage=postgres database=connected
curl -X POST "$BASE/api/v1/voice/incoming" -d "CallSid=CAFAKE"   # 403 (signature)
```

Place one test call end-to-end. Check the dashboard records page loads.

## 4. Database migrations — the asymmetric part

Revisions roll back code, NOT schema. Policy:

- **Additive migrations (new tables/columns)** — e.g. `004_record_status_events`
  — are safe to leave in place when rolling code back; old code ignores them.
  **Do not run `alembic downgrade` during an incident.**
- A migration that DROPPED or rewrote data cannot be rolled back this way;
  that scenario requires restoring the Postgres PITR backup (Azure
  Database for PostgreSQL → Point-in-time restore) to a new server and
  repointing `DATABASE_URL`. This has never been needed; all migrations to
  date are additive.

## 5. Stop the bleeding upstream

- Disable the auto-deploy if a bad commit keeps redeploying:
  GitHub → Actions → "Trigger auto deployment for nurse-triage-api" →
  Disable workflow (re-enable after the fix lands).
- If the incident is call-affecting and a fix is not imminent, point the
  Twilio number's Voice webhook at a static "we'll call you back" TwiML Bin
  (prepare one in the Twilio console ahead of the pilot — see
  [pilot/BIRCHWOOD_FAILURE_MODES.md](pilot/BIRCHWOOD_FAILURE_MODES.md)).

## 6. Afterwards

- File the defect; fix on a branch with a regression test; redeploy.
- Note the incident in the pilot log (runbook section 6) and tell
  Birchwood's contact if any of their calls were affected.
