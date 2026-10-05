export interface Inspection {
  id: string;
  line_id: string;
  run_id: string;
  seq: number;
  captured_at: number | string;
  label: string;
  confidence: number;
  needs_review: boolean;
  infer_ms?: number;
  pushed_at?: number;
}

export const LABELS = ["crazing", "inclusion", "patches", "pitted_surface", "rolled-in_scale", "scratches"];

/** Newest-first live feed, capped, deduped by id (a reconnect may resend what the REST backfill already loaded). */
export function addToFeed(feed: Inspection[], ev: Inspection, cap = 40): Inspection[] {
  if (feed.some((f) => f.id === ev.id)) return feed;
  return [ev, ...feed].slice(0, cap);
}

export interface Mix {
  total: number;
  review: number;
  byLabel: Record<string, number>;
}

/** Defect-type mix over the events currently in view. */
export function mixOf(feed: Inspection[]): Mix {
  const byLabel: Record<string, number> = Object.fromEntries(LABELS.map((l) => [l, 0]));
  let review = 0;
  for (const f of feed) {
    byLabel[f.label] = (byLabel[f.label] ?? 0) + 1;
    if (f.needs_review) review++;
  }
  return { total: feed.length, review, byLabel };
}

export function latencyMs(ev: Inspection, now = Date.now()): number | null {
  const t = typeof ev.captured_at === "number" ? ev.captured_at : Date.parse(ev.captured_at);
  return Number.isFinite(t) ? now - t : null;
}
