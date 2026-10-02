"""
舰船舱室内部模型 —— 程序化重建
════════════════════════════════════════════════════════════════════

为什么重做而不是继续用 PyroSim 导出：
  1. PyroSim → glTF 往返后材质全部退化为白色默认值，金属/涂层区分丢失；
  2. 几何是封闭的六面体，从外部俯视完全看不到舱内；
  3. 三个大文件（主机舱/机库/士兵住舱）实为同一套 FDS 试验台架，
     唯一内容分别只有 14 / 270 / 60 个三角面。

重建规格：
  · 全部开顶（无天花板），从上方/侧上方可直接俯视舱内
  · 仅使用 Principled BSDF 的 Base Color / Roughness / Metallic —— 这三项
    与 glTF 的 metallic-roughness 模型一一对应，导出后不会退化成白色
  · 舱室壳体（地板/舱壁/加强筋）+ 各舱真实设备

坐标系与 tools/build_ship.py 一致：Blender (x, -z_beam, y_up)，
导出后 three.js 侧得到 (x 长度, y 上, z 右舷)，与 src/config/shipLayout.js 对齐。

运行:
    blender --background --factory-startup --python tools/build_compartments.py
"""

import json
import math
import os
import struct
import sys

import bpy

# ---------------------------------------------------------------------
# 舱室契约 —— 与 src/config/shipLayout.js COMPARTMENTS[] 逐项对应
# ---------------------------------------------------------------------
COMP = [
    dict(id=1, key="main-engine", en="MainEngineRoom", label="主机舱",
         bounds=((-48.0, -3.6, -5.2), (-30.0, 4.0, 5.2))),
    dict(id=2, key="power-room", en="PowerRoom", label="电站间",
         bounds=((-4.0, 0.4, -5.0), (6.0, 5.0, 5.0))),
    dict(id=3, key="galley", en="Galley", label="灶炉间",
         bounds=((22.0, 4.6, -4.6), (29.0, 7.6, 4.6))),
    dict(id=4, key="berthing", en="Berthing", label="士兵住舱",
         bounds=((8.0, 0.8, -5.0), (20.0, 5.4, 5.0))),
    dict(id=5, key="hangar", en="Hangar", label="机库",
         bounds=((-28.0, 0.5, -5.6), (-6.0, 6.2, 5.6))),
]

OUT_DIR = None


def B(p):
    """ship/three.js (x, y_up, z_beam) -> Blender (x, -z_beam, y_up)"""
    return (p[0], -p[2], p[1])


def BS(s):
    """
    尺寸必须跟着 B() 做同样的轴置换，否则长宽高会整体错位。
    ship 尺寸 (沿 x, 沿 y_up, 沿 z_beam)
      -> Blender 尺寸 (沿 x, 沿 z_beam, 沿 y_up)
    只交换后两轴、保持正负号，体积与比例都不变。
    """
    return (s[0], s[2], s[1])


def np_arange(a, b, s):
    out, v = [], a
    while v < b:
        out.append(v)
        v += s
    return out


# ---------------------------------------------------------------------
# 材质
# ---------------------------------------------------------------------
MAT = {}
SMOOTH = {"ShaftPolish", "MachineGrey", "MachineMid", "MachineDark", "Brass"}


def mat(name, color, rough=0.6, metal=0.0):
    if name in MAT:
        return MAT[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    m.diffuse_color = (*color, 1.0)
    MAT[name] = m
    return m


def build_materials():
    """
    舱内配色：低饱和海军灰为底，用暖色/警示色区分设备类别。
    反照率刻意压低 —— 舱室视图里光照是定向光 + 环境光，
    过亮的底色会让所有结构糊成一片白。
    """
    mat("BulkheadPaint",  (0.255, 0.268, 0.272), 0.74)          # 舱壁涂层
    mat("BulkheadLower",  (0.130, 0.140, 0.148), 0.70)          # 舱壁下部防撞涂层
    mat("DeckPlate",      (0.105, 0.112, 0.120), 0.80)          # 地板钢板
    mat("DeckMarking",    (0.760, 0.590, 0.070), 0.66)          # 地面标线
    mat("MachineGrey",    (0.215, 0.228, 0.242), 0.44, 0.70)     # 设备本体
    mat("MachineMid",     (0.130, 0.140, 0.150), 0.46, 0.78)
    mat("MachineDark",    (0.048, 0.052, 0.058), 0.42, 0.85)     # 管道/框架
    mat("ShaftPolish",    (0.520, 0.535, 0.548), 0.20, 1.00)     # 抛光轴/栏杆
    mat("SafetyYellow",   (0.780, 0.520, 0.045), 0.52)
    mat("SafetyRed",      (0.560, 0.070, 0.052), 0.48)
    mat("Insulation",     (0.480, 0.462, 0.415), 0.88)          # 隔热/铺垫
    mat("Mattress",       (0.060, 0.082, 0.135), 0.92)          # 床垫帆布
    mat("PanelFace",      (0.070, 0.082, 0.094), 0.36, 0.30)    # 仪表/配电盘面
    mat("Indicator",      (0.820, 0.180, 0.070), 0.40)          # 指示灯
    mat("Brass",          (0.520, 0.360, 0.130), 0.30, 0.90)


# ---------------------------------------------------------------------
# 几何累积器：按材质分组，最后一次性合并为少量对象（控制 draw call）
# ---------------------------------------------------------------------
class Mesh:
    def __init__(self):
        self.v = []      # 每种材质一份 (verts, faces)
        self.mats = []
        self.ext = []    # 每种材质的包围盒 (min, max)，用于越界诊断

    def _slot(self, material):
        if material not in self.mats:
            self.mats.append(material)
            self.v.append(([], []))
            self.ext.append([[1e9] * 3, [-1e9] * 3])
        return self.v[self.mats.index(material)]

    def _track(self, material, p):
        e = self.ext[self.mats.index(material)]
        for i in range(3):
            if p[i] < e[0][i]:
                e[0][i] = p[i]
            if p[i] > e[1][i]:
                e[1][i] = p[i]

    def box(self, center, size, material):
        cx, cy, cz = B(center)
        sx, sy, sz = (s / 2 for s in BS(size))
        verts, faces = self._slot(material)
        b = len(verts)
        verts += [(cx - sx, cy - sy, cz - sz), (cx + sx, cy - sy, cz - sz),
                  (cx + sx, cy + sy, cz - sz), (cx - sx, cy + sy, cz - sz),
                  (cx - sx, cy - sy, cz + sz), (cx + sx, cy - sy, cz + sz),
                  (cx + sx, cy + sy, cz + sz), (cx - sx, cy + sy, cz + sz)]
        for f in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                  (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
            faces.append(tuple(b + i for i in f))
        for p in verts[b:]:
            self._track(material, p)

    def cyl(self, p0, p1, radius, material, seg=10):
        """任意方向圆柱（管线、栏杆、轴）。"""
        a = bpy.mathutils.Vector(B(p0)) if hasattr(bpy, "mathutils") else None
        import mathutils
        a = mathutils.Vector(B(p0))
        bb = mathutils.Vector(B(p1))
        d = bb - a
        L = d.length
        if L < 1e-4:
            return
        z = d.normalized()
        up = mathutils.Vector((0, 0, 1)) if abs(z.z) < 0.9 else mathutils.Vector((1, 0, 0))
        x = z.cross(up).normalized()
        y = z.cross(x)
        verts, faces = self._slot(material)
        base = len(verts)
        for end in (a, bb):
            for k in range(seg):
                t = 2 * math.pi * k / seg
                verts.append(tuple(end + x * (radius * math.cos(t))
                                   + y * (radius * math.sin(t))))
        for k in range(seg):
            k2 = (k + 1) % seg
            faces.append((base + k, base + k2, base + seg + k2, base + seg + k))
        faces.append(tuple(base + k for k in range(seg - 1, -1, -1)))
        faces.append(tuple(base + seg + k for k in range(seg)))
        for p in verts[base:]:
            self._track(material, p)

    def pipe_run(self, pts, radius, material, seg=8):
        for i in range(len(pts) - 1):
            self.cyl(pts[i], pts[i + 1], radius, material, seg)

    def overshoot(self, bmin, bmax):
        """
        返回越界清单 (材质, 方向, 超出量)。
        self.ext 记录的是 Blender 坐标，必须换算回 ship 坐标再比较，
        否则三轴错位会报出完全无关的超大量。
        """
        out = []
        for material, (mn, mx) in zip(self.mats, self.ext):
            lo = (mn[0], mn[2], -mx[1])   # Blender -> ship，与 bbox_world 同口径
            hi = (mx[0], mx[2], -mn[1])
            for i, axis in enumerate("xyz"):
                if lo[i] < bmin[i] - 1e-3:
                    out.append((material, axis + "-", round(bmin[i] - lo[i], 2)))
                if hi[i] > bmax[i] + 1e-3:
                    out.append((material, axis + "+", round(hi[i] - bmax[i], 2)))
        return out

    def emit(self, name, coll):
        objs = []
        for material, (verts, faces) in zip(self.mats, self.v):
            if not verts:
                continue
            me = bpy.data.meshes.new(f"{name}_{material}")
            me.from_pydata(verts, [], faces)
            me.update()
            me.materials.append(mat(material, (0.5, 0.5, 0.5)))
            if material in SMOOTH:
                me.shade_smooth()
            ob = bpy.data.objects.new(f"{name}_{material}", me)
            coll.objects.link(ob)
            objs.append(ob)
        if not objs:
            return None, 0
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        bpy.ops.object.join()
        root = bpy.context.view_layer.objects.active
        root.name = name
        root.data.name = name + "_mesh"
        bpy.ops.object.select_all(action="DESELECT")
        return root, len(self.mats)


# ---------------------------------------------------------------------
# 舱室壳体：地板 + 四壁 + 加强筋，**无顶盖**
# ---------------------------------------------------------------------
def room_shell(m, bmin, bmax, wall_frac=0.45):
    """
    舱室壳体。**故意不做全高舱壁** —— 舱室视图的相机是从上方斜看进来的，
    全高舱壁会把舱内设备整个挡住。改成剖切式矮墙（默认 45% 舱高），
    配合角部加强筋，既能读出"这是个舱室"，又不遮挡内部。
    """
    x0, y0, z0 = bmin
    x1, y1, z1 = bmax
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    L, W = x1 - x0, z1 - z0
    H = y1 - y0
    wall_h = H * wall_frac
    t = 0.12

    m.box((cx, y0 + 0.03, cz), (L, 0.06, W), "DeckPlate")

    m.box((cx, y0 + wall_h / 2, z0 + t / 2), (L, wall_h, t), "BulkheadPaint")
    m.box((cx, y0 + wall_h / 2, z1 - t / 2), (L, wall_h, t), "BulkheadPaint")
    m.box((x0 + t / 2, y0 + wall_h / 2, cz), (t, wall_h, W), "BulkheadPaint")
    m.box((x1 - t / 2, y0 + wall_h / 2, cz), (t, wall_h, W), "BulkheadPaint")

    # 舱壁下部防撞涂层：与上部形成分色，避免大片死白
    lh = wall_h * 0.40
    m.box((cx, y0 + lh / 2, z0 + t + 0.01), (L, lh, 0.03), "BulkheadLower")
    m.box((cx, y0 + lh / 2, z1 - t - 0.01), (L, lh, 0.03), "BulkheadLower")
    m.box((x0 + t + 0.01, y0 + lh / 2, cz), (0.03, lh, W), "BulkheadLower")
    m.box((x1 - t - 0.01, y0 + lh / 2, cz), (0.03, lh, W), "BulkheadLower")

    # 角部加强筋 + 顶部围板：给矮墙一个收口，读起来像被切断的舱壁
    for sx in (-1, 1):
        for sz in (-1, 1):
            px = cx + sx * (L / 2 - 0.28)
            pz = cz + sz * (W / 2 - 0.28)
            m.box((px, y0 + wall_h / 2, pz), (0.22, wall_h, 0.22), "MachineMid")
            m.box((px, y0 + wall_h + 0.05, pz), (0.36, 0.10, 0.36), "MachineMid")

    # 顶部围板：中心偏移必须 >= 半个厚度，否则会探出舱壁外沿
    m.box((cx, y0 + wall_h + 0.05, z0 + 0.15), (L, 0.10, 0.30), "MachineMid")
    m.box((cx, y0 + wall_h + 0.05, z1 - 0.15), (L, 0.10, 0.30), "MachineMid")
    m.box((x0 + 0.15, y0 + wall_h + 0.05, cz), (0.30, 0.10, W), "MachineMid")
    m.box((x1 - 0.15, y0 + wall_h + 0.05, cz), (0.30, 0.10, W), "MachineMid")
    return wall_h


def light_strip(m, xs, y, z0, z1, material="ShaftPolish"):
    """中轴线上的成排灯管（开顶舱室用悬挂灯表达顶灯）。"""
    for x in xs:
        m.cyl((x, y, z0), (x, y, z1), 0.10, material, 8)
        m.cyl((x, y + 0.07, z0), (x, y + 0.07, z1), 0.05, "MachineDark", 6)


def floor_band(m, bmin, bmax, material, axis, value, width=0.22, thick=0.02):
    x0, y0, z0 = bmin
    x1, y1, z1 = bmax
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    L, W = x1 - x0, z1 - z0
    if axis == "x":
        m.box((cx, y0 + 0.07, value), (L * 0.94, thick, width), material)
    else:
        m.box((value, y0 + 0.07, cz), (width, thick, W * 0.94), material)


# ---------------------------------------------------------------------
# 舱室 1 · 主机舱
# ---------------------------------------------------------------------
def build_main_engine(bmin, bmax):
    m = Mesh()
    wall_h = room_shell(m, bmin, bmax, 0.42)
    x0, y0, z0 = bmin
    x1, y1, z1 = bmax
    cx, cz = (x0 + x1) / 2, 0.0
    base = y0

    floor_band(m, bmin, bmax, "DeckMarking", "x", cz, 0.5)

    # 两台燃气轮机 + 排气罩 + 检修平台
    for sgn in (-1, 1):
        gx = cx + sgn * 3.6
        gz = cz + sgn * 1.5
        m.box((gx, base + 0.95, gz), (4.6, 1.5, 2.4), "MachineGrey")
        m.box((gx, base + 1.82, gz), (4.0, 0.34, 2.0), "MachineMid")
        m.box((gx, base + 0.22, gz), (5.0, 0.44, 2.8), "MachineDark")
        for k in range(5):
            m.box((gx - 1.8 + k * 0.9, base + 1.0, gz + sgn * 1.22),
                  (0.62, 0.78, 0.08), "MachineDark")
        m.pipe_run([(gx + sgn * 2.3, base + 1.7, gz),
                    (x1 - 0.6 if sgn > 0 else x0 + 0.6, base + 1.7, gz)],
                   0.42, "MachineMid", 12)
        m.box((gx, base + 0.62, gz - sgn * 2.3), (4.2, 0.10, 1.1), "MachineMid")
        pz = gz - sgn * 2.85
        m.cyl((gx - 2.0, base + 0.67, pz), (gx + 2.0, base + 0.67, pz), 0.05, "ShaftPolish")
        for dx in (-2.0, -1.0, 0.0, 1.0, 2.0):
            m.cyl((gx + dx, base + 0.67, pz), (gx + dx, base + 1.6, pz), 0.04, "ShaftPolish")

    # 传动轴 + 轴承座
    m.cyl((cx - 5.6, base + 0.75, cz), (cx + 5.6, base + 0.75, cz), 0.30, "ShaftPolish", 14)
    for dx in (-4.0, -1.4, 1.4, 4.0):
        m.box((cx + dx, base + 0.55, cz), (0.9, 0.9, 1.3), "MachineDark")
        m.cyl((cx + dx - 0.5, base + 0.75, cz), (cx + dx + 0.5, base + 0.75, cz),
              0.36, "MachineDark", 14)

    # 日用油舱 / 滑油柜
    for sgn in (-1, 1):
        z0t = cz + sgn * 3.2
        m.cyl((cx + sgn * 6.2, base + 1.1, z0t),
              (cx + sgn * 6.2, base + 1.1, z0t + sgn * 1.2),
              0.80, "MachineMid", 16)

    # 舱壁管路（消防红 / 燃油黄 / 滑油黑）
    for k, (r, material) in enumerate(((0.10, "SafetyRed"), (0.09, "SafetyYellow"),
                                       (0.08, "MachineDark"))):
        zz = z0 + 0.6 + k * 0.34
        m.pipe_run([(x0 + 0.5, base + 2.5, zz), (x1 - 0.5, base + 2.5, zz)], r, material, 8)
        for dx in np_arange(x0 + 1.0, x1 - 0.5, 2.4):
            m.box((dx, base + 2.36, zz), (0.16, 0.28, 0.16), "MachineDark")

    # 起动/控制盘
    m.box((cx, base + 1.3, z1 - 0.55), (2.6, 1.5, 0.5), "MachineMid")
    m.box((cx, base + 1.45, z1 - 0.82), (2.3, 1.0, 0.06), "PanelFace")
    for k in range(3):
        m.box((cx - 0.8 + k * 0.8, base + 1.85, z1 - 0.86), (0.14, 0.14, 0.05), "Indicator")

    light_strip(m, np_arange(x0 + 2.0, x1 - 1.0, 3.6),
                base + wall_h - 0.1, cz - 2.0, cz + 2.0)
    return m


# ---------------------------------------------------------------------
# 舱室 2 · 电站间
# ---------------------------------------------------------------------
def build_power_room(bmin, bmax):
    m = Mesh()
    wall_h = room_shell(m, bmin, bmax, 0.48)
    x0, y0, z0 = bmin
    x1, y1, z1 = bmax
    cx, cz = (x0 + x1) / 2, 0.0
    base = y0
    floor_band(m, bmin, bmax, "DeckMarking", "x", cz, 0.4)

    # 三台柴油发电机组
    for dx in (-3.2, 0.0, 3.2):
        gx = cx + dx
        m.box((gx, base + 0.24, cz), (3.0, 0.48, 1.5), "MachineDark")        # 机座
        m.box((gx - 0.6, base + 0.95, cz), (1.7, 0.95, 1.25), "MachineGrey")  # 柴油机
        m.box((gx + 0.95, base + 0.95, cz), (1.2, 0.95, 1.25), "MachineMid")  # 发电机
        for f in range(4):                                                   # 散热百叶
            m.box((gx - 1.45, base + 0.72 + f * 0.15, cz), (0.06, 0.08, 1.0), "MachineDark")
        m.cyl((gx - 1.5, base + 1.05, cz), (gx - 1.1, base + 1.05, cz), 0.26, "MachineDark", 12)
        m.pipe_run([(gx - 1.5, base + 1.05, cz),
                    (gx - 1.5, base + wall_h - 0.4, cz),
                    (gx - 1.5, base + wall_h - 0.4, z0 + 0.4)], 0.16, "MachineDark", 8)
        m.box((gx + 0.95, base + 1.55, cz), (0.9, 0.22, 0.9), "MachineMid")

    # 燃油日用柜
    m.box((cx, base + 0.75, z1 - 1.0), (1.8, 1.5, 0.9), "MachineMid")
    m.box((cx, base + 0.95, z1 - 1.47), (1.5, 0.9, 0.05), "PanelFace")

    # 主配电盘（沿一舷）
    for k in range(4):
        px = cx - 3.0 + k * 2.0
        m.box((px, base + 1.15, z1 - 0.55), (1.85, 2.3, 0.55), "MachineGrey")
        m.box((px, base + 1.25, z1 - 0.84), (1.6, 1.7, 0.05), "PanelFace")
        for a in range(3):
            for b in range(2):
                m.box((px - 0.5 + a * 0.5, base + 1.7 + b * 0.45, z1 - 0.87),
                      (0.18, 0.18, 0.04), "Indicator")

    # 母线桥架 + 电缆桥架
    m.box((cx, base + 2.55, z1 - 0.5), ((x1 - x0) * 0.9, 0.34, 0.34), "MachineMid")
    for sgn in (-1, 1):
        m.box((cx, base + wall_h - 0.5, cz + sgn * 3.0),
              ((x1 - x0) * 0.92, 0.12, 0.42), "MachineDark")

    # 应急蓄电池组
    for k in range(4):
        m.box((x0 + 1.0, base + 0.6, z0 + 0.9 + k * 0.62), (1.0, 1.2, 0.55), "MachineMid")

    light_strip(m, np_arange(x0 + 1.2, x1 - 1.0, 3.0),
                base + wall_h - 0.15, cz - 1.7, cz + 1.7)
    return m


# ---------------------------------------------------------------------
# 舱室 3 · 灶炉间
# ---------------------------------------------------------------------
def build_galley(bmin, bmax):
    m = Mesh()
    wall_h = room_shell(m, bmin, bmax, 0.72)
    x0, y0, z0 = bmin
    x1, y1, z1 = bmax
    cx, cz = (x0 + x1) / 2, 0.0
    base = y0
    D = 0.65          # 操作台进深
    CT = D + 0.06     # 台面（比柜体略出挑）
    CAB = 0.88        # 底柜高

    for sgn in (-1, 1):
        zz = cz + sgn * (abs(z1) - CT / 2)
        m.box((cx, base + CAB / 2, zz), ((x1 - x0) * 0.94, CAB, D), "MachineGrey")
        m.box((cx, base + CAB + 0.03, zz), ((x1 - x0) * 0.96, 0.06, CT), "ShaftPolish")
        for k in range(4):
            m.box((cx + (k - 1.5) * ((x1 - x0) * 0.23), base + CAB / 2,
                   zz - sgn * (D / 2 + 0.01)), (0.03, CAB * 0.8, 0.02), "MachineDark")

    # 炉灶 + 排烟罩
    sx = cx - 1.6
    zz = cz - (abs(z1) - CT / 2)
    m.box((sx, base + CAB + 0.28, zz), (1.0, 0.5, D - 0.04), "ShaftPolish")
    m.box((sx, base + CAB + 0.56, zz), (0.95, 0.05, D - 0.08), "MachineDark")
    for a in (-0.24, 0.24):
        for b in (-0.16, 0.16):
            m.cyl((sx + a, base + CAB + 0.58, zz + b),
                  (sx + a, base + CAB + 0.64, zz + b), 0.10, "MachineDark", 10)
    m.box((sx, base + CAB + 0.95, zz), (1.15, 0.5, D + 0.06), "ShaftPolish")
    m.box((sx, base + CAB + 1.55, zz), (0.5, 0.8, 0.5), "ShaftPolish")
    m.pipe_run([(sx, base + CAB + 1.9, zz), (sx, base + wall_h - 0.2, zz),
                (x0 + 0.5, base + wall_h - 0.2, zz)], 0.28, "ShaftPolish", 12)

    # 水槽 + 龙头
    kx = cx + 1.7
    zz = cz + (abs(z1) - CT / 2)
    m.box((kx, base + CAB - 0.10, zz), (0.7, 0.22, 0.52), "ShaftPolish")
    m.pipe_run([(kx, base + CAB, zz - 0.3), (kx, base + CAB + 0.5, zz - 0.3),
                (kx, base + CAB + 0.5, zz - 0.05)], 0.035, "Brass", 8)

    # 吊柜
    for sgn in (-1, 1):
        zz = cz + sgn * (abs(z1) - CT / 2 - 0.05)
        m.box((cx, base + wall_h - 0.55, zz), ((x1 - x0) * 0.9, 0.7, 0.5), "Insulation")
        m.box((cx, base + wall_h - 0.92, zz), ((x1 - x0) * 0.9, 0.05, 0.54), "ShaftPolish")
        for k in range(4):
            m.box((cx + (k - 1.5) * ((x1 - x0) * 0.22), base + wall_h - 0.55,
                   zz - sgn * 0.26), (0.03, 0.6, 0.02), "MachineDark")

    # 冷藏柜 + 备品台
    fz = cz + (abs(z1) - CT / 2)
    m.box((x1 - 1.1, base + 0.85, fz), (0.75, 1.7, D - 0.02), "ShaftPolish")
    m.box((x1 - 1.1, base + 0.85, fz - (D / 2 + 0.02)), (0.62, 1.5, 0.05), "MachineDark")
    m.box((cx + 0.2, base + 0.42, cz), (2.0, 0.05, 0.9), "ShaftPolish")
    for dx in (-0.9, 0.9):
        for dz in (-0.35, 0.35):
            m.box((cx + 0.2 + dx, base + 0.2, cz + dz), (0.06, 0.4, 0.06), "ShaftPolish")

    # 消防器材（灶炉间是重点防火舱）
    m.box((x0 + 0.8, base + 1.3, z0 + 0.4), (0.4, 0.45, 0.2), "SafetyRed")
    m.cyl((x0 + 0.8, base + 0.55, z0 + 0.4), (x0 + 0.8, base + 1.05, z0 + 0.4),
          0.11, "SafetyRed", 10)
    m.box((cx + 1.2, base + 1.6, z0 + 0.35), (0.7, 0.35, 0.06), "SafetyYellow")

    light_strip(m, np_arange(x0 + 1.2, x1 - 0.8, 2.2), base + wall_h - 0.1, cz - 0.9, cz + 0.9)
    return m


# ---------------------------------------------------------------------
# 舱室 4 · 士兵住舱
# ---------------------------------------------------------------------
def build_berthing(bmin, bmax):
    m = Mesh()
    wall_h = room_shell(m, bmin, bmax, 0.46)
    x0, y0, z0 = bmin
    x1, y1, z1 = bmax
    cx, cz = (x0 + x1) / 2, 0.0
    base = y0
    BUNK_D = 0.80                          # 铺位纵深
    BW = (x1 - x0) * 0.205                  # 单张铺位长度

    for sgn in (-1, 1):
        bz = cz + sgn * (abs(z1) - BUNK_D / 2 - 0.10)
        for tier in range(2):
            yb = base + 0.42 + tier * 1.05
            for k in range(3):
                px = cx + (k - 1) * (BW + 0.12)
                m.box((px, yb, bz), (BW, 0.10, BUNK_D), "Insulation")        # 铺板
                m.box((px, yb + 0.10, bz), (BW - 0.08, 0.12, BUNK_D - 0.12), "Mattress")
                m.box((px, yb + 0.17, bz + sgn * (BUNK_D / 2 - 0.08)),
                      (BW - 0.12, 0.10, 0.24), "Insulation")               # 枕头
                m.box((px, yb + 0.20, bz - sgn * (BUNK_D / 2 - 0.05)),
                      (BW - 0.12, 0.16, 0.03), "Mattress")                 # 床帘
                m.box((px + BW / 2 + 0.14, base + 0.5, bz),
                      (0.26, 1.0, BUNK_D), "MachineMid")                   # 端头储物柜
                m.box((px + BW / 2 + 0.14, base + 0.62, bz + sgn * (BUNK_D / 2 + 0.04)),
                      (0.06, 0.5, 0.06), "ShaftPolish")             # 柜门把手
        # 上下铺之间的爬梯
        lx = cx + (3 * (BW + 0.12)) / 2 + 0.22
        lz = bz - sgn * 0.22
        for off in (-0.16, 0.16):
            m.cyl((lx + off, base + 0.4, lz), (lx + off, base + 2.0, lz), 0.035, "ShaftPolish", 6)
        for r in range(5):
            m.cyl((lx - 0.16, base + 0.55 + r * 0.32, lz),
                  (lx + 0.16, base + 0.55 + r * 0.32, lz), 0.03, "ShaftPolish", 6)

    # 中央餐桌 + 坐凳
    m.box((cx, base + 0.76, cz), (2.4, 0.06, 0.85), "ShaftPolish")
    for dx in (-1.0, 1.0):
        m.box((cx + dx, base + 0.38, cz), (0.1, 0.74, 0.7), "MachineMid")
    for sgn in (-1, 1):
        m.box((cx, base + 0.44, cz + sgn * 0.72), (2.2, 0.05, 0.34), "ShaftPolish")
        for dx in (-0.9, 0.9):
            m.box((cx + dx, base + 0.21, cz + sgn * 0.72), (0.07, 0.42, 0.3), "MachineMid")

    light_strip(m, np_arange(x0 + 1.4, x1 - 0.8, 2.4),
                base + wall_h - 0.12, cz - 0.8, cz + 0.8)

    # 舷侧通风口
    for sgn in (-1, 1):
        for k in range(3):
            m.box((cx + (k - 1) * 3.0, base + wall_h - 0.35,
                   cz + sgn * (abs(z1) - 0.28)), (1.0, 0.5, 0.1), "MachineMid")
    return m


# ---------------------------------------------------------------------
# 舱室 5 · 机库
# ---------------------------------------------------------------------
def build_hangar(bmin, bmax):
    m = Mesh()
    wall_h = room_shell(m, bmin, bmax, 0.52)
    x0, y0, z0 = bmin
    x1, y1, z1 = bmax
    cx, cz = (x0 + x1) / 2, 0.0
    base = y0
    L, W = x1 - x0, z1 - z0

    # 中心着陆圆标线
    m.cyl((cx, base + 0.07, cz), (cx, base + 0.07, cz), 3.2, "DeckMarking", 28)
    m.cyl((cx, base + 0.075, cz), (cx, base + 0.075, cz), 2.9, "DeckPlate", 28)
    floor_band(m, bmin, bmax, "DeckMarking", "x", cz, 0.3)

    # 两侧作业平台
    for sgn in (-1, 1):
        m.box((cx, base + 1.1, cz + sgn * (abs(z1) - 0.8)), (L * 0.8, 0.10, 1.5), "MachineMid")
        m.cyl((cx - L * 0.4, base + 1.7, cz + sgn * (abs(z1) - 0.05)),
              (cx + L * 0.4, base + 1.7, cz + sgn * (abs(z1) - 0.05)), 0.05, "ShaftPolish")
        for dx in np_arange(cx - L * 0.4, cx + L * 0.4, 1.6):
            m.cyl((dx, base + 1.15, cz + sgn * (abs(z1) - 0.05)),
                  (dx, base + 1.7, cz + sgn * (abs(z1) - 0.05)), 0.04, "ShaftPolish")

    # 顶棚吊车轨道 + 吊车（轨道用深色金属，避免大面积黄抢掉视觉焦点）
    for sgn in (-1, 1):
        m.box((cx, base + wall_h - 0.45, cz + sgn * 2.6), (L * 0.92, 0.30, 0.34), "MachineDark")
        m.box((cx, base + wall_h - 0.30, cz + sgn * 2.6), (L * 0.92, 0.10, 0.44), "MachineMid")
    rx = cx + 1.5
    m.box((rx, base + wall_h - 0.62, cz), (1.6, 0.55, W * 0.72), "SafetyYellow")
    m.box((rx, base + wall_h - 1.02, cz), (0.8, 0.30, 0.9), "MachineDark")
    m.cyl((rx, base + wall_h - 1.17, cz), (rx, base + 2.6, cz), 0.035, "ShaftPolish", 6)
    m.box((rx, base + 2.45, cz), (0.5, 0.35, 0.5), "MachineDark")

    # 燃油加注站（机库最高火灾危险源）
    fx = x0 + 2.2
    m.box((fx, base + 0.7, z0 + 1.0), (1.2, 1.4, 0.8), "MachineMid")
    m.box((fx, base + 1.0, z0 + 0.58), (0.9, 0.8, 0.05), "PanelFace")
    m.cyl((fx, base + 0.8, z0 + 1.45), (fx, base + 2.6, z0 + 1.45), 0.10, "SafetyYellow", 8)
    m.cyl((fx, base + 2.6, z0 + 1.45), (fx, base + 2.6, z0 + 2.2), 0.10, "SafetyYellow", 8)
    m.box((fx, base + 0.5, z0 + 1.9), (0.7, 1.0, 0.5), "SafetyRed")
    m.box((fx, base + 1.2, z1 - 0.6), (0.8, 0.5, 0.08), "SafetyRed")

    # 工具柜与工作台
    for k in range(3):
        m.box((x1 - 1.6, base + 0.9, z1 - 1.0 - k * 1.4), (1.0, 1.8, 0.6), "MachineGrey")
        m.box((x1 - 1.6, base + 1.0, z1 - 1.32 - k * 1.4), (0.85, 1.4, 0.04), "ShaftPolish")
    m.box((x0 + 3.0, base + 0.9, z1 - 0.9), (2.4, 0.08, 0.8), "ShaftPolish")
    for dx in (-1.0, 1.0):
        m.box((x0 + 3.0 + dx, base + 0.45, z1 - 0.9), (0.08, 0.85, 0.7), "MachineMid")

    # 舱口门框（艉向）+ 门楣警示带
    m.box((x0 + 0.25, base + wall_h * 0.5, cz), (0.35, wall_h, W * 0.82), "MachineDark")
    m.box((cx, base + wall_h - 0.15, cz), (L * 0.9, 0.2, 0.2), "SafetyYellow")

    # 维护登车梯
    for k in range(6):
        m.box((cx - L * 0.35, base + 0.25 + k * 0.3, z0 + 0.7 + k * 0.16),
              (0.9, 0.06, 0.4), "MachineMid")

    light_strip(m, np_arange(x0 + 2.0, x1 - 1.0, 4.0),
                base + wall_h - 0.2, cz - 3.0, cz + 3.0)
    return m


BUILDERS = {
    "main-engine": build_main_engine,
    "power-room": build_power_room,
    "galley": build_galley,
    "berthing": build_berthing,
    "hangar": build_hangar,
}


# ---------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------
def reset_scene():
    MAT.clear()
    bpy.ops.wm.read_factory_settings(use_empty=True)


def stats(root):
    me = root.data
    me.calc_loop_triangles()
    return len(me.loop_triangles), len(me.vertices), [m.name for m in me.materials]


def bbox_world(root):
    mn = [1e9] * 3
    mx = [-1e9] * 3
    for v in root.data.vertices:
        w = root.matrix_world @ v.co
        for i in range(3):
            mn[i] = min(mn[i], w[i])
            mx[i] = max(mx[i], w[i])
    return [mn[0], mn[2], -mx[1]], [mx[0], mx[2], -mn[1]]   # Blender -> ship


def export_glb(path):
    bpy.ops.object.select_all(action="SELECT")
    kw = dict(filepath=path, export_format="GLB", export_apply=True,
              use_selection=False, export_yup=True)
    try:
        bpy.ops.export_scene.gltf(
            export_draco_mesh_compression_enable=True,
            export_draco_mesh_compression_level=6, **kw)
        draco = True
    except Exception as e:
        print(f"!!! Draco 不可用，退回未压缩: {e}")
        bpy.ops.export_scene.gltf(**kw)
        draco = False

    size = os.path.getsize(path) / 1024 / 1024
    # 空场景保护：exporter 会写入 bpy.data.scenes 里的每个场景
    with open(path, "rb") as f:
        blob = f.read()
    if struct.unpack_from("<I", blob, 0)[0] != 0x46546C67:
        raise SystemExit(f"{path} 不是合法 GLB")
    js, off, end = None, 12, struct.unpack_from("<I", blob, 8)[0]
    while off < end:
        ln, kind = struct.unpack_from("<II", blob, off)
        if kind == 0x4E4F534A:
            js = json.loads(blob[off + 8:off + 8 + ln].decode("utf-8"))
            break
        off += 8 + ln
    if not js or not js.get("scenes") or not js.get("nodes"):
        raise SystemExit(f"{path} 导出为空")
    return size, draco


def main():
    global OUT_DIR
    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.dirname(here)
    OUT_DIR = os.path.join(proj, "public", "models", "compartments")
    os.makedirs(OUT_DIR, exist_ok=True)

    report = []
    for spec in COMP:
        reset_scene()
        build_materials()
        coll = bpy.context.scene.collection
        bmin, bmax = spec["bounds"]
        m = BUILDERS[spec["key"]](bmin, bmax)

        root, _ = m.emit(spec["en"], coll)
        if root is None:
            print(f"!!! {spec['key']} 未生成几何")
            continue

        tris, verts, mats = stats(root)
        lo, hi = bbox_world(root)
        fits = all(lo[i] >= bmin[i] - 1e-3 and hi[i] <= bmax[i] + 1e-3 for i in range(3))
        print(f"[{spec['key']}] {spec['label']}: {tris} tris, {verts} verts, {len(mats)} mats")
        print(f"    bbox {tuple(round(v, 2) for v in lo)} .. {tuple(round(v, 2) for v in hi)}")
        print(f"    declared {bmin} .. {bmax}   fits={fits}")
        if not fits:
            for material, axis, amt in m.overshoot(bmin, bmax):
                print(f"       越界 {material:<16} {axis} 超出 {amt} m")
            raise SystemExit(f"{spec['key']} 超出声明包围盒")

        path = os.path.join(OUT_DIR, f"{spec['key']}.glb")
        size, draco = export_glb(path)
        print(f"    -> {spec['key']}.glb  {size * 1024:.1f} KB  draco={draco}")
        print(f"    materials: {', '.join(mats)}")
        report.append(dict(id=spec["id"], key=spec["key"], label=spec["label"],
                           tris=tris, verts=verts, materials=mats,
                           kb=round(size * 1024, 1), draco=draco,
                           bbox_min=[round(v, 2) for v in lo],
                           bbox_max=[round(v, 2) for v in hi]))

    total = sum(r["kb"] for r in report)
    print(f"\n=== 合计 {len(report)} 个舱室 / {total:.1f} KB ===")
    with open(os.path.join(OUT_DIR, "compartments.meta.json"), "w", encoding="utf-8") as f:
        json.dump(dict(generated_by="tools/build_compartments.py",
                       compartments=report), f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
