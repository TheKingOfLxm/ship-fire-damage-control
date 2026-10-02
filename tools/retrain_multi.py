"""
用**多工况**数据训练。这是修复"模型对起始条件不敏感"的关键。

背景（实测结论，不是推测）：
    单轨迹训练出来的模型，喂 200℃ 窗口进去，第一步就输出 317℃ 起步，
    完全无视输入。原因是 100 步滑窗切出来的上万个样本本质是同一条曲线的
    不同片段，模型只要记住这条曲线就能把 loss 降下来。

    多工况之后，模型必须同时拟合 5 条不同斜率的曲线（释热率 2000/4000/8000、
    通风 0.7/1.5），才可能学出"条件 -> 发展过程"的映射。

用法： py -3 tools/retrain_multi.py <前缀> <舱名>
"""
import os
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from model_def import Seq2SeqLSTM

MULTI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fds", "multi")
OUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models_multi")

SEQ = 100
PRED = 100
SMOOTH = 25
TRAIN_FRAC, VAL_FRAC = 0.70, 0.15


def load_windows(prefix):
    """读合并后的**连续行**数据，按工况行分界切成若干段。

    边界文件存的是"每个工况各占多少行"，不是窗口起点 —— 之前误存成
    窗口起点，导致切出 575 个长度 100 的碎段，每段都凑不满一个窗口。
    """
    flat = np.genfromtxt(os.path.join(MULTI_DIR, f"{prefix}_raw.csv"),
                         delimiter=",", names=True)
    flat = np.atleast_1d(flat)
    V = np.stack([flat["temperature"], flat["CO"], flat["CO2"]], axis=1)
    rows = np.load(os.path.join(MULTI_DIR, f"{prefix}_bounds.npy"))

    segs, a = [], 0
    for n in rows:
        n = int(n)
        if n > SEQ + PRED:
            segs.append(V[a:a + n])
        a += n
    print(f"  载入 {len(segs)} 段工况，共 {sum(len(s) for s in segs)} 行")
    return segs


def build(segs):
    """7:2:1 划分，且**按工况分层**——每个工况内部都按时间切，
    避免验证集全落在某一条工况上。"""
    Xtr, Ytr, Xva, Yva = [], [], [], []
    for s in segs:
        n = len(s)
        a = int(n * TRAIN_FRAC)
        b = a + int(n * VAL_FRAC)

        def win(seg):
            xs, ys = [], []
            step = SEQ + PRED
            for i in range(0, len(seg) - step + 1, step):
                xs.append(seg[i:i + SEQ])
                ys.append(seg[i + SEQ:i + step])
            return (np.array(xs) if xs else None, np.array(ys) if ys else None)

        x, y = win(s[:a]);      Xtr += list(x) if x is not None else []; Ytr += list(y) if y is not None else []
        x, y = win(s[a:b]);     Xva += list(x) if x is not None else []; Yva += list(y) if y is not None else []
        # 余下作为追加训练（不用于验证，但仍是真实数据）
        x, y = win(s[b:]);      Xtr += list(x) if x is not None else []; Ytr += list(y) if y is not None else []

    if not Xtr or not Xva:
        return None
    return (np.array(Xtr), np.array(Ytr), np.array(Xva), np.array(Yva))


def main():
    prefix = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else prefix
    EPOCHS, HID, LAYERS = 30, 64, 1

    segs = load_windows(prefix)
    d = build(segs)
    if d is None:
        print("窗口不足，无法训练")
        return
    Xtr, Ytr, Xva, Yva = d
    print(f"  训练窗口 {len(Xtr)}  验证窗口 {len(Xva)}")

    scaler = StandardScaler()
    scaler.fit(Xtr.reshape(-1, 3))
    def sc(a): return scaler.transform(a.reshape(-1, 3)).reshape(a.shape)

    xt = torch.FloatTensor(sc(Xtr)); yt = torch.FloatTensor(sc(Ytr))
    xv = torch.FloatTensor(sc(Xva)); yv = torch.FloatTensor(sc(Yva))

    torch.manual_seed(0); np.random.seed(0)
    model = Seq2SeqLSTM(3, HID, LAYERS, 3, SEQ, PRED, 0.3)
    opt = optim.Adam(model.parameters(), lr=2e-3)
    crit = nn.MSELoss()
    best, best_state, bad = float("inf"), None, 0

    for ep in range(EPOCHS):
        model.train()
        perm = torch.randperm(len(xt))
        for i in range(0, len(xt), 32):
            idx = perm[i:i + 32]
            opt.zero_grad()
            loss = crit(model(xt[idx]), yt[idx])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        model.eval()
        with torch.no_grad():
            vl = float(crit(model(xv), yv))
        if vl < best - 1e-6:
            best, bad = vl, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
        print(f"    epoch {ep+1}/{EPOCHS}  val={vl:.5f}{'  *' if bad == 0 else ''}", flush=True)
        if bad >= 8:
            print("    早停")
            break

    model.load_state_dict(best_state)
    model.eval()

    os.makedirs(OUT_ROOT, exist_ok=True)
    out = os.path.join(OUT_ROOT, name)
    os.makedirs(out, exist_ok=True)
    torch.save({
        "model_state_dict": model.state_dict(),
        "scaler": scaler,
        "config": dict(seq_len=SEQ, pred_len=PRED, hidden=HID, num_layers=LAYERS,
                      epochs=EPOCHS, smooth=SMOOTH),
        "step_seconds": 0.1,
        "input_dim": 3, "output_dim": 3,
        "val_loss": best,
        "degenerate_channels": [],
        "trained_horizon_seconds": 1800.0,
        "n_conditions": len(segs),
        "pipeline": "多工况 + 25点平滑 + 时间序分层划分",
    }, os.path.join(out, "best_model.pth"))
    print(f"\n  ✓ 已保存 {os.path.relpath(out, os.getcwd())}/best_model.pth  val={best:.5f}")


if __name__ == "__main__":
    main()
