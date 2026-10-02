"""
用与 retrain.py 完全相同的切分方式，评估已保存的新模型，
并与原始 checkpoint（按真实部署方式喂数据）做同口径对比。

同口径的含义：两边都用 0.1s 均匀网格、都只看时间顺序最后 15% 的测试段。
"""
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from lstm_pipeline import (CASES, FEATURES, load_raw, make_windows,
                           per_channel_constant, resample_uniform)
from model_def import Seq2SeqLSTM
from retrain import STEP, build, segments

ROOT = r"D:\PyrosimLSTM"
OLD_DIR = {
    "主机舱": os.path.join(ROOT, "zjc.LSTM(new)", "models"),
    "机库": os.path.join(ROOT, "JK_LSTM", "models"),
    "士兵住舱": os.path.join(ROOT, "SBZV.lstm", "models"),
    "灶炉间": os.path.join(ROOT, "LZJ.LSTM", "models"),
}
NEW_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models_v2")


def score(model, scaler, X, Y):
    if len(X) == 0:
        return None
    with torch.no_grad():
        p = model(torch.FloatTensor(X)).numpy()
    p = scaler.inverse_transform(p.reshape(-1, p.shape[2])).reshape(p.shape)
    res = []
    for c in range(Y.shape[2]):
        yt, yp = Y[:, :, c], p[:, :, c]
        rng = float(yt.max() - yt.min())
        mae = float(np.mean(np.abs(yt - yp)))
        res.append(dict(
            rel=(mae / rng * 100) if rng > 1e-12 else float("nan"),
            span_ratio=(float((yp.max() - yp.min()) / rng) * 100) if rng > 1e-12 else float("nan"),
        ))
    return res


def load_old(name):
    p = os.path.join(OLD_DIR[name], "best_model.pth")
    if not os.path.exists(p):
        return None
    ck = torch.load(p, map_location="cpu", weights_only=False)
    sd = ck["model_state_dict"]
    m = Seq2SeqLSTM(sd["encoder_lstm.weight_ih_l0"].shape[1], sd["fc.weight"].shape[1],
                    2, sd["fc.weight"].shape[0], 150, 150, 0.0)
    m.load_state_dict(sd, strict=False)
    m.eval()
    return m, ck["scaler"]


def load_new(name):
    p = os.path.join(NEW_ROOT, name, "best_model.pth")
    if not os.path.exists(p):
        return None
    ck = torch.load(p, map_location="cpu", weights_only=False)
    cfg = ck["config"]
    layers = cfg.get("num_layers", cfg.get("layers", 2))
    m = Seq2SeqLSTM(3, cfg["hidden"], layers, 3,
                    cfg["seq_len"], cfg["pred_len"], 0.0)
    m.load_state_dict(ck["model_state_dict"])
    m.eval()
    return m, ck["scaler"], cfg


def main():
    targets = sys.argv[1:] or list(CASES)
    for name in targets:
        print(f"\n{'═'*72}\n  {name}\n{'═'*72}")
        t, v = load_raw(name)
        _, data = resample_uniform(t, v, STEP)
        const = per_channel_constant(v)
        tr, va, te = segments(data)

        new = load_new(name)
        if new is None:
            print("  新模型尚未训练完成，跳过")
            continue
        nm, ns, ncfg = new
        seq, pred = ncfg["seq_len"], ncfg["pred_len"]

        # 新模型：按自己的窗口在测试段建窗
        from sklearn.preprocessing import StandardScaler
        sc = StandardScaler()
        build(tr, seq, pred, sc, fit=True)          # 复现训练时的标准化器
        Xte, Yte = build(te, seq, pred, sc)
        n_res = score(nm, ns, Xte, Yte)

        # 旧模型：按部署方式（150 步）喂同一段数据
        om = load_old(name)
        o_res = None
        if om:
            Xo, Yo = make_windows(te, 150, 150)
            if len(Xo) == 0:
                Xo, Yo = make_windows(data[-160:], 150, 150)
            o_res = score(om[0], om[1], Xo, Yo)

        print(f"  新模型窗口 输入 {seq} 步({seq*STEP:.1f}s) -> 预测 {pred} 步({pred*STEP:.1f}s)"
              f" | 测试窗 {len(Xte)}")
        for i, f in enumerate(FEATURES):
            deg = "  ⚠源数据该通道恒定" if const[i] <= 2 else ""
            n = n_res[i] if n_res else None
            o = o_res[i] if o_res else None
            of = f"旧 {o['rel']:6.2f}% / 动态 {o['span_ratio']:5.1f}%" if o else "旧  (无法评估)"
            nf = f"新 {n['rel']:6.2f}% / 动态 {n['span_ratio']:5.1f}%" if n else "新  (无窗口)"
            print(f"    {f:<20} {of}   |   {nf}{deg}")
        if n_res and o_res:
            gains = [o_res[i]["rel"] - n_res[i]["rel"] for i in range(3)
                     if np.isfinite(o_res[i]["rel"]) and np.isfinite(n_res[i]["rel"])]
            if gains:
                print(f"    → 可比通道平均相对误差改善 {np.mean(gains):+.2f} 个百分点")


if __name__ == "__main__":
    main()
