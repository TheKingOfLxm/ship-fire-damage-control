# -*- coding: utf-8 -*-
"""决定性实验：多工况数据能不能解决泛化问题。

======================================================================
要回答的问题
======================================================================
单条轨迹训练出的模型，对「当前状态 -> 未来演化」几乎没有响应：
喂 52/73/83/92/111℃ 五种输入，v4 全部输出 253.9℃（训练数据的平台温度）。
v5 用增量目标后训练损失降、验证损失升，因为时间序切分出的验证段
（衰减尾段）是训练段没见过的物理状态。

多工况能不能解决？设计上刻意让条件 B 更难：

  A 组（单工况）  训练: q100 前 85%        测试: q100 后 15%
  B 组（多工况）  训练: q035/q070/q150 前 85%
                   测试: q100 后 15%   ← B **完全没见过 q100**

B 组要在一条它从未见过的轨迹上做预测，这正是损管场景的实况：
你永远不会遇到和训练集一模一样的火。如果 B 明显好于 A，
说明多工况确实换来了泛化能力；否则这条路也该否决。

增量目标（target=delta）：零增量是中性答案，平台段贡献≈0，
增长段/衰减段贡献正/负，模型才学得到动力学而不是分布均值。
"""
import os
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from lstm_pipeline import make_windows
from model_def import Seq2SeqLSTM

HERE = os.path.dirname(os.path.abspath(__file__))
STEP = 0.5
# 轻量配置。这只是 A/B 对比实验，两组必须用**同一套**参数才可比，
# 不需要生产级容量。原来的 hidden=96 / layers=2 / seq=120 在 CPU 上
# 一个 epoch 要 2~4 分钟，A 组 60 epoch 就要几小时，B 组数据量 3 倍更久。
HID, LAYERS, SEQ, PRED = 48, 1, 60, 60
EPOCHS, LR, DROPOUT, SMOOTH = 30, 3e-3, 0.2, 10
TAIL = 0.15          # 每条轨迹末尾 15% 留作测试

# 算例 -> (目录, CHID)
CASES = {
    "q035": ("cond", "d5_q035_v100"),
    "q070": ("cond", "d5_q070_v100"),
    "q100": ("growth", "d5"),
    "q150": ("cond", "d5_q150_v100"),
}


def load_case(key):
    sub, chid = CASES[key]
    p = os.path.join(HERE, "fds", sub, f"{chid}_devc.csv")
    if not os.path.exists(p):
        return None
    d = np.genfromtxt(p, delimiter=",", names=True, skip_header=1)
    names = list(d.dtype.names)
    tl = sorted([n for n in names if n.startswith("TL")],
                key=lambda n: int(n[2:]))
    if not tl:
        return None
    T = np.mean([d[n] for n in tl], axis=0)
    CO = np.mean([d[n] for n in ("COLO", "COHI") if n in names], axis=0)
    CO2 = np.mean([d[n] for n in ("CO2LO", "CO2HI") if n in names], axis=0)
    v = np.column_stack([T, CO, CO2])
    if SMOOTH > 1:
        k = np.ones(SMOOTH) / SMOOTH
        for c in range(3):
            v[:, c] = np.convolve(v[:, c], k, mode="same")
    return v


def wdelta(data):
    X, Y = make_windows(data, SEQ, PRED)
    return X, Y - X[:, -1:, :]


def fit(train_list, test_data, tag):
    tr = np.vstack(train_list)
    mu, sd = tr.mean(axis=0), tr.std(axis=0) + 1e-8
    Xtr, Ytr = wdelta((tr - mu) / sd)
    nte = int(len(test_data) * (1 - TAIL))
    Xte, Yte = wdelta((test_data[nte:] - mu) / sd)
    dsd = Ytr.reshape(-1, 3).std(axis=0) + 1e-8
    Ytr, Yte = Ytr / dsd, Yte / dsd
    # 验证集：每条训练轨迹的末段各取一部分
    Xva, Yva = [], []
    for seg in train_list:
        n = int(len(seg) * (1 - TAIL))
        a, b = wdelta((seg[max(0, n - 400):n] - mu) / sd)
        Xva.append(a); Yva.append(b)
    Xva, Yva = np.vstack(Xva), np.vstack(Yva) / dsd

    torch.manual_seed(0); np.random.seed(0)
    m = Seq2SeqLSTM(3, HID, LAYERS, 3, SEQ, PRED, DROPOUT)
    opt = optim.Adam(m.parameters(), lr=LR)
    sch = optim.lr_scheduler.ReduceLROnPlateau(opt, "min", factor=0.5, patience=12)
    crit = nn.MSELoss()
    xt, yt = torch.FloatTensor(Xtr), torch.FloatTensor(Ytr)
    xv, yv = torch.FloatTensor(Xva), torch.FloatTensor(Yva)

    best, state, bad = float("inf"), None, 0
    for ep in range(1, EPOCHS + 1):
        m.train()
        perm = torch.randperm(len(xt))
        tot = 0.0
        for i in range(0, len(xt), 64):
            idx = perm[i:i + 64]
            opt.zero_grad()
            loss = crit(m(xt[idx]), yt[idx])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(m.parameters(), 1.0)
            opt.step()
            tot += float(loss) * len(idx)
        m.eval()
        with torch.no_grad():
            vl = float(crit(m(xv), yv))
        sch.step(vl)
        if vl < best - 1e-5:
            best, bad, state = vl, 0, {k: v.clone() for k, v in m.state_dict().items()}
        else:
            bad += 1
        if bad >= 18:
            break
    m.load_state_dict(state)
    m.eval()
    with torch.no_grad():
        pd_ = m(torch.FloatTensor(Xte)).numpy() * dsd
    # 必须一路还原回**物理量**。只加回 Xte[:, -1:] 得到的是归一化空间的值，
    # 直接打印会得到「温度 0.1℃、CO2 43%」这种物理上不可能的数字；
    # 指标虽然在比值上凑巧不变，但绝对误差会被归一化尺度严重扭曲。
    pred = (pd_ + Xte[:, -1:, :]) * sd + mu
    true = (Yte * dsd + Xte[:, -1:, :]) * sd + mu

    print(f"\n  [{tag}]  训练窗 {len(Xtr)}  验证窗 {len(Xva)}  测试窗 {len(Xte)}  "
          f"val={best:.4f}")
    print(f"     测试段真实范围: 温 {true[...,0].min():.0f}~{true[...,0].max():.0f}℃  "
          f"CO2 {true[...,2].min()*100:.2f}~{true[...,2].max()*100:.2f}%")
    print(f"     {'通道':<14}{'平均绝对误差':>14}{'相对误差':>11}{'动态比':>9}")
    for i, f in enumerate(["temperature", "CO", "CO2"]):
        ae = np.abs(pred[..., i] - true[..., i]).mean()
        rt = np.abs(true[..., i]).mean()
        sp = (pred[..., i].max() - pred[..., i].min()) / \
             (true[..., i].max() - true[..., i].min() + 1e-9)
        unit = "℃" if i == 0 else ("%" if i == 2 else "")
        scale = 1.0 if i != 2 else 100.0
        print(f"     {f:<14}{ae*scale:11.2f}{unit:<3}{ae/(rt+1e-9)*100:10.1f}%"
              f"{sp*100:8.0f}%")

    print("     输入响应（同一窗口长度，不同起点的预测末值）:")
    with torch.no_grad():
        for frac in (0.10, 0.30, 0.50):
            s = int(len(test_data) * frac)
            w = (test_data[s:s + SEQ] - mu) / sd
            if len(w) < SEQ:
                continue
            y = (m(torch.FloatTensor(w[None, :, :])).numpy()[0] * dsd
                 + w[-1, :]) * sd + mu
            print(f"       起点 {test_data[s:s + SEQ, 0].mean():6.1f}℃ -> "
                  f"预测末值 温{y[-1, 0]:6.1f}℃  CO2{y[-1, 2]*100:5.2f}%")
    return True


def main():
    data = {}
    for k in CASES:
        v = load_case(k)
        if v is None:
            print(f"  {k} 数据缺失，跳过")
        else:
            data[k] = v
            print(f"  {k:<6} {len(v)} 点  峰温 {v[:,0].max():.0f}℃  "
                  f"CO2层末 {v[-1,2]*100:.2f}%")
    if "q100" not in data:
        print("q100 缺失，实验无法进行")
        return

    test = data["q100"]
    n = int(len(test) * (1 - TAIL))
    q100_head = test[:n]

    others = [data[k] for k in ("q035", "q070", "q150") if k in data]
    if not others:
        print("没有任何其它工况，B 组无法构成")
        return

    print("\n" + "=" * 70)
    print("  A 组：单工况（只用 q100 前 85%）")
    print("  B 组：多工况（q035/q070/q150 前 85%，**从未见过 q100**）")
    print("=" * 70)
    fit([q100_head], test, "A 单工况")
    fit(others, test, "B 多工况")


if __name__ == "__main__":
    main()
