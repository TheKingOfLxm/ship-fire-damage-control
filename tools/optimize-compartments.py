"""
tools/optimize-compartments.py
==============================
Produce web-optimized per-compartment interior GLBs for the ship
fire-damage-control app.

For the four real PyroSim/FDS exports it
  * imports, filters the FDS simulation scaffolding,
  * computes a ROBUST bounding box (see `robust_bounds()`),
  * uniformly fits that box inside the compartment bounds declared in
    src/config/shipLayout.js, seating it on the compartment floor,
  * flattens ~1900 fragments into one mesh per palette material,
  * re-authors materials as Principled BSDF, and
  * exports GLB with Draco mesh compression.

For compartment 3 (灶炉间 / galley) no source exists, so it is built
procedurally from primitives (`build_galley()`).

Bounds are duplicated from src/config/shipLayout.js COMPARTMENTS[] on purpose:
this script is the Blender-side half of that single source of truth.

Usage
-----
    blender --background --factory-startup --python tools/optimize-compartments.py
    blender --background --factory-startup --python tools/optimize-compartments.py -- --dry-run
    blender --background --factory-startup --python tools/optimize-compartments.py -- --only galley
"""
import math
import os
import sys

import bpy
from mathutils import Vector

# --------------------------------------------------------------------------
# Compartment contract (mirror of src/config/shipLayout.js COMPARTMENTS[])
# --------------------------------------------------------------------------
COMP = [
    dict(id=1, key="main-engine", en="MainEngineRoom",
         src="主机舱.glb", out="main-engine.glb",
         bounds=dict(min=(-48.0, -3.6, -5.2), max=(-30.0, 4.0, 5.2))),
    dict(id=2, key="power-room", en="PowerRoom",
         src="电站间.glb", out="power-room.glb",
         bounds=dict(min=(-4.0, 0.4, -5.0), max=(6.0, 5.0, 5.0))),
    dict(id=3, key="galley", en="Galley",
         src=None, out="galley.glb",
         bounds=dict(min=(22.0, 4.6, -4.6), max=(29.0, 7.6, 4.6))),
    dict(id=4, key="berthing", en="Berthing",
         src="士兵住舱.glb", out="berthing.glb",
         bounds=dict(min=(8.0, 0.8, -5.0), max=(20.0, 5.4, 5.0))),
    dict(id=5, key="hangar", en="Hangar",
         src="机库.glb", out="hangar.glb",
         bounds=dict(min=(-28.0, 0.5, -5.6), max=(-6.0, 6.2, 5.6))),
]

MARGIN = 0.05          # 5% inset from the declared bounds
MAX_OBJECTS = 40       # draw-call budget per compartment

# 灶炉间整体平移量：手写几何的原位 (x 2.25..9.25, y 4.4..7.25) → 现声明包围盒
# (x 22..29, y 4.6..7.6)
GALLEY_SHIFT = (19.75, 0.20, 0.0)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "public", "models")
OUT_DIR = os.path.join(SRC_DIR, "compartments")

DRY_RUN = False
ONLY = None

# --------------------------------------------------------------------------
# Robust-bounds filter tuning
#
# The FDS exports are not compartment models: they are one or two simulation
# domains packed with mesh scaffolding (domain box, mid-plane sheets, mid-plane
# floor slabs, stray debris) plus the actual equipment.  Four passes strip that
# back to a single occupied volume.
# --------------------------------------------------------------------------
F1_SHELL_SPAN = 0.40   # on a domain plane + spans >=40% of domain -> FDS shell
F1_BIG_SPAN = 0.85     # spans >=85% of domain on 2 axes -> FDS shell
F2_SHEET_AREA = 400.0  # a coplanar run of faces over this total area = mesh sheet
F2_SHEET_MIN = 4       # ... only when at least this many faces share the plane
F2_FACE_AREA = 50.0    # a single face this big is scaffolding, not equipment
F3_CELL = 0.75         # clustering cell size (m)
F3_SMALL_FRAC = 0.25   # a cluster under this share of the biggest = straggler
F3_NEAR_M = 2.5        # ... unless it sits within this distance of a kept one
F4_BIN = 0.5           # storey-split histogram bin (m)
F4_QUANTILE = 0.35     # split at first empty band above this mass quantile
F4_MIN_MASS = 0.40     # lower band must hold >= this share of the kept faces
F3_LOG_MAX = 12
AREA_EPS = 1e-6

# --------------------------------------------------------------------------
# Ship material palette: (name, linear rgb, roughness, metallic)
# One entry per material group -> one joined mesh each.
# --------------------------------------------------------------------------
PALETTE = [
    ("PaintedSteelGrey",  (0.240, 0.262, 0.278), 0.55, 0.0),
    ("PaintedSteelDark",  (0.052, 0.058, 0.064), 0.62, 0.0),
    ("MachinedSteel",     (0.400, 0.412, 0.430), 0.30, 0.95),
    ("BrassFitting",      (0.520, 0.270, 0.090), 0.33, 1.0),
    ("DeckTerrazzo",      (0.130, 0.140, 0.150), 0.86, 0.0),
    ("InsulationWhite",   (0.760, 0.770, 0.745), 0.72, 0.0),
    ("RubberBlack",       (0.014, 0.014, 0.016), 0.90, 0.0),
    ("WarningOrange",     (0.620, 0.140, 0.012), 0.48, 0.0),
    ("SafetyRed",         (0.330, 0.028, 0.020), 0.50, 0.0),
    ("GlassPane",         (0.520, 0.620, 0.660), 0.08, 0.0),
    ("WoodPanel",         (0.150, 0.075, 0.028), 0.62, 0.0),
    ("CounterLaminate",   (0.620, 0.625, 0.605), 0.30, 0.0),
    ("EnamelAppliance",   (0.780, 0.790, 0.775), 0.22, 0.0),
]
PAL_BY_NAME = {n: (n, c, r, m) for n, c, r, m in PALETTE}


# ==========================================================================
# helpers
# ==========================================================================
def report(msg=""):
    print(f"[compart] {msg}")


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def make_material(name, rgb, rough, metal):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        nt.links.new(bsdf.outputs[0], nt.nodes["Material Output"].inputs[0])
    ins = bsdf.inputs
    ins["Base Color"].default_value = (*rgb, 1.0)
    ins["Roughness"].default_value = rough
    ins["Metallic"].default_value = metal
    ins["Alpha"].default_value = 1.0
    if "Specular IOR Level" in ins:
        ins["Specular IOR Level"].default_value = 0.5
    for junk in ("Emission Strength", "Transmission Weight", "Coat Weight",
                 "Sheen Weight", "Subsurface Weight", "IOR"):
        if junk in ins:
            try:
                ins[junk].default_value = 0.0
            except (TypeError, ValueError):
                pass
    if "Emission Color" in ins:
        ins["Emission Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    mat.diffuse_color = (*rgb, 1.0)
    mat.roughness = rough
    mat.metallic = metal
    return mat


def source_material_info(mat):
    """Pull base colour / metallic / roughness out of an imported glTF material."""
    rgb, rough, metal = (0.25, 0.26, 0.27), 0.6, 0.0
    if mat is not None and mat.use_nodes:
        for n in mat.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED":
                c = n.inputs["Base Color"].default_value
                rgb = (c[0], c[1], c[2])
                rough = n.inputs["Roughness"].default_value
                metal = n.inputs["Metallic"].default_value
                break
    return rgb, rough, metal


def pick_palette(rgb, rough, metal):
    """Map a source material onto the ship palette.

    Order matters: explicit metalness wins, then hue/saturation, then value.
    """
    r, g, b = rgb
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    mx, mn = max(rgb), min(rgb)
    sat = 0.0 if mx <= 0 else (mx - mn) / mx

    if metal > 0.5:
        # machined metal, warm-tinted => brass/copper fitting
        if r > g * 1.25 and sat > 0.25:
            return "BrassFitting"
        return "MachinedSteel"
    if sat > 0.35 and lum > 0.12:
        if r > g and g > b:                      # yellow/orange family
            return "WarningOrange"
        if r > b * 1.6:                          # red family
            return "SafetyRed"
    if lum < 0.022:
        return "RubberBlack"
    if lum < 0.085:
        return "PaintedSteelDark"
    if lum > 0.55:
        return "InsulationWhite"
    if r > b * 1.35 and g > b * 1.05 and sat > 0.12:   # brown / wood
        return "WoodPanel"
    if rough > 0.80:
        return "DeckTerrazzo"
    return "PaintedSteelGrey"


# ==========================================================================
# robust bounds
# ==========================================================================
class Face:
    __slots__ = ("verts", "mat", "area", "centre", "mn", "mx", "ok",
                 "nx", "ny", "nz", "cx", "cy", "cz")

    def __init__(self, verts, mat, area, centre, mn, mx, normal):
        self.verts, self.mat = verts, mat
        self.area, self.centre, self.mn, self.mx = area, centre, mn, mx
        n = normal.normalized() if normal.length > 1e-9 else Vector((0.0, 0.0, 1.0))
        self.nx, self.ny, self.nz = n.x, n.y, n.z
        self.cx, self.cy, self.cz = centre.x, centre.y, centre.z
        self.ok = True


def collect_faces(objects):
    faces = []
    for ob in objects:
        me = ob.data
        mw = ob.matrix_world
        mats = me.materials
        for poly in me.polygons:
            pts = [mw @ me.vertices[i].co for i in poly.vertices]
            if len(pts) < 3:
                continue
            c = Vector((sum(p.x for p in pts) / len(pts),
                        sum(p.y for p in pts) / len(pts),
                        sum(p.z for p in pts) / len(pts)))
            mn = [min(p[i] for p in pts) for i in range(3)]
            mx = [max(p[i] for p in pts) for i in range(3)]
            # local area == world area: every source node is a uniform scale
            area = abs(poly.area)
            mi = poly.material_index
            mat = mats[mi] if 0 <= mi < len(mats) else None
            faces.append(Face(pts, mat, area, c, mn, mx,
                              (mw.to_3x3() @ poly.normal)))
    return faces


def f1_drop_domain_shell(faces, dmin, dsize):
    """Drop the FDS simulation box: faces sitting on a domain plane and
    spanning a large part of the domain, or spanning it on two axes."""
    dropped = 0
    for f in faces:
        on_plane = []
        for i in range(3):
            if (abs(f.mn[i] - dmin[i]) < 1e-3 or abs(f.mx[i] - dmin[i] + dsize[i]) < 1e-3):
                on_plane.append(i)
        span = [f.mx[i] - f.mn[i] for i in range(3)]
        frac = [span[i] / dsize[i] if dsize[i] > 1e-9 else 0.0 for i in range(3)]
        big = sum(1 for v in frac if v >= F1_BIG_SPAN)
        if big >= 2:
            f.ok = False
            dropped += 1
            continue
        if on_plane and max((frac[i] for i in range(3) if i not in on_plane),
                            default=0.0) >= F1_SHELL_SPAN:
            f.ok = False
            dropped += 1
    return dropped


def f2b_drop_mesh_sheets(faces):
    """Drop FDS simulation-mesh sheets.

    PyroSim exports the *whole* meshing domain, so the box interior is tiled
    with large coplanar runs of faces (mid-plane sheets, the two-storey floor
    slab).  They share a plane, come in groups of hundreds, and total far more
    area than the modelled equipment.  Real equipment never has a single plane
    carrying hundreds of square metres.
    """
    planes = {}
    for f in faces:
        if not f.ok:
            continue
        n = Vector(f.centre)
        # plane key: quantised normal + plane offset, tolerant of float noise
        key = (round(f.nx, 1), round(f.ny, 1), round(f.nz, 1),
               round(f.cx * f.nx + f.cy * f.ny + f.cz * f.nz, 1))
        e = planes.setdefault(key, [0, 0.0])
        e[0] += 1
        e[1] += f.area
    bad = {k for k, (n, area) in planes.items()
           if n >= F2_SHEET_MIN and area >= F2_SHEET_AREA}
    n = 0
    for f in faces:
        if not f.ok:
            continue
        key = (round(f.nx, 1), round(f.ny, 1), round(f.nz, 1),
               round(f.cx * f.nx + f.cy * f.ny + f.cz * f.nz, 1))
        if key in bad:
            f.ok = False
            n += 1
    return n


def f2_drop_degenerate(faces):
    n = 0
    for f in faces:
        if f.area < AREA_EPS or (f.mx[0] - f.mn[0]) < 1e-5 \
                and (f.mx[1] - f.mn[1]) < 1e-5 and (f.mx[2] - f.mn[2]) < 1e-5:
            f.ok = False
            n += 1
    return n


def cluster_faces(faces):
    """Union-find on a snapped 0.75 m grid, 26-neighbourhood."""
    cell = {}
    for i, f in enumerate(faces):
        k = (int(math.floor(f.centre.x / F3_CELL)),
             int(math.floor(f.centre.y / F3_CELL)),
             int(math.floor(f.centre.z / F3_CELL)))
        cell.setdefault(k, []).append(i)

    parent = list(range(len(faces)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for k, members in cell.items():
        for i in members:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        nk = (k[0] + dx, k[1] + dy, k[2] + dz)
                        for j in cell.get(nk, ()):
                            union(i, j)

    groups = {}
    for i in range(len(faces)):
        groups.setdefault(find(i), []).append(i)
    return groups


def bbox_of(faces, subset):
    mn = [1e18] * 3
    mx = [-1e18] * 3
    for i in subset:
        f = faces[i]
        for a in range(3):
            mn[a] = min(mn[a], f.mn[a])
            mx[a] = max(mx[a], f.mx[a])
    return mn, mx


def robust_bounds(faces, tag=""):
    """Filter the FDS scaffolding and return (kept_faces, report_lines)."""
    lines = []
    total = len(faces)
    alives = [f for f in faces if f.ok]
    lines.append(f"  [{tag}] {total} faces after import")

    dmin = [min(f.mn[a] for f in alives) for a in range(3)]
    dmax = [max(f.mx[a] for f in alives) for a in range(3)]
    dsize = [dmax[a] - dmin[a] for a in range(3)]
    lines.append(f"  [{tag}] raw domain bbox = {tuple(round(v,2) for v in dmin)} .. "
                 f"{tuple(round(v,2) for v in dmax)}  size="
                 f"{tuple(round(v,2) for v in dsize)}")

    n = f1_drop_domain_shell(alives, dmin, dsize)
    lines.append(f"  [{tag}] F1 domain-shell faces dropped: {n}")
    n = f2_drop_degenerate(alives)
    lines.append(f"  [{tag}] F2 degenerate faces dropped: {n}")
    big = [f for f in alives if f.ok and f.area >= F2_FACE_AREA]
    for f in big:
        f.ok = False
    if big:
        lines.append(f"  [{tag}] F2b oversized single faces dropped: {len(big)} "
                     + ", ".join(
                         f"{round(f.area)}m2@{tuple(round(c, 1) for c in (f.cx, f.cy, f.cz))}"
                         for f in big[:6]))
    n = f2b_drop_mesh_sheets(alives)
    lines.append(f"  [{tag}] F2b simulation-mesh sheet faces dropped: {n}")

    alives = [f for f in alives if f.ok]
    groups = cluster_faces(alives)
    biggest = max(len(m) for m in groups.values())
    small = biggest * F3_SMALL_FRAC

    # Anchors: every cluster large enough to be real equipment.  Small clusters
    # survive only if they sit inside the padded union of the anchors, so the
    # decision never chains outwards from a straggler.
    boxes = [(len(m),) + bbox_of(alives, m) for m in groups.values()]
    amin = [min(b[1][a] for b in boxes if b[0] >= small) for a in range(3)]
    amax = [max(b[2][a] for b in boxes if b[0] >= small) for a in range(3)]
    pad = [amin[a] - F3_NEAR_M for a in range(3)]
    exd = [amax[a] + F3_NEAR_M for a in range(3)]

    keep_idx, dropped = [], []
    for m in sorted(groups.values(), key=len, reverse=True):
        bmn, bmx = bbox_of(alives, m)
        near = all(bmn[a] >= pad[a] and bmx[a] <= exd[a] for a in range(3))
        if len(m) >= small or near:
            keep_idx.extend(m)
        else:
            dropped.append((len(m), bmn, bmx))
    keep_idx.sort()
    lines.append(f"  [{tag}] F3 anchors span {tuple(round(v,1) for v in amin)}.."
                 f"{tuple(round(v,1) for v in amax)}")
    lines.append(f"  [{tag}] F3 straggler clusters dropped: {len(dropped)} "
                 f"({sum(d[0] for d in dropped)} faces); kept {len(keep_idx)}/"
                 f"{len(alives)} faces in {len(groups) - len(dropped)} clusters")
    for nfaces, bmn, bmx in dropped[:F3_LOG_MAX]:
        lines.append(f"  [{tag}]   F3 dropped cluster: {nfaces} faces, bbox "
                     f"{tuple(round(v, 1) for v in bmn)}..{tuple(round(v, 1) for v in bmx)}")
    if len(dropped) > F3_LOG_MAX:
        lines.append(f"  [{tag}]   ... and {len(dropped)-F3_LOG_MAX} more small clusters")

    # ---- F4 storey split: these FDS rigs are two-storey, the upper storey is a
    # second copy of the same assembly stacked 12 m above the deck.
    ys = sorted(alives[i].centre.y for i in keep_idx)
    ylo, yhi = ys[0], ys[-1]
    nb = max(1, int((yhi - ylo) / F4_BIN) + 1)
    occ = [0] * nb
    for i in keep_idx:
        occ[min(nb - 1, int((alives[i].centre.y - ylo) / F4_BIN))] += 1
    yq = ys[int(F4_QUANTILE * (len(ys) - 1))]
    b0 = next((b for b in range(nb)
               if occ[b] == 0 and ylo + b * F4_BIN > yq), None)
    if b0 is None:
        lines.append(f"  [{tag}] F4 no storey split (no empty Y band above "
                     f"y={yq:.1f})")
    else:
        cut = ylo + (b0 + 0.5) * F4_BIN
        below = [i for i in keep_idx if alives[i].centre.y < cut]
        if below and len(below) >= F4_MIN_MASS * len(keep_idx):
            lines.append(f"  [{tag}] F4 storey split at y={cut:.2f} (empty band, "
                         f"first gap above the {F4_QUANTILE*100:.0f}th mass "
                         f"percentile y={yq:.1f}): dropping "
                         f"{len(keep_idx)-len(below)} upper-storey faces")
            keep_idx = below
        else:
            lines.append(f"  [{tag}] F4 no storey split at y={cut:.2f} (lower band "
                         f"under {F4_MIN_MASS*100:.0f}% of faces)")

    kept = [alives[i] for i in keep_idx]
    kmn = [min(f.mn[a] for f in kept) for a in range(3)]
    kmx = [max(f.mx[a] for f in kept) for a in range(3)]
    ksz = [kmx[a] - kmn[a] for a in range(3)]
    lines.append(f"  [{tag}] ROBUST bbox = {tuple(round(v, 2) for v in kmn)} .. "
                 f"{tuple(round(v, 2) for v in kmx)}  size={tuple(round(v, 2) for v in ksz)}")
    lines.append(f"  [{tag}] ROBUST kept {len(kept)}/{total} faces")
    # diagnostics: which kept faces still define the extremes?
    for axis, name in ((0, "X"), (1, "Y"), (2, "Z")):
        for hi in (True, False):
            f = max(kept, key=lambda g: g.mx[axis] if hi else -g.mn[axis])
            v = round(f.mx[axis] if hi else f.mn[axis], 2)
            if abs(v - (kmx[axis] if hi else kmn[axis])) > 1e-6:
                continue
            lines.append(f"  [{tag}]   extreme {name}{'max' if hi else 'min'} "
                         f"={v} from a face spanning "
                         f"{tuple(round(f.mx[a]-f.mn[a], 2) for a in range(3))} "
                         f"at {tuple(round(c, 2) for c in (f.cx, f.cy, f.cz))}")
    return kept, lines


# ==========================================================================
# transform + build
# ==========================================================================
def fit_transform(kept, bnd):
    """Uniform scale + translate that seats the model on the compartment floor
    and centres it horizontally inside the declared bounds."""
    rmn = [min(f.mn[a] for f in kept) for a in range(3)]
    rmx = [max(f.mx[a] for f in kept) for a in range(3)]
    rsz = [rmx[a] - rmn[a] for a in range(3)]
    bmin, bmax = bnd["min"], bnd["max"]
    bsz = [bmax[a] - bmin[a] for a in range(3)]
    usable = [bsz[a] * (1.0 - 2 * MARGIN) for a in range(3)]
    scale = min(usable[a] / rsz[a] for a in range(3) if rsz[a] > 1e-6)
    cx = (bmin[0] + bmax[0]) * 0.5 - (rmn[0] + rmx[0]) * 0.5 * scale
    cz = (bmin[2] + bmax[2]) * 0.5 - (rmn[2] + rmx[2]) * 0.5 * scale
    cy = bmin[1] - rmn[1] * scale
    return scale, (cx, cy, cz), rmn, rmx, bsz


def transform_faces(kept, scale, off, bnd, tag=""):
    """Apply the fit, then clip anything that still pokes out of the box."""
    out, clipped = [], 0
    for f in kept:
        moved = [Vector((v.x * scale + off[0], v.y * scale + off[1],
                         v.z * scale + off[2])) for v in f.verts]
        mn = [min(v[a] for v in moved) for a in range(3)]
        mx = [max(v[a] for v in moved) for a in range(3)]
        if mx[1] > bnd["max"][1] + 1e-4:
            clipped += 1
            continue
        f.verts = moved
        f.mn, f.mx = mn, mx
        out.append(f)
    if clipped:
        report(f"  [{tag}] clipped {clipped} faces above the compartment ceiling")
    return out


def build_meshes(kept, root_name, dry=False):
    """One welded mesh per palette material, parented to a single root Empty."""
    # Materials this script authored itself (the galley) are already palette
    # entries and must pass through unchanged; imported FDS materials are
    # classified by their glTF base colour / metallic / roughness.
    slot = {}
    for f in kept:
        if f.mat is None:
            key = "PaintedSteelGrey"
        elif f.mat.name in PAL_BY_NAME:
            key = f.mat.name
        else:
            key = pick_palette(*source_material_info(f.mat))
        slot[id(f)] = key

    buckets = {}
    for f in kept:
        buckets.setdefault(slot[id(f)], {"v": [], "f": [], "lut": {}})

    for key, b in buckets.items():
        lut, verts, faces = b["lut"], b["v"], b["f"]
        for f in kept:
            if slot[id(f)] != key:
                continue
            ring = []
            for p in f.verts:
                k = (round(p.x, 4), round(p.y, 4), round(p.z, 4))
                j = lut.get(k)
                if j is None:
                    j = len(verts)
                    lut[k] = j
                    verts.append(tuple(p))
                ring.append(j)
            if len(set(ring)) >= 3:
                faces.append(ring)

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for m in list(bpy.data.meshes):
        if m.users == 0:
            bpy.data.meshes.remove(m)

    root = bpy.data.objects.new(root_name, None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 1.0
    bpy.context.scene.collection.objects.link(root)

    made = []
    for name, rgb, rough, metal in PALETTE:
        b = buckets.get(name)
        if not b or not b["f"]:
            continue
        me = bpy.data.meshes.new(name)
        me.from_pydata(b["v"], [], b["f"])
        me.validate(verbose=False)
        me.update()
        mat = make_material(name, rgb, rough, metal)
        me.materials.append(mat)
        for p in me.polygons:
            p.use_smooth = False
        ob = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(ob)
        ob.parent = root
        made.append(ob)
    if dry:
        for o in made:
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.objects.remove(root, do_unlink=True)
    return root, made


def purge():
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True,
                                   do_recursive=True)


# ==========================================================================
# export
# ==========================================================================
def reset_scene():
    """Factory-reset, then guarantee exactly one scene, owned by the window.

    bpy.ops.wm.read_factory_settings(use_empty=True) does not reliably leave a
    single scene, and the glTF exporter then writes a scenes[] entry with no
    "nodes" key (which several importers reject) or exports the wrong one.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.data.scenes.get("Scene") or bpy.data.scenes.new("Scene")
    for other in list(bpy.data.scenes):
        if other is not sc:
            bpy.data.scenes.remove(other)
    sc.name = "Scene"
    if bpy.context.window is not None:
        bpy.context.window.scene = sc
    return sc


def export_glb(filepath, root_name):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    # bpy.ops.import_scene.gltf adds a scene per imported file, and the glTF
    # exporter writes *every* scene: a node-less entry makes the file
    # unimportable, and `scene` can even point at the wrong one.  Keep only
    # the scene that actually holds the root Empty.
    keep = None
    for ob in bpy.data.objects:
        if ob.type != "EMPTY":
            continue
        for sc in bpy.data.scenes:
            if ob.name in sc.collection.all_objects:
                keep = sc
                break
        if keep is not None:
            break
    if keep is None:
        raise RuntimeError("no scene holds the root Empty; refusing to export")
    for sc in list(bpy.data.scenes):
        if sc is not keep:
            bpy.data.scenes.remove(sc)
    if bpy.context.window is not None:
        bpy.context.window.scene = keep
    sc = keep
    n_obj = len(sc.collection.all_objects)
    report(f"  [{root_name}] scene {sc.name!r}: {n_obj} objects, "
           f"{len(bpy.data.scenes)} scene(s) total")
    draco = True
    try:
        bpy.ops.export_scene.gltf(
            filepath=filepath, export_format="GLB", use_selection=False,
            export_apply=True, export_draco_mesh_compression_enable=True,
            export_draco_mesh_compression_level=6,
        )
    except Exception as exc:                      # noqa: BLE001
        report(f"Draco export FAILED ({exc}); retrying uncompressed")
        bpy.ops.export_scene.gltf(filepath=filepath, export_format="GLB",
                                  use_selection=False, export_apply=True)
        draco = False
    purge()
    size = os.path.getsize(filepath) / 1048576
    # never trust the exporter's exit code: re-read the file and assert
    written = glb_stats(filepath)
    if written["nodes"] < 2 or written["meshes"] < 1 or not written["scene_nodes"]:
        raise RuntimeError(
            f"{os.path.basename(filepath)} exported empty/incomplete: "
            f"nodes={written['nodes']} meshes={written['meshes']} "
            f"scenes={written['scenes']} (built {n_obj} objects in scene "
            f"{sc.name!r})")
    report(f"  [{root_name}] {n_obj} objects in 1 scene -> glb nodes="
           f"{written['nodes']} meshes={written['meshes']} "
           f"root={written['roots']}")
    return size, draco, written


def glb_stats(path):
    """Parse the GLB container and report what actually landed on disk."""
    import json
    import struct
    with open(path, "rb") as fh:
        blob = fh.read()
    magic, _ver, total = struct.unpack_from("<III", blob, 0)
    if magic != 0x46546C67:
        raise ValueError(f"{path} is not a GLB")
    off, js = 12, None
    while off + 8 <= min(total, len(blob)):
        length, kind = struct.unpack_from("<II", blob, off)
        if kind == 0x4E4F534A and js is None:
            js = json.loads(blob[off + 8: off + 8 + length].decode("utf-8"))
        off += 8 + length
    nodes = js.get("nodes", [])
    scenes = js.get("scenes", [])
    roots = [n.get("name") for idx, n in enumerate(nodes)
             if not any(idx in (o.get("children") or []) for o in nodes)]
    return dict(
        nodes=len(nodes), meshes=len(js.get("meshes", [])),
        materials=len(js.get("materials", [])),
        scenes=[s.get("name") for s in scenes],
        scene_nodes=[len(s.get("nodes") or []) for s in scenes],
        roots=roots,
        exts=js.get("extensionsUsed", []),
        draco=sum(1 for m in js.get("meshes", []) for p in m["primitives"]
                  if "KHR_draco_mesh_compression" in p.get("extensions", {})),
        prims=sum(len(m["primitives"]) for m in js.get("meshes", [])),
    )


def draco_available():
    try:
        bpy.ops.export_scene.gltf.get_rna_type()
        props = bpy.ops.export_scene.gltf.get_rna_type().properties
        return "export_draco_mesh_compression_enable" in props
    except Exception:                             # noqa: BLE001
        return False


# ==========================================================================
# existing FDS models
# ==========================================================================
def optimize_existing(c):
    reset_scene()
    src = os.path.join(SRC_DIR, c["src"])
    bpy.ops.import_scene.gltf(filepath=src)
    report(f"=== {c['src']} -> {c['out']} ===")

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    faces = collect_faces(meshes)
    report(f"  source: {len(meshes)} objects, {len(bpy.data.materials)} materials, "
           f"{sum(f.area and 1 or 1 for f in faces)} faces")

    kept, lines = robust_bounds(faces, tag=c["key"])
    for l in lines:
        report(l)

    scale, off, rmn, rmx, bsz = fit_transform(kept, c["bounds"])
    report(f"  [{c['key']}] declared bounds size = "
           f"{tuple(round(v,2) for v in bsz)}  (5% margin applied)")
    report(f"  [{c['key']}] uniform scale = {scale:.4f}   offset = "
           f"{tuple(round(v,3) for v in off)}")

    placed = transform_faces(kept, scale, off, c["bounds"], tag=c["key"])
    root, made = build_meshes(placed, c["en"], dry=DRY_RUN)
    report(f"  [{c['key']}] built {len(made)+1} objects "
           f"({len(made)} material meshes + 1 root Empty)")

    if DRY_RUN:
        purge()
        return None

    size, draco, written = export_glb(os.path.join(OUT_DIR, c["out"]), c["en"])
    report(f"  [{c['key']}] wrote {c['out']}  {size:.3f} MB  draco={draco}")
    return dict(path=c["out"], mb=size, objects=len(made) + 1,
                materials=len(made), draco=draco, scale=scale)


# ==========================================================================
# galley (procedural)
# ==========================================================================
def box(name, cx, cy, cz, sx, sy, sz, mat):
    me = bpy.data.meshes.new(name)
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    v = [(cx - hx, cy - hy, cz - hz), (cx + hx, cy - hy, cz - hz),
         (cx + hx, cy - hy, cz + hz), (cx - hx, cy - hy, cz + hz),
         (cx - hx, cy + hy, cz - hz), (cx + hx, cy + hy, cz - hz),
         (cx + hx, cy + hy, cz + hz), (cx - hx, cy + hy, cz + hz)]
    fc = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
          (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me.from_pydata(v, [], fc)
    me.validate()
    me.update()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def cyl(name, cx, cy, cz, r, h, mat, axis="Z", seg=12):
    me = bpy.data.meshes.new(name)
    v, fc = [], []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        u, w = r * math.cos(a), r * math.sin(a)
        if axis == "Z":
            v += [(cx + u, cy + w, cz - h / 2), (cx + u, cy + w, cz + h / 2)]
        elif axis == "Y":
            v += [(cx + u, cy - h / 2, cz + w), (cx + u, cy + h / 2, cz + w)]
        else:
            v += [(cx - h / 2, cy + u, cz + w), (cx + h / 2, cy + u, cz + w)]
    for i in range(seg):
        j = (i + 1) % seg
        fc.append((2 * i, 2 * j, 2 * j + 1, 2 * i + 1))
    fc.append(tuple(range(0, 2 * seg, 2))[::-1])
    fc.append(tuple(range(1, 2 * seg, 2)))
    me.from_pydata(v, [], fc)
    me.validate()
    me.update()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def build_galley(c):
    """Ship galley inside the compartment-3 bounds, ship-local coordinates.

    bounds: x 2.0..9.5 (7.5 m fore-aft), y 4.4..7.4 (3.0 m), z -5.2..5.2 (10.4 m)
    Two counter runs down the side walls, walk-through aisle between them.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    report("=== galley.glb (procedural) ===")
    reset_scene()

    M = {n: make_material(n, col, r, m) for n, col, r, m in PALETTE}
    objs = []

    CX0, CX1 = 2.25, 9.25          # counter run extent along X
    CZ_P, CZ_S = -4.60, 4.60       # port / starboard run centres
    RUN_D = 0.70                   # counter depth
    CAB_H = 0.88                   # base cabinet height
    TOP_T = 0.05                   # countertop thickness
    LOCK_Y0, LOCK_Y1 = 6.45, 7.25  # overhead locker band

    # ---- base cabinets + countertops (both runs)
    for tag, cz in (("P", CZ_P), ("S", CZ_S)):
        objs.append(box(f"Cabinet{tag}", (CX0 + CX1) / 2, 4.4 + CAB_H / 2, cz,
                        CX1 - CX0, CAB_H, RUN_D, M["PaintedSteelGrey"]))
        objs.append(box(f"Counter{tag}", (CX0 + CX1) / 2,
                        4.4 + CAB_H + TOP_T / 2, cz,
                        CX1 - CX0, TOP_T, RUN_D + 0.06, M["CounterLaminate"]))

    # ---- stove / range on the port run
    sx, sz = 3.35, CZ_P
    objs.append(box("Range", sx, 4.4 + CAB_H + 0.30, sz, 0.90, 0.60, RUN_D - 0.04,
                    M["EnamelAppliance"]))
    objs.append(box("RangeTop", sx, 4.4 + CAB_H + 0.61, sz, 0.88, 0.04,
                    RUN_D - 0.06, M["MachinedSteel"]))
    for i, dx in enumerate((-0.22, 0.22)):
        for j, dz in enumerate((-0.15, 0.15)):
            objs.append(cyl(f"Burner{i}{j}", sx + dx, 4.4 + CAB_H + 0.64,
                            sz + dz, 0.105, 0.03, M["PaintedSteelDark"],
                            axis="Y", seg=10))
    objs.append(box("RangeOvenDoor", sx, 4.4 + CAB_H + 0.10, sz - RUN_D / 2 + 0.02,
                    0.78, 0.42, 0.04, M["GlassPane"]))

    # ---- range hood over the stove
    hy0 = 4.4 + CAB_H + 0.66
    objs.append(box("Hood", sx, hy0 + 0.30, sz, 1.05, 0.30, RUN_D + 0.04,
                    M["MachinedSteel"]))
    objs.append(box("HoodDuct", sx, hy0 + 0.80, sz, 0.42, 0.70, 0.42,
                    M["MachinedSteel"]))

    # ---- sink + faucet on the starboard run
    kx, kz = 4.30, CZ_S
    objs.append(box("SinkBasin", kx, 4.4 + CAB_H - 0.08, kz, 0.62, 0.16, 0.48,
                    M["MachinedSteel"]))
    objs.append(cyl("FaucetStem", kx, 4.4 + CAB_H + 0.16, kz + 0.30, 0.035, 0.32,
                    M["BrassFitting"], axis="Y", seg=8))
    objs.append(cyl("FaucetSpout", kx, 4.4 + CAB_H + 0.32, kz + 0.15, 0.030, 0.30,
                    M["BrassFitting"], axis="Z", seg=8))

    # ---- fridge / cold store at the aft end of the starboard run
    objs.append(box("Fridge", CX1 - 0.35, 4.4 + 0.85, CZ_S, 0.68, 1.70,
                    RUN_D - 0.02, M["EnamelAppliance"]))
    objs.append(box("FridgeDoor", CX1 - 0.35, 4.4 + 0.85, CZ_S - RUN_D / 2 - 0.01,
                    0.60, 1.60, 0.05, M["MachinedSteel"]))
    objs.append(box("FridgeHandle", CX1 - 0.62, 4.4 + 0.95, CZ_S - RUN_D / 2 - 0.07,
                    0.05, 0.50, 0.05, M["BrassFitting"]))

    # ---- overhead lockers above both runs
    for tag, cz in (("P", CZ_P), ("S", CZ_S)):
        objs.append(box(f"Locker{tag}", (CX0 + CX1) / 2, (LOCK_Y0 + LOCK_Y1) / 2,
                        cz, CX1 - CX0, LOCK_Y1 - LOCK_Y0, 0.55,
                        M["InsulationWhite"]))
        objs.append(box(f"LockerRail{tag}", (CX0 + CX1) / 2, LOCK_Y0 - 0.03, cz,
                        CX1 - CX0, 0.06, 0.58, M["MachinedSteel"]))

    # ---- galley appliances on the counters
    objs.append(box("Microwave", 6.30, 4.4 + CAB_H + 0.24, CZ_P + 0.04,
                    0.52, 0.36, 0.42, M["EnamelAppliance"]))
    objs.append(box("MicrowaveDoor", 6.30, 4.4 + CAB_H + 0.24, CZ_P - 0.20,
                    0.44, 0.28, 0.03, M["GlassPane"]))
    objs.append(cyl("Kettle", 7.20, 4.4 + CAB_H + 0.20, CZ_S - 0.10, 0.15, 0.32,
                    M["MachinedSteel"], axis="Y", seg=10))
    objs.append(cyl("RiceCooker", 8.15, 4.4 + CAB_H + 0.17, CZ_S - 0.08, 0.18, 0.26,
                    M["EnamelAppliance"], axis="Y", seg=10))
    objs.append(box("PrepTable", 5.60, 4.4 + 0.42, 0.0, 2.20, 0.05, 0.80,
                    M["CounterLaminate"]))
    for dx in (-1.0, 1.0):
        for dz in (-0.32, 0.32):
            objs.append(box("PrepLeg", 5.60 + dx, 4.4 + 0.21, dz, 0.06, 0.42, 0.06,
                            M["MachinedSteel"]))
    objs.append(box("PrepShelf", 5.60, 4.4 + 0.16, 0.0, 2.10, 0.04, 0.70,
                    M["PaintedSteelGrey"]))
    # wall-mounted fire blanket + extinguisher station (E-03 is a galley fire zone)
    objs.append(box("FireBlanket", 8.90, 5.30, CZ_S + 0.42, 0.30, 0.36, 0.16,
                    M["SafetyRed"]))
    objs.append(cyl("Extinguisher", 8.90, 4.85, CZ_S + 0.42, 0.09, 0.55,
                    M["SafetyRed"], axis="Y", seg=10))
    objs.append(box("GalleySign", 5.00, 6.70, CZ_P - 0.42, 0.40, 0.26, 0.04,
                    M["WarningOrange"]))

    # 灶炉间在船体布局调整中从 x 2..9.5 / y 4.4.. 迁到了 x 22..29 / y 4.6..。
    # 上面是按旧位置手写的绝对坐标，整体平移即可，内部相对布局保持不变。
    GX, GY, GZ = GALLEY_SHIFT
    for o in objs:
        o.location.x += GX
        o.location.y += GY
        o.location.z += GZ

    report(f"  [galley] built {len(objs)} primitive objects (shifted by {(GX, GY, GZ)})")
    # 平移后可能仍有一轴越界（如 z 向 ±4.98 超出声明的 ±4.6），
    # 与导入路径一样走 fit_transform 做等比缩放 + 居中贴合。
    faces = collect_faces(objs)
    scale, off, rmn, rmx, bsz = fit_transform(faces, c["bounds"])
    report(f"  [galley] uniform scale = {scale:.4f}   offset = "
           f"{tuple(round(v, 3) for v in off)}")
    placed = transform_faces(faces, scale, off, c["bounds"], tag="galley")
    kmn = [min(f.mn[a] for f in placed) for a in range(3)]
    kmx = [max(f.mx[a] for f in placed) for a in range(3)]
    bmin, bmax = c["bounds"]["min"], c["bounds"]["max"]
    report(f"  [galley] {len(placed)} faces, bbox {tuple(round(v,2) for v in kmn)} .. "
           f"{tuple(round(v,2) for v in kmx)}")
    fits = all(kmn[a] >= bmin[a] - 1e-6 and kmx[a] <= bmax[a] + 1e-6 for a in range(3))
    report(f"  [galley] declared bounds {bmin} .. {bmax}  fits={fits}")
    if not fits:
        raise SystemExit(f"galley 未落入声明包围盒: {kmn} .. {kmx} vs {bmin} .. {bmax}")
    root, made = build_meshes(placed, c["en"], dry=DRY_RUN)
    report(f"  [galley] joined to {len(made)+1} objects "
           f"({len(made)} material meshes + 1 root Empty)")
    if DRY_RUN:
        purge()
        return None
    size, draco, written = export_glb(os.path.join(OUT_DIR, c["out"]), c["en"])
    report(f"  [galley] wrote {c['out']}  {size:.3f} MB  draco={draco}")
    return dict(path=c["out"], mb=size, objects=len(made) + 1,
                materials=len(made), draco=draco, scale=1.0)


def build_meshes_galley(objs, root_name, dry=False):
    """Reuse the palette joiner, but feed it the primitive objects' faces."""
    faces = collect_faces(objs)
    root, made = build_meshes(faces, root_name, dry=dry)
    return root, made


# ==========================================================================
def main():
    global DRY_RUN, ONLY
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    DRY_RUN = "--dry-run" in argv
    if "--only" in argv:
        ONLY = argv[argv.index("--only") + 1]

    report(f"root={ROOT}")
    report(f"draco exporter property present: {draco_available()}")
    report(f"margin={MARGIN}  max objects target={MAX_OBJECTS}  dry_run={DRY_RUN}")

    results = []
    for c in COMP:
        if ONLY and c["key"] != ONLY and c["out"] != ONLY:
            continue
        if c["src"] is None:
            results.append(build_galley(c))
        else:
            results.append(optimize_existing(c))

    ok = [r for r in results if r]
    if ok:
        report("---- export summary ----")
        for r in ok:
            report(f"  {r['path']:<20} {r['mb']:7.3f} MB  objs={r['objects']:>3} "
                   f"mats={r['materials']:>3}  draco={r['draco']}")
    if not DRY_RUN:
        purge()


main()
