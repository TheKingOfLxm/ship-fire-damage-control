# -*- coding: utf-8 -*-
"""v4 灶炉间模型的实际自回归能力诊断。

训练日志报的测试指标很难看：温度相对误差 8835%，CO/CO2 的
「预测动态/真实动态」只有 0.0% / 0.3%。但那两个数来自
**121 个验证窗口**，而且验证段正好是轨迹最后 180 秒的衰减尾段
（CO2 从 2% 掉到 1.2%），分布和训练段差得远。

所以要分清两件事：
  A. 模型真的发散        -> 自回归 rollout 会一路飞掉
  B. 只是held-out指标难取 -> 自回归 rollout 仍然稳定且物理合理

真正该看的是自回归：拿真实轨迹的中段作输入，喂 30 步（= 30 分钟），
逐块把模型自己的输出接回去，看温度曲线是否单调合理、是否复现
增长-平台-衰减三段。

判据（损管语义）：
  - 30 分钟内温度应落在训练数据的动态范围内
  - 不应出现远离训练分布的值（比如超过数据峰值 252℃ 很多）
  - 早期（增长段）应上升，平台段应大致持平
"""
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from lstm_pipeline import FEATURES, load_raw
from model_def import Seq2SeqLSTM

CKPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "models_v4", "灶炉间", "best_model.pth")


def load():
    ck = torch.load(CKPT, map_location="cpu", weights_only=False)
    cfg = ck["config"]
    # 结构参数与 retrain.py:147 一致：Seq2SeqLSTM(3, hidden, layers, 3, seq, pred, dropout)
    layers = cfg.get("num_layers", cfg.get("layers", 1))
    m = Seq2SeqLSTM(3, cfg["hidden"], layers, 3,
                    cfg["seq_len"], cfg["pred_len"], 0.0).to("cpu")
    m.load_state_dict(ck["model_state_dict"])
    m.eval()
    return m, ck["scaler"], cfg, ck


def autoregressive(m, scaler, cfg, win, n_steps, step_s):
    """把模型自己的输出接回去，逐块推进 n_steps 步。"""
    seq = int(cfg["seq_len"])
    pred = int(cfg["pred_len"])
    x = np.array(win[-seq:], dtype=np.float64)
    out = []
    with torch.no_grad():
        for _ in range(n_steps):
            t = torch.tensor(x[None, :, :], dtype=torch.float32)
            y = m(t)[0].numpy()          # (pred, 3) 标准化空间
            y = scaler.inverse_transform(y)
            out.append(y)
            x = np.vstack([x, y])[-seq:]
    return np.concatenate(out, axis=0)


def main():
    m, scaler, cfg, ck = load()
    step = float(ck["step_seconds"])
    seq, pred = int(cfg["seq_len"]), int(cfg["pred_len"])
    t, v = load_raw("灶炉间", prefer_30min=True)
    T = v[:, 0]
    print(f"checkpoint: step={step}s seq={seq} pred={pred} "
          f"horizon={ck['trained_horizon_seconds']}s val={ck['val_loss']:.5f}")
    print(f"真实轨迹 {len(t)} 点 / {t[-1]-t[0]:.0f}s  "
          f"温度 {T.min():.0f}~{T.max():.0f}℃  唯一值 {len(np.unique(T))}")
    print(f"训练数据动态范围: {T.max()-T.min():.0f}℃\n")

    # 从几个真实片段起推，每个推 30 步 = 30 分钟
    starts = [0, 60, 120, 180, 240]      # 对应 0/30/60/90/120 秒
    print(f"{'起点t(s)':>9}{'窗口均温':>9}", end="")
    for k in (5, 10, 20, 30):
        print(f"{f'+{k*step:.0f}s':>9}", end="")
    print(f"{'越界?':>7}")
    for s0 in starts:
        if s0 + seq + pred >= len(T):
            continue
        win = v[s0:s0 + seq]
        y = autoregressive(m, scaler, cfg, win, 30, step)
        wm = win[:, 0].mean()
        print(f"{t[s0]:9.0f}{wm:9.1f}", end="")
        over = False
        for k in (5, 10, 20, 30):
            idx = min(k * pred - 1, len(y) - 1)
            print(f"{y[idx, 0]:9.1f}", end="")
            if y[idx, 0] > T.max() * 1.5 or y[idx, 0] < T.min() - 20:
                over = True
        print(f"{'是' if over else '否':>7}")

    print("\n详细：起点 t=120s（增长段）逐 5 步")
    win = v[240:240 + seq]
    y = autoregressive(m, scaler, cfg, win, 30, step)
    for k in range(0, 30, 2):
        seg = y[k * pred:(k + 1) * pred]
        print(f"  +{(k+1)*pred*step:6.0f}s  温 {seg[:,0].mean():7.1f}℃  "
              f"CO {seg[:,1].mean()*100:6.3f}%  CO2 {seg[:,2].mean()*100:6.3f}%")


if __name__ == "__main__":
    main()
