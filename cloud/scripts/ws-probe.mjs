// Dashboard-side latency probe: connects to /ws like the React app does and
// records (receive time - frame capture time) for every event of one run.
//   node scripts/ws-probe.mjs ws://localhost:8095/ws <run_id> <expected_count> [timeout_s]
import WebSocket from "ws";

const [url, run, expected, timeoutS = "300"] = process.argv.slice(2);
const want = Number(expected);
const lat = [];
const ws = new WebSocket(url);

const done = () => {
  lat.sort((a, b) => a - b);
  const q = (p) => (lat.length ? lat[Math.min(lat.length - 1, Math.floor(p * lat.length))] : null);
  console.log(JSON.stringify({ run, received: lat.length, expected: want,
    p50_ms: q(0.5), p95_ms: q(0.95), p99_ms: q(0.99), max_ms: lat.at(-1) ?? null }));
  process.exit(0);
};

ws.on("open", () => console.error("probe connected"));
ws.on("message", (data) => {
  const ev = JSON.parse(data.toString());
  if (ev.run_id !== run) return;
  lat.push(Date.now() - ev.captured_at);
  if (lat.length >= want) done();
});
ws.on("error", (e) => { console.error(e.message); process.exit(1); });
setTimeout(done, Number(timeoutS) * 1000);
