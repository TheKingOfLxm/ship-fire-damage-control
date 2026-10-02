"""Scratch verifier: audit the exported compartment GLBs.

Reads the GLB container directly (JSON + BIN chunks) so the Draco extension
and the node hierarchy are checked from the bytes, then re-imports each file
in Blender to measure the world-space bounding box and confirm it sits inside
the compartment bounds declared in src/config/shipLayout.js.

    blender --background --factory-startup --python tools/compart-verify.py
"""
import json
import os
import struct
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "public", "models", "compartments")

BOUNDS = {
    "main-engine.glb": ((-42.0, 0.2, -5.6), (-12.0, 6.6, 5.6)),
    "power-room.glb": ((-10.5, 1.2, -5.4), (0.5, 6.2, 5.4)),
    "galley.glb": ((2.0, 4.4, -5.2), (9.5, 7.4, 5.2)),
    "berthing.glb": ((12.0, 4.4, -5.2), (30.0, 8.0, 5.2)),
    "hangar.glb": ((8.0, -1.6, -5.8), (32.0, 6.4, 5.8)),
}
ROOT_NAME = {
    "main-engine.glb": "MainEngineRoom", "power-room.glb": "PowerRoom",
    "galley.glb": "Galley", "berthing.glb": "Berthing", "hangar.glb": "Hangar",
}


def parse_glb(path):
    with open(path, "rb") as fh:
        blob = fh.read()
    magic, ver, total = struct.unpack_from("<III", blob, 0)
    assert magic == 0x46546C67, "not a GLB"
    off, js, bin_len = 12, None, None
    while off + 8 <= min(total, len(blob)):
        length, kind = struct.unpack_from("<II", blob, off)
        body = blob[off + 8: off + 8 + length]
        if kind == 0x4E4F534A and js is None:
            js = json.loads(body.decode("utf-8"))
        elif kind == 0x004E4942:
            bin_len = len(body)
        off += 8 + length
    if js is None:
        raise ValueError("no JSON chunk")
    return js, bin_len, ver


def audit_container(path):
    js, bin_len, ver = parse_glb(path)
    exts = js.get("extensionsUsed", [])
    nodes = js.get("nodes", [])
    meshes = js.get("meshes", [])
    mats = js.get("materials", [])
    prims = [p for m in meshes for p in m["primitives"]]
    draco = sum(1 for p in prims if "KHR_draco_mesh_compression" in p.get("extensions", {}))
    tri = 0
    for p in prims:
        mode = p.get("mode", 4)
        idx = p.get("indices")
        # With Draco the index accessors are placeholders; their counts do not
        # reflect the decoded mesh, so the triangle total is taken from the
        # Blender re-import instead (reported as `scene tris`).
        if "KHR_draco_mesh_compression" in p.get("extensions", {}):
            continue
        if idx is not None:
            acc = js["accessors"][idx]
            n = acc["count"] // ({"UNSIGNED_INT": 4, "UNSIGNED_SHORT": 2,
                                  "UNSIGNED_BYTE": 1}.get(acc["componentType"], 4))
        else:
            n = js["accessors"][p["attributes"]["POSITION"]]["count"]
        tri += n // 3 if mode == 4 else 0
    roots = [n.get("name") for i, n in enumerate(nodes)
             if not any(i in (c or []) for c in (x.get("children") for x in nodes))]
    children = [len(n.get("children", [])) for n in nodes]
    return dict(version=ver, nodes=len(nodes), meshes=len(meshes),
                materials=len(mats), prims=len(prims), draco_prims=draco,
                triangles=tri, exts=exts, roots=roots, children=children,
                bin_bytes=bin_len)


def audit_scene(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    objs = list(bpy.data.objects)
    meshes = [o for o in objs if o.type == "MESH"]
    verts = tris = 0
    for m in meshes:
        verts += len(m.data.vertices)
        tris += sum(max(0, len(p.vertices) - 2) for p in m.data.polygons)
    allv = []
    for m in meshes:
        allv.extend(m.matrix_world @ v.co for v in m.data.vertices)
    if not allv:
        return dict(objects=0, verts=0, tris=0, mats=0, mn=None, mx=None)
    mn = [min(v[i] for v in allv) for i in range(3)]
    mx = [max(v[i] for v in allv) for i in range(3)]
    return dict(objects=len(objs), verts=verts, tris=tris,
                mats=len({s.name for m in meshes for s in m.data.materials}),
                mn=[round(v, 3) for v in mn], mx=[round(v, 3) for v in mx],
                hierarchy=[(o.name, o.parent.name if o.parent else None)
                           for o in sorted(objs, key=lambda x: x.name)])


rows = []
for name in sorted(BOUNDS):
    path = os.path.join(OUT_DIR, name)
    if not os.path.exists(path):
        print(f"MISSING {name}")
        continue
    c = audit_container(path)
    s = audit_scene(path)
    bmin, bmax = BOUNDS[name]
    inside = (s["mn"] is not None
              and all(s["mn"][a] >= bmin[a] - 1e-3 for a in range(3))
              and all(s["mx"][a] <= bmax[a] + 1e-3 for a in range(3)))
    rows.append(dict(name=name, mb=os.path.getsize(path) / 1048576, cont=c,
                     scene=s, inside=inside, bmin=bmin, bmax=bmax,
                     want_root=ROOT_NAME[name]))
    print("=" * 78)
    print(f"{name}   {rows[-1]['mb']:.4f} MB   glTF v{c['version']}   "
          f"bin {c['bin_bytes']:,} B")
    print(f"  container : nodes={c['nodes']} meshes={c['meshes']} "
          f"materials={c['materials']} primitives={c['prims']} "
          f"triangles(idx-accessor)={c['triangles']}"
          f"{' [draco: placeholders, see scene tris]' if c['draco_prims'] else ''}")
    print(f"  extensions: {c['exts']}  (draco primitives: {c['draco_prims']}"
          f"/{c['prims']})")
    print(f"  root nodes: {c['roots']}   children per node: {c['children']}")
    print(f"  scene     : objects={s['objects']} verts={s['verts']} "
          f"tris={s['tris']} materials={s['mats']}")
    print(f"  bbox      : {s['mn']} .. {s['mx']}")
    print(f"  declared  : {list(bmin)} .. {list(bmax)}   FITS={inside}")
    print(f"  hierarchy : {s['hierarchy']}")
    print()

dest = os.path.join(ROOT, "tools", "compart-verify.json")
with open(dest, "w", encoding="utf-8") as fh:
    json.dump(rows, fh, ensure_ascii=False, indent=1)
print("WROTE", dest)
tot = sum(r["mb"] for r in rows)
print(f"TOTAL {tot:.4f} MB over {len(rows)} files; all inside bounds: "
      f"{all(r['inside'] for r in rows)}; max objects: "
      f"{max(r['scene']['objects'] for r in rows)}")
