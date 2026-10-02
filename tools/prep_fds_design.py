# -*- coding: utf-8 -*-
"""生成可用于 LSTM 训练的**火灾发展过程** FDS 算例（生产版）。

======================================================================
这个脚本整合了一整轮实测才定下来的结论，每一条都有实验支撑
======================================================================

【1】释热增长必须用 IMO FTP 的 t^2 斜坡
  原 PyroSim 工况是 `&SURF HRRPUA=4000.0` 且没有任何 RAMP_Q，
  火焰在 t=0 就是满功率。实测结果是温度在 **0.3 秒**内从 20℃ 冲到 415℃，
  四个舱全部如此 —— 训练数据里根本不存在「火灾增长段」。

【2】温度必须测舱室上层烟气层，不能测羽流核心
  原热电偶在火源正上方 0.6 m，测的是火焰羽流，瞬时饱和。
  同一算例里烟气层要几分钟才升上来，羽流 2 分钟就冲到 510℃。
  改成以火源为心的环形阵列（默认 9 支）取上层均温。

【3】RAMP 的写法：每行都是独立的 &RAMP 记录，重复写 ID
      &RAMP ID='FIRE', T=0,   F=0 /
      &RAMP ID='FIRE', T=60,  F=0.0625 /
  写成一行 namelist 拆多行会被当成只有一个数据点 -> ERROR(392)。

【4】规定的释热率本身是好的，不要用 hrr.csv 的 HRR 列去判断
  手册 p.346 给的权威测法是面积分：
      &DEVC QUANTITY='HRRPUA', SURF_ID='FIRE',
            SPATIAL_STATISTIC='SURFACE INTEGRAL' /
  实测 600 kW 规格读出 600.0 kW；而同一算例 hrr.csv 的 HRR 列
  峰值 0.6 kW、末值 1.2 kW。**用错测量口径会得出「释热没生效」的错误结论。**

【5】火源必须用 OBST + SURF_IDS，不能用原来的 VENT
  原工况把 VENT 放在燃料块顶面（z=0.30663），而网格面在 z=0/0.5，
  VENT 被丢弃 -> 房间里的热量全来自泡沫热解，量级差约 1000 倍。
  手册 7.2 的标准写法是燃烧块顶面直接指定释热表面：
      &SURF ID='FIRE', HRRPUA=..., RAMP_Q='FIRE' /
      &OBST XB=..., SURF_IDS='FIRE','INERT','INERT' /
  燃烧块三个方向都要大于半格，否则被当作「薄障碍」只挡不释热（实测释热变 0）。

【6】HRRPUA 的单位是 kW/m^2，不是 W/m^2
  按 W/m^2 写会放大 1000 倍，火焰温度超 5000℃，CFL 把时间步压到极小，
  仿真几乎不前进。

【7】舱室必须封闭，只留门和一处小排气口
  六面全开时 600 kW 随羽流直接排走，上层只到 52℃，形不成烟气层。
  排气口也不能太大：做成占顶棚 20% 时层温峰值只有 282℃，
  收到 3.3% 后升到 306℃（0.5m 网格）/ 325℃（0.25m 网格）。

【8】网格越粗，热烟气层被稀释得越厉害
  0.5 m -> 层温峰 306℃；0.25 m -> 325℃ 且仍在上升，羽流 446℃。
  设计火灾计算通常用 0.1~0.25 m。

用法：
    py -3 tools/prep_fds_design.py <输入.fds> <输出.fds> <T_END秒> <CHID> [峰值kW]
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

# ---- 火灾发展曲线（IMO FTP 标准房间火三阶段）----
T_GROWTH = 240.0        # 增长段 4 分钟到峰值
T_PLATEAU = 1400.0      # 平台维持到 23 分钟
RAMP_TABLE = [
    (0.0, 0.0), (60.0, 0.0625), (120.0, 0.25), (180.0, 0.5625),
    (240.0, 1.0), (T_PLATEAU, 1.0), (1600.0, 0.6), (1800.0, 0.3),
]

# ---- 火灾规模 ----
# 按舱室体积定峰值：约 25 kW/m^3，已越过常用闪燃判据 10~20 kW/m^3，
# 形成充分发展火灾。上限 1000 kW，下限 150 kW。
KW_PER_M3 = 25.0
# 峰值上限。原先 1000 kW 对大舱太小：电站间 542 m^3 塞 1000 kW
# 只有 1.8 kW/m^3，远低于闪燃判据 10~20 kW/m^3，火完全没发展起来，
# 上层峰值只有 38℃。舰上大型舱室的电气/燃油火灾到 2~3 MW 是现实的。
PEAK_KW_MIN, PEAK_KW_MAX = 150.0, 3000.0
HRRPUA_MAX = 5000.0       # 真实燃烧器释热率上限 kW/m^2
MESH_DIV = 1               # 网格粗化倍数（IJK // MESH_DIV），1 = 不粗化
SEAL_MESH = False          # True = 用 MESH 边界当舱室，六面 INERT + 留一门
# 显式指定舱段（x0,x1,y0,y1,z0,z1）与火源（cx,cy,cz,w,h）。
# 用来把算例尺度对齐到 config/compartments.json 的权威定义，
# 而不是 PyroSim 网格域 —— 见 rewrite() 里的主机舱案例说明。
FORCED_BOUNDS = None
FORCED_FIRE = None
MESH_MARGIN = 0.0         # 显式舱段时网格相对舱段的外扩量，必须 0（门要在网格边界）

# ---- 探头布点 ----
LAYER_HEIGHT_FRAC = 0.90   # 烟气层取净高 90%，落在顶层格
STANDOFF = 1.2             # 水平离火源最小距离(m)
PLUME_HEIGHT = 0.6         # 羽流核心热电偶（参考，不进 LSTM）
MAX_LAYER_SENSORS = 9
DT_DEVC = 0.5              # 设备输出间隔(s)

# ---- 排气口 ----
VENT_FRAC = 0.33           # 排气口边长占舱室最小水平尺寸的比例


def parse_floats(s):
    return [float(x) for x in re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', s)]


def find_fire_center(text):
    """定位原工况里真正在放热的 VENT 中心。"""
    burner_ids = set()
    for m in re.finditer(r"&SURF\b([^/]*)/", text, re.S | re.I):
        if re.search(r"HRRPUA\s*=", m.group(1), re.I):
            sid = re.search(r"ID\s*=\s*'([^']+)'", m.group(1))
            if sid:
                burner_ids.add(sid.group(1))
    fallback = None
    for m in re.finditer(r"&VENT\b([^/]*)/", text, re.S | re.I):
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
            return c, sid.group(1), v
        if fallback is None:
            fallback = (c, sid.group(1) if sid else None, v)
    return fallback if fallback else (None, None, None)


def find_mesh(text):
    """返回 (x0,x1,y0,y1,z0,z1, nx,ny,nz)。"""
    m = re.search(r"&MESH\b([^/]*)/", text, re.S)
    body = m.group(1) if m else ""
    v = parse_floats(re.search(r"XB\s*=\s*([-\d.,eE\s]+)", body).group(1)) \
        if re.search(r"XB\s*=\s*([-\d.,eE\s]+)", body) else [-2, 2, -2, 2, -1, 4]
    # [\d,]*\d ：避免把 IJK=8,8,10, 的分隔逗号也吃进来，split 出空串
    ijk = [int(x) for x in re.search(r"IJK\s*=\s*([\d,]*\d)", body).group(1).split(",")] \
        if re.search(r"IJK\s*=\s*([\d,]*\d)", body) else [8, 8, 10]
    return v[0], v[1], v[2], v[3], v[4], v[5], *ijk


def find_enclosure(text):
    """从薄板 OBST 推断舱室围护范围（比从 MESH 可靠）。

    MESH 通常比舱室大（网格边界是外扩的），而 OBST 里的 Plane 类薄板
    才是真正的地板/顶棚/舱壁。灶炉间的 MESH 是 Z(-1,4)，但舱室只有 Z(0,3)。
    """
    xs, ys, zs = [], [], []
    for m in re.finditer(r"&OBST\b([^/]*)/", text, re.S | re.I):
        xb = re.search(r"XB\s*=\s*([-\d.,eE\s]+)", m.group(1))
        if not xb:
            continue
        v = parse_floats(xb.group(1))
        if len(v) < 6:
            continue
        dx, dy, dz = v[1] - v[0], v[3] - v[2], v[5] - v[4]
        if dx <= dy and dx <= dz and dx < 0.35:
            xs += [v[0], v[1]]
        elif dy <= dx and dy <= dz and dy < 0.35:
            ys += [v[2], v[3]]
        elif dz < 0.35:
            zs += [v[4], v[5]]
    if not (xs and ys and zs):
        return None
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def build_layer_sensors(fx, fy, z_layer, fire):
    """以火源为心的环形上层温度阵列。"""
    import math
    cx, cy = fire[0], fire[1]
    span = min(fx[1] - fx[0], fy[1] - fy[0])
    radius = max(STANDOFF, min(span * 0.32, 2.0))
    pts = []
    for i in range(MAX_LAYER_SENSORS):
        a = 2.0 * math.pi * i / MAX_LAYER_SENSORS
        x = min(max(cx + radius * math.cos(a), fx[0] + 0.3), fx[1] - 0.3)
        y = min(max(cy + radius * math.sin(a), fy[0] + 0.3), fy[1] - 0.3)
        # 容差 5 cm：半径恰等于 STANDOFF 时浮点误差会让 < 误杀
        if ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 < STANDOFF - 0.05:
            continue
        pts.append((x, y, z_layer))
    if not pts:
        pts = [(fx[0] + 0.3, fy[0] + 0.3, z_layer), (fx[1] - 0.3, fy[1] - 0.3, z_layer)]
    return pts


def rewrite(src, dst, t_end, chid, peak_kw=None):
    text = open(src, encoding="utf-8", errors="replace").read()
    center, old_surf, old_xb = find_fire_center(text)
    if center is None:
        raise SystemExit("✗ 在原工况里找不到火源")
    if FORCED_FIRE:
        fx0, fy0, fz0, fw, fh = FORCED_FIRE
        old_xb = [fx0 - fw / 2, fx0 + fw / 2, fy0 - fh / 2, fy0 + fh / 2,
                  fz0, fz0]
        print(f"显式火源: 中心 ({fx0:g},{fy0:g},{fz0:g})  面积 {fw:g}x{fh:g} = "
              f"{fw*fh:.0f} m^2")

    x0, x1, y0, y1, z0, z1, nx, ny, nz = find_mesh(text)
    enc = find_enclosure(text)
    fx, fy, fz = enc if enc else ((x0, x1), (y0, y1), (z0, z1))

    # 显式指定舱段时，覆盖反推结果。
    #
    # 主机舱就是栽在这里：它的 PyroSim 模型里没有薄板舱壁（全是机械设备），
    # `find_enclosure` 推不出东西，早先就退回"拿 MESH 边界当舱室"，
    # 于是把 66x62x32 m = **13.1 万 m^3** 当成主机舱。按 6.5 MW 跑完，
    # 上层 9 个探头全程 20~28℃，火根本没发展起来 —— 0.05 kW/m^3 在这个
    # 体积里点不着，和探头位置无关。
    #
    # 项目自己的 config/compartments.json 里主机舱是 18x7.6x10.4 = 1423 m^3，
    # 前端 3D 舰体和后端舱室种子都用这一份。四个已经跑通的舱室里，
    # 配置体积和 FDS 薄板反推值同量级（电站间 460 vs 542、灶炉间 193 vs 约250），
    # 说明这份 bounds 就是真实围护尺寸。这里显式传进来，把尺度拉回一致。
    if FORCED_BOUNDS:
        fb = FORCED_BOUNDS
        if len(fb) != 6 or not (fb[0] < fb[1] and fb[2] < fb[3] and fb[4] < fb[5]):
            raise SystemExit(f"✗ 舱段坐标非法: {fb}")
        fx, fy, fz = (fb[0], fb[1]), (fb[2], fb[3]), (fb[4], fb[5])
        # 网格域直接取舱段本身，不留余量。
        # 留余量的话门会开在**非网格边界**的舱壁面上，FDS 立刻报
        # ERROR(819): "VENT ... is OPEN and must be on an exterior boundary"。
        # OPEN 门必须是网格外边界，所以网格和舱段必须重合。
        mg = MESH_MARGIN
        x0, x1 = fx[0] - mg, fx[1] + mg
        y0, y1 = fy[0] - mg, fy[1] + mg
        z0, z1 = fz[0] - mg, fz[1] + mg
        cell = 0.5 if MESH_DIV <= 1 else 1.0
        nx = max(4, int(round((x1 - x0) / cell)))
        ny = max(4, int(round((y1 - y0) / cell)))
        nz = max(4, int(round((z1 - z0) / cell)))
        text = re.sub(r"(&MESH\b[^/]*?)IJK\s*=\s*[\d,]+",
                      lambda m: m.group(1) + f"IJK={nx},{ny},{nz}",
                      text, count=1, flags=re.S | re.I)
        text = re.sub(r"(&MESH\b[^/]*?)XB\s*=\s*[-\d.,eE\s]+",
                      lambda m: m.group(1) + f"XB={x0:g},{x1:g},{y0:g},{y1:g},{z0:g},{z1:g}",
                      text, count=1, flags=re.S | re.I)
        print(f"显式舱段: {fx[1]-fx[0]:g}x{fy[1]-fy[0]:g}x{fz[1]-fz[0]:g} m "
              f"= {(fx[1]-fx[0])*(fy[1]-fy[0])*(fz[1]-fz[0]):.0f} m^3")
        print(f"网格域收缩到 {x1-x0:g}x{y1-y0:g}x{z1-z0:g} m，"
              f"格长 {cell:g} m -> {nx}x{ny}x{nz} = {nx*ny*nz} 格")
        SEAL_MESH = True

        # 原模型在自己网格边界上有 OPEN 通风口（主机舱有一个 Vent24 在
        # x=-31）。网格一收缩，这个面就跑到**舱内**去了。必须**整条删除**：
        # 改成 INERT 也不行，舱内的 VENT 没有实体背衬，FDS 报
        # ERROR(820) "has no solid backing or an orientation index is needed"。
        # 舱内面只能用 OBST 表达，而这里要的只是把那个旧开口堵上。
        tol = 1e-3
        n_open = 0

        def _on_face(v):
            """VENT 是否正好贴在某个网格面上（薄面 + 坐标与边界重合）。"""
            if abs(v[0] - v[1]) < tol and (abs(v[0] - x0) < tol or abs(v[0] - x1) < tol):
                return True
            if abs(v[2] - v[3]) < tol and (abs(v[2] - y0) < tol or abs(v[2] - v1) < tol):
                return True
            if abs(v[4] - v[5]) < tol and (abs(v[4] - z0) < tol or abs(v[4] - z1) < tol):
                return True
            return False

        def _intersects(v):
            return not (max(v[0], v[1]) < x0 - tol or min(v[0], v[1]) > x1 + tol
                        or max(v[2], v[3]) < y0 - tol or min(v[2], v[3]) > y1 + tol
                        or max(v[4], v[5]) < z0 - tol or min(v[4], v[5]) > z1 + tol)

        def _close(m):
            nonlocal n_open
            body = m.group(0)
            xb = re.search(r"XB\s*=\s*([-\d.,eE\s]+)", body)
            if not xb:
                return body
            try:
                v = parse_floats(xb.group(1))
            except Exception:
                return body
            if len(v) < 6 or _on_face(v) or not _intersects(v):
                return body
            n_open += 1
            return ""          # 整条删除

        text = re.sub(r"&VENT\b[^/]*/", _close, text, flags=re.S | re.I)
        if n_open:
            print(f"删除 {n_open} 个落在舱内的旧网格边界通风口")

    # 士兵住舱的 PyroSim 模型是**舱内家具布置**（铺位 krovat、长凳 setka、
    # 框架 karkas 几十块薄板），根本没有房间围护。靠薄板反推会得到
    # x(-0.4,1.6) y(-1.4,-0.6) z(0,0.8) 这种"铺位尺寸"当舱室，
    # 火源被放到床上、烟气层取 0.72m，810 kW 烧在一张铺里直接压力爆掉。
    # 这种模型只能**自己造围护**：拿 MESH 边界当舱室，六面封 INERT + 留一门。
    seal = ""
    if SEAL_MESH:
        # 显式给了舱段就封舱段，否则封 MESH 边界
        if not FORCED_BOUNDS:
            fx, fy, fz = (x0, x1), (y0, y1), (z0, z1)
        dw = 0.20 * min(x1 - x0, y1 - y0)          # 门宽
        dh = 0.60 * (z1 - z0)                      # 门高
        dxc = (fx[0] + fx[1]) / 2
        seal = "\n".join([
            f"&VENT XB={fx[0]:g},{fx[0]:g},{fy[0]:g},{fy[1]:g},{fz[0]:g},{fz[1]:g}, SURF_ID='INERT'/",
            f"&VENT XB={fx[1]:g},{fx[1]:g},{fy[0]:g},{fy[1]:g},{fz[0]:g},{fz[1]:g}, SURF_ID='INERT'/",
            f"&VENT XB={fx[0]:g},{fx[1]:g},{fy[0]:g},{fy[0]:g},{fz[0]:g},{fz[1]:g}, SURF_ID='INERT'/",
            f"&VENT XB={fx[0]:g},{fx[1]:g},{fy[1]:g},{fy[1]:g},{fz[0]:g},{fz[1]:g}, SURF_ID='INERT'/",
            f"&VENT XB={fx[0]:g},{fx[1]:g},{fy[0]:g},{fy[1]:g},{fz[0]:g},{fz[0]:g}, SURF_ID='INERT'/",
            f"&VENT XB={fx[0]:g},{fx[1]:g},{fy[0]:g},{fy[1]:g},{fz[1]:g},{fz[1]:g}, SURF_ID='INERT'/",
            f"&VENT XB={dxc-dw/2:.3f},{dxc+dw/2:.3f},{fy[0]:g},{fy[0]:g},{fz[0]:g},{fz[0]+dh:.3f},"
            f" SURF_ID='OPEN'/",
        ])
        print(f"显式围护：舱室 {fx[1]-fx[0]:g}x{fy[1]-fy[0]:g}x{fz[1]-fz[0]:g} m，"
              f"六面 INERT，y={fy[0]:g} 面留 {dw:.2f}x{dh:.2f}m 门")
    floor, ceil = fz
    height = ceil - floor
    z_layer = floor + height * LAYER_HEIGHT_FRAC
    volume = (fx[1] - fx[0]) * (fy[1] - fy[0]) * height

    peak = peak_kw if peak_kw else min(max(volume * KW_PER_M3, PEAK_KW_MIN), PEAK_KW_MAX)

    # 燃烧面沿用原工况火源的平面尺寸，高度取整格（避免被判成薄障碍）
    dx = (x1 - x0) / nx
    dy = (y1 - y0) / ny
    dz = (z1 - z0) / nz

    # 网格粗化。士兵住舱原网格 20x20x25 = 1 万格 / 0.2 m，
    # 比其它舱（0.5 m）细 2.5 倍，30 分钟仿真跑到 367 秒就崩
    # （divergence 6.5、pressure error 34，最后是硬崩溃而非 FDS 报错）。
    # 各舱统一到 0.5 m 口径，既保住增��段结构，也把算例规模拉回可跑范围。
    if MESH_DIV > 1:
        nx2, ny2, nz2 = max(2, nx // MESH_DIV), max(2, ny // MESH_DIV), max(2, nz // MESH_DIV)
        if (nx2, ny2, nz2) != (nx, ny, nz):
            print(f"网格粗化 {nx}x{ny}x{nz} ({nx*ny*nz} 格) -> "
                  f"{nx2}x{ny2}x{nz2} ({nx2*ny2*nz2} 格)")
            nx, ny, nz = nx2, ny2, nz2
            text = re.sub(r"(&MESH\b[^/]*?IJK\s*=\s*)[\d,]+",
                          lambda m: m.group(1) + f"{nx},{ny},{nz}", text, count=1, flags=re.S | re.I)
            dx, dy, dz = (x1 - x0) / nx, (y1 - y0) / ny, (z1 - z0) / nz
    bx0, bx1 = max(min(old_xb[0], old_xb[1]), fx[0]), min(max(old_xb[0], old_xb[1]), fx[1])
    by0, by1 = max(min(old_xb[2], old_xb[3]), fy[0]), min(max(old_xb[2], old_xb[3]), fy[1])
    bz = old_xb[4]
    area = (bx1 - bx0) * (by1 - by0)
    if area <= 0:
        raise SystemExit("✗ 火源面尺寸异常")

    # 燃烧面不能太小。原工况在细网格舱室里的火源只有 0.04 m^2，
    # 要凑够 810 kW 就得 HRRPUA=20250 kW/m^2，远超真实燃烧器上限
    # （一般 1000~5000 kW/m^2），温度会失真。至少给 2x2 个网格。
    min_area = (2 * dx) * (2 * dy)
    if area < min_area:
        cxm, cym = (bx0 + bx1) / 2, (by0 + by1) / 2
        hx, hy = dx, dy
        bx0, bx1 = max(fx[0], cxm - hx), min(fx[1], cxm + hx)
        by0, by1 = max(fy[0], cym - hy), min(fy[1], cym + hy)
        area = (bx1 - bx0) * (by1 - by0)
        print(f"原火源面过小，扩到 {area:.2f} m^2 (2x2 网格)")

    # HRRPUA 上限 5000 kW/m^2；凑不满就降峰值，不去抬 HRRPUA
    peak = min(peak, HRRPUA_MAX * area)
    hrrpua = peak / area

    print(f"舱室 {fx[1]-fx[0]:.1f} x {fy[1]-fy[0]:.1f} x {height:.1f} m"
          f" = {volume:.1f} m^3   网格 {nx}x{ny}x{nz} ({dx:.2f} m)")
    print(f"火源中心 ({center[0]:.2f}, {center[1]:.2f})   烟气层 {z_layer:.2f} m")
    print(f"燃烧面 {area:.2f} m^2   峰值 {peak:.0f} kW ({peak/volume:.1f} kW/m^3)"
          f"   HRRPUA={hrrpua:.0f} kW/m^2")

    # ---- 1) 时长与 CHID ----
    text, n = re.subn(r"(&TIME\b[^/]*?)\s*T_END\s*=\s*[\d.]+",
                      lambda m: m.group(1).rstrip() + f" T_END={t_end:g}", text,
                      count=1, flags=re.S | re.I)
    if n == 0:
        text = re.sub(r"(&HEAD\b[^/]*/)", rf"\1\n&TIME T_END={t_end:g}/", text,
                      count=1, flags=re.S)
    text = re.sub(r"CHID\s*=\s*'[^']*'", f"CHID='{chid}'", text, count=1, flags=re.I)

    # ---- 2) 清场：旧火源、旧探头 ----
    # ⚠ **不要**删 &RAMP。原工况里可能存在被 &CTRL 引用的斜坡
    # （电站间就有 PVC_CONDUCTIVITY_RAMP），删掉会报
    # ERROR(391): RAMP ... not found。无人引用的 RAMP 在 FDS 里无害，
    # 所以直接留着，新斜坡另起一个不冲突的 ID。
    text = re.sub(r"^&DEVC\b.*?/\s*$", "", text, flags=re.M | re.S | re.I)
    # 删掉旧的燃料块（它挡住 VENT，且自身热解会污染热量平衡）
    text = re.sub(r"^&OBST\b[^/]*SURF_ID\s*=\s*'cailiao'[^/]*/\s*$", "", text,
                  flags=re.M | re.S | re.I)
    # 删掉旧的释热表面，以及**引用它的 VENT**。
    # 只删 SURF 不删 VENT 会直接报 ERROR(812): VENT Vent SURF_ID burner not found
    dead = {s for s in (old_surf, "burner") if s}
    for sid in dead:
        text = re.sub(rf"&SURF\s+ID\s*=\s*'{re.escape(sid)}'[^/]*/", "", text)
        text = re.sub(rf"&VENT\b[^/]*SURF_IDS?\s*=\s*'{re.escape(sid)}'[^/]*/", "", text)

    # ---- 3) 新的火源：OBST 顶面 + 干净释热表面 ----
    # RAMP ID 用独立名字，避免和原工况里已被 &CTRL 引用的斜坡撞车
    rid = "FIRE_DESIGN"
    ramp = "\n".join(f"&RAMP ID='{rid}', T={t:g}, F={f:g} /" for t, f in RAMP_TABLE)
    fire_surf = (f"&SURF ID='FIRE', HRRPUA={hrrpua:.1f}, RAMP_Q='{rid}'/\n"
                 f"&OBST ID='FIRE_BURNER', XB={bx0:g},{bx1:g},{by0:g},{by1:g},"
                 f"{bz:.4f},{bz + dz:.4f}, SURF_IDS='FIRE','INERT','INERT'/")

    # ---- 4) 通风 ----
    # PyroSim 导出的舱室是由**内部薄板 OBST** 围起来的，MESH 边界在外扩一圈。
    # 所以舱室顶棚不是网格外边界，在那里开 OPEN 口会直接报
    # ERROR(819): VENT is OPEN ... and must be on an exterior boundary。
    # 只有当舱室顶棚正好落在 MESH 边界上时才加顶部排气口；
    # 否则靠原工况 MESH 边界上的开口经门缝形成自然通风路径。
    vs = VENT_FRAC * min(fx[1] - fx[0], fy[1] - fy[0])
    vx = (fx[0] + fx[1]) / 2 - vs / 2
    vy = (fy[0] + fy[1]) / 2 - vs / 2
    on_mesh = (abs(ceil - z1) < 1e-6 or abs(ceil - z0) < 1e-6)
    if on_mesh:
        vent = (f"&VENT XB={vx:.3f},{vx+vs:.3f},{vy:.3f},{vy+vs:.3f},"
                f"{ceil:.4f},{ceil:.4f}, SURF_ID='OPEN'/")
        print(f"顶部排气口 {vs:.2f} x {vs:.2f} m（占顶棚 "
              f"{vs*vs/((fx[1]-fx[0])*(fy[1]-fy[0]))*100:.1f}%）")
    else:
        vent = ""
        print(f"舱室顶棚 z={ceil:g} 不在 MESH 边界 [{z0:g},{z1:g}] 上，"
              f"不加顶部排气口（ERROR(819)）；通风走原工况 MESH 开口经门缝的路径")

    # ---- 5) 探头 ----
    # Q_BURNER 的 XB 必须框住**吸附后**的燃烧顶面。燃烧块原始高度
    # 0.3066~0.8066 会被 FDS 吸附到 0~1.0（跨两个 0.5m 格），顶面在 z=1.0。
    # 用原始足迹去框会漏掉，读数恒为 0。这里直接用整个舱室范围，
    # 保证任何吸附结果都落在框内。
    devs = [f"&DEVC ID='Q_BURNER', QUANTITY='HRRPUA', QUANTITY_RANGE(1)=1.0E-10,"
            f" XB={fx[0]:g},{fx[1]:g},{fy[0]:g},{fy[1]:g},{bz:.4f},{ceil:.4f},"
            f" SURF_ID='FIRE', SPATIAL_STATISTIC='SURFACE INTEGRAL'/"]
    for i, (x, y, z) in enumerate(build_layer_sensors(fx, fy, z_layer, center), 1):
        devs.append(f"&DEVC ID='TL{i}', QUANTITY='TEMPERATURE', "
                    f"XYZ={x:.4f},{y:.4f},{z:.4f}/")
    # 羽流核心热电偶必须放在吸附后的燃烧块**上方**。原来取 center_z+0.6，
    # 而 center_z 正是燃烧块顶面，加高 dz 之后这个点就埋进固体里了，
    # 读数恒为环境温度 20℃。改成从吸附顶面往上抬。
    z_plume = min(floor + height * 0.60, max(bz + 3.0 * dz, z_layer - 0.5))
    devs.append(f"&DEVC ID='TC', QUANTITY='TEMPERATURE', "
                f"XYZ={center[0]:.4f},{center[1]:.4f},{z_plume:.4f}/")
    sx = -1.0 if center[0] > (fx[0] + fx[1]) / 2 else 1.0
    sy = -1.0 if center[1] > (fy[0] + fy[1]) / 2 else 1.0
    gx = min(max(center[0] + sx * 2.0, fx[0] + 0.4), fx[1] - 0.4)
    gy = min(max(center[1] + sy * 2.0, fy[0] + 0.4), fy[1] - 0.4)
    z_breath = floor + 1.6
    for tag, pos in (("LO", (gx, gy, z_breath)), ("HI", (gx, gy, z_layer))):
        devs.append(f"&DEVC ID='CO{tag}', QUANTITY='VOLUME FRACTION', "
                    f"SPEC_ID='CARBON MONOXIDE', XYZ={pos[0]:.4f},{pos[1]:.4f},{pos[2]:.4f}/")
        devs.append(f"&DEVC ID='CO2{tag}', QUANTITY='VOLUME FRACTION', "
                    f"SPEC_ID='CARBON DIOXIDE', XYZ={pos[0]:.4f},{pos[1]:.4f},{pos[2]:.4f}/")

    # ---- 6) 输出间隔 ----
    text, n_d = re.subn(r"(&DUMP\b[^/]*?)\s*(DT_DEVC\s*=\s*[\d.]+\s*)?/",
                        lambda m: m.group(1).rstrip().rstrip(",")
                        + f", DT_DEVC={DT_DEVC:g} /", text, count=1, flags=re.S | re.I)
    if n_d == 0:
        text = re.sub(r"(&TAIL\s*/)", f"&DUMP DT_DEVC={DT_DEVC:g}/\n\n\\1", text, count=1)

    # ---- 7) 自定义 CO/CO2 物种与内置全名冲突，读数恒 0 ----
    text = re.sub(r"^&SPEC\s+ID\s*=\s*'(CO|CO2)'\b.*?/\s*$", "", text,
                  flags=re.M | re.S | re.I)

    block = "\n".join([ramp, "", fire_surf, "", vent, "", *devs, "", "&TAIL /"]) \
        if vent else "\n".join([ramp, "", fire_surf, "", *devs, "", "&TAIL /"])
    if seal:
        block = block.replace("\n\n&TAIL /", f"\n\n{seal}\n\n&TAIL /")
    text = text.replace("&TAIL /", block, 1)
    if "&TAIL /" not in text:
        text = text.rstrip() + "\n\n" + block

    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(dst, "w", encoding="ascii", errors="replace") as f:
        f.write(text)
    print(f"DT_DEVC={DT_DEVC}s  T_END={t_end:g}s  -> {dst}")


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(__doc__)
        raise SystemExit(1)
    # 峰值可省略。PowerShell 会吞掉空字符串参数（'' 消失后 2 被当成峰值，
    # 于是算出 2 kW / HRRPUA=12 的荒谬组合），所以这里显式判无效值。
    try:
        peak_kw = float(sys.argv[5])
        if peak_kw <= 0:
            peak_kw = None
    except (IndexError, ValueError):
        peak_kw = None
    if len(sys.argv) > 6:
        globals()["MESH_DIV"] = int(sys.argv[6])
    if len(sys.argv) > 7:
        globals()["SEAL_MESH"] = sys.argv[7].lower() in ("1", "true", "seal", "yes")
    # 显式围护：直接给舱段坐标，格式 x0,x1,y0,y1,z0,z1
    if len(sys.argv) > 8 and sys.argv[8].lower() not in ("", "none", "-"):
        globals()["FORCED_BOUNDS"] = [
            float(x) for x in re.split(r"[,; ]+", sys.argv[8].strip()) if x]
    if len(sys.argv) > 9 and sys.argv[9].lower() not in ("", "none", "-"):
        globals()["FORCED_FIRE"] = [
            float(x) for x in re.split(r"[,; ]+", sys.argv[9].strip()) if x]
    rewrite(sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4], peak_kw)
