-- ---------------------------------------------------------------------------
-- Extensions and roles.
--
-- Two roles, deliberately separated, because the whole safety model of this
-- application rests on the monitor path being physically unable to write:
--
--   dbopt_monitor  read-only, pg_monitor.  Used for ALL polling and analysis.
--   dbopt_apply    may create/drop indexes.  Used ONLY when a human has
--                  clicked approve on a specific suggestion.
--
-- Tables are owned by app_owner (NOLOGIN) and dbopt_apply is a member, which
-- is what lets it issue CREATE INDEX without being a superuser.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pgstattuple;   -- exact bloat measurement
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- so a trigram index suggestion is actionable

CREATE ROLE app_owner NOLOGIN;

-- PostgreSQL 15 removed the implicit CREATE grant on the public schema from
-- PUBLIC, so a non-superuser owner must be given it explicitly. Making
-- app_owner the schema owner is the cleanest form: ALTER DEFAULT PRIVILEGES
-- in 04-grants.sql then behaves as expected for everything it creates.
ALTER SCHEMA public OWNER TO app_owner;
GRANT USAGE ON SCHEMA public TO PUBLIC;

CREATE ROLE dbopt_monitor LOGIN PASSWORD 'monitor';
GRANT pg_monitor TO dbopt_monitor;
-- Belt and braces: even if a bug sent a write down the monitor path, the
-- server itself refuses it.
ALTER ROLE dbopt_monitor SET default_transaction_read_only = on;
ALTER ROLE dbopt_monitor SET statement_timeout = '5s';
ALTER ROLE dbopt_monitor SET lock_timeout = '2s';
ALTER ROLE dbopt_monitor SET idle_in_transaction_session_timeout = '10s';

CREATE ROLE dbopt_apply LOGIN PASSWORD 'apply' IN ROLE app_owner;
ALTER ROLE dbopt_apply SET lock_timeout = '3s';
-- CREATE INDEX CONCURRENTLY can legitimately run for a long time.
ALTER ROLE dbopt_apply SET statement_timeout = '0';
