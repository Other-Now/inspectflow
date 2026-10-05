// Runs against a real Postgres when INSPECTFLOW_TEST_DB is set (CI service container, or local PG).
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import pg from "pg";
import { PgStore } from "../src/store.js";

const url = process.env.INSPECTFLOW_TEST_DB;

describe.skipIf(!url)("PgStore (real Postgres)", () => {
  let pool: pg.Pool;
  let store: PgStore;
  const run = `test-${Date.now()}`;

  beforeAll(async () => {
    pool = new pg.Pool({ connectionString: url });
    store = new PgStore(pool);
    await store.migrate();
  });
  afterAll(async () => {
    await pool.query("DELETE FROM inspections WHERE run_id = $1", [run]);
    await pool.end();
  });

  const ev = (seq: number, label = "crazing") => ({
    id: `11111111-2222-5333-8444-${String(seq).padStart(12, "0")}`, line_id: "LT", run_id: run, seq,
    captured_at: Date.now(), label, confidence: seq % 2 ? 0.6 : 0.95, needs_review: seq % 2 === 1,
    infer_ms: 5, model: "t", thumb_jpeg_b64: Buffer.from("jpg").toString("base64"),
  });

  it("ON CONFLICT makes concurrent redeliveries harmless", async () => {
    const results = await Promise.all([0, 0, 0, 1, 1, 2].map((s) => store.insert(ev(s))));
    expect(results.filter(Boolean)).toHaveLength(3);
    expect(await store.runAudit(run)).toMatchObject({ rows: 3, distinct_seq: 3 });
  });

  it("computes the line mix and review rate", async () => {
    await store.insert(ev(3, "scratches"));
    const s = await store.lineStats("LT", 60);
    expect(s.total).toBe(4);
    expect(s.by_label).toEqual({ crazing: 3, scratches: 1 });
    expect(s.review_rate).toBeCloseTo(0.5);
    expect((await store.thumb(ev(0).id))?.toString()).toBe("jpg");
  });
});
