"""Fault-injection + latency harness.

Scenarios
  steady        no fault; a WebSocket probe measures frame -> dashboard latency
  broker        kill -9 RabbitMQ 30 s into the run, keep it down 60 s, restart
  cloud         hard-kill the Node service 30 s in, keep it down 60 s, restart
  broker-direct same broker fault, but the worker runs without its spool (control)

Environments
  --env compose  docker compose (CI): edge runs in its container capped at 2 CPUs / 2 GB,
                 faults are `docker compose kill` / `start`
  --env local    this Windows laptop: RabbitMQ in WSL, Postgres native, worker + Node as
                 host processes

Each run is audited in Postgres via the API: lost = N - distinct seq stored,
duplicate rows = rows - distinct seq. The PK makes duplicate rows impossible by
construction, so the interesting number is how many redeliveries were absorbed.

    python bench/outage.py --env compose --scenario broker --frames 3000 --fps 25
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
API = "http://localhost:8095"


def get(path):
    with urllib.request.urlopen(API + path, timeout=5) as r:
        return r.read().decode()


def healthy(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        try:
            get("/healthz")
            return True
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("cloud did not come up")


def wait_broker(host, timeout=90):
    import pika
    end = time.time() + timeout
    while time.time() < end:
        try:
            pika.BlockingConnection(pika.URLParameters(f"amqp://guest:guest@{host}:5672/?socket_timeout=2")).close()
            return True
        except Exception:
            time.sleep(1)
    raise RuntimeError("broker did not come up")


def dup_counter() -> int:
    try:
        m = re.search(r'cloud_deliveries_total\{outcome="duplicate"\} (\d+)', get("/metrics"))
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


class Compose:
    """Everything in docker compose; the worker is a one-off `compose run` of the edge service."""
    name = "compose"
    broker_host = "localhost"

    def sh(self, *a, **kw):
        return subprocess.run(["docker", "compose", *a], cwd=ROOT, check=True, **kw)

    def setup(self):
        healthy()
        wait_broker(self.broker_host)

    def broker_kill(self): self.sh("kill", "rabbitmq")
    def broker_start(self): self.sh("start", "rabbitmq")
    def cloud_kill(self): self.sh("kill", "cloud")
    def cloud_start(self): self.sh("start", "cloud")
    def teardown(self): pass

    def worker(self, run, frames, fps, mode, out_dir):
        # /results is bind-mounted to ./results/ci; the spool lives on the container's own volume.
        return subprocess.Popen(
            ["docker", "compose", "run", "--rm", "--no-deps", "-e", f"RUN_ID={run}", "edge",
             "python", "worker.py", "--count", str(frames), "--fps", str(fps), "--run", run, "--mode", mode,
             "--spool", f"/spool/{run}.db", "--metrics-port", "0", "--drain-timeout", "180",
             "--summary", f"/results/{run}.worker.json"], cwd=ROOT)

    def summary_path(self, run, out_dir):
        return os.path.join(out_dir, f"{run}.worker.json")


class Local:
    """Windows laptop: RabbitMQ in WSL (reached by VM IP, not localhost: the WSL localhost
    relay adds ~42 ms to every confirm round trip), Postgres on 5433, Node + worker as processes."""
    name = "local"
    NODE = os.path.join(os.environ.get("NODE_DIR", r"D:\self_projects\.tools\node-v22.20.0-win-x64"), "node.exe")
    PG_URL = os.environ.get("DATABASE_URL", "postgres://inspectflow:inspectflow@localhost:5433/inspectflow")

    def wsl(self, cmd):
        env = dict(os.environ, MSYS_NO_PATHCONV="1")
        return subprocess.run(["wsl", "-d", "Ubuntu-24.04", "--", "bash", "-lc", cmd],
                              capture_output=True, text=True, env=env)

    def setup(self):
        self.broker_host = self.wsl("hostname -I").stdout.split()[0]
        self.amqp = f"amqp://guest:guest@{self.broker_host}:5672/"
        try:
            wait_broker(self.broker_host, 5)
        except RuntimeError:
            self.broker_start()
            wait_broker(self.broker_host)
        self.log = open(os.path.join(ROOT, "results", "local", "cloud.log"), "a")
        self.cloud_start()

    def broker_kill(self): self.wsl("pkill -9 -f beam.smp; true")
    def broker_start(self): self.wsl(". ~/rmq/env.sh; rabbitmq-server -detached")

    def cloud_start(self):
        env = dict(os.environ, DATABASE_URL=self.PG_URL, AMQP_URL=self.amqp, PORT="8095")
        self.cloud = subprocess.Popen([self.NODE, "dist/main.js"], cwd=os.path.join(ROOT, "cloud"), env=env,
                                      stdout=self.log, stderr=subprocess.STDOUT)
        healthy()

    def cloud_kill(self):
        self.cloud.kill()  # TerminateProcess: no graceful shutdown, unacked messages stay with the broker
        self.cloud.wait()

    def teardown(self):
        self.cloud_kill()

    def worker(self, run, frames, fps, mode, out_dir):
        return subprocess.Popen(
            [PY, "worker.py", "--count", str(frames), "--fps", str(fps), "--run", run, "--amqp", self.amqp,
             "--spool", os.path.join(out_dir, f"{run}.spool.db"), "--mode", mode, "--metrics-port", "0",
             "--summary", self.summary_path(run, out_dir), "--drain-timeout", "180"],
            cwd=os.path.join(ROOT, "edge"))

    def summary_path(self, run, out_dir):
        return os.path.join(out_dir, f"{run}.worker.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=["compose", "local"], default="compose")
    ap.add_argument("--scenario", choices=["steady", "broker", "cloud", "broker-direct"], required=True)
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--fps", type=float, default=25)
    ap.add_argument("--fault-at", type=float, default=30)
    ap.add_argument("--fault-for", type=float, default=60)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    env = Compose() if args.env == "compose" else Local()
    out = args.out or os.path.join(ROOT, "results", "ci" if args.env == "compose" else "local")
    os.makedirs(out, exist_ok=True)
    env.setup()
    run = f"{args.scenario}-{int(time.time())}"

    probe = None
    if args.scenario == "steady":
        node = Local.NODE if args.env == "local" else "node"
        probe = subprocess.Popen([node, "scripts/ws-probe.mjs", "ws://localhost:8095/ws", run, str(args.frames),
                                  str(int(args.frames / args.fps + 120))],
                                 cwd=os.path.join(ROOT, "cloud"), stdout=subprocess.PIPE, text=True)
        time.sleep(1)

    mode = "direct" if args.scenario == "broker-direct" else "spool"
    worker = env.worker(run, args.frames, args.fps, mode, out)
    t0 = time.time()
    dups_before_kill = 0
    timeline = []
    if args.scenario != "steady":
        time.sleep(args.fault_at)
        if args.scenario == "cloud":
            dups_before_kill = dup_counter()
            env.cloud_kill()
        else:
            env.broker_kill()
        timeline.append(("fault", round(time.time() - t0, 1)))
        time.sleep(args.fault_for)
        if args.scenario == "cloud":
            env.cloud_start()
            healthy()
        else:
            env.broker_start()
            wait_broker(env.broker_host)
        timeline.append(("restored", round(time.time() - t0, 1)))

    worker.wait()
    timeline.append(("worker_exit", round(time.time() - t0, 1)))
    # Let the consumer drain whatever is still queued.
    last, stable_since = -1, time.time()
    while time.time() - stable_since < 10:
        rows = json.loads(get(f"/api/runs/{run}/audit"))["rows"]
        if rows != last:
            last, stable_since = rows, time.time()
        if rows >= args.frames:
            break
        time.sleep(1)
    timeline.append(("db_settled", round(time.time() - t0, 1)))
    audit = json.loads(get(f"/api/runs/{run}/audit"))
    dups = dups_before_kill + dup_counter()
    ws = json.loads(probe.communicate(timeout=180)[0].strip().splitlines()[-1]) if probe else None
    with open(os.path.join(out, f"{args.scenario}.metrics.txt"), "w") as f:
        f.write(get("/metrics"))
    env.teardown()

    w = json.load(open(env.summary_path(run, out)))
    result = {
        "env": env.name, "scenario": args.scenario, "run": run, "frames": args.frames, "fps": args.fps,
        "fault": None if args.scenario == "steady" else f"{args.fault_for:.0f}s at t={args.fault_at:.0f}s",
        "stored_rows": audit["rows"], "distinct_seq": audit["distinct_seq"],
        "lost": args.frames - audit["distinct_seq"], "duplicate_rows": audit["rows"] - audit["distinct_seq"],
        "redeliveries_absorbed": dups,
        "edge_dropped": w["dropped"], "edge_max_spool_depth": w["max_spool_depth"],
        "edge_achieved_fps": w["achieved_fps"],
        "edge_infer_ms": {k: w[k] for k in ("infer_ms_p50", "infer_ms_p95", "infer_ms_p99")},
        "edge_handoff_ms": {k: w[k] for k in ("handoff_ms_p50", "handoff_ms_p99", "handoff_ms_max")},
        "frame_to_db_ms": audit["latency"], "frame_to_dashboard_ms": ws, "timeline": timeline,
    }
    print(json.dumps(result, indent=2))
    with open(os.path.join(out, f"{args.scenario}.json"), "w") as f:
        json.dump(result, f, indent=2)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(os.path.join(out, f"{run}.spool.db{suffix}"))
        except OSError:
            pass


if __name__ == "__main__":
    main()
