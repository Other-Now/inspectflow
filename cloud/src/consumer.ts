import amqp from "amqplib";
import { handleDelivery } from "./handler.js";
import type { InspectionEvent } from "./event.js";
import type { Store } from "./store.js";
import { brokerUp, consumed, edgeToDb, insertSeconds } from "./metrics.js";

export const QUEUE = "inspections";

/**
 * Consume forever. If the broker goes away, reconnect with backoff; anything
 * delivered but not yet acked is redelivered by RabbitMQ, and the store's
 * ON CONFLICT turns that into a no-op.
 */
export function startConsumer(url: string, store: Store, onInserted: (ev: InspectionEvent) => void, prefetch = 32) {
  let stopped = false;
  let conn: amqp.ChannelModel | null = null;
  let backoff = 500;

  const connect = async (): Promise<void> => {
    while (!stopped) {
      try {
        conn = await amqp.connect(url, { timeout: 3000 });
        const ch = await conn.createChannel();
        await ch.assertQueue(QUEUE, { durable: true, arguments: { "x-queue-type": "quorum" } });
        await ch.prefetch(prefetch);
        conn.on("error", () => {});
        conn.on("close", () => {
          brokerUp.set(0);
          conn = null;
          if (!stopped) setTimeout(connect, backoff);
        });
        await ch.consume(QUEUE, async (msg) => {
          if (!msg) return;
          const t0 = process.hrtime.bigint();
          const outcome = await handleDelivery(msg.content, store, (ev) => {
            edgeToDb.observe(Math.max(0, Date.now() - ev.captured_at) / 1000);
            onInserted(ev);
          });
          insertSeconds.observe(Number(process.hrtime.bigint() - t0) / 1e9);
          consumed.inc({ outcome });
          try {
            if (outcome === "inserted" || outcome === "duplicate") ch.ack(msg);
            else if (outcome === "invalid") ch.reject(msg, false);
            else setTimeout(() => { try { ch.nack(msg, false, true); } catch { /* channel gone: broker redelivers */ } }, 1000);
          } catch {
            // Channel closed under us: the message was not acked, broker will redeliver.
          }
        });
        brokerUp.set(1);
        backoff = 500;
        console.log(`consumer attached to ${QUEUE}`);
        return;
      } catch (e) {
        brokerUp.set(0);
        console.warn(`amqp connect failed (${(e as Error).message}); retry in ${backoff} ms`);
        await new Promise((r) => setTimeout(r, backoff));
        backoff = Math.min(backoff * 2, 5000);
      }
    }
  };

  void connect();
  return {
    async stop() {
      stopped = true;
      if (conn) await conn.close().catch(() => {});
    },
  };
}
