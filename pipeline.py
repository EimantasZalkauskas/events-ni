"""
Ingestion pipeline: normalised events -> the two-layer store.

Source-agnostic. Every adapter (skiddle_client, a future eventbrite adapter,
the submissions endpoint) hands ingest() a list of normalised dicts in the same
shape, and this file does the rest:

    raw_events  (landing)  ->  source_events  (per-source, never merged)
                                     |  dedupe fingerprint / resolver
                                     v
                              canonical_events (deduped, what the site reads)

For this first slice the resolver is deterministic only: a fingerprint of
(normalised title + date + venue). Cross-source fuzzy matching (pg_trgm blocking
by date+venue) is where this grows once a second source lands -- see resolve().

Uses SQLite so it runs with zero setup. Column names mirror the Postgres
schema.sql exactly, so moving to Postgres is a driver swap, not a rewrite:
set DATABASE_URL to a postgres:// DSN and connect() does the rest (see below).
"""

import difflib
import hashlib
import json
import math
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
    precedence INTEGER NOT NULL DEFAULT 50, enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT
);
CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
    address_line TEXT, postcode TEXT, city TEXT NOT NULL DEFAULT 'Belfast',
    latitude REAL, longitude REAL, website TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS venue_aliases (
    alias TEXT PRIMARY KEY, venue_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_events (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_event_id TEXT,
    payload TEXT NOT NULL, content_hash TEXT NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'pending', fetched_at TEXT,
    UNIQUE (source_id, content_hash)
);
CREATE TABLE IF NOT EXISTS canonical_events (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT,
    starts_at TEXT NOT NULL, ends_at TEXT, timezone TEXT DEFAULT 'Europe/London',
    venue_id TEXT, category TEXT, price_min REAL, is_free INTEGER,
    booking_url TEXT, image_url TEXT, status TEXT NOT NULL DEFAULT 'scheduled',
    primary_source_id TEXT, is_published INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS source_events (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_event_id TEXT,
    raw_event_id TEXT, canonical_event_id TEXT,
    title TEXT NOT NULL, description TEXT, starts_at TEXT NOT NULL, ends_at TEXT,
    venue_id TEXT, venue_name_raw TEXT, category TEXT, price_min REAL, is_free INTEGER,
    booking_url TEXT, source_url TEXT, image_url TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled', dedupe_fingerprint TEXT,
    first_seen_at TEXT, last_seen_at TEXT, created_at TEXT, updated_at TEXT,
    UNIQUE (source_id, source_event_id)
);
CREATE INDEX IF NOT EXISTS idx_se_fp ON source_events (dedupe_fingerprint);
CREATE INDEX IF NOT EXISTS idx_ce_start ON canonical_events (starts_at);
"""


# --- Database dialect plumbing -------------------------------------------------
# The point of this module (see header) is that going from local SQLite to hosted
# Postgres is a driver swap, not a rewrite. Set DATABASE_URL to a postgres:// DSN
# and connect() hands back a Postgres-backed connection wearing the same tiny
# interface (.execute / .executescript / .commit / .close) the rest of this file
# already uses. No env var -> zero-setup SQLite file, unchanged.

try:  # psycopg is only needed for the hosted Postgres path.
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError:  # local SQLite dev doesn't need it installed
    psycopg = None
    Jsonb = None

_SCHEMA_SQL = os.path.join(os.path.dirname(__file__), "schema.sql")


class _Conn:
    """Uniform wrapper so pipeline/api code is written once, dialect-agnostic."""

    is_postgres = False

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(sql, params)

    def executescript(self, script):
        self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


class _PgConn(_Conn):
    is_postgres = True

    def execute(self, sql, params=()):
        return self._conn.execute(_to_pg(sql), params)

    def executescript(self, script):
        for stmt in _split_statements(script):
            self._conn.execute(stmt)


def _to_pg(sql):
    """Translate the SQLite-flavoured SQL in this file to its Postgres form."""
    ignore = "INSERT OR IGNORE" in sql
    sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO").replace("?", "%s")
    if ignore:  # SQLite's OR IGNORE == Postgres's ON CONFLICT DO NOTHING
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


def _split_statements(script):
    # Drop `--` line comments first so a semicolon inside prose doesn't split a
    # statement in two. (Our schema.sql has no `--` inside string literals.)
    code = "\n".join(line.split("--", 1)[0] for line in script.splitlines())
    return [s.strip() for s in code.split(";") if s.strip()]


def connect(path="belfast_events.db"):
    """SQLite by default; Postgres when DATABASE_URL is set (hosted)."""
    url = os.environ.get("DATABASE_URL")
    if url:
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is set but psycopg is not installed "
                               "(pip install 'psycopg[binary]').")
        return _PgConn(psycopg.connect(url, row_factory=dict_row))
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return _Conn(conn)


def init_db(conn):
    if conn.is_postgres:
        with open(_SCHEMA_SQL) as f:
            conn.executescript(f.read())
    else:
        conn.executescript(SCHEMA)
    conn.commit()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uid():
    return str(uuid.uuid4())


def _norm(s):
    """Lowercase, strip punctuation, collapse whitespace -- for matching only."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()


def _slug(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (s or "").lower())).strip("-")


# --- Venue entity resolution ---------------------------------------------------
# Different sources (and even one source) spell the same venue differently:
# "The Black Box" vs "Black Box", a typo, a trailing "Belfast". We collapse those
# to one venue by matching a new raw name against existing venues on name
# similarity + geographic proximity, then recording every spelling we've seen in
# venue_aliases so the next occurrence is an O(1) exact hit.
#
# Done in Python so it behaves identically on SQLite (dev) and Postgres (prod);
# the pg_trgm indexes in schema.sql are the seam for pushing candidate *blocking*
# into SQL once the venue count outgrows "scan them all" (fine for Belfast today).

_ROMAN = {"i", "ii", "iii", "iv", "v", "vi"}
_ARTICLE_RE = re.compile(r"^(the|a|an)\s+")

# False merges (two real venues -> one) lose data and mislabel events, so they're
# worse than false splits: the geo gate is tight and a differing room designator
# is an automatic veto.
_SAME_SPOT_M = 75      # within this, a similar name is almost surely the same place
_NEAR_M = 500          # a very strong name match may still merge within this
_SEQ_SAME_SPOT = 0.80  # typo-tolerant char ratio, when co-located
_SEQ_STRONG = 0.90     # char ratio required without tight geo
_JAC_MIN = 0.50        # token overlap required alongside a strong char ratio


def _match_key(name):
    """Normalised venue name for matching: _norm plus a dropped leading article."""
    return _ARTICLE_RE.sub("", _norm(name))


def _designators(key):
    """Room/number tokens that keep co-located venues apart ('Limelight 1' vs 2)."""
    return {t for t in key.split() if t.isdigit() or len(t) == 1 or t in _ROMAN}


def _name_scores(a, b):
    """(char-level ratio, token Jaccard) between two match keys."""
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return seq, jac


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _resolve_venue(conn, v):
    """Existing venue_id this raw venue is a variant of, or None to mint a new one."""
    key = _match_key(v["name"])
    lat, lng = v.get("latitude"), v.get("longitude")
    best_id, best_score = None, 0.0

    for row in conn.execute(
        "SELECT id, canonical_name, latitude, longitude FROM venues"
    ).fetchall():
        ckey = _match_key(row["canonical_name"])
        if _designators(key) != _designators(ckey):
            continue  # e.g. 'Limelight 1' vs 'Limelight 2' -> distinct rooms

        have_geo = None not in (lat, lng, row["latitude"], row["longitude"])
        dist = _haversine_m(lat, lng, row["latitude"], row["longitude"]) if have_geo else None
        seq, jac = _name_scores(key, ckey)

        if key == ckey:
            merge = dist is None or dist <= _NEAR_M
        elif dist is not None and dist <= _SAME_SPOT_M and (seq >= _SEQ_SAME_SPOT or jac >= 0.60):
            merge = True
        elif seq >= _SEQ_STRONG and jac >= _JAC_MIN and (dist is None or dist <= _NEAR_M):
            merge = True
        else:
            merge = False

        if merge and seq > best_score:  # identical keys score 1.0 and always win
            best_id, best_score = row["id"], seq

    return best_id


def _unique_slug(conn, name):
    """A venue slug not already taken (the column is UNIQUE)."""
    base = _slug(name) or "venue"
    slug, i = base, 2
    while conn.execute("SELECT 1 FROM venues WHERE slug=?", (slug,)).fetchone():
        slug, i = f"{base}-{i}", i + 1
    return slug


def _date_only(dt):
    return (dt or "")[:10]


def fingerprint(title, starts_at, venue_name):
    key = f"{_norm(title)}|{_date_only(starts_at)}|{_norm(venue_name)}"
    return hashlib.sha1(key.encode()).hexdigest()


def get_or_create_source(conn, name, kind, precedence=50):
    row = conn.execute("SELECT id FROM sources WHERE name=?", (name,)).fetchone()
    if row:
        conn.execute("UPDATE sources SET last_run_at=? WHERE id=?", (_now(), row["id"]))
        return row["id"]
    sid = _uid()
    conn.execute(
        "INSERT INTO sources (id,name,kind,precedence,last_run_at) VALUES (?,?,?,?,?)",
        (sid, name, kind, precedence, _now()),
    )
    return sid


def upsert_venue(conn, v):
    """Resolve a raw venue dict to a venue_id, creating it (and an alias) if new.

    Order: exact alias hit (fast path) -> fuzzy entity resolution against existing
    venues (name similarity + geo, see _resolve_venue) -> mint a new venue. Every
    raw spelling we see is recorded in venue_aliases so its next occurrence is the
    O(1) fast path.
    """
    if not v or not v.get("name"):
        return None
    alias = _norm(v["name"])
    hit = conn.execute("SELECT venue_id FROM venue_aliases WHERE alias=?", (alias,)).fetchone()
    if hit:
        return hit["venue_id"]

    vid = _resolve_venue(conn, v)
    if vid is None:
        vid = _uid()
        conn.execute(
            """INSERT INTO venues (id,canonical_name,slug,address_line,postcode,city,
                                   latitude,longitude,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (vid, v["name"], _unique_slug(conn, v["name"]), v.get("address"), v.get("postcode"),
             v.get("city") or "Belfast", v.get("latitude"), v.get("longitude"),
             _now(), _now()),
        )
    conn.execute("INSERT OR IGNORE INTO venue_aliases (alias,venue_id) VALUES (?,?)", (alias, vid))
    return vid


def resolve(conn, fp):
    """
    Deterministic match: an existing source_event with the same fingerprint
    already points at a canonical event -> reuse it. Otherwise the caller mints
    a new canonical. This is the seam where cross-source fuzzy matching plugs in.
    """
    row = conn.execute(
        """SELECT canonical_event_id FROM source_events
           WHERE dedupe_fingerprint=? AND canonical_event_id IS NOT NULL LIMIT 1""",
        (fp,),
    ).fetchone()
    return row["canonical_event_id"] if row else None


def ingest(conn, source_name, kind, events, precedence=50):
    """Store a batch of normalised events. Idempotent per (source, source_event_id)."""
    source_id = get_or_create_source(conn, source_name, kind, precedence)
    stats = {"new": 0, "updated": 0}

    for ev in events:
        venue_id = upsert_venue(conn, ev.get("venue"))
        venue_name = (ev.get("venue") or {}).get("name")
        fp = fingerprint(ev["title"], ev.get("starts_at"), venue_name)

        # Stage 1: land the raw payload. Idempotent via the (source_id, content_hash)
        # unique constraint: a byte-identical re-pull is ignored. rowcount tells us
        # whether a new row actually landed -- portable across both drivers, and
        # (unlike catching the violation) it doesn't abort a Postgres transaction.
        raw = ev.get("raw", {})
        payload = json.dumps(raw, sort_keys=True)  # canonical form -> content_hash
        content_hash = hashlib.sha1(payload.encode()).hexdigest()
        # Postgres stores it as queryable JSONB; SQLite keeps the text above.
        payload_param = Jsonb(raw) if conn.is_postgres else payload
        raw_id = _uid()
        cur = conn.execute(
            """INSERT OR IGNORE INTO raw_events (id,source_id,source_event_id,payload,
                                       content_hash,processing_status,fetched_at)
               VALUES (?,?,?,?,?, 'normalised', ?)""",
            (raw_id, source_id, ev["source_event_id"], payload_param, content_hash, _now()),
        )
        if cur.rowcount == 0:
            raw_id = None  # unchanged payload already landed on a prior pull

        # Stage 3: resolve to a canonical event (or create one). Priority:
        #   1. this exact source event already has one  (idempotent re-ingest)
        #   2. another source event shares its fingerprint (dedupe)
        #   3. neither -> mint a new canonical
        prior = conn.execute(
            "SELECT canonical_event_id FROM source_events WHERE source_id=? AND source_event_id=?",
            (source_id, ev["source_event_id"]),
        ).fetchone()
        canonical_id = (prior["canonical_event_id"] if prior else None) or resolve(conn, fp)
        if canonical_id is None:
            canonical_id = _uid()
            _insert_canonical(conn, canonical_id, ev, venue_id, source_id)
            stats["new"] += 1
        else:
            _update_canonical(conn, canonical_id, ev, venue_id, source_id)
            stats["updated"] += 1

        # Stage 2: upsert the per-source record, linked to its canonical.
        _upsert_source_event(conn, source_id, raw_id, canonical_id, ev, venue_id, venue_name, fp)

    conn.commit()
    return stats


def _insert_canonical(conn, cid, ev, venue_id, source_id):
    conn.execute(
        """INSERT INTO canonical_events (id,title,description,starts_at,ends_at,venue_id,
                category,price_min,is_free,booking_url,image_url,primary_source_id,
                is_published,last_seen_at,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 1, ?,?,?)""",
        (cid, ev["title"], ev.get("description"), ev.get("starts_at"), ev.get("ends_at"),
         venue_id, ev.get("category"), ev.get("price_min"),
         1 if ev.get("is_free") else 0 if ev.get("is_free") is not None else None,
         ev.get("booking_url"), ev.get("image_url"), source_id, _now(), _now(), _now()),
    )


def _update_canonical(conn, cid, ev, venue_id, source_id):
    # Single-source slice: refresh fields + freshness. Multi-source arbitration by
    # `sources.precedence` lands here later (highest-precedence non-null wins).
    conn.execute(
        """UPDATE canonical_events
           SET title=?, description=?, starts_at=?, ends_at=?, venue_id=?, category=?,
               price_min=?, is_free=?, booking_url=?, image_url=?, is_published=1,
               last_seen_at=?, updated_at=?
           WHERE id=?""",
        (ev["title"], ev.get("description"), ev.get("starts_at"), ev.get("ends_at"),
         venue_id, ev.get("category"), ev.get("price_min"),
         1 if ev.get("is_free") else 0 if ev.get("is_free") is not None else None,
         ev.get("booking_url"), ev.get("image_url"), _now(), _now(), cid),
    )


def _upsert_source_event(conn, source_id, raw_id, canonical_id, ev, venue_id, venue_name, fp):
    row = conn.execute(
        "SELECT id FROM source_events WHERE source_id=? AND source_event_id=?",
        (source_id, ev["source_event_id"]),
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE source_events
               SET title=?, description=?, starts_at=?, ends_at=?, venue_id=?, venue_name_raw=?,
                   category=?, price_min=?, is_free=?, booking_url=?, source_url=?, image_url=?,
                   dedupe_fingerprint=?, canonical_event_id=?, last_seen_at=?, updated_at=?
               WHERE id=?""",
            (ev["title"], ev.get("description"), ev.get("starts_at"), ev.get("ends_at"),
             venue_id, venue_name, ev.get("category"), ev.get("price_min"),
             1 if ev.get("is_free") else 0 if ev.get("is_free") is not None else None,
             ev.get("booking_url"), ev.get("source_url"), ev.get("image_url"),
             fp, canonical_id, _now(), _now(), row["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO source_events (id,source_id,source_event_id,raw_event_id,
                    canonical_event_id,title,description,starts_at,ends_at,venue_id,venue_name_raw,
                    category,price_min,is_free,booking_url,source_url,image_url,dedupe_fingerprint,
                    first_seen_at,last_seen_at,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_uid(), source_id, ev["source_event_id"], raw_id, canonical_id,
             ev["title"], ev.get("description"), ev.get("starts_at"), ev.get("ends_at"),
             venue_id, venue_name, ev.get("category"), ev.get("price_min"),
             1 if ev.get("is_free") else 0 if ev.get("is_free") is not None else None,
             ev.get("booking_url"), ev.get("source_url"), ev.get("image_url"), fp,
             _now(), _now(), _now(), _now()),
        )
