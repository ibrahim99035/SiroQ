-- Runs once, automatically, on first container init (docker-entrypoint-initdb.d convention)

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS postgis;

DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'siroq_app') THEN
      CREATE ROLE siroq_app LOGIN PASSWORD 'siroq_app_dev_password';
   END IF;
END
$$;

GRANT CONNECT ON DATABASE siroq TO siroq_app;
GRANT USAGE ON SCHEMA public TO siroq_app;

-- Any table created later by the migration-owner role ('siroq') automatically
-- grants these privileges to siroq_app — this is what makes future migrations
-- safe without manually re-granting every time.
ALTER DEFAULT PRIVILEGES FOR ROLE siroq IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO siroq_app;
ALTER DEFAULT PRIVILEGES FOR ROLE siroq IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO siroq_app;