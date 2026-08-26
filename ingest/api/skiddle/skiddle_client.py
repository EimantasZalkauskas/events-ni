"""
Skiddle ingestion adapter.

Two responsibilities, kept together because they are the only Skiddle-specific
code in the system:
  1. fetch  -- pull raw events from the Skiddle Events API (or a fixture)
  2. map    -- translate one raw Skiddle event into the normalised dict that
               pipeline.py knows how to store. Every other source gets its own
               file exactly like this one; downstream code never sees raw JSON.

ToS NOTE (read skiddle.com/api/join.php): Skiddle's terms require you to credit
Skiddle as the data source and link back via the event `link`, and forbid use
on anything that "directly competes" with Skiddle. `booking_url` below is that
required link -- keep it, surface it, and get written sign-off before going
public with this source.

Get a free key: https://www.skiddle.com/api/join.php
API docs:       https://github.com/Skiddle/web-api
"""

import json
import re
import requests

SEARCH_URL = "https://www.skiddle.com/api/v1/events/search/"

# Greater Belfast. Radius is in miles; 15 comfortably covers the metro area.
BELFAST_LAT = 54.5973
BELFAST_LNG = -5.9301
RADIUS_MILES = 15


def fetch_live(api_key, limit=100, offset=0):
    """Pull a page of live events near Belfast. Returns the raw `results` list."""
    params = {
        "api_key": api_key,
        "latitude": BELFAST_LAT,
        "longitude": BELFAST_LNG,
        "radius": RADIUS_MILES,
        "order": "date",       # soonest first
        "description": 1,      # include the long description
        "limit": limit,
        "offset": offset,
    }
    resp = requests.get(SEARCH_URL, params=params, timeout=20)
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise RuntimeError(f"Skiddle API error: {body}")
    return body.get("results", [])


def fetch_fixture(path):
    """Load a saved Skiddle response so the pipeline runs with no key."""
    with open(path) as f:
        return json.load(f).get("results", [])


def _parse_price(raw):
    """Skiddle `entryprice` is a free-text string. Return (price_min, is_free)."""
    if raw is None:
        return None, None
    text = str(raw).strip().lower()
    if not text or text in {"free", "0", "£0", "£0.00"}:
        return 0.0, True
    m = re.search(r"\d+(?:\.\d{1,2})?", text)
    return (float(m.group()), False) if m else (None, None)


def map_skiddle_event(raw):
    """
    Raw Skiddle event -> normalised dict consumed by pipeline.ingest().

    Written against Skiddle's documented/typical field names. Verify against a
    real response the first time you run live -- if a key differs, this mapper
    is the ONE place you fix it, and nothing downstream changes.
    """
    venue = raw.get("venue") or {}
    price_min, is_free = _parse_price(raw.get("entryprice"))
    genres = raw.get("genres") or []

    return {
        "source_event_id": str(raw.get("id")),
        "title": raw.get("eventname") or "(untitled)",
        "description": raw.get("description"),
        # Skiddle datetimes are Europe/London local, "YYYY-MM-DD HH:MM:SS".
        "starts_at": raw.get("startdate") or raw.get("date"),
        "ends_at": raw.get("enddate"),
        "venue": {
            "name": venue.get("name"),
            "latitude": _as_float(venue.get("latitude")),
            "longitude": _as_float(venue.get("longitude")),
            "address": venue.get("address"),
            "postcode": venue.get("postcode"),
            "city": venue.get("town") or "Belfast",
        } if venue.get("name") else None,
        "category": genres[0].get("name") if genres else raw.get("eventcode"),
        "price_min": price_min,
        "is_free": is_free,
        "booking_url": raw.get("link"),      # required Skiddle back-link
        "source_url": raw.get("link"),
        "image_url": raw.get("largeimageurl") or raw.get("imageurl"),
        "raw": raw,
    }


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
