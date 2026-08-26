import { EventCard } from "@/components/EventCard";
import { getEvents } from "@/lib/api";

export const revalidate = 900;

export default async function Home() {
  let data;
  try {
    data = await getEvents();
  } catch {
    return (
      <main>
        <h1>What&apos;s On — Belfast</h1>
        <p className="sub">Could not reach the API. Is uvicorn running on :8000?</p>
      </main>
    );
  }

  return (
    <main>
      <h1>What&apos;s On — Belfast</h1>
      <p className="sub">
        {data.count} upcoming events · {data.attribution}
      </p>
      <div>
        {data.events.map((e) => (
          <EventCard key={e.id} event={e} />
        ))}
      </div>
    </main>
  );
}