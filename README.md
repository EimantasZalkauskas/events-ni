# Belfast events — Skiddle vertical slice

First ingestion path, end to end: **Skiddle API → canonical store → webpage.**
Runs today on a bundled fixture (no key), with your real Skiddle key as a
one-line swap.

## The API reality (why Skiddle only)

Of the three sources originally planned, only Skiddle has a usable self-serve API:

| Source | Status | Verdict |
|---|---|---|
| **Skiddle** | Free official API, geo-search by lat/lng/radius, JSON | ✅ This slice. Note ToS below. |
| **Eventbrite** | Public event **search** removed in 2020; API only returns events for orgs you own/manage. Public access needs their distribution-partner application. | ✗ Not a discovery source. Fits the *submissions*/partner stream, not bulk pull. |
| **Resident Advisor** | No official public API. Only the undocumented internal GraphQL (brittle, grey-area) or paid third-party scrapers. | ✗ Belongs in the *scraping* phase later. Electronic-only. |

**Skiddle ToS to respect** (`skiddle.com/api/join.php`): you must credit Skiddle
and link back via each event's `link`, and you may not use the data on anything
that "directly competes" with Skiddle — which a Belfast what's-on arguably does.
Fine for a private dev spike; get written sign-off before anything public.

## Run it

Run everything from the repo root.

```bash
pip install requests fastapi uvicorn httpx

# 1. Ingest (fixture — no key needed)
python -m ingest.api.skiddle.ingest
#    ...or live:
python -m ingest.api.skiddle.ingest --api-key YOUR_SKIDDLE_KEY

# 2. Serve the read API
uvicorn api:app --reload      # http://localhost:8000/api/events

# 3. Run the frontend (separate terminal)
cd web
npm install
cp .env.example .env.local    # points EVENTS_API_URL at the API above
npm run dev                   # http://localhost:3000
```

The API exposes two endpoints: `GET /api/events` (upcoming list) and
`GET /api/events/{id}` (one event, for detail pages). The Next.js app fetches
both **server-side**, so pages are server-rendered (good for SEO) and the browser
never calls the API directly.

### Hosted (Postgres)

Local dev uses SQLite with zero setup. To run against Postgres (e.g. Neon in
production), install the driver and point `DATABASE_URL` at it — no code change:

```bash
pip install "psycopg[binary]"
export DATABASE_URL="postgresql://user:pass@host/db"
python -m ingest.api.skiddle.ingest    # creates tables from schema.sql, then ingests
uvicorn api:app --reload
```

`connect()` picks the driver off `DATABASE_URL`; `init_db()` applies `schema.sql`
(which enables the `pg_trgm` and `postgis` extensions — both available on Neon).
The same INSERT/SELECT statements run on either backend.

## Layout

Ingestion is organised by *how* a source is reached, so each new source is a
self-contained folder and nothing shared has to change:

```
ingest/
  api/                 # sources reached via an official API
    skiddle/
      skiddle_client.py   # the ONLY Skiddle-specific code: fetch + map raw JSON
      ingest.py           # entrypoint: wires this adapter to the pipeline
      fixtures/skiddle_sample.json
  scrape/              # (future) sources with no API — Resident Advisor, etc.
pipeline.py            # source-agnostic: schema, dedupe fingerprint, the
                       #   raw_events → source_events → canonical_events upserts,
                       #   and the SQLite/Postgres driver switch (DATABASE_URL)
schema.sql             # Postgres schema (JSONB, pg_trgm + PostGIS) for hosting
api.py                 # read API: /api/events + /api/events/{id}
web/                   # Next.js (App Router) frontend, deploys to Vercel
  app/page.tsx            # server-rendered event list
  app/events/[id]/        # server-rendered event detail (per-event SEO pages)
  lib/api.ts              # the one fetch contract with FastAPI (EVENTS_API_URL)
  components/EventCard.tsx
```

Each future source drops in beside `skiddle/` (an API one under `ingest/api/`,
a scraped one under `ingest/scrape/`) with its own client + entrypoint;
`pipeline.py`, `api.py`, and the frontend stay untouched.

## Venue resolution

The same venue is spelled differently across (and within) sources — "The Black
Box" vs "Black Box", a typo, a trailing "Belfast". `upsert_venue()` collapses
these to one venue:

1. **Exact alias hit** — every raw spelling seen is stored in `venue_aliases`, so
   a repeat is an O(1) lookup.
2. **Entity resolution** (`_resolve_venue`) — otherwise, match the new name against
   existing venues on name similarity (article-stripped, typo-tolerant char ratio
   + token overlap) **and** geographic proximity. Merges are conservative: a tight
   geo gate, and a differing room designator ("Limelight 1" vs "Limelight 2") is
   an automatic veto — a false merge loses data, a false split doesn't.
3. **Mint a new venue** if nothing matches.

It runs in Python so behaviour is identical on SQLite and Postgres; the `pg_trgm`
indexes are the seam for pushing candidate *blocking* into SQL once the venue
count outgrows a full scan (fine for Belfast today).

## Deliberately deferred (v0.1 scope)

- **`TIMESTAMPTZ` columns.** `schema.sql` keeps the `*_at` / `starts_at` columns
  as `TEXT` for now (event times are Europe/London wall-clock strings compared by
  date prefix; audit stamps are ISO-8601 UTC). Promoting them means binding
  `datetime` objects at every call site — a separate step from the driver swap.
- **Cross-source fuzzy dedupe.** Only the deterministic fingerprint runs today.
  The `resolve()` function is the seam where pg_trgm blocking (by date+venue)
  plugs in once a second source exists.
- **Freshness sweeper.** `last_seen_at` is populated; the job that unpublishes
  events whose sources went quiet isn't written yet.
- **Live field-name check.** The mapper follows Skiddle's documented shape;
  verify against a real response on first live run and fix in one place if needed.
