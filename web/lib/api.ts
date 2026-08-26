// The one contract with the FastAPI backend. Mirrors the columns api.py returns.

export type EventItem = {
  id: string;
  title: string;
  description: string | null;
  starts_at: string;
  ends_at: string | null;
  category: string | null;
  price_min: number | null;
  is_free: number | null;
  booking_url: string | null;
  image_url: string | null;
  venue: string | null;
  latitude: number | null;
  longitude: number | null;
};

export type EventsResponse = { count: number; attribution: string; events: EventItem[] };
export type EventResponse = { attribution: string; event: EventItem };

const API_BASE = process.env.EVENTS_API_URL ?? "http://localhost:8000";
const REVALIDATE = 900; // seconds: cache server-side, refetch every 15 min

export async function getEvents(limit = 100): Promise<EventsResponse> {
  const res = await fetch(`${API_BASE}/api/events?limit=${limit}`, {
    next: { revalidate: REVALIDATE },
  });
  if (!res.ok) throw new Error(`Events API returned ${res.status}`);
  return res.json();
}

export async function getEvent(id: string): Promise<EventResponse | null> {
  const res = await fetch(`${API_BASE}/api/events/${encodeURIComponent(id)}`, {
    next: { revalidate: REVALIDATE },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Event API returned ${res.status}`);
  return res.json();
}