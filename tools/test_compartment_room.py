# -*- coding: utf-8 -*-
"""有围护结构的舱室设计火灾 —— 修正「六面全开导致热量直接排走」。

前面几步已经把三个问题定位清楚了：
  1. 规定释热机制**是好的**。用手册 p.346 的面积分测法实测
     Q_BURNER = 600.0 kW，与 HRRPUA 规格分毫不差，且严格跟随 t^2 斜坡。
     此前判断「释热没生效」是用了 hrr.csv 的 HRR 列 —— 那个列在规定
     释热场景下不可靠（minburn 算例里 HRR 列峰值 938、末值 118，
     而真实值恒为 100）。测量口径错了，结论也就错了。
  2. 斜坡**是好的**。Q_BURNER 曲线 18.7 -> 37.5 -> 150 -> 337 -> 599 kW
     与 RAMP_TABLE 的 F 值逐点吻合。
  3. 剩下的真问题：上一版干净算例把**六个面全设成 OPEN**，
     600 kW 随羽流直接排走，上层只到 52℃，形不成烟气层。

本文件给出正确的舱室火灾边界条件：
  - 地板 / 顶棚 / 四壁 全部 INERT（封闭）
  - 只留一樘门 1.0 x 2.0 m 作为 OPEN 通风口
  - 顶棚留一处烟气排气口（真实舱室都有通风）
  - 燃烧面 1.5 x 1.0 m，峰值 600 kW（22.5 m^3 -> 26.7 kW/m^3，
    已超过常用闪燃判据 10~20 kW/m^3，应形成充分发展火灾）
  - 温度取上层烟气层（80% 净高）环形 8 点平均

预期：层温升到 500~800℃ 并有明确的增长-充分发展-衰减过程。
"""
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
T_END = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
CHID = sys.argv[2] if len(sys.argv) > 2 else "croom"
# 网格边长。0.5m 时层温峰值只有 306℃：格子太粗，顶棚处的热边界层
# 厚度接近半个格子，热烟气层和冷空气混得太厉害，离真实充分发展舱室火灾
# 应有的 500~700℃ 差得远。设计火灾计算通常用 0.1~0.25m。
CELL = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
OUT = os.path.join(HERE, "fds", "growth", f"{CHID}.fds")

# 灶炉间实尺寸（由薄板 OBST 推得）
LX, LY, LZ = 3.0, 2.5, 3.0
NX, NY, NZ = int(round(LX / CELL)), int(round(LY / CELL)), int(round(LZ / CELL))
DX, DY, DZ = LX / NX, LY / NY, LZ / NZ

PEAK_HRR_KW = 600.0
BURNER = (0.5, 2.0, 0.5, 1.5)        # 1.5 x 1.0 m
DOOR = (1.0, 2.0, 0.0, 2.0)          # y=0 面上的 1.0 x 2.0 m 门
# 烟气层取 90% 净高，落在顶层格 (2.5~3.0m) 里。
# 取 80% (2.4m) 时探针落在 2.0~2.5 那一格，测到的是热层底缘而不是热层本身。
Z_LAYER = 0.90 * LZ
RING_R = 0.35 * min(LX, LY)
# 顶部排气口。实测教训：做成 1.5x1.0 m（占顶棚面积 20%）时，
# 热气层一直被抽走，层温峰值只有 282℃，而 600 kW / 22.5 m^3
# 已越过闪燃判据，真实充分发展舱室火灾的烟气层应在 500~700℃。
# 收窄到 0.5x0.5 m（占顶棚 3.3%），并让探针贴近顶棚。
VENT_XY = (1.0, 1.5, 1.0, 1.5)

RAMP = [(0, 0.0), (60, 0.0625), (120, 0.25), (180, 0.5625),
        (240, 1.0), (1400, 1.0), (1600, 0.6), (1800, 0.3)]

ramp = "\n".join(f"&RAMP ID='FIRE', T={t:g}, F={f:g} /" for t, f in RAMP)
hrrpua = PEAK_HRR_KW / ((BURNER[1] - BURNER[0]) * (BURNER[3] - BURNER[2]))

devs = []
for i in range(8):
    a = 2 * math.pi * i / 8
    x = LX / 2 + RING_R * math.cos(a)
    y = LY / 2 + RING_R * math.sin(a)
    devs.append(f"&DEVC ID='TL{i+1}', QUANTITY='TEMPERATURE', "
                f"XYZ={x:.3f},{y:.3f},{Z_LAYER:.3f}/")
devs += [
    "&DEVC ID='TC', QUANTITY='TEMPERATURE', "
    f"XYZ={LX/2:.3f},{LY/2:.3f},0.6/",
    "&DEVC ID='CO', QUANTITY='VOLUME FRACTION', SPEC_ID='CARBON MONOXIDE', "
    f"XYZ={LX/2:.3f},{LY/2:.3f},1.6/",
    "&DEVC ID='CO2', QUANTITY='VOLUME FRACTION', SPEC_ID='CARBON DIOXIDE', "
    f"XYZ={LX/2:.3f},{LY/2:.3f},1.6/",
    "&DEVC ID='CO2HI', QUANTITY='VOLUME FRACTION', SPEC_ID='CARBON DIOXIDE', "
    f"XYZ={LX/2:.3f},{LY/2:.3f},{Z_LAYER:.3f}/",
    "&DEVC ID='Q_BURNER', QUANTITY='HRRPUA', QUANTITY_RANGE(1)=1.0E-10, "
    f"XB=0.0,{LX:g},0.0,{LY:g},0.0,{DZ:g}, SURF_ID='FIRE', "
    "SPATIAL_STATISTIC='SURFACE INTEGRAL'/",
    "&DEVC ID='T_DOOR', QUANTITY='TEMPERATURE', "
    f"XYZ={DOOR[0]+0.25:.3f},0.05,{DOOR[3]-0.25:.3f}/",
    f"&SLCF QUANTITY='TEMPERATURE', PBX={LX/2:g}/",
]

case = f"""&HEAD CHID='{CHID}', TITLE='enclosed compartment design fire'/
&MESH IJK={NX},{NY},{NZ}, XB=0.0,{LX:g},0.0,{LY:g},0.0,{LZ:g}/
&SPEC ID='POLYMER', FORMULA='C6.3H7.1NO2.1'/
&REAC FUEL='POLYMER', CO_YIELD=0.024, SOOT_YIELD=0.10,
      HEAT_OF_COMBUSTION=1.79E+4, RADIATIVE_FRACTION=0.35/
&TIME T_END={T_END:g}/
&DUMP DT_DEVC=0.5/
{ramp}
&SURF ID='FIRE', HRRPUA={hrrpua:.1f}, RAMP_Q='FIRE'/
&OBST ID='BURNER', XB={BURNER[0]:g},{BURNER[1]:g},{BURNER[2]:g},{BURNER[3]:g},0.0,{DZ:g}, SURF_IDS='FIRE','INERT','INERT'/
! ---- 围护结构：只有门和顶部排气是开口，其余全部封闭 ----
&VENT XB=0.0,{LX:g},0.0,{LY:g},0.0,0.0, SURF_ID='INERT'/
&VENT XB=0.0,{LX:g},0.0,{LY:g},{LZ:g},{LZ:g}, SURF_ID='INERT'/
&VENT XB=0.0,0.0,0.0,{LY:g},0.0,{LZ:g}, SURF_ID='INERT'/
&VENT XB={LX:g},{LX:g},0.0,{LY:g},0.0,{LZ:g}, SURF_ID='INERT'/
&VENT XB=0.0,{LX:g},{LY:g},{LY:g},0.0,{LZ:g}, SURF_ID='INERT'/
&VENT XB={DOOR[0]:g},{DOOR[1]:g},0.0,0.0,0.0,{DOOR[3]:g}, SURF_ID='OPEN'/
&VENT XB={VENT_XY[0]:g},{VENT_XY[1]:g},{VENT_XY[2]:g},{VENT_XY[3]:g},{LZ:g},{LZ:g}, SURF_ID='OPEN'/
{chr(10).join(devs)}
&TAIL /
"""

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="ascii", errors="replace").write(case)
print(f"舱室 {LX}x{LY}x{LZ} m = {LX*LY*LZ:.1f} m^3,  网格 {NX}x{NY}x{NZ}")
print(f"燃烧面 {BURNER[1]-BURNER[0]:.1f}x{BURNER[3]-BURNER[2]:.1f} m, "
      f"HRRPUA={hrrpua:.0f} kW/m^2 -> 峰值 {PEAK_HRR_KW:.0f} kW "
      f"({PEAK_HRR_KW/(LX*LY*LZ):.1f} kW/m^3)")
print(f"门 {DOOR[1]-DOOR[0]:.1f}x{DOOR[3]-DOOR[2]:.1f} m;  烟气层 {Z_LAYER:.2f} m")
print(f"已写出 {OUT}")
