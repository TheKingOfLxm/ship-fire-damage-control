# -*- coding: utf-8 -*-
"""验证假设：燃烧面 VENT 没落在网格面上，导致规定的释热率完全没生效。

实测：加 t^2 斜坡后，释热率严格线性跟随斜坡（F=0.5625 -> 0.5kW，
F=1.0 -> 0.9kW），但**满功率只有 0.9 kW**，而 HRRPUA=4000 kW/m^2
乘以 0.23 m^2 的燃烧面本该是 920 kW。差了 1000 倍 —— 说明规定的
释热根本没进房间，房间里那点热量全来自泡沫燃料块的热解。

对照几何：
    燃烧面 VENT  z = 0.30663
    网格         Z(-1.0 .. 4.0) IJK=10  ->  网格面在 -1, -0.5, 0, 0.5, 1 ...

FDS 的 VENT 必须落在网格面上，0.30663 不是网格面，VENT 被丢弃。
本脚本把 VENT 吸附到最近的网格面，重跑 300 秒看释热率对不对。
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
CASE = os.path.join(HERE, "fds", "growth", "g5.fds")
OUT = os.path.join(HERE, "fds", "growth", "t_align.fds")


def parse_floats(s):
    return [float(x) for x in re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', s)]


def main():
    text = open(CASE, encoding="ascii").read()

    # 用 [^/]* 而不是 (?<![A-Z0-9_])/：MESH 以数字结尾（如 XB=...4.0/）时，
    # 那个前瞻要求 / 前面不是数字，直接匹配失败并一路跳到文件后部。
    m = re.search(r"&MESH\b([^/]*)/", text, re.S)
    body = m.group(1)
    v = parse_floats(re.search(r"XB\s*=\s*([-\d.,eE\s]+)", body).group(1))
    # [\d,]+ 会把 IJK=8,8,10, 后面那个分隔逗号一起吃掉，split 出空串
    ijk = [int(x) for x in re.search(r"IJK\s*=\s*([\d,]*\d)", body).group(1).split(",")]
    x0, y0, z0 = v[0], v[2], v[4]
    dx = (v[1] - v[0]) / ijk[0]
    dy = (v[3] - v[2]) / ijk[1]
    dz = (v[5] - v[4]) / ijk[2]
    print(f"网格 X{v[0]}..{v[1]} / Y{v[2]}..{v[3]} / Z{v[4]}..{v[5]}  IJK={ijk}")
    print(f"网格尺寸 {dx:.3f} x {dy:.3f} x {dz:.3f} m")
    print(f"Z 方向网格面: {', '.join(f'{z0 + k*dz:g}' for k in range(ijk[2]+1))}")

    vm = re.search(r"&VENT\s+ID\s*=\s*'Vent'([^/]*)/", text, re.S)
    xb = parse_floats(re.search(r"XB\s*=\s*([-\d.,eE\s]+)", vm.group(1)).group(1))
    print(f"\n原燃烧面: x {xb[0]}..{xb[1]}  y {xb[2]}..{xb[3]}  z {xb[4]}..{xb[5]}")

    def snap(val, origin, d, n):
        k = round((val - origin) / d)
        k = max(0, min(n, k))
        return origin + k * d

    z = snap(xb[4], z0, dz, ijk[2])
    print(f"z {xb[4]:.5f} -> 吸附到最近网格面 {z:g}  "
          f"(距离 {abs(xb[4]-z):.3f} m，网格面间距 {dz:g} m)")

    new_xb = f"XB={xb[0]:g},{xb[1]:g},{xb[2]:g},{xb[3]:g},{z:g},{z:g}"
    text = text.replace(vm.group(0), f"&VENT ID='Vent', SURF_ID='burner', {new_xb}/")
    text = re.sub(r"T_END\s*=\s*[\d.]+", "T_END=300", text, count=1)
    text = re.sub(r"CHID\s*=\s*'[^']*'", "CHID='talign'", text, count=1)
    open(OUT, "w", encoding="ascii", errors="replace").write(text)
    print(f"\n已写出 {OUT}  (T_END=300s 快速验证)")


if __name__ == "__main__":
    main()
