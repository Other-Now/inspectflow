import { describe, expect, it } from "vitest";
import request from "supertest";
import { createApp } from "../src/server.js";

const calls: unknown[] = [];
const fake = {
  async list(f: unknown) { calls.push(f); return [{ id: "x", label: "patches" }]; },
  async thumb(id: string) { return id.endsWith("0") ? Buffer.from([0xff, 0xd8, 0xff]) : null; },
  async lines() { return [{ line_id: "L1", total: 3 }]; },
  async lineStats(line: string, minutes: number) { return { line_id: line, minutes, total: 0 }; },
  async runAudit() { return { rows: 1, distinct_seq: 1 }; },
  async runLatency() { return { p50_ms: 1 }; },
};
const app = createApp(fake as any);

describe("REST API", () => {
  it("passes filters through to the store", async () => {
    const r = await request(app).get("/api/inspections?line=L1&label=patches&review=true&limit=5");
    expect(r.status).toBe(200);
    expect(calls.at(-1)).toEqual({ line: "L1", label: "patches", review: true, limit: 5 });
  });

  it("rejects an unknown label with 400", async () => {
    expect((await request(app).get("/api/inspections?label=rust")).status).toBe(400);
  });

  it("serves thumbnails as jpeg and 404s missing ones", async () => {
    const ok = await request(app).get("/api/inspections/00000000-0000-0000-0000-000000000000/thumb");
    expect(ok.status).toBe(200);
    expect(ok.headers["content-type"]).toBe("image/jpeg");
    expect((await request(app).get("/api/inspections/00000000-0000-0000-0000-000000000001/thumb")).status).toBe(404);
    expect((await request(app).get("/api/inspections/nope/thumb")).status).toBe(400);
  });

  it("returns per-line stats with the requested window", async () => {
    const r = await request(app).get("/api/lines/L2/stats?minutes=15");
    expect(r.body).toEqual({ line_id: "L2", minutes: 15, total: 0 });
  });

  it("exposes prometheus metrics", async () => {
    const r = await request(app).get("/metrics");
    expect(r.status).toBe(200);
    expect(r.text).toContain("cloud_deliveries_total");
  });
});
