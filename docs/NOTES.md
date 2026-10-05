# inspectflow: interview notes

Read this top to bottom once. Then re-explain each section out loud without looking.

## 1. The 30-second pitch

> A small version of what an industrial-inspection company ships. A camera-side worker classifies
> steel-surface defects with a CNN in ONNX Runtime. It runs inside a container capped at 2 CPUs / 2 GB,
> like an edge box. Every result goes into a local SQLite outbox first, then to RabbitMQ with publisher
> confirms. A Node.js/TypeScript service consumes it and acks only after the Postgres commit. It dedupes
> by event id and pushes new rows to a React dashboard over WebSocket. I killed RabbitMQ and the Node
> service for 60 s mid-run: 0 of 3000 events lost, 0 duplicate rows. The same broker fault without the
> outbox lost about half the events.

## 2. Data flow (draw this)

```
 frames ─► OpenCV resize/normalise ─► ONNX Runtime (DefectNet) ─► event JSON + 96px JPEG thumb
                                                                   │
                                     inspection thread ────────────┤ spool.put()  (SQLite, WAL, local disk)
                                                                   ▼
                                     sender thread ◄──── outbox ──►  basic_publish + publisher confirm
                                                                   │  delete rows only after confirm
                                                                   ▼
                                                RabbitMQ quorum queue "inspections"
                                                                   │  prefetch 32, manual ack
                                                                   ▼
          Node consumer ─► parse/validate ─► INSERT … ON CONFLICT (id) DO NOTHING ─► ack
                                   │ invalid → reject (no requeue)   │ DB error → nack + requeue after 1 s
                                   ▼                                  ▼ inserted (not duplicate)
                               Postgres  ◄── REST API ──►  React     WebSocket broadcast ─► dashboard
```

## 3. Delivery guarantees: the core of the interview

**Edge → broker: at-least-once.**
- Order of operations: write to the outbox, publish, wait for the confirm, delete the outbox rows.
- A crash between confirm and delete means the event is sent again. That is fine.
- A crash before the confirm means the row is still on disk and gets replayed.
- So nothing is lost as long as the edge disk survives.

**Broker → DB: at-least-once.**
- Ack happens only after `INSERT` returns. That is a single-statement autocommit, so the commit is already done.
- If the service dies after the commit but before the ack, the message is redelivered.

**Exactly-once *effect*: idempotent consumer.**
- The event id is `uuid5(line, run, seq)`. It is deterministic, so a replay carries the same id.
- The PK plus `ON CONFLICT DO NOTHING RETURNING id` turns a redelivery into a no-op.
- `RETURNING` tells us whether this insert was the new one. Only then do we broadcast to dashboards.
- Say clearly: "exactly-once delivery doesn't exist. I built at-least-once plus idempotency."

**Why not ack first?** Acking on receive gives at-most-once. A crash mid-insert loses the event.

**Poison messages:** an invalid body is `reject(requeue=false)`. Otherwise one bad message loops forever
and blocks a prefetch slot. Production would add a dead-letter exchange. That's the obvious next step
(say so).

**DB down:** `nack(requeue=true)` after 1 s. The broker becomes the buffer.

**Ordering:** the outbox replays oldest-first, and one consumer keeps order. The design does not
*depend* on order: rows are keyed by id and `captured_at` comes from the edge clock.

**Why a quorum queue:** it's replicated via Raft and survives node failure. Classic mirrored queues are
deprecated. Here it's a single node, so it's mainly durability on `kill -9`.

## 4. The three bugs found by measuring (the best stories)

**(a) 43 ms per publish: the WSL localhost relay.**
- First smoke run: the worker managed 17 fps against a 50 fps target. Inference was 6.6 ms.
- Profiled each stage. A confirmed publish took **49 ms**. It was the same for quorum, classic and
  transient queues. WSL `fsync` was only ~2 ms, so it wasn't disk.
- ~40 ms is the Nagle + delayed-ACK signature. Client `TCP_NODELAY` was already 1.
- Connecting to the WSL VM's IP directly instead of `localhost`: **0.76 ms**. The Windows→WSL localhost
  forwarder adds ~42 ms per request/response round trip.
- Lesson: profile each stage before tuning. A request/response protocol (confirms) is where a 40 ms
  stall shows up; fire-and-forget hides it.

**(b) Reconnects blocked inference.**
- In the first broker-outage run, the edge fell to **18.8 fps** while the broker was down.
- Cause: `drain()` ran on the inspection loop. Every backoff expiry tried a reconnect that could block up
  to the 2 s socket timeout.
- Fix: a dedicated sender thread owns the connection. The inspection loop only writes to local disk.

**(c) SQLite lock contention after the threading fix.**
- After (b), frame handoff (`spool.put`) was **p50 93 ms** with no fault at all.
- Two connections were fighting over SQLite's single write lock, one row-DELETE commit per message.
  SQLite's busy handler sleeps in 10–100 ms steps.
- Fix: one shared connection behind an in-process lock, and confirmed rows deleted in batches (one
  `DELETE … WHERE n <= last` per batch). Handoff p50 → **0.4 ms**.
- Trade-off: a crash mid-batch re-sends up to 50 events. Idempotency makes that free.

## 5. The model

- **Dataset:** NEU-CLS (figshare release): 6 hot-rolled steel defect classes × 295 grayscale 200×200
  images = 1,770.
- **Split:** stratified, seeded 70/15/15. **The test split is also the "camera" feed**, so the live demo
  never shows a training image.
- **DefectNet:** 4 × (conv3×3 → GroupNorm → ReLU → maxpool), then global average pool → linear.
  186k params, 128×128 input.
- **GroupNorm, not BatchNorm:** the edge runs batch = 1. BatchNorm's train/eval statistics gap is a known
  silent accuracy loss after export. (In wsi-scan-pipeline it cost 0.94 → 0.39.)
- **Training:** AdamW + OneCycle, 40 epochs, flips, label smoothing 0.05. Best epoch chosen on *val*;
  test touched once.
- **Result:** test macro-F1 **0.9963** (269/270). ONNX vs PyTorch argmax agreement 100% on test.
- **Be honest:** NEU-CLS is an easy, clean benchmark. Its images are centred crops of single defects,
  and 99%+ is common in papers. The project's point is the pipeline, not the model.
- **There is no "good" class in NEU-CLS.** Every image is a defect. So the dashboard shows the
  defect-type mix and a **needs-review rate** (confidence < 0.80), not a pass/fail defect rate.
- **Stretch goal (not built):** anomaly detection trained only on good samples (PatchCore / PaDiM on
  MVTec AD). That's how real lines catch defect types they've never seen.

## 6. Edge-device realities to talk about

- **CPU cap:** `cpus: 2` / `mem_limit: 2g` in compose (cgroup CFS quota). `bench_infer.py` also pins
  affinity and sets ORT `intra_op_num_threads = 2`. Oversubscribing threads under a CFS quota causes
  throttling stalls.
- **Line speed vs capacity:** the line runs at 25 fps. Measure the headroom (see the README for the CI
  number). If inference can't keep up, the fix is frame skipping or batching, never an unbounded queue.
- **Outbox sizing:** at 25 fps × ~4 KB per event (mostly the thumbnail), 1 hour offline ≈ 90k rows
  ≈ 360 MB. Next steps would be a cap with oldest-first drop *of thumbnails only*, plus a metric.
- **Clock:** latency uses the edge `captured_at` against the cloud clock. That's fine on one host or in
  CI. Across real machines you need NTP/PTP, or you measure only one-way deltas on the same clock.

## 7. Cloud service: what to say about Node

- **Event loop:** the consumer callback is async. `prefetch(32)` bounds in-flight messages, which is
  backpressure. Without prefetch, RabbitMQ pushes everything and memory grows without bound.
- **pg.Pool max 10.** Each insert is one round trip. Throughput scales with prefetch until the pool
  saturates.
- **WebSocket fan-out:** clients whose `bufferedAmount` exceeds 1 MB are skipped, so one slow client
  can't grow server memory without bound. The dashboard reconnects every 1 s and backfills via REST,
  deduping by id.
- **Express vs Fastify:** Express is more familiar, and throughput isn't the bottleneck here.
- **Metrics:** `cloud_deliveries_total{outcome}`, `cloud_db_insert_seconds`,
  `cloud_edge_to_db_seconds`, `cloud_ws_clients`, `cloud_broker_connected`. On the edge:
  `edge_inference_seconds`, `edge_spool_depth`, `edge_published_total`.

## 8. Likely questions → short answers

- **"Kafka instead?"** With a single consumer group and per-line ordering, Kafka works well too: key by
  `line_id`, commit offsets after the DB write. RabbitMQ fits per-message acks, small volume and simple
  operations at the edge.
- **"What if Postgres is slow?"** Unacked messages pile up to the prefetch limit, then the broker queues
  the rest. The edge doesn't notice.
- **"Scaling to 100 lines?"** Run several consumers on the same queue; dedupe still holds because it's
  in the DB. Batch inserts (multi-row `INSERT … ON CONFLICT`) once the per-row round trip dominates.
  Partition by line if ordering matters.
- **"Where can this still lose data?"** If the edge disk dies while offline. If the outbox fills up
  (not capped yet). If a message is rejected as invalid (by design).
- **"Why SQLite and not a file?"** You get atomic append, ordered replay, delete-by-range and crash
  safety for free. WAL mode allows readers during writes.
- **"How do you test this?"** Unit tests for the handler decisions and the replay storm (exactly one row
  per id after 3× replays). A real-Postgres test of concurrent `ON CONFLICT`. Pytest for outbox FIFO,
  reopen and drain-stops-at-first-failure. Then the fault-injection harness in CI.

## 9. Commands

```bash
docker compose up -d --build rabbitmq postgres cloud     # dashboard: http://localhost:8095
docker compose run --rm edge                             # endless line at 25 fps
python bench/outage.py --env compose --scenario broker   # one fault scenario
python bench/summarize.py results/ci
```
