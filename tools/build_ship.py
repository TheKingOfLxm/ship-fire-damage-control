"""
海鹰级护卫舰 (HAIYING-class Frigate) —— 参数化程序化建模脚本
=============================================================

设计坐标系 (Blender, Z-up):
    +X = 船艏 (bow)        -X = 船艉 (stern)
    +Y = 左舷 (port)        -Y = 右舷 (starboard)
    +Z = 向上 (up)          Z = 0 为设计水线面 (DWL)
    单位 = 米

导出 glTF 时使用 export_yup=True，Blender→glTF 坐标变换为
    (bx, by, bz) -> (bx, bz, -by)
因此 three.js / glTF 侧得到:
    +X = 船艏, +Y = 向上, +Z = 右舷
与 src/config/shipLayout.js 中记录的约定完全一致。

舱室锚点/包围盒与 shipLayout.js 保持同步，见 COMPARTMENT_SPECS。

运行:
    blender --background --factory-startup --python tools/build_ship.py
"""

import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector

# ---------------------------------------------------------------------
# 1. 参数 —— 与 src/config/shipLayout.js 同步
# ---------------------------------------------------------------------

LOA = 115.0          # 总长
BEAM = 13.6          # 型宽
HALF_BEAM = BEAM / 2
DRAFT = 4.2          # 吃水
DWL = 0.0            # 设计水线

MAIN_DECK_Z = 4.5    # 主甲板高度 (中部)
FLIGHT_DECK_Z = 9.2  # 飞行甲板高度
BULWARK_H = 1.15     # 舷墙高度

OUTPUT_DIR = None    # 由 main() 设置

# 舱室定义：bounds 为 three.js 坐标 (x 长度, y 上, z 舷向)
# 构建时转换为 Blender 坐标: (x, -z, y)
COMPARTMENT_SPECS = [
    dict(id=1, name="主机舱", node="COMPARTMENT_1",
         bounds=((-48.0, -3.6, -5.2), (-30.0, 4.0, 5.2)),
         anchor=(-39.0, 0.5, 0.0)),
    dict(id=2, name="电站间", node="COMPARTMENT_2",
         bounds=((-4.0, 0.4, -5.0), (6.0, 5.0, 5.0)),
         anchor=(1.0, 2.6, 0.0)),
    dict(id=3, name="灶炉间", node="COMPARTMENT_3",
         bounds=((22.0, 4.6, -4.6), (29.0, 7.6, 4.6)),
         anchor=(25.5, 6.0, 0.0)),
    dict(id=4, name="士兵住舱", node="COMPARTMENT_4",
         bounds=((8.0, 0.8, -5.0), (20.0, 5.4, 5.0)),
         anchor=(14.0, 3.0, 0.0)),
    dict(id=5, name="机库", node="COMPARTMENT_5",
         bounds=((-28.0, 0.5, -5.6), (-6.0, 6.2, 5.6)),
         anchor=(-17.0, 3.2, 0.0)),
]


def to_blender(p):
    """three.js (x, y_up, z_beam) -> Blender (x, -y_beam, z_up)"""
    return (p[0], -p[2], p[1])


# ---------------------------------------------------------------------
# 2. 通用工具
# ---------------------------------------------------------------------

def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


MATERIALS = {}


def mat(name, base, rough=0.6, metal=0.0, emit=None, emit_strength=0.0):
    """创建/获取 PBR 材质。emit 为 (r,g,b) 0-1 元组。"""
    if name in MATERIALS:
        return MATERIALS[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    r, g, b = base
    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if emit is not None:
        er, eg, eb = emit
        bsdf.inputs["Emission Color"].default_value = (er, eg, eb, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emit_strength
    m.diffuse_color = (r, g, b, 1.0)
    MATERIALS[name] = m
    return m


def build_materials():
    """
    海军灰配色体系。
    参照真实驱逐舰/护卫舰涂装：低反照率冷灰干舷、深色防污漆、近黑水线带。
    刻意避免高亮浅灰 —— 过曝会让所有结构细节糊成一片白。
    """
    mat("Hull_AntiFouling", (0.150, 0.048, 0.042), rough=0.80)   # 水线下防污漆
    mat("Hull_BootTop",    (0.022, 0.024, 0.028), rough=0.85)   # 水线带
    mat("Hull_Topside",    (0.300, 0.325, 0.348), rough=0.68)   # 干舷海军灰
    mat("Hull_Flare",      (0.330, 0.356, 0.380), rough=0.66)   # 舷侧外飘
    mat("Superstructure",  (0.375, 0.400, 0.422), rough=0.64)   # 上层建筑
    mat("Superstructure_Dk", (0.205, 0.222, 0.240), rough=0.66)
    mat("Deck_Main",       (0.115, 0.125, 0.135), rough=0.86)   # 主甲板
    mat("FlightDeck",      (0.068, 0.074, 0.080), rough=0.90)   # 飞行甲板
    mat("Metal_Dark",      (0.085, 0.092, 0.100), rough=0.45, metal=0.85)
    mat("Metal_Light",     (0.400, 0.420, 0.440), rough=0.34, metal=0.90)
    mat("Metal_Gun",       (0.110, 0.120, 0.130), rough=0.32, metal=0.95)
    mat("Glass_Dark",      (0.012, 0.024, 0.038), rough=0.08, metal=0.40)
    mat("Radome_White",    (0.560, 0.572, 0.580), rough=0.70)
    mat("Safety_Yellow",   (0.620, 0.430, 0.030), rough=0.70)
    mat("Safety_Red",      (0.480, 0.055, 0.045), rough=0.65)
    mat("Lifeboat_Orange", (0.680, 0.230, 0.030), rough=0.58)
    mat("Deck_Black",      (0.028, 0.030, 0.034), rough=0.90)
    mat("Rotor_Dark",      (0.055, 0.060, 0.066), rough=0.48, metal=0.60)
    mat("Light_Nav",       (0.90, 0.92, 0.95), rough=0.3,
        emit=(1.0, 0.95, 0.80), emit_strength=4.0)
    mat("Light_Red",       (0.85, 0.10, 0.08), rough=0.3,
        emit=(1.0, 0.12, 0.08), emit_strength=4.0)
    mat("Light_Green",     (0.10, 0.85, 0.25), rough=0.3,
        emit=(0.15, 1.0, 0.30), emit_strength=4.0)


def new_object(name, verts, faces, materials, mat_indices=None, smooth=True):
    """由顶点/面列表创建网格对象。materials 为材质名列表。"""
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    me.update()
    for mname in materials:
        me.materials.append(mat(mname, (0.5, 0.5, 0.5)))
    if mat_indices is not None:
        for poly, mi in zip(me.polygons, mat_indices):
            poly.material_index = mi
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    if smooth and len(me.polygons):
        shade_smooth_by_angle(ob)
    return ob


def shade_smooth_by_angle(ob, angle=math.radians(35)):
    try:
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        bpy.ops.object.shade_smooth_by_angle(angle=angle)
        ob.select_set(False)
    except Exception:
        try:
            for p in ob.data.polygons:
                p.use_smooth = True
        except Exception:
            pass


def recalc_normals(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()


def join_objects(objs, name):
    """合并多个对象为一个。"""
    objs = [o for o in objs if o is not None]
    if not objs:
        return None
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    res = bpy.context.view_layer.objects.active
    res.name = name
    res.data.name = name + "_mesh"
    bpy.ops.object.select_all(action="DESELECT")
    return res


def add_box(verts, faces, mat_ids, center, size, mi, taper=None, top_shift=0.0):
    """向累积列表追加一个盒体（可锥形）。返回新增顶点数。"""
    cx, cy, cz = center
    sx, sy, sz = (s / 2 for s in size)
    base = len(verts)
    if taper is None:
        taper = 1.0
    # 底面四点
    verts += [
        (cx - sx, cy - sy, cz - sz), (cx + sx, cy - sy, cz - sz),
        (cx + sx, cy + sy, cz - sz), (cx - sx, cy + sy, cz - sz),
    ]
    tx, ty = sx * taper, sy * taper
    verts += [
        (cx - tx + top_shift, cy - ty, cz + sz), (cx + tx + top_shift, cy - ty, cz + sz),
        (cx + tx + top_shift, cy + ty, cz + sz), (cx - tx + top_shift, cy + ty, cz + sz),
    ]
    quads = [
        (0, 3, 2, 1),          # bottom
        (4, 5, 6, 7),          # top
        (0, 1, 5, 4),          # -Y
        (1, 2, 6, 5),          # +X
        (2, 3, 7, 6),          # +Y
        (3, 0, 4, 7),          # -X
    ]
    for q in quads:
        faces.append(tuple(base + i for i in q))
        mat_ids.append(mi)
    return 8


def add_prism(verts, faces, mat_ids, center, size, mi, n=8, rot=0.0):
    """棱柱（用于桅杆、栏杆柱、炮管等）。"""
    cx, cy, cz = center
    rx, ry, rz = (s / 2 for s in size)
    base = len(verts)
    for k in range(n):
        a = rot + 2 * math.pi * k / n
        verts.append((cx + rx * math.cos(a), cy + ry * math.sin(a), cz - rz))
    for k in range(n):
        a = rot + 2 * math.pi * k / n
        verts.append((cx + rx * math.cos(a), cy + ry * math.sin(a), cz + rz))
    faces.append(tuple(base + k for k in range(n - 1, -1, -1)))
    mat_ids.append(mi)
    faces.append(tuple(base + n + k for k in range(n)))
    mat_ids.append(mi)
    for k in range(n):
        k2 = (k + 1) % n
        faces.append((base + k, base + k2, base + n + k2, base + n + k))
        mat_ids.append(mi)


def smooth_curve(table, n=1201, kernel=51):
    """把控制点表 (t, value) 平滑成连续曲线，返回可调用插值器。"""
    ts = np.array([p[0] for p in table], dtype=float)
    vs = np.array([p[1] for p in table], dtype=float)
    grid = np.linspace(0.0, 1.0, n)
    vals = np.interp(grid, ts, vs)
    # 两端各补 kernel//2 样本，卷积后长度恰好回到 n
    half = kernel // 2
    k = np.ones(kernel) / kernel
    vals = np.convolve(np.pad(vals, (half, half), mode="edge"), k, mode="valid")
    assert len(vals) == len(grid), f"curve length mismatch {len(vals)} != {len(grid)}"
    return lambda t: float(np.interp(t, grid, vals))


# ---------------------------------------------------------------------
# 3. 船体型线 (Hull lines)
# ---------------------------------------------------------------------

# 半宽分布 t: 0=艉 1=艏，归一化到 HALF_BEAM
HALF_BEAM_TBL = [
    (0.00, 0.615), (0.05, 0.780), (0.12, 0.900), (0.22, 0.968),
    (0.35, 1.000), (0.50, 1.000), (0.62, 0.990), (0.72, 0.940),
    (0.82, 0.815), (0.90, 0.600), (0.955, 0.330), (0.985, 0.130),
    (1.00, 0.022),
]

# 龙骨线 (型深底) t -> z
KEEL_TBL = [
    (0.00, -3.35), (0.06, -4.05), (0.15, -4.20), (0.50, -4.20),
    (0.66, -4.15), (0.78, -3.95), (0.88, -3.20), (0.95, -1.70),
    (0.99, 0.10), (1.00, 0.90),
]

# 舷弧线 (甲板边线) t -> z
SHEER_TBL = [
    (0.00, 4.95), (0.12, 4.62), (0.30, 4.48), (0.50, 4.50),
    (0.66, 4.68), (0.78, 5.02), (0.88, 5.55), (0.95, 6.15),
    (1.00, 6.70),
]

# 横剖面形状 h: 0=龙骨 1=舷顶，归一化半宽系数
# 水线位于 h≈0.48（中体）。水下部分收敛成细底，最大半宽落在水线附近，
# 水线以上先微外张再向舷顶轻微内倾 —— 这是军舰剖面的典型形态。
SECTION_TBL = [
    (0.00, 0.150), (0.05, 0.365), (0.13, 0.590), (0.23, 0.780),
    (0.33, 0.900), (0.41, 0.962), (0.48, 0.995), (0.55, 1.000),
    (0.63, 0.996), (0.73, 0.972), (0.84, 0.935), (0.93, 0.898),
    (1.00, 0.868),
]

half_beam_fn = smooth_curve(HALF_BEAM_TBL)
keel_fn = smooth_curve(KEEL_TBL)
sheer_fn = smooth_curve(SHEER_TBL)
section_fn = smooth_curve(SECTION_TBL)


def x_of_t(t):
    return -LOA / 2 + t * LOA


def t_of_x(x):
    return (x + LOA / 2) / LOA


def flare_of_t(t):
    """艏部外飘：仅在 t>0.62 生效。"""
    u = max(0.0, min(1.0, (t - 0.62) / 0.38))
    return 0.42 * (u ** 1.8)


def section_point(t, h):
    """返回该站位在高度比例 h 处的 (y_beam, z_height)，仅右舷(+半宽)侧。"""
    keel = keel_fn(t)
    sheer = sheer_fn(t)
    z = keel + (sheer - keel) * h
    frac = section_fn(h)
    frac += flare_of_t(t) * (h ** 1.9)
    y = HALF_BEAM * half_beam_fn(t) * frac
    return y, z


def deck_z_at(x):
    return sheer_fn(t_of_x(x))


# ---------------------------------------------------------------------
# 4. 船体
# ---------------------------------------------------------------------

def build_hull(n_stations=181, n_half=25):
    """放样船体（含水下防污漆分色、水线带、艏柱、球鼻艏、艉部舵桨）。"""
    verts, faces, mat_ids = [], [], []
    MI_AF, MI_BOOT, MI_TOP, MI_FLARE = 0, 1, 2, 3
    materials = ["Hull_AntiFouling", "Hull_BootTop", "Hull_Topside", "Hull_Flare"]

    grid = []  # grid[i][j] = vertex index, j: 0..2*n_half-1 (右舷 -> 左舷)
    hs = np.linspace(0.0, 1.0, n_half)
    # 每列对应的剖面高度比例 h（左右对称）
    cols = 2 * n_half - 1
    col_h = [float(hs[min(j, cols - 1 - j)]) for j in range(cols)]

    for i in range(n_stations):
        t = i / (n_stations - 1)
        x = x_of_t(t)
        row = []
        for h in hs:  # 右舷
            y, z = section_point(t, float(h))
            row.append(len(verts))
            verts.append((x, y, z))
        for h in reversed(hs[1:]):  # 左舷 (不含重复的 h=1)
            y, z = section_point(t, float(h))
            row.append(len(verts))
            verts.append((x, -y, z))
        grid.append(row)

    def h_waterline(t):
        """当前站位水线所在的剖面高度比例。"""
        k, s = keel_fn(t), sheer_fn(t)
        return (DWL - k) / (s - k)

    for i in range(n_stations - 1):
        t_mid = (i + 0.5) / (n_stations - 1)
        hw = h_waterline(t_mid)
        for j in range(cols - 1):
            a, b = grid[i][j], grid[i][j + 1]
            c, d = grid[i + 1][j + 1], grid[i + 1][j]
            faces.append((a, b, c, d))
            # 按剖面比例分色：水线带随型线自然起伏，不会在斜剖面上产生锯齿
            h_face = 0.5 * (col_h[j] + col_h[j + 1])
            if h_face < hw - 0.012:
                mat_ids.append(MI_AF)
            elif h_face < hw + 0.040:
                mat_ids.append(MI_BOOT)
            elif h_face > 0.80:
                mat_ids.append(MI_FLARE)
            else:
                mat_ids.append(MI_TOP)

    hull = new_object("Hull", verts, faces, materials, mat_ids, smooth=True)
    recalc_normals(hull)

    parts = [hull]

    # ---- 球鼻艏：沿艏柱抬升段的长条状鼻锥，而不是一个外挂的球 ----
    bulb_verts, bulb_faces, bulb_ids = [], [], []
    bu, bv, bw = 5.2, 1.85, 1.25
    bc = (53.2, 0.0, -1.15)
    seg, ring = 20, 10
    for i in range(ring + 1):
        phi = math.pi * i / ring
        # 沿 X 收细成纺锤形，避免与船体脱节
        taper_x = 1.0 - 0.45 * abs(math.cos(phi)) ** 2
        for j in range(seg):
            th = 2 * math.pi * j / seg
            bulb_verts.append((
                bc[0] + bu * math.cos(phi) * taper_x,
                bc[1] + bv * math.sin(phi) * math.cos(th),
                bc[2] + bw * math.sin(phi) * math.sin(th),
            ))
    for i in range(ring):
        for j in range(seg):
            j2 = (j + 1) % seg
            bulb_faces.append((i * seg + j, i * seg + j2,
                               (i + 1) * seg + j2, (i + 1) * seg + j))
            bulb_ids.append(MI_AF)
    bulb = new_object("Bulb", bulb_verts, bulb_faces,
                      ["Hull_AntiFouling"], bulb_ids, smooth=True)
    recalc_normals(bulb)
    parts.append(bulb)

    # ---- 艉部：尾鳍 + 舵 + 螺旋桨轴 ----
    v, f, mi = [], [], []
    add_box(v, f, mi, (-LOA / 2 + 1.2, 0, -4.3), (7.0, 1.1, 3.4), MI_AF)   # 尾鳍/skeg
    add_box(v, f, mi, (-LOA / 2 + 0.6, 0, -2.2), (1.6, 0.7, 3.6), MI_AF)    # 舵
    stern_gear = new_object("SternGear", v, f, ["Hull_AntiFouling"], mi, smooth=False)
    parts.append(stern_gear)

    # 螺旋桨 (4 叶)
    pv, pf, pmi = [], [], []
    px = -LOA / 2 + 3.2
    for blade in range(4):
        a = blade * math.pi / 2
        for k in range(6):
            r = 0.45 + k * 0.28
            aa = a + 0.55 * (r / 2.0)
            pv.append((px, r * math.sin(aa) * 1.05, -2.1 - r * math.cos(aa) * 0.75))
        for k in range(6):
            r = 0.45 + k * 0.28
            aa = a - 0.42 * (r / 2.0)
            pv.append((px, r * math.sin(aa) * 1.05, -2.1 - r * math.cos(aa) * 0.75))
    for b in range(4):
        o0 = b * 12
        for k in range(5):
            pf.append((o0 + k, o0 + k + 1, o0 + 7 + k, o0 + 6 + k))
            pmi.append(0)
    prop = new_object("Propeller", pv, pf, ["Metal_Gun"], pmi, smooth=False)
    parts.append(prop)

    return join_objects(parts, "Hull_Assembly")


# ---------------------------------------------------------------------
# 5. 甲板与舷墙
# ---------------------------------------------------------------------

def build_deck(n_stations=141):
    """主甲板（随舷弧起伏 + 横向拱度）与舷墙。"""
    MI_DECK, MI_BLACK = 0, 1
    materials = ["Deck_Main", "Deck_Black"]

    verts, faces, mat_ids = [], [], []
    crown = 0.30  # 甲板横向拱度

    grid = []
    for i in range(n_stations):
        t = i / (n_stations - 1)
        x = x_of_t(t)
        z_deck = sheer_fn(t)
        hb = HALF_BEAM * half_beam_fn(t) * section_fn(1.0)
        # 外缘向内收 8%，为舷墙留出厚度
        y_out = hb * 0.985
        row = []
        for frac, lift in ((-1.0, 0.0), (-0.62, crown * 0.72), (-0.24, crown * 0.95),
                           (0.0, crown), (0.24, crown * 0.95), (0.62, crown * 0.72),
                           (1.0, 0.0)):
            row.append(len(verts))
            verts.append((x, frac * y_out, z_deck + lift))
        grid.append(row)

    for i in range(n_stations - 1):
        for j in range(6):
            a, b = grid[i][j], grid[i][j + 1]
            c, d = grid[i + 1][j + 1], grid[i + 1][j]
            faces.append((a, b, c, d))
            mat_ids.append(MI_DECK)

    deck = new_object("MainDeck", verts, faces, materials, mat_ids, smooth=True)
    recalc_normals(deck)

    # ---- 舷墙 (bulwark)：甲板外缘向上的一圈板 ----
    bv, bf, bmi = [], [], []
    for i in range(n_stations - 1):
        t = i / (n_stations - 1)
        t2 = (i + 1) / (n_stations - 1)
        x, x2 = x_of_t(t), x_of_t(t2)
        z, z2 = sheer_fn(t), sheer_fn(t2)
        hb, hb2 = HALF_BEAM * half_beam_fn(t) * section_fn(1.0), \
            HALF_BEAM * half_beam_fn(t2) * section_fn(1.0)
        for sgn in (1, -1):
            o = len(bv)
            yb, yb2 = sgn * hb * 0.985, sgn * hb2 * 0.985
            yi, yi2 = sgn * (hb * 0.985 - 0.14), sgn * (hb2 * 0.985 - 0.14)
            bh = BULWARK_H * (0.55 + 0.45 * min(1.0, (1.0 - t) * 3.2))
            bv += [(x, yb, z), (x2, yb2, z2), (x2, yi2, z2 + bh), (x, yi, z + bh)]
            bf.append((o, o + 1, o + 2, o + 3))
            bmi.append(MI_BLACK)
    bulwark = new_object("Bulwark", bv, bf, materials, bmi, smooth=False)
    recalc_normals(bulwark)

    return join_objects([deck, bulwark], "Deck_Assembly")


# ---------------------------------------------------------------------
# 6. 栏杆
# ---------------------------------------------------------------------

def build_railings(n_stations=121, post_step=2.4):
    """甲板两侧栏杆：立柱 + 上下两道横杆，合并为单一网格。"""
    verts, faces, mat_ids = [], [], []
    xs = np.linspace(-LOA / 2 + 4, LOA / 2 - 3, n_stations)
    rail_heights = (0.55, 1.02)

    def edge_y(x, sgn, inset=0.0):
        t = t_of_x(x)
        hb = HALF_BEAM * half_beam_fn(t) * section_fn(1.0) * 0.985
        return sgn * max(0.4, hb - inset)

    def edge_z(x):
        return sheer_fn(t_of_x(x))

    for sgn in (1, -1):
        # 横杆：沿船长方向扫掠成管
        for rh in rail_heights:
            base = len(verts)
            n = len(xs)
            for i, x in enumerate(xs):
                t = t_of_x(x)
                y = edge_y(x, sgn, 0.30)
                z = edge_z(x) + rh + BULWARK_H * 0.30
                r = 0.045
                for k in range(6):
                    a = 2 * math.pi * k / 6
                    verts.append((x, y + r * math.cos(a), z + r * math.sin(a)))
            for i in range(n - 1):
                for k in range(6):
                    k2 = (k + 1) % 6
                    a0, a1 = base + i * 6, base + (i + 1) * 6
                    faces.append((a0 + k, a0 + k2, a1 + k2, a1 + k))
                    mat_ids.append(0)
        # 立柱
        post_xs = np.arange(-LOA / 2 + 5, LOA / 2 - 3.5, post_step)
        for px in post_xs:
            z0 = edge_z(px)
            y = edge_y(px, sgn, 0.30)
            h = 1.12
            add_prism(verts, faces, mat_ids, (px, y, z0 + h / 2 + 0.02),
                      (0.09, 0.09, h), 0, n=6)

    rail = new_object("Railings", verts, faces, ["Metal_Light"], mat_ids, smooth=False)
    return rail


# ---------------------------------------------------------------------
# 7. 上层建筑
# ---------------------------------------------------------------------

def build_superstructure():
    """前部上层建筑 + 驾驶室 + 烟囱。"""
    parts = []
    M_SUP, M_DK, M_GLASS, M_MET = 0, 1, 2, 3
    materials = ["Superstructure", "Superstructure_Dk", "Glass_Dark", "Metal_Dark"]

    v, f, mi = [], [], []
    # 前部主上层建筑 (阶梯式收分)
    add_box(v, f, mi, (23.0, 0, 6.6), (14.0, 11.0, 4.2), M_DK)          # 下层台阶
    add_box(v, f, mi, (22.5, 0, 9.6), (12.4, 10.2, 3.0), M_SUP, taper=0.97)
    add_box(v, f, mi, (23.5, 0, 12.1), (9.6, 9.2, 2.4), M_SUP, taper=0.98)  # 驾驶室
    add_box(v, f, mi, (26.0, 0, 13.6), (5.0, 8.2, 1.2), M_DK)            # 驾驶室顶棚
    fore = new_object("ForeSuperstructure", v, f, materials, mi, smooth=False)
    parts.append(fore)

    # 翼桥：驾驶室两侧挑出的开放式平台，军舰最标志性的轮廓特征之一
    v, f, mi = [], [], []
    for sgn in (1, -1):
        y = sgn * 6.2
        add_box(v, f, mi, (23.0, y, 11.7), (7.2, 3.2, 0.35), M_DK)         # 平台
        add_box(v, f, mi, (26.4, y, 12.1), (0.5, 3.2, 1.4), M_DK)          # 前缘
        for k in range(5):                                                # 平台栏杆立柱
            add_prism(v, f, mi, (20.2 + k * 1.4, y, 12.15), (0.10, 0.10, 0.85), 1, n=6)
        add_box(v, f, mi, (23.0, y, 12.55), (7.2, 0.12, 0.12), 1)         # 栏杆横杆
    wings = new_object("BridgeWings", v, f, ["Superstructure_Dk", "Metal_Light"], mi, smooth=False)
    parts.append(wings)

    # 第二层甲板室（驾驶室之后），把上层建筑分成两段，避免读成一个整块
    v, f, mi = [], [], []
    add_box(v, f, mi, (15.0, 0, 8.4), (6.0, 9.0, 3.6), M_SUP, taper=0.94)
    add_box(v, f, mi, (15.0, 0, 10.4), (5.2, 8.2, 0.7), M_DK)
    deckhouse = new_object("AftDeckhouse", v, f, materials, mi, smooth=False)
    parts.append(deckhouse)

    # 驾驶室窗带 (前后左右)
    v, f, mi = [], [], []
    wz0, wz1 = 12.1, 13.0
    add_box(v, f, mi, (28.75, 0, (wz0 + wz1) / 2), (0.18, 9.0, wz1 - wz0), 0)   # 前窗
    add_box(v, f, mi, (23.4, 0, (wz0 + wz1) / 2), (0.18, 8.0, wz1 - wz0), 0)    # 后窗
    for sgn in (1, -1):
        add_box(v, f, mi, (25.2, sgn * 4.55, (wz0 + wz1) / 2),
                (6.0, 0.18, wz1 - wz0), 0)                                    # 侧窗
    # 上层建筑舷侧窗带
    for sgn in (1, -1):
        add_box(v, f, mi, (21.5, sgn * 5.05, 10.0), (7.0, 0.16, 0.75), 0)
    win = new_object("BridgeWindows", v, f, ["Glass_Dark"], mi, smooth=False)
    parts.append(win)

    # 烟囱 / 排烟管 (2 组, 带斜切顶)
    v, f, mi = [], [], []
    for sgn in (1, -1):
        add_box(v, f, mi, (3.0, sgn * 3.2, 7.6), (5.2, 2.6, 6.2), M_DK, taper=0.88)
        # 斜切排气口
        for k in range(3):
            add_box(v, f, mi, (3.0 + (k - 1) * 1.5, sgn * 3.2, 10.9),
                    (1.0, 1.9, 0.55), M_MET)
    funnel = new_object("Funnels", v, f, materials, mi, smooth=False)
    parts.append(funnel)

    return join_objects(parts, "Superstructure_Assembly")


# ---------------------------------------------------------------------
# 8. 桅杆与雷达
# ---------------------------------------------------------------------

def build_mast():
    """三脚桅杆 + 旋转雷达 + 桅顶球罩。"""
    parts = []
    v, f, mi = [], [], []

    # 三脚主桅 (从 z=13.4 起到 z=27)
    base_z, top_z = 13.4, 27.0
    for sgn, tilt in ((1, 1.0), (-1, 1.0), (0, 0.0)):
        y0 = sgn * 3.1
        y1 = sgn * 0.9
        o = len(v)
        for (yy, zz) in ((y0, base_z), (y1, top_z)):
            for k in range(6):
                a = 2 * math.pi * k / 6
                v.append((22.5 + (zz - base_z) * 0.05, yy + 0.34 * math.cos(a),
                          zz + 0.34 * math.sin(a)))
        for k in range(6):
            k2 = (k + 1) % 6
            f.append((o + k, o + k2, o + 6 + k2, o + 6 + k))
            mi.append(0)
    # 横向桁架
    for zz in (17.5, 21.0, 24.5):
        t = (zz - base_z) / (top_z - base_z)
        y = 3.1 + (0.9 - 3.1) * t
        add_box(v, f, mi, (22.5 + (zz - base_z) * 0.05, 0, zz), (0.22, 2 * y, 0.22), 0)
        add_box(v, f, mi, (22.5 + (zz - base_z) * 0.05, 0, zz - 1.6),
                (0.18, 2 * y * 0.55, 0.18), 0)
    mast = new_object("Mast", v, f, ["Metal_Light"], mi, smooth=False)
    parts.append(mast)

    # 旋转雷达天线 (矩形阵面)
    v, f, mi = [], [], []
    add_box(v, f, mi, (22.7, 0, 19.6), (0.30, 5.6, 1.5), 0)          # 阵面
    add_box(v, f, mi, (22.5, 0, 19.6), (0.7, 0.7, 0.9), 1)           # 转台
    add_box(v, f, mi, (22.6, 0, 21.2), (0.16, 4.2, 0.55), 0)         # 上副天线
    radar = new_object("RadarArray", v, f, ["Radome_White", "Metal_Dark"], mi, smooth=False)
    parts.append(radar)

    # 桅顶球罩
    sv, sf, smi = [], [], []
    seg, ring = 14, 8
    cz = 25.6
    for i in range(ring + 1):
        phi = math.pi * i / ring
        for j in range(seg):
            th = 2 * math.pi * j / seg
            sv.append((22.7 + 1.05 * math.cos(phi),
                       1.05 * math.sin(phi) * math.cos(th),
                       cz + 1.15 * math.sin(phi) * math.sin(th)))
    for i in range(ring):
        for j in range(seg):
            j2 = (j + 1) % seg
            sf.append((i * seg + j, i * seg + j2, (i + 1) * seg + j2, (i + 1) * seg + j))
            smi.append(0)
    dome = new_object("MastDome", sv, sf, ["Radome_White"], smi, smooth=True)
    recalc_normals(dome)
    parts.append(dome)

    return join_objects(parts, "Mast_Assembly")


# ---------------------------------------------------------------------
# 9. 机库与飞行甲板
# ---------------------------------------------------------------------

def build_hangar_deck():
    """机库主体 + 后舱门 + 飞行甲板 + 安全标线。"""
    parts = []
    M_SUP, M_DK, M_MET, M_YEL = 0, 1, 2, 3
    materials = ["Superstructure", "Superstructure_Dk", "Metal_Dark", "Safety_Yellow"]

    # 机库主体
    v, f, mi = [], [], []
    add_box(v, f, mi, (-17.0, 0, 6.85), (22.0, 11.6, 4.7), M_SUP)      # 机库外壳
    add_box(v, f, mi, (-17.0, 0, 9.3), (22.6, 12.0, 0.42), M_DK)      # 机库顶
    hangar = new_object("Hangar", v, f, materials, mi, smooth=False)
    parts.append(hangar)

    # 后舱门 (朝艉, 通向飞行甲板)
    v, f, mi = [], [], []
    dz0, dz1 = 4.7, 9.1
    add_box(v, f, mi, (-28.05, 0, (dz0 + dz1) / 2), (0.35, 9.6, dz1 - dz0), 0)
    for k in range(7):  # 门板横缝
        add_box(v, f, mi, (-28.3, 0, dz0 + 0.25 + k * 0.62), (0.12, 9.4, 0.07), 1)
    door = new_object("HangarDoor", v, f, ["Superstructure_Dk", "Metal_Dark"], mi, smooth=False)
    parts.append(door)

    # 飞行甲板：随艉部型宽放样，带外伸
    n = 45
    fv, ff, fmi = [], [], []
    grid = []
    for i in range(n):
        t = i / (n - 1)
        x = -46.0 + t * 44.0
        tt = t_of_x(x)
        hb = HALF_BEAM * half_beam_fn(tt) * section_fn(1.0)
        y = min(hb * 0.99 + 0.55, HALF_BEAM)   # 向外悬出甲板边线
        z = FLIGHT_DECK_Z
        row = []
        for frac in (-1.0, -0.5, 0.0, 0.5, 1.0):
            row.append(len(fv))
            fv.append((x, frac * y, z + (1 - frac * frac) * 0.12))
        grid.append(row)
    for i in range(n - 1):
        for j in range(4):
            ff.append((grid[i][j], grid[i][j + 1], grid[i + 1][j + 1], grid[i + 1][j]))
            fmi.append(0)
    # 甲板下缘加厚
    for i in range(n - 1):
        x, x2 = fv[grid[i][0]][0], fv[grid[i + 1][0]][0]
        y, y2 = fv[grid[i][0]][1], fv[grid[i + 1][0]][1]
        for sgn in (1, -1):
            o = len(fv)
            fv += [(x, sgn * y, FLIGHT_DECK_Z), (x2, sgn * y2, FLIGHT_DECK_Z),
                   (x2, sgn * y2, FLIGHT_DECK_Z - 0.42), (x, sgn * y, FLIGHT_DECK_Z - 0.42)]
            ff.append((o, o + 1, o + 2, o + 3))
            fmi.append(1)
    fdeck = new_object("FlightDeck", fv, ff, ["FlightDeck", "Metal_Dark"], fmi, smooth=True)
    recalc_normals(fdeck)
    parts.append(fdeck)

    # 飞行甲板安全标线 (黄)
    v, f, mi = [], [], []
    for i in range(n - 1):
        t = i / (n - 1)
        x = -46.0 + t * 44.0
        x2 = -46.0 + (i + 1) / (n - 1) * 44.0
        tt = t_of_x(x)
        y = min(HALF_BEAM * half_beam_fn(tt) * section_fn(1.0) * 0.99 + 0.55, HALF_BEAM)
        o = len(v)
        v += [(x, y - 0.45, FLIGHT_DECK_Z + 0.14), (x2, y - 0.45, FLIGHT_DECK_Z + 0.14),
              (x2, y - 0.10, FLIGHT_DECK_Z + 0.14), (x, y - 0.10, FLIGHT_DECK_Z + 0.14)]
        f.append((o, o + 1, o + 2, o + 3))
        mi.append(0)
    # 直升机着陆圆心标记
    add_prism(v, f, mi, (-30.0, 0, FLIGHT_DECK_Z + 0.15), (4.2, 4.2, 0.06), 0, n=28)
    marks = new_object("DeckMarkings", v, f, ["Safety_Yellow"], mi, smooth=False)
    parts.append(marks)

    # 飞行甲板周边安全拦杆 + 支撑斜撑
    v, f, mi = [], [], []
    for sgn in (1, -1):
        # 立柱
        for k in range(21):
            x = -45.0 + k * 2.2
            tt = t_of_x(x)
            y = min(HALF_BEAM * half_beam_fn(tt) * section_fn(1.0) * 0.99 + 0.45, HALF_BEAM)
            add_prism(v, f, mi, (x, sgn * y, FLIGHT_DECK_Z + 0.62), (0.09, 0.09, 1.0), 0, n=6)
        # 横杆
        o = len(v)
        prev = None
        for k in range(41):
            x = -45.0 + k * 1.1
            tt = t_of_x(x)
            y = min(HALF_BEAM * half_beam_fn(tt) * section_fn(1.0) * 0.99 + 0.45, HALF_BEAM)
            for z in (0.72, 1.05):
                if prev is not None:
                    f.append((o - 2, o, o + 1, o - 1))
                    mi.append(0)
                v.append((x, sgn * y, FLIGHT_DECK_Z + z))
                o += 1
            prev = x
    # 艉缘吊机基座
    add_box(v, f, mi, (-44.0, 0, FLIGHT_DECK_Z + 0.7), (1.6, 2.4, 1.2), 1)
    edge = new_object("FlightDeckEdge", v, f, ["Metal_Light", "Metal_Dark"], mi, smooth=False)
    parts.append(edge)

    return join_objects(parts, "Hangar_Assembly")


# ---------------------------------------------------------------------
# 10. 武器与舰载设备
# ---------------------------------------------------------------------

def build_weapons():
    """76mm 主炮 + 垂直发射单元 + 反潜鱼雷发射器。"""
    parts = []
    M_MET, M_GUN, M_DK, M_RED = 0, 1, 2, 3
    materials = ["Metal_Dark", "Metal_Gun", "Superstructure_Dk", "Safety_Red"]

    # ---- 76mm 舰炮 (艏) ----
    v, f, mi = [], [], []
    gx, gz = 43.0, deck_z_at(43.0) + BULWARK_H
    add_prism(v, f, mi, (gx, 0, gz + 0.85), (5.2, 2.9, 1.7), M_DK, n=12)   # 炮座
    add_box(v, f, mi, (gx + 0.3, 0, gz + 1.95), (4.0, 2.5, 1.5), M_DK, taper=0.86)
    add_box(v, f, mi, (gx + 2.1, 0, gz + 2.30), (1.2, 1.7, 0.95), M_GUN, taper=0.9)  # 炮塔
    barrel = add_prism(v, f, mi, (gx + 4.0, 0, gz + 2.35), (3.4, 0.34, 0.34), M_GUN, n=12)
    barrel = len(v) - 24
    # 炮管略微仰起
    gun = new_object("GunMount", v, f, materials, mi, smooth=False)
    parts.append(gun)

    # ---- 垂直发射单元 (VLS) ----
    v, f, mi = [], [], []
    vz = deck_z_at(33.0) + BULWARK_H
    add_box(v, f, mi, (33.0, 0, vz + 0.28), (7.6, 7.0, 0.56), M_DK)
    for r in range(2):
        for c in range(4):
            cx = 30.6 + r * 1.75
            cy = -2.4 + c * 1.6
            add_box(v, f, mi, (cx, cy, vz + 0.66), (1.5, 1.35, 0.26),
                    M_RED if (r + c) % 3 == 0 else M_MET)
    vls = new_object("VLS", v, f, materials, mi, smooth=False)
    parts.append(vls)

    # ---- 反潜鱼雷发射器 (两舷) ----
    v, f, mi = [], [], []
    for sgn in (1, -1):
        x = 8.0
        z = deck_z_at(x) + BULWARK_H
        add_box(v, f, mi, (x, sgn * (HALF_BEAM * 0.72), z + 0.5),
                (3.2, 1.1, 1.0), M_MET)
    tubes = new_object("TorpedoTubes", v, f, materials, mi, smooth=False)
    parts.append(tubes)

    return join_objects(parts, "Weapons_Assembly")


def build_helicopter():
    """简化直升机（停在飞行甲板上）——低面数但轮廓可辨。"""
    parts = []
    v, f, mi = [], [], []
    hx, hz = -30.0, FLIGHT_DECK_Z + 1.35

    # 机身：分段椭球
    seg, ring = 14, 9
    for i in range(ring + 1):
        ph = math.pi * i / ring
        for j in range(seg):
            th = 2 * math.pi * j / seg
            f_x = math.cos(ph)
            v.append((hx + 2.9 * f_x,
                      1.05 * math.sin(ph) * math.cos(th),
                      hz + 1.05 * math.sin(ph) * math.sin(th)))
    for i in range(ring):
        for j in range(seg):
            j2 = (j + 1) % seg
            f.append((i * seg + j, i * seg + j2, (i + 1) * seg + j2, (i + 1) * seg + j))
            mi.append(0)
    # 尾梁
    o = len(v)
    v += [(hx - 2.4, 0.22, hz + 0.15), (hx - 7.4, 0.10, hz + 0.85),
          (hx - 7.4, -0.10, hz + 0.85), (hx - 2.4, -0.22, hz + 0.15)]
    f.append((o, o + 1, o + 2, o + 3))
    mi.append(0)
    # 尾翼
    add_box(v, f, mi, (hx - 6.9, 0, hz + 1.45), (0.9, 2.9, 0.14), 0)
    add_box(v, f, mi, (hx - 6.4, 0, hz + 0.95), (1.1, 0.12, 0.7), 0)
    # 起落滑橇
    for sgn in (1, -1):
        add_box(v, f, mi, (hx + 0.1, sgn * 0.95, hz - 1.25), (4.4, 0.16, 0.16), 1)
        add_box(v, f, mi, (hx + 0.1, sgn * 0.95, hz - 0.75), (0.16, 0.16, 1.0), 1)
    # 旋翼
    add_prism(v, f, mi, (hx + 0.2, 0, hz + 1.30), (0.30, 0.30, 0.9), 1, n=8)
    for ang in (0.0, math.pi / 2):
        ca, sa = math.cos(ang), math.sin(ang)
        o = len(v)
        for s in (-1, 1):
            v.append((hx + 0.2 + s * 5.4 * ca, s * 5.4 * sa, hz + 1.80))
            v.append((hx + 0.2 + s * 5.4 * ca - 0.25 * sa, s * 5.4 * sa + 0.25 * ca,
                      hz + 1.80))
        f.append((o, o + 1, o + 3, o + 2))
        mi.append(1)
    heli = new_object("Helicopter", v, f, ["Superstructure", "Rotor_Dark"], mi, smooth=True)
    recalc_normals(heli)
    parts.append(heli)

    return join_objects(parts, "Helicopter_Assembly")


# ---------------------------------------------------------------------
# 11. 甲板属具
# ---------------------------------------------------------------------

def build_deck_details():
    """舷梯、救生艇、蘑菇通风口、系缆桩、舱口、锚设备。"""
    parts = []

    # ---- 蘑菇通风头 ----
    v, f, mi = [], [], []
    for x in (18.0, 27.0, -2.0, -12.0, -22.0, 8.0):
        z = deck_z_at(x) + BULWARK_H
        add_prism(v, f, mi, (x, 3.2, z + 0.55), (0.7, 0.7, 1.1), 0, n=10)
        add_prism(v, f, mi, (x, 3.2, z + 1.22), (1.1, 1.1, 0.55), 0, n=10)
        add_prism(v, f, mi, (x, -3.2, z + 0.55), (0.7, 0.7, 1.1), 0, n=10)
        add_prism(v, f, mi, (x, -3.2, z + 1.22), (1.1, 1.1, 0.55), 0, n=10)
    vents = new_object("VentMushrooms", v, f, ["Metal_Light"], mi, smooth=False)
    parts.append(vents)

    # ---- 舱口 ----
    v, f, mi = [], [], []
    for x, w in ((36.0, 2.2), (30.0, 1.8), (10.0, 2.4), (-2.0, 2.0), (-14.0, 2.2)):
        z = deck_z_at(x) + BULWARK_H
        for sgn in (1, -1):
            add_box(v, f, mi, (x, sgn * 3.6, z + 0.20), (w, w * 0.8, 0.32), 0)
    hatches = new_object("Hatches", v, f, ["Metal_Dark"], mi, smooth=False)
    parts.append(hatches)

    # ---- 系缆桩 ----
    v, f, mi = [], [], []
    for x in np.arange(-44, 48, 6.0):
        z = deck_z_at(x) + BULWARK_H
        tt = t_of_x(x)
        hb = HALF_BEAM * half_beam_fn(tt) * section_fn(1.0) * 0.90
        if hb < 2.2:
            continue
        for sgn in (1, -1):
            add_prism(v, f, mi, (x, sgn * hb, z + 0.28), (0.42, 0.42, 0.56), 0, n=8)
    bollards = new_object("Bollards", v, f, ["Metal_Dark"], mi, smooth=False)
    parts.append(bollards)

    # ---- 封闭式救生艇 + 吊艇架 ----
    v, f, mi = [], [], []
    for x in (12.0, 4.0):
        z = deck_z_at(x) + BULWARK_H
        for sgn in (1, -1):
            add_box(v, f, mi, (x, sgn * (HALF_BEAM * 0.78), z + 1.05),
                    (5.2, 1.7, 1.7), 2, taper=0.55)          # 艇体
            add_box(v, f, mi, (x - 1.6, sgn * (HALF_BEAM * 0.86), z + 2.2),
                    (0.24, 0.24, 2.0), 0)                  # 吊架
            add_box(v, f, mi, (x + 1.6, sgn * (HALF_BEAM * 0.86), z + 2.2),
                    (0.24, 0.24, 2.0), 0)
    boats = new_object("Lifeboats", v, f,
                       ["Metal_Dark", "Metal_Light", "Lifeboat_Orange"], mi, smooth=False)
    parts.append(boats)

    # ---- 锚 + 锚链筒 (艏部) ----
    v, f, mi = [], [], []
    for sgn in (1, -1):
        add_box(v, f, mi, (49.0, sgn * 3.0, deck_z_at(49.0) + 0.9), (1.6, 0.9, 1.6), 0)
        add_box(v, f, mi, (48.6, sgn * 3.0, deck_z_at(49.0) + 0.35), (1.0, 0.7, 0.7), 0)
    anchor = new_object("Anchors", v, f, ["Metal_Dark"], mi, smooth=False)
    parts.append(anchor)

    # ---- 桅灯 / 航行灯 ----
    v, f, mi = [], [], []
    lz = deck_z_at(50.0) + 2.4
    add_box(v, f, mi, (50.0, 0, lz), (0.4, 0.4, 0.5), 0)
    add_box(v, f, mi, (-27.0, 5.4, 9.6), (0.35, 0.35, 0.45), 1)     # 右舷绿灯
    add_box(v, f, mi, (-27.0, -5.4, 9.6), (0.35, 0.35, 0.45), 2)    # 左舷红灯
    add_box(v, f, mi, (22.7, 0, 26.9), (0.3, 0.3, 0.3), 0)          # 桅顶白灯
    lights = new_object("NavLights", v, f,
                        ["Light_Nav", "Light_Green", "Light_Red"], mi, smooth=False)
    parts.append(lights)

    return join_objects(parts, "DeckDetails_Assembly")


# ---------------------------------------------------------------------
# 12. 舱室拾取体
# ---------------------------------------------------------------------

def build_compartment_volumes():
    """
    创建 5 个与 shipLayout.js bounds 完全一致的舱室包围盒，
    命名规则 COMPARTMENT_<id>，供 three.js 射线拾取与高亮使用。
    材质为全透明，渲染不可见但仍参与 raycast。
    """
    parts = []
    for spec in COMPARTMENT_SPECS:
        (x0, y0, z0), (x1, y1, z1) = spec["bounds"]
        bmin = to_blender((x0, y0, z0))
        bmax = to_blender((x1, y1, z1))
        v, f, mi = [], [], []
        add_box(v, f, mi,
                ((bmin[0] + bmax[0]) / 2, (bmin[1] + bmax[1]) / 2, (bmin[2] + bmax[2]) / 2),
                (abs(bmax[0] - bmin[0]), abs(bmax[1] - bmin[1]), abs(bmax[2] - bmin[2])), 0)
        ob = new_object(spec["node"], v, f, ["CompartmentPick"], mi, smooth=False)
        ob.display_type = "WIRE"
        ob["compartmentId"] = spec["id"]
        ob["compartmentName"] = spec["name"]
        parts.append(ob)
    return parts


def build_pick_material():
    """全透明拾取材质。"""
    m = bpy.data.materials.new("CompartmentPick")
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        if n.type != "OUTPUT_MATERIAL":
            nt.nodes.remove(n)
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    tr.inputs[0].default_value = (1, 1, 1, 1)
    nt.links.new(tr.outputs[0], out.inputs[0])
    m.blend_method = "BLEND" if hasattr(m, "blend_method") else None
    MATERIALS["CompartmentPick"] = m
    return m


# ---------------------------------------------------------------------
# 13. 统计与导出
# ---------------------------------------------------------------------

def scene_stats():
    tris = 0
    verts = 0
    objects = []
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH":
            continue
        me = ob.data
        me.calc_loop_triangles()
        t = len(me.loop_triangles)
        tris += t
        verts += len(me.vertices)
        objects.append(dict(name=ob.name, tris=t, verts=len(me.vertices),
                            materials=[m.name for m in me.materials]))
    return dict(objects=objects, total_tris=tris, total_verts=verts,
                object_count=len(objects))


def bbox_world():
    mn = [1e9] * 3
    mx = [-1e9] * 3
    dg = bpy.context.evaluated_depsgraph_get()
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH" or ob.name.startswith("COMPARTMENT_"):
            continue
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        for vtx in me.vertices:
            w = ev.matrix_world @ vtx.co
            for i in range(3):
                mn[i] = min(mn[i], w[i])
                mx[i] = max(mx[i], w[i])
        ev.to_mesh_clear()
    return mn, mx


def main():
    global OUTPUT_DIR
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    here = os.path.dirname(os.path.abspath(__file__))
    project = os.path.dirname(here)
    OUTPUT_DIR = os.path.join(project, "public", "models")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    glb_path = os.path.join(OUTPUT_DIR, "ship.glb")

    print(">>> 清空场景")
    clear_scene()
    build_materials()
    build_pick_material()

    print(">>> 放样船体")
    build_hull()
    print(">>> 甲板与舷墙")
    build_deck()
    print(">>> 栏杆")
    build_railings()
    print(">>> 上层建筑")
    build_superstructure()
    print(">>> 桅杆与雷达")
    build_mast()
    print(">>> 机库与飞行甲板")
    build_hangar_deck()
    print(">>> 武器")
    build_weapons()
    print(">>> 舰载直升机")
    build_helicopter()
    print(">>> 甲板属具")
    build_deck_details()
    print(">>> 舱室拾取体")
    build_compartment_volumes()

    stats = scene_stats()
    mn, mx = bbox_world()
    print(">>> 统计")
    print("OBJECTS :", stats["object_count"])
    print("TRIS    :", f"{stats['total_tris']:,}")
    print("VERTS   :", f"{stats['total_verts']:,}")
    for o in sorted(stats["objects"], key=lambda d: -d["tris"]):
        print(f"   - {o['name']:<28} tris={o['tris']:>7,}  mats={','.join(o['materials'])}")

    print(">>> 导出 GLB ->", glb_path)
    bpy.ops.object.select_all(action="SELECT")
    kw = dict(filepath=glb_path, export_format="GLB", export_apply=True,
              use_selection=False, export_yup=True)
    draco = True
    try:
        bpy.ops.export_scene.gltf(
            export_draco_mesh_compression_enable=True,
            export_draco_mesh_compression_level=6, **kw)
    except Exception as e:
        print("!!! Draco 导出失败，退回未压缩:", e)
        draco = False
        bpy.ops.export_scene.gltf(**kw)

    size_mb = os.path.getsize(glb_path) / 1024 / 1024
    print(">>> 完成")
    print("GLB     :", glb_path)
    print("SIZE_MB :", f"{size_mb:.2f}")
    print("DRACO   :", draco)
    print("BBOX_MIN:", [round(v, 2) for v in mn])
    print("BBOX_MAX:", [round(v, 2) for v in mx])

    meta = dict(
        glb=glb_path, size_mb=round(size_mb, 3), draco=draco,
        total_tris=stats["total_tris"], total_verts=stats["total_verts"],
        object_count=stats["object_count"],
        bbox_min=[round(v, 3) for v in mn], bbox_max=[round(v, 3) for v in mx],
        objects=stats["objects"],
        compartments=[dict(id=s["id"], name=s["name"], node=s["node"],
                           bounds=s["bounds"], anchor=s["anchor"])
                      for s in COMPARTMENT_SPECS],
    )
    meta_path = os.path.join(OUTPUT_DIR, "ship.meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("META    :", meta_path)


if __name__ == "__main__":
    main()
