import http from "node:http";
import pg from "pg";
import { startConsumer } from "./consumer.js";
import { attachWs, createApp } from "./server.js";
import { PgStore } from "./store.js";

const PORT = Number(process.env.PORT ?? 8095);
const DATABASE_URL = process.env.DATABASE_URL ?? "postgres://inspectflow:inspectflow@localhost:5432/inspectflow";
const AMQP_URL = process.env.AMQP_URL ?? "amqp://guest:guest@localhost:5672";

async function main() {
  const pool = new pg.Pool({ connectionString: DATABASE_URL, max: 10 });
  pool.on("error", (e) => console.warn("pg pool error:", e.message));
  const store = new PgStore(pool);
  for (let i = 0; ; i++) {
    try { await store.migrate(); break; } catch (e) {
      if (i > 30) throw e;
      console.warn("waiting for postgres:", (e as Error).message);
      await new Promise((r) => setTimeout(r, 1000));
    }
  }

  const app = createApp(store, process.env.DASHBOARD_DIR);
  const server = http.createServer(app);
  const broadcast = attachWs(server);
  const consumer = startConsumer(AMQP_URL, store, broadcast, Number(process.env.PREFETCH ?? 32));
  server.listen(PORT, () => console.log(`inspectflow-cloud listening on :${PORT}`));

  const shutdown = async () => {
    await consumer.stop();
    server.close();
    await pool.end();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

main().catch((e) => { console.error(e); process.exit(1); });
