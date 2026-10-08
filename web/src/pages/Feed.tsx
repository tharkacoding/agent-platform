import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { api, fmtAgo, type FeedCard } from "../api";
import { useChannel } from "../ws";
import { Empty, ErrorBox, Page, PageHeader } from "../components/ui";
import { Markdown } from "../components/Markdown";

export default function Feed() {
  const [cards, setCards] = useState<FeedCard[]>([]);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    api.feed().then(setCards).catch(setErr);
  }, []);
  useChannel("feed", (c: FeedCard) => setCards((p) => [c, ...p.filter((x) => x.id !== c.id)]));
  return (
    <Page>
      <PageHeader title="Home" subtitle="Briefings and results your agents prepared on their own." />
      <ErrorBox error={err} />
      {cards.length === 0 && (
        <Empty>
          Nothing here yet. Create a <Link to="/schedules" className="underline">schedule</Link> and its results will appear as cards.
        </Empty>
      )}
      <div className="space-y-4">
        {cards.map((c) => (
          <article key={c.id} className="card p-5">
            <div className="flex items-center gap-2 text-xs text-muted mb-2">
              <Sparkles size={13} />
              <span>{c.source || "agent"}</span>
              <span>· {fmtAgo(c.created_at)}</span>
              {c.run_id && (
                <Link to={`/runs/${c.run_id}`} className="ml-auto hover:underline">
                  details
                </Link>
              )}
            </div>
            <h2 className="text-lg font-semibold mb-2">{c.title}</h2>
            <Markdown text={c.body} />
          </article>
        ))}
      </div>
    </Page>
  );
}
