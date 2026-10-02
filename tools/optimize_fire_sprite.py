"""
火焰精灵图转码 —— 把 4K 6x6 PNG 压到 Web 可接受体积。
加性混合下黑色即透明，因此可以直接输出无 alpha 的 JPEG。
运行:
    blender --background --factory-startup --python tools/optimize_fire_sprite.py
"""
import os
import sys

import bpy

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(_HERE)
PUB = os.path.join(_PROJ, "public")
SRC = os.path.join(PUB, "CampFire_l_nosmoke_front_Loop_01_4K_6x6.png")
DST = os.path.join(PUB, "fire-sprite.jpg")
MAX_DIM = 2048
QUALITY = 88

if not os.path.exists(SRC):
    print(">>> 源文件不存在:", SRC)
    sys.exit(1)

bpy.ops.wm.read_factory_settings(use_empty=True)

img = bpy.data.images.load(SRC)
w, h = img.size
print(f">>> 源尺寸 {w}x{h}  {os.path.getsize(SRC)/1024/1024:.2f} MB")

scale = MAX_DIM / max(w, h)
if scale < 1.0:
    img.scale(int(w * scale), int(h * scale))
print(f">>> 缩放后 {img.size[0]}x{img.size[1]}  (每帧 {img.size[0]//6}x{img.size[1]//6} px)")

# 帧数 6x6 = 36，总尺寸必须是 6 的整数倍，否则 UV 会错位
img.scale((img.size[0] // 6) * 6, (img.size[1] // 6) * 6)

# Blender 4.x 的 JPEG 质量设置在 render.image_settings 上，不在 Image 数据块上
scene = bpy.context.scene
scene.render.image_settings.file_format = "JPEG"
scene.render.image_settings.quality = QUALITY
scene.render.image_settings.color_mode = "RGB"   # 加性混合不需要 alpha

img.filepath_raw = DST
img.save_render(DST, scene=scene)

size_kb = os.path.getsize(DST) / 1024
saved_w, saved_h = img.size
print(f">>> 输出 {DST}")
print(f">>> 尺寸 {saved_w}x{saved_h}  {size_kb:.0f} KB")
print(f">>> 相比源文件节省 {(1 - os.path.getsize(DST)/os.path.getsize(SRC))*100:.1f}%")
