import { describe, expect, it } from "vitest";
import { addToFeed, latencyMs, mixOf, type Inspection } from "./feed";

const ev = (seq: number, label = "patches", needs_review = false): Inspection => ({
  id: `id-${seq}`, line_id: "L1", run_id: "r", seq, captured_at: 1000, label, confidence: 0.9, needs_review,
});

describe("live feed", () => {
  it("is newest-first and capped", () => {
    let f: Inspection[] = [];
    for (let i = 0; i < 50; i++) f = addToFeed(f, ev(i), 40);
    expect(f).toHaveLength(40);
    expect(f[0].seq).toBe(49);
  });

  it("ignores an event it already has (REST backfill + WS overlap)", () => {
    const f = addToFeed([ev(1)], ev(1));
    expect(f).toHaveLength(1);
  });

  it("computes the defect mix and review count", () => {
    const m = mixOf([ev(1, "patches"), ev(2, "scratches", true), ev(3, "patches")]);
    expect(m.total).toBe(3);
    expect(m.review).toBe(1);
    expect(m.byLabel.patches).toBe(2);
    expect(m.byLabel.crazing).toBe(0);
  });

  it("handles ISO timestamps from the REST API", () => {
    expect(latencyMs({ ...ev(1), captured_at: new Date(1000).toISOString() }, 1250)).toBe(250);
  });
});
