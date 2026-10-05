"""Edge inspection worker: frames -> OpenCV preprocess -> ONNX Runtime -> spool -> RabbitMQ.

Frames come from a folder (the held-out test split) replayed at line speed.

    python worker.py --frames ../data/train/train/images --list ../models/test_split.txt \
        --count 2000 --fps 25 --line L1 --amqp amqp://guest:guest@localhost:5672/

--mode direct is the naive control: publish straight to the broker, drop the
event if that fails. It exists only so the outage test has something to compare against.
"""
import argparse
import base64
import json
import os
import threading
import time
import uuid

import numpy as np
import onnxruntime as ort
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from preprocess import CLASSES, load_gray, thumbnail_jpeg, to_tensor
from publisher import Publisher
from spool import Spool

NS = uuid.UUID("6f1c2b2e-5d0a-4c55-9a43-2f3b9a1e7c10")
REVIEW_THRESHOLD = 0.80

INFER = Histogram("edge_inference_seconds", "preprocess + model time per frame",
                  buckets=(.001, .002, .004, .006, .008, .01, .015, .02, .03, .05, .1))
SPOOL_DEPTH = Gauge("edge_spool_depth", "events waiting in the local outbox")
PUBLISHED = Counter("edge_published_total", "events confirmed by the broker")
DROPPED = Counter("edge_dropped_total", "events lost (direct mode only)")


def make_session(model: str, threads: int) -> ort.InferenceSession:
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


def inspect(sess, gray):
    probs = softmax(sess.run(None, {"image": to_tensor(gray)})[0][0])
    k = int(probs.argmax())
    return CLASSES[k], float(probs[k])


def build_event(line, run, seq, captured_ms, label, conf, infer_ms, thumb):
    return {
        "id": str(uuid.uuid5(NS, f"{line}:{run}:{seq}")),
        "line_id": line, "run_id": run, "seq": seq,
        "captured_at": captured_ms, "label": label, "confidence": round(conf, 4),
        "needs_review": conf < REVIEW_THRESHOLD, "infer_ms": round(infer_ms, 3),
        "model": "defectnet-v1", "thumb_jpeg_b64": base64.b64encode(thumb).decode(),
    }


class Sender(threading.Thread):
    """Owns the broker connection. Drains the outbox oldest-first; a dead broker
    (connect timeouts, backoff) only ever blocks this thread, never inspection."""

    def __init__(self, spool: Spool, amqp_url: str):
        super().__init__(daemon=True, name="sender")
        self.sp, self.pub = spool, Publisher(amqp_url)
        self.wake = threading.Event()
        self.deadline = None  # set by finish(): exit once empty or past deadline

    def run(self):
        sp = self.sp
        while True:
            n = self.pub.drain(sp, budget=500)
            PUBLISHED.inc(n)
            if self.deadline is not None and (sp.depth() == 0 or time.monotonic() > self.deadline):
                break
            if n == 0:
                self.pub.pump()
                self.wake.wait(0.05)
                self.wake.clear()
        self.pub.close()

    def finish(self, timeout_s: float):
        self.deadline = time.monotonic() + timeout_s
        self.wake.set()
        self.join()


def pct(xs, q):
    return float(np.percentile(xs, q)) if xs else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default=os.environ.get("FRAMES_DIR", "../data/train/train/images"))
    ap.add_argument("--list", default=os.environ.get("FRAMES_LIST", "../models/test_split.txt"))
    ap.add_argument("--model", default=os.environ.get("MODEL_PATH", "../models/defectnet.onnx"))
    ap.add_argument("--amqp", default=os.environ.get("AMQP_URL", "amqp://guest:guest@localhost:5672/"))
    ap.add_argument("--spool", default=os.environ.get("SPOOL_PATH", "spool.db"))
    ap.add_argument("--line", default=os.environ.get("LINE_ID", "L1"))
    ap.add_argument("--run", default=os.environ.get("RUN_ID", "r1"))
    ap.add_argument("--count", type=int, default=int(os.environ.get("FRAME_COUNT", "1000")))
    ap.add_argument("--fps", type=float, default=float(os.environ.get("LINE_FPS", "25")))
    ap.add_argument("--threads", type=int, default=int(os.environ.get("ORT_THREADS", "2")))
    ap.add_argument("--mode", choices=["spool", "direct"], default="spool")
    ap.add_argument("--metrics-port", type=int, default=int(os.environ.get("METRICS_PORT", "9101")))
    ap.add_argument("--drain-timeout", type=float, default=120.0)
    ap.add_argument("--summary", default="")
    args = ap.parse_args()

    names = [l.strip() for l in open(args.list) if l.strip()]
    images = [load_gray(os.path.join(args.frames, n)) for n in names]  # "camera" buffer
    sess = make_session(args.model, args.threads)
    inspect(sess, images[0])  # warm-up
    if args.metrics_port:
        start_http_server(args.metrics_port)

    spool = Spool(args.spool)
    sender = Sender(spool, args.amqp) if args.mode == "spool" else None
    pub = None if sender else Publisher(args.amqp)
    if sender:
        sender.start()
    period = 1.0 / args.fps if args.fps > 0 else 0.0
    infer_ms, handoff_ms, max_depth, dropped, down_frames = [], [], 0, 0, 0
    t_start = time.perf_counter()
    next_t = t_start
    for seq in range(args.count):
        if period:
            now = time.perf_counter()
            if now < next_t:
                time.sleep(next_t - now)
            next_t += period
        gray = images[seq % len(images)]
        captured_ms = time.time() * 1000.0
        t0 = time.perf_counter()
        label, conf = inspect(sess, gray)
        dt = (time.perf_counter() - t0) * 1000.0
        INFER.observe(dt / 1000.0)
        infer_ms.append(dt)
        ev = build_event(args.line, args.run, seq, captured_ms, label, conf, dt, thumbnail_jpeg(gray))
        body = json.dumps(ev).encode()

        t1 = time.perf_counter()
        if sender:
            # The inspection loop only ever touches local disk; the sender thread owns the network.
            spool.put(ev["id"], body)
            sender.wake.set()
            depth = spool.depth()
            SPOOL_DEPTH.set(depth)
            max_depth = max(max_depth, depth)
            if depth > 1:
                down_frames += 1
        else:
            if pub.publish(body, ev["id"]):
                PUBLISHED.inc()
            else:
                dropped += 1
                DROPPED.inc()
            pub.pump()
        handoff_ms.append((time.perf_counter() - t1) * 1000.0)
    run_s = time.perf_counter() - t_start

    # End of shift: let the sender empty the outbox (or give up after drain_timeout).
    reconnects = pub.reconnects if pub else 0
    if sender:
        sender.finish(args.drain_timeout)
        reconnects = sender.pub.reconnects
    left = spool.depth()
    SPOOL_DEPTH.set(left)

    summary = {
        "mode": args.mode, "line": args.line, "run": args.run, "frames": args.count,
        "target_fps": args.fps, "achieved_fps": round(args.count / run_s, 2), "threads": args.threads,
        "infer_ms_p50": round(pct(infer_ms, 50), 3), "infer_ms_p95": round(pct(infer_ms, 95), 3),
        "infer_ms_p99": round(pct(infer_ms, 99), 3),
        "handoff_ms_p50": round(pct(handoff_ms, 50), 3), "handoff_ms_p99": round(pct(handoff_ms, 99), 3),
        "handoff_ms_max": round(max(handoff_ms), 3), "max_spool_depth": max_depth,
        "frames_with_backlog": down_frames, "left_in_spool": left, "dropped": dropped,
        "reconnects": reconnects,
    }
    print(json.dumps(summary))
    if args.summary:
        with open(args.summary, "w") as f:
            json.dump(summary, f, indent=2)
    if pub:
        pub.close()
    spool.close()


if __name__ == "__main__":
    main()
