"""Inference throughput/latency on a CPU budget that mimics an edge box.

Pins the process to N cores (Linux: sched_setaffinity, Windows: SetProcessAffinityMask)
and gives ONNX Runtime the same N threads. Measures the full per-frame path the
worker runs: OpenCV resize/normalise + model + softmax, batch=1, unpaced.

    python bench_infer.py --cores 2 --frames 3000
"""
import argparse
import json
import os
import platform
import sys
import time

import numpy as np

from preprocess import load_gray
from worker import inspect, make_session


def pin(cores: int) -> str:
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, set(range(cores)))
        return f"sched_setaffinity({cores})"
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        # Without argtypes ctypes passes the pseudo-handle/mask as 32-bit ints and the call fails.
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        mask = (1 << cores) - 1
        ok = k32.SetProcessAffinityMask(k32.GetCurrentProcess(), mask)
        return f"SetProcessAffinityMask(0x{mask:x}) ok={bool(ok)}"
    return "not pinned"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="../models/defectnet.onnx")
    ap.add_argument("--frames-dir", default="../data/train/train/images")
    ap.add_argument("--list", default="../models/test_split.txt")
    ap.add_argument("--cores", type=int, default=2)
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    how = pin(args.cores)
    names = [l.strip() for l in open(args.list) if l.strip()]
    imgs = [load_gray(os.path.join(args.frames_dir, n)) for n in names]
    sess = make_session(args.model, args.cores)
    for i in range(50):
        inspect(sess, imgs[i % len(imgs)])
    lat = []
    t0 = time.perf_counter()
    for i in range(args.frames):
        a = time.perf_counter()
        inspect(sess, imgs[i % len(imgs)])
        lat.append((time.perf_counter() - a) * 1000)
    wall = time.perf_counter() - t0
    r = {
        "host": platform.processor() or platform.machine(), "pin": how, "ort_threads": args.cores,
        "frames": args.frames, "fps": round(args.frames / wall, 1),
        "p50_ms": round(float(np.percentile(lat, 50)), 3), "p95_ms": round(float(np.percentile(lat, 95)), 3),
        "p99_ms": round(float(np.percentile(lat, 99)), 3),
    }
    print(json.dumps(r))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(r, f, indent=2)


if __name__ == "__main__":
    main()
