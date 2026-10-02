"""把 30 分钟 FDS 探头数据转成 LSTM 训练格式。

与 180 秒版本同构，只是 Time 列覆盖 0~1800 秒。转换时把时间重采样到
0.1 秒均匀网格，与 retrain.py 的训练口径一致。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

STEP = 0.1
HERE = os.path.dirname(os.path.abspath(__file__))
FDS_DIR = os.path.join(HERE, "fds")
OUT_DIR = os.path.join(FDS_DIR, "train30")

# 舱名 -> FDS 算例前缀
CASES = {
    "灶炉间": "ship_5_1800",
    "机库": "ship_6_1800",
    "士兵住舱": "ship_8_1800",
    "电站间": "ship_4_1800",
}


def convert(name, case):
    src = os.path.join(FDS_DIR, f"{case}_devc.csv")
    if not os.path.exists(src):
        return None
    df = pd.read_csv(src, skiprows=[0])
    df.columns = [c.strip().strip('"') for c in df.columns]
    t = df[df.columns[0]].to_numpy(float)
    if t[-1] < 1795:                     # 没跑完的先不转
        print(f"{name:<8} 未跑完 ({t[-1]:.0f}s)，跳过")
        return None

    v = df[["TC", "COLO", "CO2LO"]].to_numpy(float)
    n = int(np.floor((t[-1] - t[0]) / STEP)) + 1
    grid = t[0] + np.arange(n) * STEP
    out = np.empty((n, 3))
    for c in range(3):
        out[:, c] = np.interp(grid, t, v[:, c])

    os.makedirs(OUT_DIR, exist_ok=True)
    dst = os.path.join(OUT_DIR, f"{name}.csv")
    pd.DataFrame({
        "Time": grid,
        "temperature": out[:, 0],
        "CO": out[:, 1],
        "CO2": out[:, 2],
    }).to_csv(dst, index=False)

    uniq = [len(np.unique(np.round(out[:, c], 9))) for c in range(3)]
    peak = int(np.argmax(out[:, 0]))
    print(f"{name:<8} {n:>6} 点 / {grid[-1]:.0f}s   "
          f"温度峰 {out[:,0].max():.0f}℃ @ {grid[peak]:.0f}s   "
          f"唯一值 T/CO/CO2 = {uniq}")
    return n


if __name__ == "__main__":
    for name, case in CASES.items():
        convert(name, case)
    print(f"\n输出目录: {os.path.relpath(OUT_DIR, os.getcwd())}")
