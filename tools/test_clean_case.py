# -*- coding: utf-8 -*-
"""干净算例：验证「规定的释热率」在 FDS 6.10 里到底能不能生效。

前面的排查结论：
  - FDS 能正确解析表面： 5 FIRE / HRR Per Unit Area 600.0 kW/m2
  - 去掉 RAMP_Q 也不变（0.67 -> 1.09 kW），所以不是斜坡的问题
  - 满负荷本该 750 kW，实测 1.09 kW，差约 700 倍
  - FDS 启动就报两条警告：
        SPEC SFPE POLYURETHANE_GM37_fuel is not in the table of
        pre-defined species
        MATL FOAM, REAC 1. No product yields (NUs) set

即：PyroSim 导出的工况里，燃料用的是自定义 SPEC、REAC 又没有产物，
注入的燃料烧不起来，所以热释放几乎为零。

本文件用 FDS **内置**物种 POLYURETHANE + 完整 REAC 重建一个最小算例，
其余条件（房间尺寸、燃烧面、探头）与灶炉间一致。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fds", "growth", "t_clean.fds")

# 灶炉间实尺寸（由薄板 OBST 推得）：x 3.0  y 2.5  z 3.0 m
NX, NY, NZ = 6, 5, 6
LX, LY, LZ = 3.0, 2.5, 3.0
DX, DY, DZ = LX / NX, LY / NY, LZ / NZ

PEAK_HRR_KW = 600.0
BURNER = (0.75, 2.25, 0.75, 1.75)     # 1.5 x 1.0 m 燃烧面
Z_LAYER = 2.4                          # 上层烟气层高度

ramp = "\n".join(
    f"&RAMP ID='FIRE', T={t:g}, F={f:g} /"
    for t, f in [(0, 0.0), (60, 0.0625), (120, 0.25), (180, 0.5625),
                 (240, 1.0), (1400, 1.0), (1600, 0.6), (1800, 0.3)])

devs = []
import math
for i in range(8):
    a = 2 * math.pi * i / 8
    x, y = 1.5 + 0.85 * math.cos(a), 1.25 + 0.85 * math.sin(a)
    devs.append(f"&DEVC ID='TL{i+1}', QUANTITY='TEMPERATURE', "
                f"XYZ={x:.3f},{y:.3f},{Z_LAYER:g}/")
devs.append("&DEVC ID='TC', QUANTITY='TEMPERATURE', XYZ=1.5,1.25,0.6/")
devs.append("&DEVC ID='CO', QUANTITY='VOLUME FRACTION', "
            "SPEC_ID='CARBON MONOXIDE', XYZ=1.5,1.25,1.6/")
devs.append("&DEVC ID='CO2', QUANTITY='VOLUME FRACTION', "
            "SPEC_ID='CARBON DIOXIDE', XYZ=1.5,1.25,1.6/")

case = f"""&HEAD CHID='tclean', TITLE='clean design fire test'/
&MESH IJK={NX},{NY},{NZ}, XB=0.0,{LX:g},0.0,{LY:g},0.0,{LZ:g}/
&SPEC ID='POLYMER', FORMULA='C6.3H7.1NO2.1'/
&REAC FUEL='POLYMER', CO_YIELD=0.024, SOOT_YIELD=0.10,
      HEAT_OF_COMBUSTION=1.79E+4, RADIATIVE_FRACTION=0.35/
&TIME T_END=300/
&DUMP DT_DEVC=0.5/
{ramp}
&SURF ID='FIRE', HRRPUA={PEAK_HRR_KW / ((BURNER[1]-BURNER[0])*(BURNER[3]-BURNER[2])):.1f}, RAMP_Q='FIRE'/
&OBST ID='BURNER', XB={BURNER[0]:g},{BURNER[1]:g},{BURNER[2]:g},{BURNER[3]:g},0.0,{DZ:g}, SURF_IDS='FIRE','INERT','INERT'/
&VENT XB=0.0,{LX:g},0.0,{LY:g},0.0,0.0, SURF_ID='OPEN'/
&VENT XB=0.0,{LX:g},0.0,{LY:g},{LZ:g},{LZ:g}, SURF_ID='OPEN'/
&VENT XB=0.0,0.0,0.0,{LY:g},0.0,{LZ:g}, SURF_ID='OPEN'/
&VENT XB={LX:g},{LX:g},0.0,{LY:g},0.0,{LZ:g}, SURF_ID='OPEN'/
&VENT XB=0.0,{LX:g},0.0,0.0,0.0,{LZ:g}, SURF_ID='OPEN'/
&VENT XB=0.0,{LX:g},{LY:g},{LY:g},0.0,{LZ:g}, SURF_ID='OPEN'/
&SLCF QUANTITY='TEMPERATURE', PBX={LX/2:g}/
{chr(10).join(devs)}
&TAIL /
"""

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="ascii", errors="replace").write(case)
print(f"网格 {NX}x{NY}x{NZ}  单元 {DX:.2f}x{DY:.2f}x{DZ:.2f} m")
print(f"燃烧面 {(BURNER[1]-BURNER[0]):.2f}x{(BURNER[3]-BURNER[2]):.2f} m"
      f"  目标峰值 {PEAK_HRR_KW:.0f} kW")
print(f"已写出 {OUT}")
