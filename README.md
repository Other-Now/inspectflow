# inspectflow

An edge-to-cloud visual inspection pipeline: a small version of what an industrial AI-vision line ships.

- **Edge:** a worker classifies steel-surface defects with a CNN in **ONNX Runtime**. It runs inside a
  container capped at **2 CPUs / 2 GB**, like a small industrial PC.
- **Transport:** each result is **store-and-forwarded** through a local SQLite outbox, then sent to
  **RabbitMQ** with publisher confirms.
- **Cloud:** a **Node.js + TypeScript** service acks each message only after the **PostgreSQL** commit
  and dedupes by event id. It serves a REST API, and a WebSocket feed pushes each result to a live
  **React** dashboard.

```
camera frames ─► OpenCV ─► ONNX Runtime ─► SQLite outbox ─► RabbitMQ ─► Node/TS consumer ─► Postgres
   (edge container, 2 CPU / 2 GB)        (sender thread,   (quorum    (ack after commit,      │
                                          confirms)         queue)     ON CONFLICT dedupe)     ├─► REST
                                                                                               └─► WebSocket ─► React
```

## Results

All performance numbers come from **CI run [37308189547](https://github.com/Other-Now/inspectflow/actions/runs/37308189547)**:
a GitHub Ubuntu runner, the full stack in Docker Compose, and the edge container capped at
`cpus: 2, mem_limit: 2g`. Raw JSON is in [`results/ci/`](results/ci/).

**Model.** NEU-CLS, 6 defect classes, stratified 70/15/15 split, seed 7. See [`models/train_report.json`](models/train_report.json).

| | |
|---|---|
| test macro-F1 | **0.9963** (269 / 270 held-out images) |
| params | 186k (4 conv blocks + GroupNorm) |
| ONNX vs PyTorch argmax agreement | 100% on the test set |

**Edge inference.** Batch 1, OpenCV preprocess + model + softmax, 2 CPUs, 3,000 frames.

| fps | p50 | p95 | p99 |
|---|---|---|---|
| **250** | 3.97 ms | **4.03 ms** | 4.83 ms |

The line runs at 25 fps, so the edge box has about 10× headroom.

**Pipeline under faults.** Line at 25 fps. Each fault starts 30 s into the run and lasts 60 s.

| scenario | frames | stored | **lost** | **dup rows** | redeliveries absorbed | max outbox depth | edge fps during run |
|---|---|---|---|---|---|---|---|
| steady state | 1,500 | 1,500 | 0 | 0 | 0 | 8 | 25.0 |
| RabbitMQ `kill` for 60 s | 3,000 | 3,000 | **0** | **0** | 1 | 1,723 | **25.0** |
| Node service `kill` for 60 s | 3,000 | 3,000 | **0** | **0** | 1 | 5 | 25.0 |
| *control:* no outbox, RabbitMQ `kill` for 60 s | 3,000 | 1,290 | **1,710** | 0 | 0 | — | 25.0 |

- **Latency (steady state):** frame capture → row committed is p50 **4.8 ms**. Frame capture → event
  received on a dashboard WebSocket is p50 **4.9 ms** (p95 114 ms, p99 207 ms on a shared runner).
- **Recovery:** after the broker came back, the 1,723-event backlog replayed oldest-first while the live
  line kept running. Everything had committed by the end of the run.

**Local runs** ([`results/local/`](results/local/)) were on a Windows laptop with RabbitMQ in WSL. Their
correctness results match CI: 0 lost and 0 duplicates in every outbox scenario; the control lost
1,579–2,234 of 3,000. **Their latency columns are not meaningful.** The laptop had <300 MB of free RAM
and was paging at 10–30k pages/s during those runs.

## How the guarantees work

| hop | guarantee | mechanism |
|---|---|---|
| edge → broker | at-least-once | outbox row written **before** publish; deleted only after the publisher confirm |
| broker → DB | at-least-once | manual ack only **after** `INSERT` returns; prefetch 32 bounds in-flight work |
| end to end | exactly-once *effect* | event id = `uuid5(line, run, seq)`; `INSERT … ON CONFLICT (id) DO NOTHING RETURNING id` |

Failure handling in the consumer:
- **Bad message:** `reject` without requeue, so a poison message can't loop forever.
- **Postgres down:** `nack` + requeue after 1 s, so the broker acts as the buffer.
- **Duplicate:** acked and not re-broadcast. Dashboards see each event once.

## Three things measuring found

1. **The WSL localhost relay costs ~42 ms per publish confirm.**
   - The first smoke test ran at 17 fps against a 50 fps target, while inference took 6.6 ms.
   - Profiling showed a confirmed publish took 49 ms on quorum, classic and transient queues alike.
     WSL fsync is ~2 ms, so it wasn't disk.
   - Connecting to the WSL VM's IP instead of `localhost` cut it to **0.76 ms** (p50 43.3 → 0.76 ms).
2. **Reconnect attempts blocked inference.**
   - In the first broker-outage run the edge fell to **18.8 fps**. Each reconnect attempt ran on the
     inspection loop and could block for the 2 s socket timeout.
   - A dedicated sender thread now owns the connection, and the inspection loop only touches local
     disk. In CI the edge holds **25.0 fps** through the outage.
3. **SQLite lock contention.**
   - With separate connections in the two threads, handing a frame to the outbox took **p50 93 ms**.
     SQLite's busy handler sleeps in 10–100 ms steps.
   - Fix: one connection behind an in-process lock, and confirmed rows deleted with one
     `DELETE … WHERE n <= last` per batch. Handoff is now **p50 0.1–0.4 ms**.
   - Trade-off: a crash mid-batch can re-send up to 50 events. The id dedupe makes that harmless.

## Honest limits

- **NEU-CLS is an easy benchmark.** Images are centred crops of single defects, and 99%+ accuracy is
  common. The project's subject is the pipeline, not the model.
- **There is no "good" class in NEU-CLS.** Every image is a defect. The dashboard therefore shows the
  per-line **defect-type mix** and a **needs-review rate** (confidence < 0.80), not a pass/fail rate.
- **Anomaly detection (train on good parts only) is not built.**
- **The outbox has no size cap or eviction policy yet.** At ~4 KB per event, an hour offline at 25 fps
  is about 360 MB.
- **One broker node.** The quorum queue here buys durability across `kill -9`, not replication.
- **Latency uses the edge's capture timestamp.** That is valid here because CI runs everything on one
  host. Across machines it needs clock sync.

## Layout

```
edge/       preprocess.py · train.py (PyTorch → ONNX) · worker.py · spool.py (outbox) · publisher.py · bench_infer.py · tests/
cloud/      src/{event,handler,store,consumer,server,metrics,main}.ts · test/ (vitest; pg.test.ts uses real Postgres) · scripts/ws-probe.mjs
dashboard/  React + Vite: live feed, image viewer, defect-mix bars, per-minute series
bench/      outage.py (fault injection, --env compose|local) · summarize.py (table + assertions)
models/     defectnet.onnx · train_report.json · test_split.txt
data/line/  the 270 held-out test images the edge replays as its "camera"
docs/NOTES.md  design notes and interview prep
```

## Run it

```bash
docker compose up -d --build rabbitmq postgres cloud   # dashboard + API on http://localhost:8095
docker compose run --rm edge                           # an endless line at 25 fps (LINE_FPS, LINE_ID env)

# fault scenarios (what CI runs)
pip install pika && (cd cloud && npm ci)
python bench/outage.py --env compose --scenario broker --frames 3000 --fps 25
python bench/summarize.py results/ci

# retrain (needs the NEU-CLS zip from figshare 28903550 unpacked in data/)
cd edge && python train.py
```

**Metrics:** the cloud's `/metrics` exposes deliveries by outcome, DB insert time, edge→DB latency,
WebSocket clients and broker connection state. The edge's `:9101/metrics` exposes inference time,
outbox depth, published and dropped counts.

**Tests:** 8 pytest (edge), 18 vitest (cloud, including a real-Postgres `ON CONFLICT` test) and 4 vitest
(dashboard). All run in CI.
