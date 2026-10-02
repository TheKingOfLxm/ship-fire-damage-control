"""Inspect GLB assets: report node/mesh counts, bounding boxes, materials, file size."""
import bpy
import json
import os
import sys

MODELS_DIR = bpy.path.abspath("//../public/models") if bpy.data.filepath else os.path.join(
    os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.getcwd(), "public", "models"
)


def inspect(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    size_mb = os.path.getsize(path) / 1024 / 1024
    print("=" * 70)
    print(f"FILE: {os.path.basename(path)}  ({size_mb:.2f} MB)")

    before = set(bpy.data.objects.keys())
    bpy.ops.import_scene.gltf(filepath=path)
    objs = [o for o in bpy.data.objects if o.type in {"MESH", "EMPTY"}]

    meshes = [o for o in objs if o.type == "MESH"]
    print(f"  objects: {len(objs)}   meshes: {len(meshes)}")

    tris = 0
    verts = 0
    for m in meshes:
        me = m.data
        verts += len(me.vertices)
        tris += len(me.loop_triangles) if me.loop_triangles else sum(
            max(0, len(p.vertices) - 2) for p in me.polygons
        )
    print(f"  vertices: {verts:,}   ~triangles: {tris:,}")

    mats = set()
    for m in meshes:
        for slot in m.data.materials:
            if slot:
                mats.add(slot.name)
    print(f"  materials ({len(mats)}): {', '.join(sorted(mats)[:12])}")

    # world bounding box
    mn = [1e9] * 3
    mx = [-1e9] * 3
    for m in meshes:
        for corner in m.bound_box:
            w = m.matrix_world @ __import__("mathutils").Vector(corner)
            for i in range(3):
                mn[i] = min(mn[i], w[i])
                mx[i] = max(mx[i], w[i])
    print(f"  bbox min: ({mn[0]:.2f}, {mn[1]:.2f}, {mn[2]:.2f})")
    print(f"  bbox max: ({mx[0]:.2f}, {mx[1]:.2f}, {mx[2]:.2f})")
    print(f"  bbox size: ({mx[0]-mn[0]:.2f}, {mx[1]-mn[1]:.2f}, {mx[2]-mn[2]:.2f})")

    top = sorted(meshes, key=lambda o: -len(o.data.vertices))[:8]
    print("  largest meshes:")
    for o in top:
        print(f"    - {o.name}  v={len(o.data.vertices):,}  m={len(o.data.materials)}")


args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
base = os.path.join(os.getcwd(), "public", "models")
targets = args or [
    os.path.join(base, "trawler.glb"),
    os.path.join(base, "主机舱.glb"),
    os.path.join(base, "电站间.glb"),
    os.path.join(base, "机库.glb"),
    os.path.join(base, "士兵住舱.glb"),
]
for t in targets:
    if os.path.exists(t):
        try:
            inspect(t)
        except Exception as e:
            print(f"FAILED {t}: {e}")
    else:
        print(f"MISSING: {t}")
