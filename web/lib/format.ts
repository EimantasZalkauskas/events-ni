import type { EventItem } from "@/lib/api";

// Skiddle times are "YYYY-MM-DD HH:MM:SS" (Europe/London local); render readably.
export function fmtDate(s: string | null): string {
  if (!s) return "";
  const d = new Date(s.replace(" ", "T"));
  return isNaN(d.getTime())
    ? s
    : d.toLocaleString("en-GB", {
        weekday: "short",
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
}

export function price(e: Pick<EventItem, "is_free" | "price_min">): string {
  if (e.is_free) return "Free";
  return e.price_min != null ? `£${e.price_min}` : "";
}