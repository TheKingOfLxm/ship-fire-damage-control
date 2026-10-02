# -*- coding: utf-8 -*-
"""对照实验：去掉 RAMP_Q，看规定的 600 kW/m^2 释热到底进没进房间。

tstd.fds 的 FDS 解析结果是对的：
    5 FIRE
      HRR Per Unit Area (kW/m2)   600.0
    5 格 x 0.25 m^2 = 1.25 m^2 -> 满负荷应输出 750 kW，
但实测峰值只有 0.7 kW，正好差约 1000 倍。

嫌疑锁定 RAMP_Q：如果多行 &RAMP 记录被当成各自独立、后者覆盖前者，
生效的就只剩最后一行 T=1800, F=0.3，释热被压到极小。
本脚本删掉 RAMP_Q 做对照。
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(HERE, "fds", "growth")

src = open(os.path.join(G, "t_std.fds"), encoding="ascii").read()
src = src.replace(", RAMP_Q='FIRE'/", " /")
src = re.sub(r"CHID\s*=\s*'[^']*'", "CHID='tnoramp'", src, count=1)
src = re.sub(r"T_END\s*=\s*[\d.]+", "T_END=300", src, count=1)
out = os.path.join(G, "t_noramp.fds")
open(out, "w", encoding="ascii", errors="replace").write(src)
print("已生成 t_noramp.fds (无 RAMP_Q)")

# 顺带确认 tstd 里的 HRR 原始量级
import csv
with open(os.path.join(G, "tstd_hrr.csv")) as f:
    rows = list(csv.DictReader(f))
hrr_key = [k for k in rows[0] if k.strip() == "HRR"][0]
vals = [float(r[hrr_key]) for r in rows if r[hrr_key].strip()]
print(f"tstd (带 RAMP) HRR 原始: min={min(vals):.3g} max={max(vals):.3g} "
      f"均值={sum(vals)/len(vals):.3g}  [共 {len(vals)} 行]")
