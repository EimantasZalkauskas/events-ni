"""
Read API the website calls.

    uvicorn api:app --reload
    GET http://localhost:8000/api/events        -> upcoming list
    GET http://localhost:8000/api/events/{id}   -> one event (for detail pages)

Serves published canonical events joined to their venue. This is the whole
contract between the store and the frontend -- the ingestion machinery behind it
can grow arbitrarily without this changing.
"""

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import pipeline

app = FastAPI(title="Belfast Events API")

# Dev-only: lets the frontend call in from another origin. In production the
# Next.js server fetches this API server-side, so lock this down to the deployed
# web origin (or drop it) rather than shipping "*".
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"],
)

_ATTRIBUTION = "Event data via Skiddle (skiddle.com)"  # ToS: credit + link back

# The public event shape, shared by the list and detail queries so they can't drift.
_EVENT_COLUMNS = """ce.id, ce.title, ce.description, ce.starts_at, ce.ends_at,
                    ce.category, ce.price_min, ce.is_free, ce.booking_url, ce.image_url,
                    v.canonical_name AS venue, v.latitude, v.longitude
             FROM canonical_events ce
             LEFT JOIN venues v ON v.id = ce.venue_id"""


@app.get("/api/events")
def list_events(limit: int = 100):
    conn = pipeline.connect()
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        f"""SELECT {_EVENT_COLUMNS}
            WHERE ce.is_published = 1 AND ce.starts_at >= ?
            ORDER BY ce.starts_at ASC
            LIMIT ?""",
        (now[:10], limit),  # compare on date; Skiddle times are Europe/London local
    ).fetchall()
    conn.close()

    return {
        "count": len(rows),
        "attribution": _ATTRIBUTION,
        "events": [dict(r) for r in rows],
    }


@app.get("/api/events/{event_id}")
def get_event(event_id: str):
    """One event by id, upcoming or past -- detail pages should resolve old links."""
    conn = pipeline.connect()
    row = conn.execute(
        f"""SELECT {_EVENT_COLUMNS}
            WHERE ce.is_published = 1 AND ce.id = ?""",
        (event_id,),
    ).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"attribution": _ATTRIBUTION, "event": dict(row)}