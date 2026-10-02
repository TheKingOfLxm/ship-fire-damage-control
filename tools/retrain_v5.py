# -*- coding: utf-8 -*-
"""v5：改用**增量目标**训练 LSTM。

======================================================================
为什么要改目标定义
======================================================================
v4（绝对值目标）在灶炉间上的实测行为：

    起点窗口均温   52.1   73.6   83.7   92.2  111.7 ℃
    模型输出      253.9  253.9  253.9  253.9  253.9 ℃

完全无输入响应，一律输出训练数据的平台温度。原因不是网络太小，
而是**任务定义**决定的：

  绝对值目标下，「当前 80℃ 时 60 秒后是多少」的正确答案是
  「所有可能结果的均值」。而这条 30 分钟轨迹里 90% 的时间是
  220~250℃ 的平台段，均值自然就是 ~252℃。MSE 回归到条件均值
  在数学上就是最优解，再调容量/轮数都改不动。

改成**增量目标**后，零增量才是中性答案：

    增长段(0~240s)   60 秒增量  +50 ~ +80 ℃
    平台段(240~1400s) 60 秒增量  ≈ 0 ℃
    衰减段(1400s~)   60 秒增量  -30 ~ -50 ℃

而且这三个阶段的**斜率**在 60 秒历史窗口里是能区分的
（增长 ~+1℃/s，平台 ~+0.3℃/s，衰减 ~-0.7℃/s），
所以这是一个可学的映射，绝对值那个不是。

推理时还原：pred_abs = pred_delta + x_last。
"""
import os
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from lstm_pipeline import (FEATURES, load_raw, make_windows,
                           per_channel_constant, resample_uniform)
from model_def import Seq2SeqLSTM

STEP = 0.5
OUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "models_v5")

HID, LAYERS, SEQ, PRED = 96, 2, 120, 120
EPOCHS, LR, DROPOUT, SMOOTH = 80, 1.5e-3, 0.2, 10


def windows_delta(data, seq, pred):
    """返回 (X, Y_delta)：Y_delta = Y - X[:, -1:, :]，逐通道。"""
    X, Y = make_windows(data, seq, pred)
    return X, Y - X[:, -1:, :]


def main():
    targets = sys.argv[1:] or ["灶炉间"]
    os.makedirs(OUT_ROOT, exist_ok=True)

    for name in targets:
        print(f"\n{'='*68}\n  {name}  (增量目标)\n{'='*68}")
        t, v = load_raw(name, prefer_30min=True)
        const = per_channel_constant(v)
        bad = [FEATURES[i] for i in range(3) if const[i] <= 2]
        print(f"  数据 {len(t)} 点 / {t[-1]-t[0]:.0f}s   退化通道 {bad or '无'}")

        data = resample_uniform(t, v, STEP)[1]
        if SMOOTH > 1:
            k = np.ones(SMOOTH) / SMOOTH
            for c in range(data.shape[1]):
                data[:, c] = np.convolve(data[:, c], k, mode="same")

        # 时间序切分：训练段 / 验证段 / 测试段互不重叠
        n = len(data)
        vlen = max(int(n * 0.12), SEQ + PRED + 8)
        tr_d, va_d, te_d = data[:n - 2 * vlen], data[n - 2 * vlen:n - vlen], data[n - vlen:]
        print(f"  切分 训{tr_d.shape[0]} 验{va_d.shape[0]} 测{te_d.shape[0]}")

        mu = tr_d.reshape(-1, 3).mean(axis=0)
        sd = tr_d.reshape(-1, 3).std(axis=0) + 1e-8
        # 增量目标的尺度用**训练段的增量分布**，不是全序列的绝对值尺度
        Xtr, Ytr = windows_delta((tr_d - mu) / sd, SEQ, PRED)
        Xva, Yva = windows_delta((va_d - mu) / sd, SEQ, PRED)
        Xte, Yte = windows_delta((te_d - mu) / sd, SEQ, PRED)
        dmu, dsd = Ytr.reshape(-1, 3).mean(axis=0), Ytr.reshape(-1, 3).std(axis=0) + 1e-8
        Ytr, Yva, Yte = Ytr / dsd, Yva / dsd, Yte / dsd
        print(f"  窗 训{len(Xtr)} 验{len(Xva)} 测{len(Xte)}   "
              f"增量std={np.round(dsd,2)}")

        torch.manual_seed(0)
        np.random.seed(0)
        model = Seq2SeqLSTM(3, HID, LAYERS, 3, SEQ, PRED, DROPOUT)
        opt = optim.Adam(model.parameters(), lr=LR)
        sched = optim.lr_scheduler.ReduceLROnPlateau(opt, "min", factor=0.5, patience=15)
        crit = nn.MSELoss()
        xt = torch.FloatTensor(Xtr); yt = torch.FloatTensor(Ytr)
        xv = torch.FloatTensor(Xva); yv = torch.FloatTensor(Yva)

        best, best_state, bad_ep = float("inf"), None, 0
        for ep in range(1, EPOCHS + 1):
            model.train()
            perm = torch.randperm(len(xt))
            tot = 0.0
            for i in range(0, len(xt), 64):
                idx = perm[i:i + 64]
                opt.zero_grad()
                loss = crit(model(xt[idx]), yt[idx])
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                tot += float(loss) * len(idx)
            model.eval()
            with torch.no_grad():
                vl = float(crit(model(xv), yv))
            sched.step(vl)
            if vl < best - 1e-5:
                best, bad_ep = vl, 0
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
            else:
                bad_ep += 1
            if ep % 5 == 0 or ep == 1:
                print(f"      epoch {ep:>3}/{EPOCHS}  train={tot/len(xt):.5f}  val={vl:.5f}")
            if bad_ep >= 25:
                print(f"      早停 @ epoch {ep}")
                break

        model.load_state_dict(best_state)
        model.eval()
        with torch.no_grad():
            pt = model(torch.FloatTensor(Xte)).numpy()
        pd_ = pt * dsd                                   # 还原成物理增量
        pred_abs = pd_ + Xte[:, -1:, :]
        true_abs = Yte * dsd + Xte[:, -1:, :]

        print(f"  最佳 val={best:.5f}")
        print(f"  {'通道':<20}{'增量相对误差':>14}{'绝对相对误差':>14}{'动态比':>10}")
        for i, f in enumerate(FEATURES):
            rd = np.abs(pd_[..., i]).mean()
            rt = np.abs(true_abs[..., i]).mean()
            span_t = true_abs[..., i].max() - true_abs[..., i].min()
            span_p = pred_abs[..., i].max() - pred_abs[..., i].min()
            print(f"  {f:<20}{rd/ (rt+1e-9)*100:13.1f}%"
                  f"{(np.abs(pred_abs[..., i]-true_abs[..., i]).mean()/(rt+1e-9))*100:13.1f}%"
                  f"{(span_p/ (span_t+1e-9))*100:9.0f}%")

        out = os.path.join(OUT_ROOT, name)
        os.makedirs(out, exist_ok=True)
        torch.save({
            "model_state_dict": best_state,
            "config": {"hidden": HID, "layers": LAYERS, "num_layers": LAYERS,
                       "seq_len": SEQ, "pred_len": PRED, "dropout": DROPOUT,
                       "lr": LR, "epochs": EPOCHS, "smooth": SMOOTH},
            "target": "delta",
            "channel_mean": mu, "channel_std": sd,
            "delta_mean": dmu, "delta_std": dsd,
            "step_seconds": STEP,
            "input_dim": 3, "output_dim": 3,
            "val_loss": best,
            "degenerate_channels": bad,
            "trained_horizon_seconds": round(float(t[-1] - t[0]), 1),
            "pipeline": "0.5s 网格 + 增量目标 + 时间序切分",
        }, os.path.join(out, "best_model.pth"))
        print(f"  -> {os.path.relpath(out, os.getcwd())}/best_model.pth")


if __name__ == "__main__":
    main()
