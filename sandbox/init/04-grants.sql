-- ---------------------------------------------------------------------------
-- Grants.  Runs last, after every object exists.
-- ---------------------------------------------------------------------------

GRANT CONNECT ON DATABASE shopdb TO dbopt_monitor, dbopt_apply;
GRANT USAGE ON SCHEMA public TO dbopt_monitor, dbopt_apply;

-- Monitor: read only.  pg_monitor (granted in 01) already covers the stats
-- views; this adds table reads so EXPLAIN can plan real queries.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dbopt_monitor;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO dbopt_monitor;

-- Apply: needs CREATE on the schema to build indexes.  Table ownership comes
-- from its membership in app_owner, granted at role creation.
GRANT CREATE ON SCHEMA public TO dbopt_apply;

-- The workload generator writes, so it gets its own role rather than
-- borrowing either of the application's.
CREATE ROLE workload LOGIN PASSWORD 'workload' IN ROLE app_owner;
GRANT CONNECT ON DATABASE shopdb TO workload;
GRANT USAGE ON SCHEMA public TO workload;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO workload;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO workload;
