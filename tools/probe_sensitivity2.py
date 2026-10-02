"""
用**真实轨迹里的窗口**做敏感性检验。

上一版 probe_sensitivity.py 喂的是人造窗口
（温度线性上升 + CO/CO2 恒定），这在训练分布之外 ——
任何模型都只能回落到训练分布均值，于是"不敏感"是必然结果，
测出来的结论没有意义。

正确做法：从每条真实轨迹里取真实窗口，在窗口上**整体平移温度**
（物理上相当于"把同样的火灾过程整体升温 50℃"），
看模型输出是否跟着平移。这样输入仍在真实分布附近。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

MULTI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fds", "multi")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def real_windows(prefix, seq):
    """从各工况轨迹里切出真实窗口。"""
    flat = np.genfromtxt(os.path.join(MULTI_DIR, f"{prefix}_raw.csv"),
                         delimiter=",", names=True)
    flat = np.atleast_1d(flat)
    V = np.stack([flat["temperature"], flat["CO"], flat["CO2"]], axis=1)
    rows = np.load(os.path.join(MULTI_DIR, f"{prefix}_bounds.npy"))
    segs, a = [], 0
    for n in rows:
        n = int(n)
        s = V[a:a + n]
        a += n
        # 取每段中段的一个窗口，那里温度高、烟气充分，最接近真实燃烧态
        mid = len(s) // 2
        lo = max(0, mid - seq // 2)
        if lo + seq <= len(s):
            segs.append(s[lo:lo + seq].copy())
    return segs


def probe(model_dir, compartment, tag, windows, shifts):
    import torch
    from model_def import Seq2SeqLSTM
    p = os.path.join(model_dir, compartment, "best_model.pth")
    if not os.path.exists(p):
        print(f"  {tag}: 无模型")
        return None
    ck = torch.load(p, map_location="cpu", weights_only=False)
    c = ck["config"]
    seq, pred = c["seq_len"], c["pred_len"]
    m = Seq2SeqLSTM(3, c["hidden"], c.get("num_layers", 1), 3, seq, pred, 0.0)
    m.load_state_dict(ck["model_state_dict"]); m.eval()
    sc = ck["scaler"]

    print(f"\n  {tag}")
    print(f"    {'工况':>4} {'窗口均值T':>9} | " +
          " ".join(f"{f'+{d}C':>8}" for d in shifts))
    spans = []
    for w in windows:
        base_T = float(w[:, 0].mean())
        outs = []
        for d in shifts:
            w2 = w.copy()
            w2[:, 0] += d
            xs = sc.transform(w2)
            with torch.no_grad():
                o = m(torch.FloatTensor(xs).unsqueeze(0))
            first = sc.inverse_transform(o[0, 0].numpy().reshape(1, -1))[0]
            outs.append(first[0])
        spans.append(max(outs) - min(outs))
        print(f"    {'-':>4} {base_T:>9.0f} | " +
              " ".join(f"{v:>8.1f}" for v in outs))
    mean_span = float(np.mean(spans))
    print(f"    平均响应跨度 {mean_span:.0f}℃")
    return mean_span


if __name__ == "__main__":
    prefix = sys.argv[1] if len(sys.argv) > 1 else "c5"
    comp = sys.argv[2] if len(sys.argv) > 2 else "灶炉间"
    seq = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    shifts = [0, 50, 100, 150, 200]
    wins = real_windows(prefix, seq)
    print(f"  取到 {len(wins)} 个真实窗口")
    a = probe(os.path.join(ROOT, "models_multi"), comp,
              "多工况（5 组条件）", wins, shifts)
    b = probe(os.path.join(ROOT, "models_v3"), comp,
              "单轨迹 v3（1 条曲线）", wins, shifts)
    if a is not None and b is not None:
        better = "多工况" if a > b else "单轨迹"
        print(f"\n  => 更敏感的是: {better}  (多工况 {a:.0f}℃ vs 单轨迹 {b:.0f}℃)")
