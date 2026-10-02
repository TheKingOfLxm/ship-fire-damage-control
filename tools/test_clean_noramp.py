# -*- coding: utf-8 -*-
"""决定性对照：干净算例去掉 RAMP_Q，恒定释热能否烧起来。

目前事实（全部实测，非推断）：
  - FDS 解析表面正确：HRR Per Unit Area 400 kW/m2（已按实际面积自动折算）
  - 注入燃料质量通量 2.235E-02 kg/s/m2，理论应放 900 kW
  - 实测 HRR 峰值仅 0.6 kW；CO2 升到 2%，对应只有约 0.04% 的注入燃料被烧掉
  - 去掉 RAMP_Q 后（t_noramp）仍只有 1.09 kW

所以要先分清：是「斜坡压小了」还是「规定的释热根本没生效」。
本脚本生成恒定 HRRPUA（无 RAMP_Q）的干净算例，峰值取 400 kW/m2。
若层温明显高于带斜坡那版，说明斜坡有效、问题在量级；
若几乎一样，说明规定的释热在本环境下压根没生效。
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(HERE, "fds", "growth")

src = open(os.path.join(G, "t_clean.fds"), encoding="ascii").read()
src = src.replace(", RAMP_Q='FIRE'", "")
src = re.sub(r"CHID\s*=\s*'[^']*'", "CHID='cnramp'", src, count=1)
src = re.sub(r"T_END\s*=\s*[\d.]+", "T_END=300", src, count=1)
out = os.path.join(G, "c_noramp.fds")
open(out, "w", encoding="ascii", errors="replace").write(src)
print("已生成 c_noramp.fds (恒定 HRRPUA, 无斜坡)")

# 同时看一眼 t_clean 里 RAMP 与 SURF 的实际写法
m = re.search(r"&SURF ID='FIRE'[^/]*/", src)
print("  SURF:", m.group(0).strip() if m else "未找到")
r = open(os.path.join(G, "t_clean.fds"), encoding="ascii").read()
print("  RAMP 行数:", len(re.findall(r"&RAMP ID='FIRE'", r)))
