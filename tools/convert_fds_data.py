"""
把新生成的 FDS 180 秒探头数据转成 LSTM 训练格式。

输入：FDS 导出的 *_devc.csv
      列 = Time, TC, COLO, CO2LO, COHI, CO2HI, HRRPUV
输出：与原训练数据同构的 CSV
      列 = Time, temperature, CO, CO2

几点说明：
* 气体取**呼吸高度**（COLO / CO2LO）而不是烟气层。烟气层是火灾探测器视角，
  呼吸高度是人员暴露视角，后者对损管决策更有意义，且各舱都有稳定信号。
* 统一重采样到 0.1s 网格，和 retrain.py 的训练口径一致。
* 不做任何插值外推，只在原始跨度内重采样。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

STEP = 0.1


def load_devc(path):
    df = pd.read_csv(path, skiprows=[0])
    df.columns = [c.strip().strip('"') for c in df.columns]
    t = df[df.columns[0]].to_numpy(float)
    v = df[["TC", "COLO", "CO2LO"]].to_numpy(float)   # 温度, CO, CO2
    order = np.argsort(t)
    return t[order], v[order]


def resample(t, v, step=STEP):
    n = int(np.floor((t[-1] - t[0]) / step)) + 1
    grid = t[0] + np.arange(n) * step
    out = np.empty((n, v.shape[1]))
    for c in range(v.shape[1]):
        out[:, c] = np.interp(grid, t, v[:, c])
    return grid, out


def convert(src, dst):
    t, v = load_devc(src)
    g, r = resample(t, v)
    out = pd.DataFrame({
        "Time": g,
        "temperature": r[:, 0],
        "CO": r[:, 1],
        "CO2": r[:, 2],
    })
    out.to_csv(dst, index=False)
    uniq = [len(np.unique(np.round(r[:, c], 9))) for c in range(3)]
    print(f"{os.path.basename(src):<22} -> {os.path.basename(dst):<18} "
          f"{len(g):>5} 点 / {g[-1]:.0f}s   温度峰值 {r[:, 0].max():.0f}℃  "
          f"CO2 末值 {r[-1, 2]:.5f}   唯一值 T/CO/CO2 = {uniq}")
    return len(g)


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "tools/fds"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "tools/fds/train"
    os.makedirs(outdir, exist_ok=True)
    cases = [("ship_4_180", "电站间"), ("ship_5_180", "灶炉间"),
             ("ship_6_180", "机库"), ("ship_8_180", "士兵住舱")]
    for case, name in cases:
        src = os.path.join(base, f"{case}_devc.csv")
        if not os.path.exists(src):
            print(f"缺 {src}，跳过")
            continue
        convert(src, os.path.join(outdir, f"{name}_180.csv"))
