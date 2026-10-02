"""
船模离线预览渲染 —— headless 渲染多视角 PNG，用于目视校验建模质量。
用法:
    blender --background --factory-startup --python tools/preview_ship.py -- <glb> <outdir> [samples]
"""

import math
import os
import sys

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
_HERE = os.path.dirname(os.path.abspath(__file__))          # .../tools
_PROJ = os.path.dirname(_HERE)                               # 项目根
GLB = args[0] if args else os.path.join(_PROJ, "public", "models", "ship.glb")
OUT = os.path.abspath(args[1]) if len(args) > 1 else os.path.join(_HERE, "preview")
SAMPLES = int(args[2]) if len(args) > 2 else 48
RES = (1280, 720)

if not os.path.isabs(OUT):
    OUT = os.path.join(_PROJ, OUT)
os.makedirs(OUT, exist_ok=True)
print(">>> out dir:", OUT)
bpy.ops.wm.read_factory_settings(use_empty=True)

bpy.ops.import_scene.gltf(filepath=GLB)
print(">>> imported objects:",
      [o.name for o in bpy.context.scene.objects if o.type == "MESH"])

# 隐藏拾取体
for o in bpy.context.scene.objects:
    if o.name.startswith("COMPARTMENT_"):
        o.hide_render = True

# 世界：Nishita 物理天空
# 世界：纯色环境光。Nishita 物理天空的辐射量级很高，直接用会把模型打爆，
# 而这里只需要一个可控的、可判断明暗关系的诊断光照。
world = bpy.data.worlds.new("W")
bpy.context.scene.world = world
world.use_nodes = True
nt = world.node_tree
for n in list(nt.nodes):
    if n.type != "OUTPUT_WORLD":
        nt.nodes.remove(n)
out = next(n for n in nt.nodes if n.type == "OUTPUT_WORLD")
bg = nt.nodes.new("ShaderNodeBackground")
bg.inputs["Color"].default_value = (0.42, 0.50, 0.60, 1.0)
bg.inputs["Strength"].default_value = 0.55
nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


# 太阳光
sun_data = bpy.data.lights.new("Sun", type="SUN")
sun_data.energy = 3.0
sun_data.angle = math.radians(1.5)
sun = link(bpy.data.objects.new("Sun", sun_data))
sun.rotation_euler = (math.radians(52), 0, math.radians(38))

# 补光
fill_data = bpy.data.lights.new("Fill", type="SUN")
fill_data.energy = 0.45
fill = link(bpy.data.objects.new("Fill", fill_data))
fill.rotation_euler = (math.radians(68), 0, math.radians(-140))

# 相机
cam_data = bpy.data.cameras.new("Cam")
cam_data.lens = 58
cam = link(bpy.data.objects.new("Cam", cam_data))
bpy.context.scene.camera = cam

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = RES
scene.render.film_transparent = False
try:
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = 0.0
except Exception as e:
    print(">>> view_transform fallback:", e)

# 视图：(名称, 方位角deg, 仰角deg, 距离m, 视点偏移)
# 方位角 0 = 正艏(+X)，90 = 正右舷(+Z)；仰角 0 = 水平
VIEWS = [
    ("01-bow-quarter-stbd",  38,  20,  165, (0, 5, 0)),
    ("02-beam-stbd",         90,   6,  175, (0, 3, 0)),
    ("03-bow",                4,  12,  120, (14, 5, 0)),
    ("04-stern-quarter",    215,  26,  160, (-18, 5, 0)),
    ("05-top",                90,  89,  175, (0, 0, 0)),
    ("06-bridge-closeup",     55,  16,   62, (20, 12, 0)),
    ("07-superstructure",     70,  30,   95, (14, 11, 0)),
    ("08-hangar-flightdeck", 200,  22,   95, (-24, 6, 0)),
]

# 取景：根据视点偏移与距离自动计算 up / 目标，避免 up 向量与视线共线
for name, az, el, dist, aim in VIEWS:
    a, e = math.radians(az), math.radians(el)
    aim_v = Vector(aim)
    offset = Vector((
        dist * math.cos(e) * math.cos(a),
        dist * math.sin(e),
        dist * math.cos(e) * math.sin(a),
    ))
    cam.location = aim_v + offset
    d = (aim_v - cam.location).normalized()
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    p = os.path.join(OUT, f"{name}.png")
    scene.render.filepath = p
    bpy.ops.render.render(write_still=True)
    print(">>> rendered", p)

print(">>> done")
