# -*- coding: utf-8 -*-
"""能量平衡诊断：规定的释热到底有没有进房间。

HRR 列只有 0.6~1.2 kW，而 FDS 明确说注入了 0.05 kg/s 的 POLYMER
（理论 900 kW）。两种可能：
  A. 能量注入了，但 HRR 列不统计「规定释热」这类源
     -> 判据：Q_ENTH / Q_TOTAL 应当是几百 kW 量级
  B. 能量压根没注入
     -> 判据：Q_ENTH 也是 1 kW 量级

另外逐列对比带斜坡/不带斜坡两版，看热量到底往哪个出口走
（辐射 / 对流 / 壁面导热 / 开口带出）。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
G = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fds", "growth")

COLS = ["HRR", "Q_RADI", "Q_CONV", "Q_COND", "Q_DIFF", "Q_PRES",
        "Q_PART", "Q_ENTH", "Q_TOTAL"]


def show(tag, chid):
    p = os.path.join(G, f"{chid}_hrr.csv")
    if not os.path.exists(p):
        print(f"{tag}: 缺 {chid}_hrr.csv")
        return
    d = pd.read_csv(p, skiprows=[0])
    d.columns = [str(c).strip() for c in d.columns]
    t = d["Time"].to_numpy(float)
    print("=" * 74)
    print(f"{tag}   ({len(t)} 行, 0..{t[-1]:.0f}s)")
    print(f"  {'列':<8}{'峰值(kW)':>12}{'末值(kW)':>12}{'峰值时刻':>10}")
    for c in COLS:
        if c not in d.columns:
            continue
        a = d[c].to_numpy(float) / 1000.0      # FDS 输出 kW
        if np.allclose(a, 0):
            print(f"  {c:<8}{'0':>12}")
            continue
        print(f"  {c:<8}{a.max():12.1f}{a[-1]:12.1f}{t[np.argmax(a)]:9.0f}s")
    # 终段（火焰稳定后）更能反映真实工况
    late = t > 0.7 * t[-1]
    print(f"  -- 末 30% 时段均值 --")
    for c in COLS:
        if c not in d.columns:
            continue
        a = d[c].to_numpy(float) / 1000.0
        if np.allclose(a, 0):
            continue
        print(f"  {c:<8}{a[late].mean():12.1f}")


if __name__ == "__main__":
    show("带 t^2 斜坡", "tclean")
    show("恒定 HRR 无斜坡", "cnramp")
