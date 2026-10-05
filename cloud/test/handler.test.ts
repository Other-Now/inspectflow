import { describe, expect, it } from "vitest";
import { handleDelivery } from "../src/handler.js";
import { InvalidEvent, parseEvent, type InspectionEvent } from "../src/event.js";
import type { Store } from "../src/store.js";

const ev = (over: Partial<InspectionEvent> = {}) => ({
  id: "0b6f6f2a-4b8e-5c1d-9a0e-3f2b1c4d5e6f", line_id: "L1", run_id: "r1", seq: 7,
  captured_at: 1_700_000_000_000, label: "scratches", confidence: 0.97, needs_review: false,
  infer_ms: 6.1, model: "defectnet-v1", ...over,
});
const buf = (o: unknown) => Buffer.from(JSON.stringify(o));

/** In-memory store with the same contract as PgStore.insert (PK on id). */
class MemStore implements Store {
  rows = new Map<string, InspectionEvent>();
  failNext = 0;
  async insert(e: InspectionEvent) {
    if (this.failNext > 0) { this.failNext--; throw new Error("connection refused"); }
    if (this.rows.has(e.id)) return false;
    this.rows.set(e.id, e);
    return true;
  }
}

describe("parseEvent", () => {
  it("accepts a valid event and lower-cases the id", () => {
    const e = parseEvent(buf(ev({ id: "0B6F6F2A-4B8E-5C1D-9A0E-3F2B1C4D5E6F" })));
    expect(e.id).toBe("0b6f6f2a-4b8e-5c1d-9a0e-3f2b1c4d5e6f");
    expect(e.label).toBe("scratches");
  });

  it.each([
    ["not json", Buffer.from("{oops")],
    ["bad id", buf(ev({ id: "123" }))],
    ["unknown label", buf(ev({ label: "rust" }))],
    ["confidence > 1", buf(ev({ confidence: 1.5 }))],
    ["negative seq", buf(ev({ seq: -1 }))],
    ["empty line", buf(ev({ line_id: "" }))],
  ])("rejects %s", (_name, body) => {
    expect(() => parseEvent(body)).toThrow(InvalidEvent);
  });
});

describe("handleDelivery", () => {
  it("inserts once, then treats a redelivery as a duplicate (still acked)", async () => {
    const store = new MemStore();
    const pushed: string[] = [];
    const push = (e: InspectionEvent) => pushed.push(e.id);
    expect(await handleDelivery(buf(ev()), store, push)).toBe("inserted");
    expect(await handleDelivery(buf(ev()), store, push)).toBe("duplicate");
    expect(store.rows.size).toBe(1);
    expect(pushed).toHaveLength(1); // dashboards see it once
  });

  it("asks for a retry (no ack) when the database is down", async () => {
    const store = new MemStore();
    store.failNext = 1;
    expect(await handleDelivery(buf(ev()), store, () => {})).toBe("retry");
    expect(store.rows.size).toBe(0);
    expect(await handleDelivery(buf(ev()), store, () => {})).toBe("inserted");
  });

  it("marks poison messages invalid so they are not requeued forever", async () => {
    expect(await handleDelivery(Buffer.from("garbage"), new MemStore(), () => {})).toBe("invalid");
  });

  it("an at-least-once replay storm still yields exactly one row per id", async () => {
    const store = new MemStore();
    const ids = Array.from({ length: 100 }, (_, i) => `0b6f6f2a-4b8e-5c1d-9a0e-${String(i).padStart(12, "0")}`);
    let inserted = 0, dup = 0;
    for (let round = 0; round < 3; round++)
      for (const id of ids) {
        const o = await handleDelivery(buf(ev({ id })), store, () => {});
        if (o === "inserted") inserted++; else if (o === "duplicate") dup++;
      }
    expect(inserted).toBe(100);
    expect(dup).toBe(200);
  });
});
