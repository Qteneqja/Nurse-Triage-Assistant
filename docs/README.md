# Documentation Index

This index keeps the root README from becoming the only map of the project.
Root-level documents remain in place for compatibility with existing links.

## Deployment And Operations

- [Azure deployment](AZURE_DEPLOYMENT.md)
- [Deployment guide](../DEPLOYMENT.md)
- [Runbook](../RUNBOOK.md)
- [Monitoring](../MONITORING.md)
- [Staging runbook](../STAGING_RUNBOOK.md)
- [Staging manual test pack](../STAGING_MANUAL_TEST_PACK.md)

## Security And Hardening

- [Security policy](../SECURITY.md)
- [Security cleanup](../SECURITY_CLEANUP.md)
- [Security posture](../SECURITY_POSTURE.md)
- [Hardening report](../HARDENING_REPORT.md)
- [History rewrite procedure](HISTORY_REWRITE_PROCEDURE.md) — executed 2026-06-10
- [GitHub security setup checklist](GITHUB_SECURITY_SETUP.md) — manual UI steps for the operator

## Birchwood Pilot (PR 5 — authoritative set)

- [Pilot readiness gate](../PILOT_READINESS.md) — the checklist
- [Final validation pack (35 calls)](STAGING_VALIDATION_PR5_FINAL.md) — supersedes the PR 1 pack
- [Pilot runbook](pilot/BIRCHWOOD_PILOT_RUNBOOK.md)
- [Escalation workflow](pilot/BIRCHWOOD_ESCALATION_WORKFLOW.md)
- [System limitations](pilot/BIRCHWOOD_LIMITATIONS.md)
- [Success metrics](pilot/BIRCHWOOD_SUCCESS_METRICS.md) — computable via `scripts/pilot_metrics.py`
- [Failure-mode response plan](pilot/BIRCHWOOD_FAILURE_MODES.md)
- [Client one-pager](pilot/BIRCHWOOD_ONE_PAGER.md)
- [Pricing assumptions](pilot/BIRCHWOOD_PRICING_ASSUMPTIONS.md)
- [Rollback procedure](ROLLBACK_PROCEDURE.md)
- [Changelog](../CHANGELOG.md)

## Pilot Readiness (legacy healthcare-era docs)

- [PR 1 staging validation pack](STAGING_VALIDATION_PR1.md) — superseded by the PR 5 final pack
- [Pilot escalation workflow](../PILOT_ESCALATION_WORKFLOW.md)
- [Pilot success metrics](../PILOT_SUCCESS_METRICS.md)
- [Pilot system limitations](../PILOT_SYSTEM_LIMITATIONS.md)

## Platform And Vertical Notes

- [Workflow engine: spec-defined workflows](WORKFLOW_ENGINE.md) — schema, routing config, hard-wired safety
- [Dashboard walkthrough: intake records](DASHBOARD_RECORDS.md) — status workflow, audit log, filters, privacy policy
- [Insurance FNOL vertical](insurance_fnol_vertical.md)
- [Dashboard shell](phase12_dashboard_shell.md)
- [Phase 12 dashboard](../PHASE_12_DASHBOARD.md)
- [OpenClaw dashboard readiness](openclaw_dashboard_readiness.md)
- [Phase 10.5 scripted intake refactor](phase10_5_scripted_intake_next_refactor.md)

## Evaluation

- [DeepEval autonomous evals](DEEPEVAL_AUTONOMOUS_EVALS.md)
- [Phase 4 delivery summary](phase4/delivery_summary.md)
- [Phase 4 test plan](phase4/test_plan.md)
- [Golden dataset spec](phase4/golden_dataset_spec.md)
- [How to run Phase 4 tests](phase4/how_to_run_tests.md)
- [Phase 4 known limitations](phase4/known_limitations.md)
