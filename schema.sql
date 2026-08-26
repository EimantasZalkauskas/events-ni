-- Postgres schema for the hosted deployment.
--
-- Column names and shape mirror the inline SQLite SCHEMA in pipeline.py exactly,
-- so the same INSERT/SELECT statements run against both. The differences here are
-- the things Postgres does better and SQLite can't:
--
--   * raw_events.payload is JSONB (queryable) instead of a text blob.
--   * pg_trgm indexes on venue names/aliases -- the groundwork for cross-source
--     fuzzy dedupe and venue aliasing (see resolve() / upsert_venue()).
--   * a PostGIS functional GiST index for real "events within N miles" queries
--     off the venue lat/lng we already store.
--
-- Deliberately NOT changed yet: ids stay TEXT (uuid4 strings the app mints), and
-- the *_at / starts_at columns stay TEXT. Event times are Europe/London wall-clock
-- strings from Skiddle and are compared by date prefix (see api.py); the audit
-- timestamps are ISO-8601 UTC strings. Promoting these to TIMESTAMPTZ means
-- binding datetime objects at every call site, so it's a separate, later step.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
    precedence INTEGER NOT NULL DEFAULT 50, enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
    address_line TEXT, postcode TEXT, city TEXT NOT NULL DEFAULT 'Belfast',
    latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, website TEXT,
    created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS venue_aliases (
    alias TEXT PRIMARY KEY, venue_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_events (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_event_id TEXT,
    payload JSONB NOT NULL, content_hash TEXT NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'pending', fetched_at TEXT,
    UNIQUE (source_id, content_hash)
);

CREATE TABLE IF NOT EXISTS canonical_events (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT,
    starts_at TEXT NOT NULL, ends_at TEXT, timezone TEXT DEFAULT 'Europe/London',
    venue_id TEXT, category TEXT, price_min DOUBLE PRECISION, is_free INTEGER,
    booking_url TEXT, image_url TEXT, status TEXT NOT NULL DEFAULT 'scheduled',
    primary_source_id TEXT, is_published INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS source_events (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_event_id TEXT,
    raw_event_id TEXT, canonical_event_id TEXT,
    title TEXT NOT NULL, description TEXT, starts_at TEXT NOT NULL, ends_at TEXT,
    venue_id TEXT, venue_name_raw TEXT, category TEXT, price_min DOUBLE PRECISION, is_free INTEGER,
    booking_url TEXT, source_url TEXT, image_url TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled', dedupe_fingerprint TEXT,
    first_seen_at TEXT, last_seen_at TEXT, created_at TEXT, updated_at TEXT,
    UNIQUE (source_id, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_se_fp ON source_events (dedupe_fingerprint);
CREATE INDEX IF NOT EXISTS idx_ce_start ON canonical_events (starts_at);

-- Groundwork for venue aliasing / cross-source fuzzy dedupe (pg_trgm).
CREATE INDEX IF NOT EXISTS idx_venues_name_trgm ON venues USING gin (canonical_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_venue_aliases_trgm ON venue_aliases USING gin (alias gin_trgm_ops);

-- Geo radius search off the lat/lng we already store, without adding a column.
CREATE INDEX IF NOT EXISTS idx_venues_geog ON venues USING gist (
    (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography)
);