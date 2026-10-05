import { useEffect, useMemo, useState } from "react";
import { addToFeed, LABELS, latencyMs, mixOf, type Inspection } from "./feed";

interface LineStats {
  line_id: string;
  total: number;
  review_rate: number;
  by_label: Record<string, number>;
  series: { minute: string; total: number; review: number }[];
}

function useLiveFeed(line: string) {
  const [feed, setFeed] = useState<Inspection[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastLatency, setLastLatency] = useState<number | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout>;
    fetch(`/api/inspections?line=${encodeURIComponent(line)}&limit=40`)
      .then((r) => r.json())
      .then((rows: Inspection[]) => setFeed(rows.reverse().reduce((f, r) => addToFeed(f, r), [] as Inspection[])))
      .catch(() => {});
    const open = () => {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(open, 1000);
      };
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data) as Inspection;
        if (ev.line_id !== line) return;
        setLastLatency(latencyMs(ev));
        setFeed((f) => addToFeed(f, ev));
      };
    };
    open();
    return () => {
      closed = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [line]);
  return { feed, connected, lastLatency };
}

function MixBars({ byLabel, total }: { byLabel: Record<string, number>; total: number }) {
  return (
    <div className="bars">
      {LABELS.map((l) => {
        const n = byLabel[l] ?? 0;
        const pct = total ? (100 * n) / total : 0;
        return (
          <div className="bar-row" key={l}>
            <span className="bar-label">{l}</span>
            <span className="bar-track"><span className="bar-fill" style={{ width: `${pct}%` }} /></span>
            <span className="bar-value">{n} · {pct.toFixed(0)}%</span>
          </div>
        );
      })}
    </div>
  );
}

function Series({ series }: { series: LineStats["series"] }) {
  const max = Math.max(1, ...series.map((s) => s.total));
  return (
    <svg className="series" viewBox={`0 0 ${Math.max(series.length, 1) * 12} 60`} preserveAspectRatio="none">
      {series.map((s, i) => (
        <g key={s.minute}>
          <rect x={i * 12 + 1} y={60 - (60 * s.total) / max} width={10} height={(60 * s.total) / max} className="s-total" />
          <rect x={i * 12 + 1} y={60 - (60 * s.review) / max} width={10} height={(60 * s.review) / max} className="s-review" />
          <title>{`${new Date(s.minute).toLocaleTimeString()} — ${s.total} inspected, ${s.review} for review`}</title>
        </g>
      ))}
    </svg>
  );
}

export default function App() {
  const [lines, setLines] = useState<string[]>(["L1"]);
  const [line, setLine] = useState("L1");
  const [stats, setStats] = useState<LineStats | null>(null);
  const [selected, setSelected] = useState<Inspection | null>(null);
  const { feed, connected, lastLatency } = useLiveFeed(line);
  const live = useMemo(() => mixOf(feed), [feed]);

  useEffect(() => {
    fetch("/api/lines").then((r) => r.json())
      .then((rows: { line_id: string }[]) => rows.length && setLines(rows.map((r) => r.line_id)))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const load = () => fetch(`/api/lines/${encodeURIComponent(line)}/stats?minutes=60`)
      .then((r) => r.json()).then(setStats).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [line]);

  const shown = selected ?? feed[0] ?? null;

  return (
    <div className="app">
      <header>
        <h1>inspectflow</h1>
        <select value={line} onChange={(e) => { setLine(e.target.value); setSelected(null); }}>
          {lines.map((l) => <option key={l}>{l}</option>)}
        </select>
        <span className={`dot ${connected ? "up" : "down"}`}>{connected ? "live" : "reconnecting"}</span>
        {lastLatency !== null && <span className="muted">frame→screen {lastLatency} ms</span>}
      </header>

      <main>
        <section className="card feed">
          <h2>Live feed</h2>
          <ul>
            {feed.map((f) => (
              <li key={f.id} className={f.id === shown?.id ? "sel" : ""} onClick={() => setSelected(f)}>
                <img src={`/api/inspections/${f.id}/thumb`} alt="" width={40} height={40} />
                <span className="lbl">{f.label}</span>
                <span className={f.needs_review ? "conf review" : "conf"}>{(f.confidence * 100).toFixed(1)}%</span>
                <span className="muted">#{f.seq}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="card viewer">
          <h2>Inspection</h2>
          {shown ? (
            <>
              <img src={`/api/inspections/${shown.id}/thumb`} alt={shown.label} className="big" />
              <dl>
                <dt>Predicted</dt><dd>{shown.label}</dd>
                <dt>Confidence</dt><dd>{(shown.confidence * 100).toFixed(1)}%{shown.needs_review && " — needs review"}</dd>
                <dt>Line / seq</dt><dd>{shown.line_id} / {shown.seq}</dd>
                <dt>Edge inference</dt><dd>{shown.infer_ms?.toFixed(1) ?? "–"} ms</dd>
              </dl>
            </>
          ) : <p className="muted">waiting for frames…</p>}
        </section>

        <section className="card">
          <h2>Defect mix — last 60 min {stats && <span className="muted">({stats.total} inspected, {(stats.review_rate * 100).toFixed(1)}% for review)</span>}</h2>
          {stats && <MixBars byLabel={stats.by_label} total={stats.total} />}
          <h2>Per minute</h2>
          {stats && <Series series={stats.series} />}
          <p className="muted">Live window: {live.total} events, {live.review} below the 0.80 confidence review threshold.</p>
        </section>
      </main>
    </div>
  );
}
