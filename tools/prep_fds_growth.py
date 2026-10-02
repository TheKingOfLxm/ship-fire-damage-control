# -*- coding: utf-8 -*-
"""
生成有「火灾发展过程」的 FDS 算例 —— 修复温度从 20℃ 瞬间跳到 400℃ 的问题。

======================================================================
为什么必须重做算例（实测证据，不是推测）
======================================================================
train30 里的真实轨迹长这样（灶炉间，dt=0.1s）：

    t=0.0 ~ 0.7s   20.0 → 25.1℃      环境温度
    t=0.8s         181.6℃
    t=0.9s         415.4℃            ← 0.3 秒内冲上 400℃
    此后           270 ~ 520℃ 乱抖

四个舱全都是这样。原因有三个，叠加在一起：

1. **释热率没有斜坡**：&SURF ID='burner' 直接 HRRPUA=4000.0，
   没有 RAMP_Q。火焰在 t=0 就是满功率，而真实舱室火灾要几分钟才发展到峰值。
2. **温度探头在羽流核心**：原热电偶位于火源正上方 0.6 m，
   那里测的是火焰羽流，瞬时饱和到接近火焰温度。
3. **网格太粗**：灶炉间 8x8x10 = 640 格，0.5 m 一格，燃烧器只有 1 格大，
   根本没有空间去分辨一个上层烟气层。

三者叠加的结果：训练数据里根本**不存在「火灾增长段」**，
连一段 5 秒（模型窗口长度）的全环境温度都没有 —— 最长只有 0.5~0.8 秒。

所以仿真里一给模型 20℃ 的窗口，它看到的是从没见过的输入，
MSE 训练下只会输出整个分布的均值 ≈ 400~500℃。
**「20℃ 一步跳到几百℃、然后在高位浮动」不是模型在编，是数据逼它这么答。**
调 LSTM 容量、epoch、损失函数都改不动这个结果，因为根因不在网络里。

======================================================================
这里怎么修
======================================================================
1. 加 IMO FTP 标准的 t² 增长斜坡（RAMP_Q），让释热率从 0 长到峰值再衰减，
   火势有真实的「起火 → 增长 → 充分发展 → 衰减」过程。
2. 温度探头改成**舱室上层烟气温度阵列**：横向离火源 ≥ STANDOFF，
   高度取舱室上部 80%。这才是损管真正关心的量 —— 人和烟气所在那层的温度。
   阵列输出多支，最后在转换脚本里求平均，等价于舱室上部均温。
3. 保留一支羽流核心热电偶 TC 作为参考，但它**不进 LSTM**。

输出间隔 DT_DEVC=0.5s：有了真实的分钟级发展过程，0.1s 的采样是浪费。
"""
import os
import re
import sys
import math

sys.stdout.reconfigure(encoding="utf-8")

# ---- 火灾发展曲线（IMO FTP 标准的房间火三阶段）----
# 增长段 t^2 上升 -> 充分发展平台 -> 衰减段指数下降
T_GROWTH = 240.0        # 增长段时长(s)：4 分钟到峰值
T_PLATEAU = 1400.0      # 平台结束时刻(s)
T_END_DEFAULT = 1800.0  # 仿真总时长(s)

RAMP_TABLE = [
    (0.0,   0.0),       # 起火
    (60.0,  0.0625),    # t^2 @ 0.25 t_growth
    (120.0, 0.25),      # t^2 @ 0.50
    (180.0, 0.5625),    # t^2 @ 0.75
    (240.0, 1.0),       # 达峰
    (1400.0, 1.0),      # 平台维持到 23 分钟
    (1600.0, 0.6),      # 衰减（燃料消耗）
    (1800.0, 0.3),
]

# ---- 探头布点 ----
LAYER_HEIGHT_FRAC = 0.80   # 烟气层取舱室净高的 80%
STANDOFF = 1.5             # 温度探头水平离火源的最小距离(m)
PLUME_HEIGHT = 0.6         # 羽流核心热电偶：火源正上方高度
BREATH_HEIGHT = 1.6        # 人员呼吸高度（距地板）
MAX_LAYER_SENSORS = 9      # 上层温度阵列最大支数
DT_DEVC = 0.5              # 设备输出间隔(s)


def parse_floats(s):
    return [float(x) for x in re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', s)]


def find_fire_center(text):
    """定位真正在放热的 VENT 中心。"""
    burner_ids = set()
    for m in re.finditer(r"&SURF\b(.*?)(?:/|$)", text, re.S | re.I):
        if re.search(r"HRRPUA\s*=", m.group(1), re.I):
            sid = re.search(r"ID\s*=\s*'([^']+)'", m.group(1))
            if sid:
                burner_ids.add(sid.group(1))

    fallback = None
    for m in re.finditer(r"&VENT\b(.*?)(?:/\s*\n|/|$)", text, re.S | re.I):
        body = m.group(1)
        if re.search(r"SURF_ID\s*=\s*'OPEN'", body, re.I):
            continue
        xb = re.search(r"XB\s*=\s*([-\d.,eE\s]+)", body)
        if not xb:
            continue
        v = parse_floats(xb.group(1))
        if len(v) < 6:
            continue
        sid = re.search(r"SURF_IDS?\s*=\s*'([^']+)'", body)
        c = ((v[0] + v[1]) / 2, (v[2] + v[3]) / 2, (v[4] + v[5]) / 2)
        if sid and sid.group(1) in burner_ids:
            return c, sid.group(1)
        if fallback is None:
            fallback = (c, sid.group(1) if sid else None)
    return fallback if fallback else (None, None)


def find_mesh_bounds(text):
    """舱室范围取 MESH 的 XB —— 比从 OBST 反推可靠。
    OBST 里的门/隔板只到 1.5m，照它布探头会全塞在地板附近。"""
    m = re.search(r"&MESH\b(.*?)(?:/\s*\n|/|$)", text, re.S | re.I)
    if not m:
        return (-2.0, 2.0), (-2.0, 2.0), (-1.0, 4.0)
    xb = re.search(r"XB\s*=\s*([-\d.,eE\s]+)", m.group(1))
    v = parse_floats(xb.group(1)) if xb else [-2, 2, -2, 2, -1, 4]
    if len(v) < 6:
        return (-2.0, 2.0), (-2.0, 2.0), (-1.0, 4.0)
    return (v[0], v[1]), (v[2], v[3]), (v[4], v[5])


def build_layer_sensors(fx, fy, z_layer, fire):
    """在舱室上部以火源为心布一圈温度探头，避开羽流正上方。

    用**环形**而不是矩形网格：小舱室里火源往往靠中心，矩形网格在
    STANDOFF 约束下会被筛到只剩一两个点（灶炉间实测只剩 1 支）。
    环形布局保证的是方位覆盖 —— 上层烟气温度本来就要各方向都能采到。
    """
    cx, cy, cz = fire
    span = min(fx[1] - fx[0], fy[1] - fy[0])
    radius = max(STANDOFF, min(span * 0.30, 2.0))
    pts = []
    for i in range(MAX_LAYER_SENSORS):
        ang = 2.0 * math.pi * i / MAX_LAYER_SENSORS
        x = cx + radius * math.cos(ang)
        y = cy + radius * math.sin(ang)
        # 贴壁的点拉回来一点，免得落到舱外或埋进结构里
        x = min(max(x, fx[0] + 0.3), fx[1] - 0.3)
        y = min(max(y, fy[0] + 0.3), fy[1] - 0.3)
        # 环形半径本来就 >= STANDOFF，这道检查只为剔除被舱壁拉回、
        # 反而落进羽流里的点。容差 5 cm：半径恰好等于 STANDOFF 时
        # 浮点误差会让 < 判定随机误杀（实测 9 支只剩 5 支）。
        if ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 < STANDOFF - 0.05:
            continue
        pts.append((x, y))
    if not pts:                       # 极端小舱室，退到对角
        pts = [(fx[0] + 0.3, fy[0] + 0.3), (fx[1] - 0.3, fy[1] - 0.3)]
    return [(x, y, z_layer) for x, y in pts]


def rewrite(text, out_path, t_end, chid):
    center, surf_id = find_fire_center(text)
    if center is None:
        raise SystemExit("✗ 找不到火源（带 HRRPUA 的 VENT）")

    fx, fy, fz = find_mesh_bounds(text)
    floor = fz[0]
    height = fz[1] - fz[0]
    z_layer = floor + height * LAYER_HEIGHT_FRAC

    print(f"火源中心 = ({center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f})  SURF='{surf_id}'")
    print(f"舱室范围 X{fx} Y{fy} Z{fz}  净高 {height:.2f} m")
    print(f"烟气层高度 = {z_layer:.2f} m ({LAYER_HEIGHT_FRAC*100:.0f}% 净高)")

    # ---- 1) 仿真时长 ----
    text, n = re.subn(r"(&TIME\b[^/]*?)\s*T_END\s*=\s*[\d.]+",
                      lambda m: m.group(1).rstrip() + f" T_END={t_end:g}", text,
                      count=1, flags=re.S | re.I)
    if n == 0:
        text = re.sub(r"(&HEAD\b[^/]*/)", rf"\1\n&TIME T_END={t_end:g}/", text,
                      count=1, flags=re.S)

    # ---- 2) CHID 必须 ASCII 且不能含小数点 ----
    text = re.sub(r"CHID\s*=\s*'[^']*'", f"CHID='{chid}'", text, count=1, flags=re.I)

    # ---- 3) 释热率加 t^2 增长斜坡 ----
    # FDS 的 RAMP：**每一行都是独立完整的 &RAMP 记录，重复写 ID**，
    # 例如  &RAMP ID='fire', T=0, F=0 /
    # 写成  &RAMP ID='fire', \n T=0,F=0 / \n T=60,F=... / 会被当成只有一个点，
    # FDS 直接报 ERROR(392): RAMP ... has only one point。
    text = re.sub(r"^&RAMP\b.*?/\s*$", "", text, flags=re.M | re.S | re.I)
    ramp = [f"&RAMP ID='FIRE', T={t:g}, F={f:g} /" for t, f in RAMP_TABLE]

    # 给带 HRRPUA 的 SURF 挂上斜坡
    def add_ramp_q(m):
        body = m.group(0)
        if "RAMP_Q" in body:
            return body
        return body.rstrip().rstrip("/").rstrip() + ", RAMP_Q='FIRE'/"

    text, n_ramp = re.subn(r"&SURF\s+ID\s*=\s*'[^']*'[^/]*?HRRPUA\s*=[^/]*?/",
                           add_ramp_q, text, flags=re.S | re.I)
    print(f"RAMP_Q 挂到 {n_ramp} 个燃烧面")
    print("释热曲线: " + "  ".join(f"{t:g}s={f:.0%}" for t, f in RAMP_TABLE))

    # ---- 4) 重建探头 ----
    text = re.sub(r"^&DEVC\b.*?/\s*$", "", text, flags=re.M | re.S | re.I)

    devs = ["", "! ================= 探头布置 =================",
            "! TL* 上层烟气温度阵列（损管关注量，进 LSTM）",
            "! TC  羽流核心温度（参考，不进 LSTM）",
            "! CO*/CO2* 呼吸高度 + 烟气层", ""]

    for i, (x, y, z) in enumerate(build_layer_sensors(fx, fy, z_layer, center), 1):
        devs.append(f"&DEVC ID='TL{i}', QUANTITY='TEMPERATURE', "
                    f"XYZ={x:.4f},{y:.4f},{z:.4f}/")

    tc = (center[0], center[1], center[2] + PLUME_HEIGHT)
    devs.append(f"&DEVC ID='TC', QUANTITY='TEMPERATURE', "
                f"XYZ={tc[0]:.4f},{tc[1]:.4f},{tc[2]:.4f}/")

    # 呼吸高度取远离火源的一侧
    sx = -1.0 if center[0] > (fx[0] + fx[1]) / 2 else 1.0
    sy = -1.0 if center[1] > (fy[0] + fy[1]) / 2 else 1.0
    gx = min(max(center[0] + sx * 2.0, fx[0] + 0.5), fx[1] - 0.5)
    gy = min(max(center[1] + sy * 2.0, fy[0] + 0.5), fy[1] - 0.5)
    z_breath = floor + BREATH_HEIGHT

    for tag, pos in (("LO", (gx, gy, z_breath)),
                     ("HI", (gx, gy, z_layer))):
        devs.append(f"&DEVC ID='CO{tag}', QUANTITY='VOLUME FRACTION', "
                    f"SPEC_ID='CARBON MONOXIDE', XYZ={pos[0]:.4f},{pos[1]:.4f},{pos[2]:.4f}/")
        devs.append(f"&DEVC ID='CO2{tag}', QUANTITY='VOLUME FRACTION', "
                    f"SPEC_ID='CARBON DIOXIDE', XYZ={pos[0]:.4f},{pos[1]:.4f},{pos[2]:.4f}/")

    devs.append(f"&DEVC ID='HRRPUV', QUANTITY='HRRPUV', "
                f"XYZ={gx:.4f},{gy:.4f},{z_layer:.4f}/")

    # ---- 5) 输出间隔 ----
    text, n_d = re.subn(r"(&DUMP\b[^/]*?)\s*(DT_DEVC\s*=\s*[\d.]+\s*)?/",
                        lambda m: m.group(1).rstrip().rstrip(",")
                        + f", DT_DEVC={DT_DEVC:g} /", text, count=1, flags=re.S | re.I)
    if n_d == 0:
        text = re.sub(r"(&TAIL\s*/)", f"&DUMP DT_DEVC={DT_DEVC:g}/\n\n\\1", text, count=1)

    # ---- 6) 自定义 CO/CO2 物种会和 FDS 内置全名冲突，读数恒 0 ----
    text = re.sub(r"^&SPEC\s+ID\s*=\s*'(CO|CO2)'\b.*?/\s*$", "", text,
                  flags=re.M | re.S | re.I)

    block = "\n".join(ramp) + "\n" + "\n".join(devs) + "\n\n&TAIL /"
    text = text.replace("&TAIL /", block, 1)
    if "&TAIL /" not in text:
        text = text.rstrip() + "\n\n" + block

    with open(out_path, "w", encoding="ascii", errors="replace") as f:
        f.write(text)
    print(f"DT_DEVC={DT_DEVC}s  T_END={t_end:g}s  -> {out_path}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    src, dst = sys.argv[1], sys.argv[2]
    t_end = float(sys.argv[3]) if len(sys.argv) > 3 else T_END_DEFAULT
    chid = sys.argv[4] if len(sys.argv) > 4 else os.path.splitext(os.path.basename(dst))[0]
    with open(src, encoding="utf-8", errors="replace") as f:
        rewrite(f.read(), dst, t_end, chid)


if __name__ == "__main__":
    main()
