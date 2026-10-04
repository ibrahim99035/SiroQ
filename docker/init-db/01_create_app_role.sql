-- Reference for provisioning the least-privilege application role on the
-- Neon branch. Not run automatically any more: there is no local Postgres
-- container to run it on. Execute it once against the branch's database, as
-- the branch owner, substituting a real password — Neon supports CREATE ROLE.
--
-- Only pgcrypto, and only because migration 001 needs gen_random_uuid() for
-- primary keys. PostGIS is not installed: no table declares a geometry or
-- geography column and nothing imports geoalchemy2, so requiring an extension
-- nothing uses only makes provisioning harder.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'siroq_app') THEN
      CREATE ROLE siroq_app LOGIN PASSWORD 'replace-with-a-real-password';
   END IF;
END
$$;

GRANT CONNECT ON DATABASE neondb TO siroq_app;
GRANT USAGE ON SCHEMA public TO siroq_app;

-- Any table created later by the migration-owner role automatically grants
-- these privileges to siroq_app — this is what makes future migrations safe
-- without manually re-granting every time. Run this again for whichever role
-- Neon named as the branch owner.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO siroq_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO siroq_app;