"""Train a small defect classifier on NEU-CLS and export it to ONNX.

Split is stratified and seeded (70/15/15). The test split is never touched
during training or model selection; it is also what the edge worker replays as
the "production line", so the live demo only ever sees unseen images.

    python train.py --data ../data/train/train/images --out ../models
"""
import argparse
import json
import os
import random
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from preprocess import CLASSES, label_from_filename, load_gray, to_tensor


class DefectNet(nn.Module):
    """~0.3M params. GroupNorm, not BatchNorm: the edge worker runs batch=1 and
    BatchNorm's train/eval statistics gap is a known way to lose accuracy
    silently after export."""

    def __init__(self, n_classes: int = len(CLASSES)):
        super().__init__()

        def block(cin, cout):
            return nn.Sequential(
                nn.Conv2d(cin, cout, 3, padding=1, bias=False),
                nn.GroupNorm(8, cout),
                nn.ReLU(inplace=True),
                nn.MaxPool2d(2),
            )

        self.features = nn.Sequential(block(1, 32), block(32, 64), block(64, 96), block(96, 128))
        self.head = nn.Linear(128, n_classes)

    def forward(self, x):
        x = self.features(x)
        x = F.adaptive_avg_pool2d(x, 1).flatten(1)
        return self.head(x)


def split(files, seed=7):
    by_cls = {}
    for f in files:
        by_cls.setdefault(label_from_filename(f), []).append(f)
    rng = random.Random(seed)
    tr, va, te = [], [], []
    for c in sorted(by_cls):
        fs = sorted(by_cls[c])
        rng.shuffle(fs)
        n = len(fs)
        a, b = int(0.70 * n), int(0.85 * n)
        tr += fs[:a]
        va += fs[a:b]
        te += fs[b:]
    return tr, va, te


def load(data, files):
    x = np.concatenate([to_tensor(load_gray(os.path.join(data, f))) for f in files])
    y = np.array([label_from_filename(f) for f in files], dtype=np.int64)
    return torch.from_numpy(x), torch.from_numpy(y)


def augment(x):
    if random.random() < 0.5:
        x = x.flip(3)
    if random.random() < 0.5:
        x = x.flip(2)
    return x


def macro_f1(y, p, k):
    f1s, per = [], {}
    for c in range(k):
        tp = int(((p == c) & (y == c)).sum())
        fp = int(((p == c) & (y != c)).sum())
        fn = int(((p != c) & (y == c)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        f1s.append(f1)
        per[CLASSES[c]] = round(f1, 4)
    return float(np.mean(f1s)), per


@torch.no_grad()
def predict(model, x, dev):
    model.eval()
    out = []
    for i in range(0, len(x), 256):
        out.append(model(x[i:i + 256].to(dev)).argmax(1).cpu())
    return torch.cat(out).numpy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data/train/train/images")
    ap.add_argument("--out", default="../models")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    files = sorted(f for f in os.listdir(args.data) if f.endswith(".jpg"))
    tr, va, te = split(files, args.seed)
    xtr, ytr = load(args.data, tr)
    xva, yva = load(args.data, va)
    xte, yte = load(args.data, te)
    print(f"train {len(tr)} val {len(va)} test {len(te)} device {dev}")

    model = DefectNet().to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=args.epochs * ((len(tr) + 31) // 32))
    best, best_state = -1.0, None
    t0 = time.time()
    for ep in range(args.epochs):
        model.train()
        perm = torch.randperm(len(xtr))
        for i in range(0, len(perm), 32):
            idx = perm[i:i + 32]
            xb, yb = augment(xtr[idx]).to(dev), ytr[idx].to(dev)
            loss = F.cross_entropy(model(xb), yb, label_smoothing=0.05)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
        f1, _ = macro_f1(yva.numpy(), predict(model, xva, dev), len(CLASSES))
        if f1 > best:
            best, best_state = f1, {k: v.detach().clone() for k, v in model.state_dict().items()}
        if ep % 5 == 4 or ep == args.epochs - 1:
            print(f"epoch {ep + 1:3d} loss {loss.item():.3f} val_macroF1 {f1:.4f}")
    model.load_state_dict(best_state)
    train_s = time.time() - t0

    pte = predict(model, xte, dev)
    f1, per = macro_f1(yte.numpy(), pte, len(CLASSES))
    acc = float((pte == yte.numpy()).mean())
    cm = np.zeros((len(CLASSES), len(CLASSES)), dtype=int)
    for a, b in zip(yte.numpy(), pte):
        cm[a, b] += 1
    print(f"TEST macroF1 {f1:.4f} acc {acc:.4f}")

    os.makedirs(args.out, exist_ok=True)
    model.cpu().eval()
    onnx_path = os.path.join(args.out, "defectnet.onnx")
    torch.onnx.export(model, torch.zeros(1, 1, 128, 128), onnx_path, input_names=["image"],
                      output_names=["logits"], dynamic_axes={"image": {0: "n"}, "logits": {0: "n"}},
                      opset_version=17)

    # Parity check: ONNX Runtime must agree with PyTorch on the held-out set.
    import onnxruntime as ort
    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    ort_pred = sess.run(None, {"image": xte.numpy()})[0].argmax(1)
    parity = float((ort_pred == pte).mean())
    print(f"onnx/torch argmax agreement on test: {parity:.4f}")

    report = {
        "dataset": "NEU-CLS (figshare 28903550), 6 classes x 295 images",
        "split": {"train": len(tr), "val": len(va), "test": len(te), "seed": args.seed},
        "params": sum(p.numel() for p in model.parameters()),
        "epochs": args.epochs, "train_seconds": round(train_s, 1), "device": dev,
        "val_macro_f1_best": round(best, 4),
        "test_macro_f1": round(f1, 4), "test_accuracy": round(acc, 4), "test_per_class_f1": per,
        "test_confusion": cm.tolist(), "classes": CLASSES, "onnx_parity": parity,
    }
    with open(os.path.join(args.out, "train_report.json"), "w") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(args.out, "test_split.txt"), "w", newline="\n") as f:
        f.write("\n".join(te) + "\n")
    print(json.dumps({k: report[k] for k in ("test_macro_f1", "test_accuracy", "params")}))


if __name__ == "__main__":
    main()
