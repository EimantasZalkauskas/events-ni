"""
Entrypoint for the Skiddle ingestion run.

    python -m ingest.api.skiddle.ingest                    # bundled fixture (no key)
    python -m ingest.api.skiddle.ingest --api-key YOUR_KEY # live events near Belfast

Run from the repo root. Either way it lands data in belfast_events.db (at the
repo root), ready for api.py to serve. Each future source gets its own sibling
entrypoint just like this -- the pipeline it calls is source-agnostic.
"""

import argparse
import os
import sys

# Make the repo root importable so `pipeline` and the fully-qualified adapter
# import resolve whether this is run via `-m` or as a plain script.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pipeline  # noqa: E402
from ingest.api.skiddle.skiddle_client import (  # noqa: E402
    fetch_fixture,
    fetch_live,
    map_skiddle_event,
)

_DEFAULT_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "skiddle_sample.json")
_DEFAULT_DB = os.path.join(_ROOT, "belfast_events.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", help="Skiddle API key. Omit to use the fixture.")
    ap.add_argument("--fixture", default=_DEFAULT_FIXTURE)
    ap.add_argument("--db", default=_DEFAULT_DB)
    args = ap.parse_args()

    if args.api_key:
        raw = fetch_live(args.api_key)
        print(f"Fetched {len(raw)} live events from Skiddle.")
    else:
        raw = fetch_fixture(args.fixture)
        print(f"Loaded {len(raw)} events from fixture {args.fixture}.")

    events = [map_skiddle_event(r) for r in raw]

    conn = pipeline.connect(args.db)
    pipeline.init_db(conn)
    stats = pipeline.ingest(conn, source_name="skiddle", kind="api", precedence=50, events=events)
    conn.close()

    print(f"Done. canonical events: {stats['new']} new, {stats['updated']} updated.")


if __name__ == "__main__":
    main()