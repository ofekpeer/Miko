"""Build the rigged Miko robot from newmodelmiko.glb (Sketchfab "Cartoon Robot",
alexandermadrews, CC-BY-4.0).

blender -b --python robot_build.py -- <newmodelmiko.glb> <tex_0.png> <out_dir>

* Normalizes: feet on z=0, height 1.0, facing -Y.
* Erases the painted eyes from the visor in the base-color texture; the face
  is drawn live by the Godot visor shader instead (no static face remains).
* Splits the visor's front faces into "RobotVisorScreen" with a planar
  "FaceUV" map (0..1 across the visor) for that shader.
* Humanoid skeleton; small rigid pieces follow one bone, long shells (e.g. a
  leg shell from hip to ankle) blend per vertex across the joint.
* Exports RobotMiko.glb + RobotMiko.meta.json (glTF/Godot coordinates).
"""

import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
source, base_tex, out_dir = (os.path.abspath(a) for a in args[:3])
os.makedirs(out_dir, exist_ok=True)

HEIGHT = 1.898          # source height
LOW = -0.949            # source feet

# Bones in SOURCE coordinates (x right on screen, -y front, z up): head, tail, radius.
BONES = {
    "root": ((0, 0, -0.949), (0, 0, -0.85), 0.0),
    "hips": ((0, 0, -0.58), (0, 0, -0.38), 0.22),
    "spine": ((0, 0, -0.38), (0, 0, -0.03), 0.25),
    "head": ((0, 0, -0.03), (0, 0, 0.90), 0.0),
}
for side, s in (("L", -1), ("R", 1)):
    BONES[f"upperarm.{side}"] = ((0.32 * s, 0.01, -0.12), (0.43 * s, 0.02, -0.40), 0.10)
    BONES[f"forearm.{side}"] = ((0.43 * s, 0.02, -0.40), (0.47 * s, -0.02, -0.55), 0.09)
    BONES[f"hand.{side}"] = ((0.47 * s, -0.02, -0.55), (0.49 * s, -0.06, -0.68), 0.08)
    BONES[f"thigh.{side}"] = ((0.16 * s, 0.0, -0.55), (0.20 * s, 0.0, -0.72), 0.11)
    BONES[f"shin.{side}"] = ((0.20 * s, 0.0, -0.72), (0.22 * s, 0.02, -0.86), 0.10)
    BONES[f"foot.{side}"] = ((0.22 * s, 0.02, -0.86), (0.24 * s, -0.16, -0.93), 0.09)
PARENT = {"hips": "root", "spine": "hips", "head": "spine"}
for side in ("L", "R"):
    PARENT.update({f"upperarm.{side}": "spine", f"forearm.{side}": f"upperarm.{side}",
                   f"hand.{side}": f"forearm.{side}", f"thigh.{side}": "hips",
                   f"shin.{side}": f"thigh.{side}", f"foot.{side}": f"shin.{side}"})


def norm(p):
    return Vector(((p[0]) / HEIGHT, (p[1]) / HEIGHT, (p[2] - LOW) / HEIGHT))


# ------------------------------------------------------------------ import + normalize
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=source)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
for o in bpy.context.scene.objects:
    o.select_set(o in meshes)
bpy.context.view_layer.objects.active = meshes[0]
bpy.ops.object.join()
body = bpy.context.view_layer.objects.active
bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
for o in list(bpy.context.scene.objects):
    if o is not body:
        bpy.data.objects.remove(o, do_unlink=True)
body.name = body.data.name = "RobotBody"
src = np.array([v.co[:] for v in body.data.vertices])
for v in body.data.vertices:
    v.co = norm(v.co)

# ------------------------------------------------------------------ erase painted eyes
img = bpy.data.images.load(base_tex)
W, H = img.size
px = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)      # row 0 = bottom
# Visor island: top-left of the atlas (u 0.01..0.19, v 0.75..1.0 in Blender UV).
x0, x1, y0, y1 = int(0.010 * W), int(0.190 * W), int(0.745 * H), H
region = px[y0:y1, x0:x1, :3]
r, g, b = region[..., 0], region[..., 1], region[..., 2]
brightest = region.max(axis=2)
white = region.min(axis=2) > 0.55                     # helmet rim: never touched, never a source
# Visor background is navy (~0.01, 0.05, 0.08); the eyes are bright cyan.
mask = (brightest > 0.20) & ((b - r) > 0.15) & ~white
for _ in range(7):                                    # cover the soft glow halo
    grown = mask.copy()
    grown[1:, :] |= mask[:-1, :]; grown[:-1, :] |= mask[1:, :]
    grown[:, 1:] |= mask[:, :-1]; grown[:, :-1] |= mask[:, 1:]
    mask = grown & ~white & (brightest > 0.085)
fill = region.copy()
known = ~mask & ~white & (brightest < 0.2)
for _ in range(500):   # diffusion inpaint from the surrounding visor navy only
    acc = np.zeros_like(fill); cnt = np.zeros(fill.shape[:2])
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        shifted = np.roll(np.roll(fill, dy, 0), dx, 1)
        valid = np.roll(np.roll(known, dy, 0), dx, 1)
        acc += shifted * valid[..., None]; cnt += valid
    upd = mask & ~known & (cnt > 0)
    fill[upd] = acc[upd] / cnt[upd, None]
    known = known | upd
px[y0:y1, x0:x1, :3] = fill
print(f"EYES erased pixels={int(mask.sum())}")
clean_path = os.path.join(out_dir, "RobotBaseColor.png")
clean = bpy.data.images.new("RobotBaseColor", W, H, alpha=True)
clean.pixels.foreach_set(px.ravel())
clean.filepath_raw = clean_path; clean.file_format = "PNG"; clean.save()
for mat in body.data.materials:
    for node in mat.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image and node.image.filepath == "" and node.image.size[0] == W:
            pass
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf and bsdf.inputs["Base Color"].links:
        bsdf.inputs["Base Color"].links[0].from_node.image = bpy.data.images.load(clean_path)

# ------------------------------------------------------------------ visor screen
mesh = body.data
uv = mesh.uv_layers.active.data
visor_faces = []
for poly in mesh.polygons:
    us = [uv[i].uv for i in poly.loop_indices]
    cu = sum(u.x for u in us) / len(us); cv = sum(u.y for u in us) / len(us)
    c = poly.center
    if 0.010 < cu < 0.190 and cv > 0.745 and c.z > 0.45:
        # Follow the painted outline: navy glass becomes the screen, the
        # white helmet rim (same UV island) stays part of the body.
        texel = px[min(H - 1, int(cv * H)), min(W - 1, int(cu * W)), :3]
        if texel.max() < 0.25:
            visor_faces.append(poly.index)
bm = bmesh.new(); bm.from_mesh(mesh)
bm.faces.ensure_lookup_table()
geom = [bm.faces[i] for i in visor_faces]
dup = bmesh.ops.duplicate(bm, geom=geom)
new_faces = [g for g in dup["geom"] if isinstance(g, bmesh.types.BMFace)]
new_verts = {v for f in new_faces for v in f.verts}
for v in new_verts:
    v.co += v.normal * 0.0008
screen_bm = bmesh.new()
vmap = {}
for v in new_verts:
    vmap[v] = screen_bm.verts.new(v.co)
for f in new_faces:
    screen_bm.faces.new([vmap[v] for v in f.verts])
# The source visor mixes triangle windings; face every triangle outward (-Y)
# so none is back-face culled into a see-through hole.
screen_bm.normal_update()
flipped = 0
head_center = Vector((0.0, 0.0, 0.73))
for f in screen_bm.faces:
    if f.normal.dot(f.calc_center_median() - head_center) < 0.0:
        f.normal_flip(); flipped += 1
screen_bm.normal_update()
print(f"VISOR flipped={flipped}")
# The screen replaces the original visor faces outright: two coincident
# surfaces would z-fight (white flicker) and could show the old painted face.
bmesh.ops.delete(bm, geom=geom + new_faces, context="FACES")
bm.to_mesh(mesh); bm.free()
screen_mesh = bpy.data.meshes.new("RobotVisorScreen")
screen_bm.to_mesh(screen_mesh); screen_bm.free()
xs = [v.co.x for v in screen_mesh.vertices]; zs = [v.co.z for v in screen_mesh.vertices]
vx0, vx1, vz0, vz1 = min(xs), max(xs), min(zs), max(zs)
face_uv = screen_mesh.uv_layers.new(name="FaceUV")
for poly in screen_mesh.polygons:
    for li in poly.loop_indices:
        co = screen_mesh.vertices[screen_mesh.loops[li].vertex_index].co
        face_uv.data[li].uv = ((co.x - vx0) / (vx1 - vx0), (co.z - vz0) / (vz1 - vz0))
for poly in screen_mesh.polygons:
    poly.use_smooth = True
# The source also has a second, white-mapped glass shell hugging the visor.
# Delete every body face lying on the screen (within 8 mm, inside its outline).
from mathutils.bvhtree import BVHTree
screen_bvh = BVHTree.FromPolygons([v.co for v in screen_mesh.vertices], [p.vertices for p in screen_mesh.polygons])
bm = bmesh.new(); bm.from_mesh(mesh); bm.faces.ensure_lookup_table()
shell = []
for f in bm.faces:
    c = f.calc_center_median()
    if not (vx0 - 0.01 < c.x < vx1 + 0.01 and vz0 - 0.01 < c.z < vz1 + 0.01):
        continue
    for direction in (Vector((0, 1, 0)), Vector((0, -1, 0))):
        hit = screen_bvh.ray_cast(c - direction * 0.012, direction, 0.024)
        if hit[0] is not None and abs(hit[0].y - c.y) < 0.008:
            shell.append(f)
            break
bmesh.ops.delete(bm, geom=shell, context="FACES")
bm.to_mesh(mesh); bm.free()
print(f"VISOR shell faces removed={len(shell)}")
screen = bpy.data.objects.new("RobotVisorScreen", screen_mesh)
bpy.context.scene.collection.objects.link(screen)
face_mat = bpy.data.materials.new("RobotFace"); face_mat.use_nodes = True
face_mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1)
screen_mesh.materials.append(face_mat)
print(f"VISOR faces={len(visor_faces)} bbox x[{vx0:.3f},{vx1:.3f}] z[{vz0:.3f},{vz1:.3f}]")

# ------------------------------------------------------------------ armature
arm = bpy.data.armatures.new("RobotRig")
rig = bpy.data.objects.new("RobotRig", arm)
bpy.context.scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
for name, (h, t, _r) in BONES.items():
    eb = arm.edit_bones.new(name)
    eb.head, eb.tail = norm(h), norm(t)
for name, parent in PARENT.items():
    arm.edit_bones[name].parent = arm.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")
arm.bones["root"].use_deform = False

# ------------------------------------------------------------------ weights
def seg_dist(p, a, b):
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
    return np.linalg.norm(p - (a + np.outer(t, ab)), axis=1)


deform = [n for n in BONES if n not in ("root", "head")]
verts = np.array([v.co[:] for v in mesh.vertices])
dist = np.stack([seg_dist(verts, np.array(norm(BONES[n][0])), np.array(norm(BONES[n][1])))
                 - BONES[n][2] / HEIGHT for n in deform], axis=1)
order = np.argsort(dist, axis=1)
best, second = order[:, 0], order[:, 1]
d1 = dist[np.arange(len(verts)), best]; d2 = dist[np.arange(len(verts)), second]
blend = np.clip((d2 - d1) / (0.035 / HEIGHT * 2), 0, 1)            # 1 = only best
w_best = 0.5 + 0.5 * blend
head_zone = verts[:, 2] > norm((0, 0, -0.025)).z

# islands
bm = bmesh.new(); bm.from_mesh(mesh); bm.verts.ensure_lookup_table()
island_of = np.full(len(verts), -1); islands = []
for v in bm.verts:
    if island_of[v.index] >= 0:
        continue
    stack, members = [v], []
    island_of[v.index] = len(islands)
    while stack:
        cur = stack.pop(); members.append(cur.index)
        for e in cur.link_edges:
            o = e.other_vert(cur)
            if island_of[o.index] < 0:
                island_of[o.index] = len(islands); stack.append(o)
    islands.append(np.array(members))
bm.free()

groups = {n: body.vertex_groups.new(name=n) for n in BONES if n != "root"}
index_of = {n: i for i, n in enumerate(deform)}
children = {n: [c for c, par in PARENT.items() if par == n] for n in BONES}
rigid = blended = 0
for members in islands:
    if head_zone[members].mean() > 0.5:
        groups["head"].add(members.tolist(), 1.0, "REPLACE"); rigid += 1
        continue
    majority = deform[int(np.bincount(best[members], minlength=len(deform)).argmax())]
    span = verts[members].max(0) - verts[members].min(0)
    if span.max() < 0.09:            # compact hard piece: moves rigidly with one bone
        groups[majority].add(members.tolist(), 1.0, "REPLACE"); rigid += 1
        continue
    # A long shell may bend only across joints of its own chain (e.g. a leg
    # shell across the knee), never jump to an unrelated limb.
    def family(b):
        if b in ("hips", "spine"):
            return "torso"
        return ("arm." if b.startswith(("upperarm", "forearm", "hand")) else "leg.") + b[-1]
    allowed = {family(majority)}
    if majority.startswith("thigh"):
        allowed.add("torso")              # the top of a leg shell may follow the hips
    chain = [majority] + [b for b in [PARENT.get(majority)] + children[majority]
                          if b in index_of and family(b) in allowed]
    cols = [index_of[b] for b in chain]
    sub = dist[np.ix_(members, cols)]
    order_sub = np.argsort(sub, axis=1)
    rows = np.arange(len(members))
    first, second_ = order_sub[:, 0], order_sub[:, 1] if len(cols) > 1 else order_sub[:, 0]
    d_first, d_second = sub[rows, first], sub[rows, second_]
    w = 0.5 + 0.5 * np.clip((d_second - d_first) / (0.035 / HEIGHT * 2), 0, 1)
    blended += 1
    for k, i in enumerate(members):
        groups[chain[first[k]]].add([int(i)], float(w[k]), "REPLACE")
        if w[k] < 0.999 and len(cols) > 1:
            groups[chain[second_[k]]].add([int(i)], float(1 - w[k]), "ADD")
print(f"WEIGHTS islands={len(islands)} rigid={rigid} blended={blended}")

for obj, group in ((body, None), (screen, "head")):
    if group:
        g = obj.vertex_groups.new(name=group)
        g.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    obj.parent = rig
    mod = obj.modifiers.new("Armature", "ARMATURE"); mod.object = rig

# No decimation: collapsing edges across UV seams on this atlas-mapped model
# blends UVs of neighbouring islands and paints white blotches on dark parts.
# 120k triangles render comfortably on a laptop iGPU.
print(f"BODY faces={len(body.data.polygons)}")

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, "robot.blend"))
glb = os.path.join(out_dir, "RobotMiko.glb")
bpy.ops.object.select_all(action="DESELECT")
for o in (rig, body, screen):
    o.select_set(True)
bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True, export_skins=True,
                          export_animations=False, export_yup=True, export_image_format="JPEG",
                          export_jpeg_quality=92, export_texcoords=True)
print("EXPORTED", glb, os.path.getsize(glb) // 1024, "KB")


def gl(v):
    v = Vector(v); return [round(v.x, 5), round(v.z, 5), round(-v.y, 5)]


meta = {
    "source": "Cartoon Robot by alexandermadrews (Sketchfab), CC-BY-4.0",
    "units": "height 1.0, feet at y=0, faces +Z in glTF/Godot",
    "visor": {"min": gl((vx0, 0, vz0)), "max": gl((vx1, 0, vz1))},
    "bones": {n: {"head": gl(norm(h)), "tail": gl(norm(t))} for n, (h, t, _r) in BONES.items()},
}
json.dump(meta, open(os.path.join(out_dir, "RobotMiko.meta.json"), "w"), indent=2)
print("META written")
