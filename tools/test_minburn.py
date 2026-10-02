# -*- coding: utf-8 -*-
"""最小验证算例：照 FDS 手册 9.2 的标准燃烧器写法，判断「规定释热」在本机 FDS 里到底能不能用。

手册 p.224 的原式：
    &VENT XB=4.0,5.0,4.0,5.0,0.0,0.0, SURF_ID='FIRE1' /     # 1 m^2 网格面
    &SURF ID='FIRE1', HRRPUA=... /
手册 p.346 给的权威测法：
    &DEVC QUANTITY='HRRPUA', SURF_ID='FIRE1',
          SPATIAL_STATISTIC='SURFACE INTEGRAL' /

这里不掺任何舱室几何、不加斜坡、燃料用库里的 PROPANE，
1 m 网格 + 1 m^2 燃烧面 + 100 kW/m^2，期望读数 100 kW。

读数：
  ≈100 kW  -> 规定释热在本机是好的，问题出在舱室算例的某个具体环节
  ≈0   kW  -> 本机 FDS 6.10.1 的规定释热机制整体失效，
              之前三种几何轮番失败就都解释得通了
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fds", "growth", "minburn.fds")

case = """&HEAD CHID='minburn', TITLE='minimal prescribed-HRR burner test'/
&MESH IJK=10,10,10, XB=0.0,10.0,0.0,10.0,0.0,10.0/
&REAC FUEL='PROPANE',/
&TIME T_END=120/
&DUMP DT_DEVC=0.5/
&SURF ID='FIRE1', HRRPUA=100.0/
&VENT XB=4.0,5.0,4.0,5.0,0.0,0.0, SURF_ID='FIRE1', COLOR='RED'/
&VENT XB=0.0,10.0,0.0,10.0,0.0,0.0, SURF_ID='OPEN'/
&VENT XB=0.0,10.0,0.0,10.0,10.0,10.0, SURF_ID='OPEN'/
&VENT XB=0.0,0.0,0.0,10.0,0.0,10.0, SURF_ID='OPEN'/
&VENT XB=10.0,10.0,0.0,10.0,0.0,10.0, SURF_ID='OPEN'/
&VENT XB=0.0,10.0,0.0,0.0,0.0,10.0, SURF_ID='OPEN'/
&VENT XB=0.0,10.0,10.0,10.0,0.0,10.0, SURF_ID='OPEN'/
&DEVC ID='Q_BURNER', QUANTITY='HRRPUA', QUANTITY_RANGE(1)=1.0E-10,
      XB=0.0,10.0,0.0,10.0,0.0,1.0, SURF_ID='FIRE1',
      SPATIAL_STATISTIC='SURFACE INTEGRAL'/
&DEVC ID='T_MID', XYZ=5.0,5.0,5.0, QUANTITY='TEMPERATURE'/
&DEVC ID='T_PLUME', XYZ=5.0,5.0,1.0, QUANTITY='TEMPERATURE'/
&DEVC ID='CO2', XYZ=5.0,5.0,1.0, QUANTITY='VOLUME FRACTION', SPEC_ID='CARBON DIOXIDE'/
&TAIL /
"""

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="ascii", errors="replace").write(case)
print("已写出 minburn.fds  (1m 网格, 1 m^2 燃烧面, HRRPUA=100 kW/m^2, 期望 100 kW)")
print(OUT)
