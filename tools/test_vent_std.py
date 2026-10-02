# -*- coding: utf-8 -*-
"""第三次验证：按 FDS 手册原文的标准燃烧器写法重建火源。

前两轮失败的证据链：
  轮1  VENT 吸附到网格面  -> 无效，峰值仍 1.1 kW
  轮2  OBST 顶面 SURF_ID6  -> 峰值 0.0 kW

FDS 6.10 的 .out 一直在报：
    Passive Vent to Atmosphere
    Wall or Vent Temperature (C)   300.0
说明它把这个面当成**被动定温开口**，不是热释放源。

手册 7.2 给的标准燃烧器写法是：
    &SURF ID='FIRE', HRRPUA=1000.0 /
    &OBST XB=..., SURF_IDS='FIRE','INERT','INERT' /      # 只烧顶面

同时手册 7.2.2 说明：**相对网格很薄的障碍会被当成无限薄隔板**，
只起阻挡流动/辐射的作用，不产生气相释热。上一轮那个 0.1 m 高的
燃烧块在 0.5 m 网格里正好落进这一类，所以释热率直接变成 0。

本轮三处改动：
  1) 新建干净的 FIRE 表面：只有 HRRPUA + RAMP_Q，
     不带 TMP_FRONT / COLOR / TEXTURE_MAP（TMP_FRONT 会让 FDS 按定温面处理）
  2) 燃烧块做到 1.0 x 1.0 x 0.5 m，三个方向都大于半格(0.25m)，
     确保不被判为薄障碍
  3) 用 SURF_IDS 只让顶面释热，侧面和底面为 INERT
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
CASE = os.path.join(HERE, "fds", "growth", "g5.fds")
OUT = os.path.join(HERE, "fds", "growth", "t_std.fds")

TARGET_PEAK_KW = 600.0       # 600 kW：IMO FTP 灶台火灾量级
BX = (0.0, 1.0)               # 地板净空区：避开炉灶(y 0..1)、燃料块(x -0.5..0)
BY = (-1.0, 0.0)
BZ = (0.0, 0.5)               # 整格高，避免被判为薄障碍


def main():
    text = open(CASE, encoding="ascii").read()
    area = (BX[1] - BX[0]) * (BY[1] - BY[0])
    # ⚠ FDS 输入单位是 kW 和 m，HRRPUA 的单位是 **kW/m^2**。
    # 按 W/m^2 写会放大 1000 倍（600000 kW/m^2 = 600 MW/m^2），
    # 火焰温度直接超过 5000℃，时间步被 CFL 压到极小，仿真几乎不前进。
    hrrpua = TARGET_PEAK_KW / area

    # 1) 旧的 burner 表面和燃烧面 VENT 全部删掉
    text = re.sub(r"&SURF\s+ID\s*=\s*'burner'[^/]*/", "", text, flags=re.S)
    text = re.sub(r"&VENT\s+ID\s*=\s*'Vent'[^/]*/", "", text)

    # 2) 干净的释热表面
    fire_surf = (f"&SURF ID='FIRE', HRRPUA={hrrpua:.1f}, RAMP_Q='FIRE'/")

    # 3) 燃烧块：只烧顶面
    burner = (f"&OBST ID='FIRE_BURNER', XB={BX[0]:g},{BX[1]:g},"
              f"{BY[0]:g},{BY[1]:g},{BZ[0]:g},{BZ[1]:g}, "
              f"SURF_IDS='FIRE','INERT','INERT'/")

    text = text.replace("&TAIL /", f"{fire_surf}\n{burner}\n\n&TAIL /")
    text = re.sub(r"T_END\s*=\s*[\d.]+", "T_END=300", text, count=1)
    text = re.sub(r"CHID\s*=\s*'[^']*'", "CHID='tstd'", text, count=1)
    open(OUT, "w", encoding="ascii", errors="replace").write(text)

    print(f"顶面面积 {area:.2f} m^2  目标 {TARGET_PEAK_KW:.0f} kW"
          f"  -> HRRPUA={hrrpua:.0f} kW/m^2")
    print(f"  {fire_surf}")
    print(f"  {burner}")
    print(f"已写出 {OUT}")


if __name__ == "__main__":
    main()
