import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { getEvent } from "@/lib/api";
import { fmtDate, price } from "@/lib/format";

export const revalidate = 900;

// Next 15: route params are async.
type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  const data = await getEvent(id);
  if (!data) return { title: "Event not found" };
  return {
    title: `${data.event.title} — Belfast`,
    description: data.event.description ?? undefined,
  };
}

export default async function EventPage({ params }: Props) {
  const { id } = await params;
  const data = await getEvent(id);
  if (!data) notFound();

  const e = data.event;
  const meta = [fmtDate(e.starts_at), e.venue, price(e)].filter(Boolean).join(" · ");

  return (
    <main>
      <p>
        <Link href="/">← All events</Link>
      </p>
      <h1>{e.title}</h1>
      <p className="sub">{meta}</p>
      {e.image_url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="hero" src={e.image_url} alt="" />
      )}
      {e.category && (
        <p>
          <span className="tag">{e.category}</span>
        </p>
      )}
      {e.description && <p>{e.description}</p>}
      {e.booking_url && (
        <p>
          <a href={e.booking_url} target="_blank" rel="noopener">
            Book / more info ↗
          </a>
        </p>
      )}
      <p className="sub">{data.attribution}</p>
    </main>
  );
}