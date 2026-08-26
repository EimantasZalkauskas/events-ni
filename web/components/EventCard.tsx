import Link from "next/link";

import type { EventItem } from "@/lib/api";
import { fmtDate, price } from "@/lib/format";

export function EventCard({ event: e }: { event: EventItem }) {
  const meta = [fmtDate(e.starts_at), e.venue, price(e)].filter(Boolean).join(" · ");
  return (
    <article className="event">
      {/* Plain <img>: event images come from arbitrary source CDNs, so we skip
          next/image's per-host allowlist. Swap in next/image once hosts settle. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img className="thumb" src={e.image_url ?? undefined} alt="" />
      <div>
        <h2>
          <Link href={`/events/${e.id}`}>{e.title}</Link>
        </h2>
        <p className="meta">{meta}</p>
        {e.category && <span className="tag">{e.category}</span>}
      </div>
    </article>
  );
}