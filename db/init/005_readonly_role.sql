-- Public-API read-only role (public-dashboard spec §3.1). Idempotent: safe to re-apply.
--
-- Password is NOT set here — deploy.sh runs ALTER ROLE from PG_RO_PASSWORD so no
-- secret lands in the repo. On a fresh volume this runs at first boot; on the live
-- droplet apply by hand (init scripts only run on an empty volume — Manual 05):
--   docker compose exec -T postgres psql -U skynyc -d skynyc < db/init/005_readonly_role.sql
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'skynyc_ro') THEN
    CREATE ROLE skynyc_ro LOGIN PASSWORD 'changeme-set-by-deploy';
  END IF;
END $$;

GRANT CONNECT ON DATABASE skynyc TO skynyc_ro;
GRANT USAGE ON SCHEMA public TO skynyc_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO skynyc_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO skynyc_ro;

-- analytics schema is created by dbt; guard for fresh volumes where it doesn't exist yet.
-- dbt re-creates views under the owner role; the default privileges cover re-creations.
DO $$
BEGIN
  IF EXISTS (SELECT FROM information_schema.schemata WHERE schema_name = 'analytics') THEN
    GRANT USAGE ON SCHEMA analytics TO skynyc_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO skynyc_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO skynyc_ro;
  END IF;
END $$;
