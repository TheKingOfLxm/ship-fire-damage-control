# -*- coding: utf-8 -*-
"""在舱室算例上补测燃烧面实际释热（Q_BURNER）。

minburn 最小算例证明：手册 p.346 的
    QUANTITY='HRRPUA', SURF_ID='FIRE1', SPATIAL_STATISTIC='SURFACE INTEGRAL'
测出来是精确的 100.0 kW，**规定释热机制本身是好的**。

之前判断舱室算例「释热没生效」，依据只有 hrr.csv 的 HRR 列，
而那一列在规定释热场景下并不可靠（minburn 里 HRR 列峰值 938、
末值 118，而真实值是恒定 100）。所以必须用同一个测法重测。

本脚本给 t_clean.fds（灶炉间尺寸 + 干净物种）加上 Q_BURNER 和
燃烧面积，重新跑 300 秒。
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(HERE, "fds", "growth")

src = open(os.path.join(G, "t_clean.fds"), encoding="ascii").read()

dev = """&DEVC ID='Q_BURNER', QUANTITY='HRRPUA', QUANTITY_RANGE(1)=1.0E-10,
      XB=0.0,3.0,0.0,2.5,0.0,0.5, SURF_ID='FIRE', SPATIAL_STATISTIC='SURFACE INTEGRAL'/
&DEVC ID='A_BURNER', QUANTITY='HRRPUA', QUANTITY_RANGE(1)=1.0E-10,
      XB=0.0,3.0,0.0,2.5,0.0,0.5, SURF_ID='FIRE', SPATIAL_STATISTIC='SURFACE AREA'/
&DEVC ID='HRRPUV_ROOM', QUANTITY='HRRPUV', XB=0.75,2.25,0.75,1.75,0.0,0.5/
"""
src = src.replace("&TAIL /", dev + "\n&TAIL /")
src = re.sub(r"CHID\s*=\s*'[^']*'", "CHID='cq'", src, count=1)
src = re.sub(r"T_END\s*=\s*[\d.]+", "T_END=300", src, count=1)
out = os.path.join(G, "c_qburner.fds")
open(out, "w", encoding="ascii", errors="replace").write(src)
print("已生成 c_qburner.fds（含 Q_BURNER 面积分测量）")
