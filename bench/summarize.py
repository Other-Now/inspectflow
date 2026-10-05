"""Render results/<env>/*.json as a markdown table and fail if the guarantees broke.

    python bench/summarize.py results/ci
"""
import json
import os
import sys

d = sys.argv[1]
out = []
infer = os.path.join(d, "infer_2cpu.json")
if os.path.exists(infer):
    r = json.load(open(infer))
    out += ["### Edge inference (batch=1, preprocess + model, 2 CPUs)", "",
            f"{r['fps']} fps · p50 {r['p50_ms']} ms · p95 {r['p95_ms']} ms · p99 {r['p99_ms']} ms ({r['pin']})", ""]

out += ["### Pipeline runs", "",
        "| scenario | frames | stored | lost | dup rows | redeliveries absorbed | max spool | edge fps | frame→DB p50/p95 ms | frame→dashboard p50/p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|"]
bad = []
for s in ("steady", "broker", "cloud", "broker-direct"):
    p = os.path.join(d, f"{s}.json")
    if not os.path.exists(p):
        out.append(f"| {s} | — missing — |||||||||")
        bad.append(f"{s}: missing")
        continue
    r = json.load(open(p))
    db = r["frame_to_db_ms"]
    ws = r["frame_to_dashboard_ms"] or {}
    f = lambda v: "—" if v is None else f"{v:.1f}"
    out.append(f"| {s} | {r['frames']} | {r['stored_rows']} | {r['lost']} | {r['duplicate_rows']} | "
               f"{r['redeliveries_absorbed']} | {r['edge_max_spool_depth']} | {r['edge_achieved_fps']} | "
               f"{f(db['p50_ms'])} / {f(db['p95_ms'])} | {f(ws.get('p50_ms'))} / {f(ws.get('p95_ms'))} |")
    if r["duplicate_rows"]:
        bad.append(f"{s}: {r['duplicate_rows']} duplicate rows")
    if s != "broker-direct" and r["lost"]:
        bad.append(f"{s}: lost {r['lost']}")
    if s == "broker-direct" and r["lost"] == 0:
        bad.append("control lost nothing: the fault did not bite")

text = "\n".join(out) + "\n"
with open(os.path.join(d, "SUMMARY.md"), "w") as fh:
    fh.write(text)
print(text)
if bad:
    print("FAILED: " + "; ".join(bad))
    sys.exit(1)
