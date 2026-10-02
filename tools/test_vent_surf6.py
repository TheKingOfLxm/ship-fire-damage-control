# -*- coding: utf-8 -*-
"""第二次验证：规定的释热率为什么没生效。

上一轮把燃烧面吸附到网格面，无效（峰值仍是 1.1 kW）。
这一轮看 FDS 自己的输出找到了真因：

    4 burner
    Wall or Vent Temperature (C)   300.0
    Passive Vent to Atmosphere

FDS 把这个 VENT 当成了**被动等温开口**（固定 300℃），而不是热释放源。
所以房间里的那 1.1 kW 全部来自泡沫燃料块的热解，HRRPUA=4000 从未生效。

改用 FDS 验证算例的标准燃烧器几何：建一个专用燃烧块，
把 burner 表面直接指定给它的**顶面**（SURF_ID6 = Z+ 面），
不再经过 VENT。顶面面积 = 长 x 宽，侧面不参与释热。
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
CASE = os.path.join(HERE, "fds", "growth", "g5.fds")
OUT = os.path.join(HERE, "fds", "growth", "t_surf6.fds")

# 目标峰值释热率(W)。600 kW 是 IMO FTP 里灶台/厨房火灾的量级，
# 对 22.5 m^3 的灶炉间属于「已充分发展」火灾，会形成高温烟气层。
TARGET_PEAK_W = 600.0e3
BURNER_X = (0.0, 1.0)      # 地板净空区，避开炉灶(y 0..1)和燃料块(x -0.5..0)
BURNER_Y = (-1.0, 0.0)
BURNER_H = 0.1             # 燃烧块高度，只为有个顶面


def main():
    text = open(CASE, encoding="ascii").read()

    area = (BURNER_X[1] - BURNER_X[0]) * (BURNER_Y[1] - BURNER_Y[0])
    hrrpua = TARGET_PEAK_W / area
    print(f"燃烧块顶面 {area:.2f} m^2   目标峰值 {TARGET_PEAK_W/1000:.0f} kW"
          f"   -> HRRPUA = {hrrpua:.0f} kW/m^2")

    # 1) 删掉原来那个不生效的燃烧面 VENT
    text = re.sub(r"&VENT\s+ID\s*=\s*'Vent'[^/]*/", "", text)

    # 2) burner 表面补上按面积换算过的 HRRPUA
    def fix_surf(m):
        b = m.group(0)
        b = re.sub(r"HRRPUA\s*=\s*[\d.]+", f"HRRPUA={hrrpua:.1f}", b)
        return b

    text = re.sub(r"&SURF\s+ID\s*=\s*'burner'[^/]*/", fix_surf, text, flags=re.S)

    # 3) 建燃烧块，顶面(SURF_ID6 = Z+) 用 burner，其余面用普通材料
    block = (f"&OBST ID='FIRE_BURNER', "
             f"XB={BURNER_X[0]:g},{BURNER_X[1]:g},{BURNER_Y[0]:g},{BURNER_Y[1]:g},"
             f"0.0,{BURNER_H:g}, "
             f"SURF_ID='Default_Material', SURF_ID6='burner'/")
    text = text.replace("&TAIL /", block + "\n\n&TAIL /")

    text = re.sub(r"T_END\s*=\s*[\d.]+", "T_END=300", text, count=1)
    text = re.sub(r"CHID\s*=\s*'[^']*'", "CHID='tsurf6'", text, count=1)
    open(OUT, "w", encoding="ascii", errors="replace").write(text)
    print(f"已写出 {OUT}  (T_END=300s 快速验证)")
    print(f"  {block}")


if __name__ == "__main__":
    main()
