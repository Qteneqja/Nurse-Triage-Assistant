-- ============================================================================
-- dashboard_reader — least-privilege read-only role for the ORCA dashboard
-- ============================================================================
--
-- WHY THIS FILE EXISTS
--   The dashboard_reader role was created by hand on the live Azure Postgres
--   instance and never captured in version control, so its actual grants
--   could neither be verified nor reproduced. This script is the canonical
--   least-privilege definition. Reconcile the live role to it (see the AUDIT
--   section at the bottom) and revoke anything broader you find there —
--   in particular any ALTER DEFAULT PRIVILEGES auto-grant.
--
-- HOW TO RUN
--   Operator-only, under an ADMIN login (e.g. the Azure server admin), after
--   `alembic upgrade head` so all tables exist:
--
--       psql "$ADMIN_DATABASE_URL" -f scripts/db/dashboard_reader_role.sql
--
--   The password is NOT in this file. Generate it yourself and store it in
--   Azure Key Vault; nothing secret is committed to the repo.
--
-- SCOPE (deliberate)
--   * SELECT only, table-by-table — NO GRANT ... ON ALL TABLES.
--   * default_transaction_read_only=on as a second seatbelt.
--   * NO "ALTER DEFAULT PRIVILEGES ... GRANT SELECT" — a table added by a
--     future migration must be granted here, explicitly, on purpose.
--     (If the live role has such a default-privileges grant today, the
--     AUDIT section shows how to find and revoke it.)
--   * The application's read models keep PHI governance upstream of this
--     role: with STORE_PHI=false, text/entities/session-blob are redacted
--     before they are written (src/storage/state_redaction.py), so this
--     role's SELECT reach is masked data, not raw PHI.
--
-- ============================================================================

-- 1. Role -------------------------------------------------------------------
-- CHANGE_ME: generate a strong password out-of-band and store it in Azure
-- Key Vault (never in the repo, never in app config files).
CREATE ROLE dashboard_reader
    LOGIN
    PASSWORD 'CHANGE_ME__set_out_of_band_then_store_in_key_vault'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT
    NOREPLICATION
    CONNECTION LIMIT 10;

-- Read-only at the session level, even if a broader grant ever slips in.
ALTER ROLE dashboard_reader SET default_transaction_read_only = on;

-- 2. Schema access ------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO dashboard_reader;

-- 3. Table grants — SELECT ONLY, named tables only ---------------------------
-- Operational call tables the dashboard reads
-- (verified against src/storage/models.py __tablename__ definitions):
GRANT SELECT ON public.triage_sessions          TO dashboard_reader;
GRANT SELECT ON public.triage_turns             TO dashboard_reader;
GRANT SELECT ON public.messages                 TO dashboard_reader;
GRANT SELECT ON public.conversation_extractions TO dashboard_reader;
GRANT SELECT ON public.decisions                TO dashboard_reader;
GRANT SELECT ON public.rule_triggers            TO dashboard_reader;
GRANT SELECT ON public.safety_events            TO dashboard_reader;
GRANT SELECT ON public.record_status_events     TO dashboard_reader;
GRANT SELECT ON public.enrichment_results       TO dashboard_reader;

-- Config/label tables the dashboard needs to render names and routing:
GRANT SELECT ON public.organizations            TO dashboard_reader;
GRANT SELECT ON public.verticals                TO dashboard_reader;
GRANT SELECT ON public.organization_workflows   TO dashboard_reader;
GRANT SELECT ON public.phone_numbers            TO dashboard_reader;

-- Explicitly NOT granted: alembic_version (migration state), and any table
-- not listed above. No INSERT/UPDATE/DELETE/TRUNCATE anywhere. Dashboard
-- status changes are written by the APPLICATION role, not this one.

-- ============================================================================
-- 4. AUDIT the live role — run these, then reconcile
-- ============================================================================
-- In psql, inspect what the hand-created role actually has today:
--
--   \du+ dashboard_reader
--   \dp public.*
--
--   -- Table privileges held by the role:
--   SELECT table_schema, table_name, privilege_type
--     FROM information_schema.role_table_grants
--    WHERE grantee = 'dashboard_reader'
--    ORDER BY table_name, privilege_type;
--
--   -- Future auto-grants (ALTER DEFAULT PRIVILEGES) that mention the role;
--   -- any row here is a violation of this definition:
--   SELECT pg_get_userbyid(defaclrole) AS granting_role,
--          defaclnamespace::regnamespace AS schema,
--          defaclobjtype, defaclacl
--     FROM pg_default_acl
--    WHERE defaclacl::text LIKE '%dashboard_reader%';
--
-- Reconcile the live role to this file:
--
--   -- Revoke the future-default grant (run AS the role that created it,
--   -- typically the admin; repeat per schema found above):
--   ALTER DEFAULT PRIVILEGES IN SCHEMA public
--       REVOKE SELECT ON TABLES FROM dashboard_reader;
--
--   -- Strip any broad grants, then re-apply section 3 above:
--   REVOKE ALL ON ALL TABLES IN SCHEMA public FROM dashboard_reader;
--   REVOKE ALL ON DATABASE postgres FROM dashboard_reader;  -- adjust db name
--
--   -- Verify: re-run the two SELECTs above; the grants list must match
--   -- section 3 exactly (SELECT on the 13 named tables, nothing else) and
--   -- pg_default_acl must return zero rows for dashboard_reader.
-- ============================================================================
