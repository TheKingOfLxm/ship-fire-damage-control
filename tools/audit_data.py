"""严格核算每个舱室训练数据的真实时间跨度与各通道信息量。

之前对外披露的 "模型有效时域 30s" 来自 config 里的硬编码值，
这里从原始 CSV 重新推导，用来判断那个数字到底对不对。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\PyrosimLSTM"
CASES = [
    ("zjc.LSTM(new)", "主机舱.csv", "主机舱"),
    ("JK_LSTM", "机库.csv", "机库"),
    ("SBZV.lstm", "士兵住舱.csv", "士兵住舱"),
    ("LZJ.LSTM", "炉灶间.csv", "灶炉间"),
    ("jiaban_LSTM", "jiaban.csv", "甲板"),
]

for folder, fn, name in CASES:
    p = os.path.join(ROOT, folder, fn)
    if not os.path.exists(p):
        print(f"[{name}] 缺文件 {p}")
        continue

    # 第 1 行是单位，第 2 行是字段名，第 0 列是时间
    df = pd.read_csv(p, skiprows=[0])
    df.columns = [c.strip().strip('"') for c in df.columns]
    t = df[df.columns[0]].to_numpy(float)
    cols = df.columns[1:4]
    v = df[list(cols)].to_numpy(float)

    dt = np.diff(t)
    span = t[-1] - t[0]

    print(f"══ {name}  ({folder}) ══")
    print(f"   样本 {len(t)}  |  采样间隔 {dt.mean():.4f}s (min {dt.min():.4f} / max {dt.max():.4f})")
    print(f"   真实时间跨度 {span:.1f}s   <-- 此前对外披露为 30s")
    for i, c in enumerate(cols):
        x = v[:, i]
        uniq = len(np.unique(np.round(x, 9)))
        if x.max() > 1e-3:          # 已经是 ppm 量级
            lo, hi, rng = x[0] * 1e6, x.max() * 1e6, (x.max() - x[0]) * 1e6
            unit = "ppm"
        else:
            lo, hi, rng = x[0], x.max(), x.max() - x[0]
            unit = ""
        print(f"   {c:<20} 起 {lo:10.3f} -> 峰 {hi:12.3f} {unit:<4} 变化量 {rng:12.3f}  唯一值 {uniq}")
        if uniq <= 2:
            print(f"       ⚠ 该通道几乎无变化，模型无法从中学习任何动力学")
    print()
