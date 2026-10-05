import { InvalidEvent, parseEvent, type InspectionEvent } from "./event.js";
import type { Store } from "./store.js";

export type Outcome = "inserted" | "duplicate" | "invalid" | "retry";

/**
 * Decide what to do with one delivery. The consumer acks only on
 * inserted/duplicate, i.e. only after the row is durably in Postgres.
 *
 *   inserted  -> ack, broadcast to dashboards
 *   duplicate -> ack, nothing else (redelivery / edge replay absorbed)
 *   invalid   -> reject without requeue (poison message must not loop forever)
 *   retry     -> nack with requeue (DB down: the broker keeps it for us)
 */
export async function handleDelivery(
  body: Buffer,
  store: Store,
  onInserted: (ev: InspectionEvent) => void,
): Promise<Outcome> {
  let ev: InspectionEvent;
  try {
    ev = parseEvent(body);
  } catch (e) {
    if (e instanceof InvalidEvent) return "invalid";
    throw e;
  }
  let inserted: boolean;
  try {
    inserted = await store.insert(ev);
  } catch {
    return "retry";
  }
  if (inserted) onInserted(ev);
  return inserted ? "inserted" : "duplicate";
}
