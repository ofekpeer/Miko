"""Stage 3: animated face parts - eyeballs, eyelids and a talking mouth.

blender -b --python stage3_face.py -- <stage2.blend> <face.json> <out.blend> <eye_texture.png>

face.json holds the concept->mesh alignment and painted feature positions
(image pixels, y from top). Every part is built on the actual body surface:

* Eyes: glossy bead eyeballs half-embedded in the face (iris texture with
  painted catch-lights), forward axis stored for look-at.
* Lids: upper/lower shells around each eyeball, modeled OPEN; rotating them
  about the eye's right axis (bones in stage 4) closes them without ever
  cutting into the eyeball.
* Mouth: a surface-conforming patch with shape keys. Basis is closed (zero
  area, the painted smile shows); viseme keys open it over the smile.
"""

import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

args = sys.argv[sys.argv.index("--") + 1:]
blend_in, face_json, blend_out, eye_tex_out = (os.path.abspath(a) for a in args[:4])
cfg = json.loads(open(face_json, encoding="utf-8").read())

bpy.ops.wm.open_mainfile(filepath=blend_in)
body = bpy.data.objects["MikoBody"]
depsgraph = bpy.context.evaluated_depsgraph_get()
bvh = BVHTree.FromObject(body, depsgraph)
verts = np.array([v.co[:] for v in body.data.vertices])
vnormals = np.array([v.normal[:] for v in body.data.vertices])

A, BX, BZ, IMG_H = cfg["A"], cfg["BX"], cfg["BZ"], cfg["image_height"]


def to_mesh_xz(px, py_top):
    return (px - BX) / A, ((IMG_H - py_top) - BZ) / A


def surface(x, z):
    hit, normal, _index, _dist = bvh.ray_cast(Vector((x, -5.0, z)), Vector((0, 1, 0)))
    near = np.linalg.norm(verts - np.array(hit[:]), axis=1) < 0.03
    smooth = Vector(vnormals[near].mean(axis=0)).normalized() if near.any() else normal
    return hit, smooth


def frame(forward):
    up = Vector((0, 0, 1))
    up = (up - forward * up.dot(forward)).normalized()
    right = up.cross(forward).normalized()
    return right, up, forward


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def material(name, color=None, roughness=0.5, coat=0.0, image=None, vertex_color=None, emission=None):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    if image is not None:
        tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = image
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    elif vertex_color:
        attr = nt.nodes.new("ShaderNodeVertexColor"); attr.layer_name = vertex_color
        nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    elif color:
        bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = roughness
    if coat and "Coat Weight" in bsdf.inputs:
        bsdf.inputs["Coat Weight"].default_value = coat
        bsdf.inputs["Coat Roughness"].default_value = 0.03
    return mat


# ------------------------------------------------------------------ eye texture
def make_eye_texture(path, size=512):
    yy, xx = np.mgrid[0:size, 0:size]
    u = (xx + 0.5) / size * 2 - 1           # -1..1, right
    v = (yy + 0.5) / size * 2 - 1           # -1..1, up (Blender rows start at bottom)
    rad = np.sqrt(u * u + v * v)
    deep, warm = np.array([0.16, 0.05, 0.025]), np.array([0.80, 0.36, 0.13])
    lower_glow = np.clip((-v - 0.05) / 0.9, 0, 1) * np.clip(1 - rad, 0, 1) * 1.6
    iris = deep + (warm - deep) * np.clip(lower_glow, 0, 1)[..., None]
    fibers = 0.9 + 0.1 * np.sin(np.arctan2(v, u) * 38)
    iris = iris * fibers[..., None]
    color = iris
    color = np.where((rad < 0.46)[..., None], np.array([0.006, 0.004, 0.004]), color)   # pupil
    ring = np.clip((rad - 0.82) / 0.12, 0, 1)
    color = color * (1 - 0.85 * ring)[..., None]                                       # dark limbal ring
    for cx, cy, r in ((-0.30, 0.34, 0.20), (0.32, -0.30, 0.08)):                        # catch-lights
        d = np.sqrt((u - cx) ** 2 + (v - cy) ** 2)
        glow = np.clip((r - d) / (r * 0.18), 0, 1)
        color = color * (1 - glow)[..., None] + glow[..., None] * np.array([1.0, 0.98, 0.95])
    rgba = np.dstack([np.clip(color, 0, 1), np.ones((size, size))]).astype(np.float32)
    img = bpy.data.images.new("MikoEye", size, size, alpha=False, float_buffer=False)
    img.colorspace_settings.name = "sRGB"
    # numpy colors above are authored in display space
    img.pixels.foreach_set(rgba.ravel())
    img.filepath_raw = path; img.file_format = "PNG"; img.save()
    return bpy.data.images.load(path)


def srgb_to_linear(rgb):
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]


eye_image = make_eye_texture(eye_tex_out)
eye_mat = material("MikoEye", roughness=0.05, coat=1.0, image=eye_image)


# ------------------------------------------------------------------ eyes + lids
def sphere_points(radius, rings=24, segments=40):
    """Lat-long sphere in a local frame where +Y is the eye's forward axis."""
    pts, faces = [], []
    for i in range(rings + 1):
        theta = math.pi * i / rings                     # 0 = forward pole
        for j in range(segments):
            phi = 2 * math.pi * j / segments
            pts.append((radius * math.sin(theta) * math.cos(phi),     # right
                        radius * math.cos(theta),                     # forward
                        radius * math.sin(theta) * math.sin(phi)))    # up
    for i in range(rings):
        for j in range(segments):
            a, b = i * segments + j, i * segments + (j + 1) % segments
            c, d = a + segments, b + segments
            faces.append((a, c, d, b))
    return pts, faces


def to_world(origin, right, up, forward, p):
    pr, pf, pu = p
    return origin + right * pr + forward * pf + up * pu


def build_object(name, local_pts, faces, origin, right, up, forward, uvs=None, mat=None, colors=None,
                 outward_from=None, world_rotation=None):
    """local points are (right, forward, up) components around `origin`.
    world_rotation: optional (angle, axis) rotation about `origin` applied in world space.
    Faces are flipped so normals point away from `outward_from` (default origin)."""
    world = [to_world(origin, right, up, forward, p) for p in local_pts]
    if world_rotation is not None:
        rot = Matrix.Rotation(world_rotation[0], 3, world_rotation[1])
        world = [origin + rot @ (w - origin) for w in world]
    me = bpy.data.meshes.new(name)
    me.from_pydata([w[:] for w in world], [], faces)
    me.update()
    ref = outward_from if outward_from is not None else origin
    bm = bmesh.new(); bm.from_mesh(me)
    for face in bm.faces:
        if face.normal.dot(face.calc_center_median() - ref) < 0:
            face.normal_flip()
    bm.to_mesh(me); bm.free()
    me.update()
    if uvs is not None:
        layer = me.uv_layers.new(name="UVMap")
        for poly in me.polygons:
            for li in poly.loop_indices:
                layer.data[li].uv = uvs[me.loops[li].vertex_index]
    if colors is not None:
        attr = me.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
        attr.data.foreach_set("color", np.array(colors, dtype=np.float32).ravel())
    for poly in me.polygons:
        poly.use_smooth = True
    obj = link(bpy.data.objects.new(name, me))
    if mat:
        me.materials.append(mat)
    return obj


eyes_info = {}
lid_open_deg = cfg.get("lid_open_deg", 78)
for side, (px, py) in (("L", cfg["eye_left_px"]), ("R", cfg["eye_right_px"])):
    x, z = to_mesh_xz(px, py)
    hit, normal = surface(x, z)
    r = cfg["eye_radius"]
    center = hit - normal * (cfg.get("eye_embed", 0.42) * r)
    forward = (normal * 0.55 + Vector((0, -1, 0)) * 0.45).normalized()
    right, up, forward = frame(forward)

    pts, faces = sphere_points(r)
    uvs = []
    for (pr, pf, pu) in pts:
        if pf > 0:
            uvs.append((0.5 + pr / (2 * r) * 0.97, 0.5 + pu / (2 * r) * 0.97))
        else:  # hidden back of the eyeball: dark limbal color
            k = 0.49 / max(1e-6, math.hypot(pr, pu))
            uvs.append((0.5 + pr * k / r, 0.5 + pu * k / r))
    eye = build_object(f"MikoEye_{side}", pts, faces, center, right, up, forward, uvs, eye_mat)

    # Lids: closed geometry is a sphere band; stored rotated OPEN about `right`.
    rl = r * 1.07
    lid_pts, lid_faces = sphere_points(rl, rings=28, segments=48)
    up_edge, low_edge = cfg.get("lid_up_edge", -0.32), cfg.get("lid_low_edge", -0.30)
    for kind, keep, color in (("Up", lambda pf, pu: pu > up_edge * rl and pf > -0.55 * rl, srgb_to_linear(cfg["lid_up_color"])),
                              ("Low", lambda pf, pu: pu < low_edge * rl and pf > -0.55 * rl, srgb_to_linear(cfg["lid_low_color"]))):
        kept = {i for i, (pr, pf, pu) in enumerate(lid_pts) if keep(pf, pu)}
        faces_kept = [f for f in lid_faces if all(i in kept for i in f)]
        remap = {old: new for new, old in enumerate(sorted({i for f in faces_kept for i in f}))}
        # Positive rotation about `right` moves up -> forward (closes the upper lid);
        # negative moves down -> forward (closes the lower lid). Model them OPEN.
        open_angle = (-math.radians(lid_open_deg) if kind == "Up"
                      else math.radians(cfg.get("lid_low_open_deg", 55)))
        local, cols = [], []
        for old in sorted(remap, key=remap.get):
            pr, pf, pu = lid_pts[old]
            edge = abs(pu - (up_edge if kind == "Up" else low_edge) * rl) / rl
            shade = 0.55 + 0.45 * min(1.0, edge / 0.12)       # darker lash line at the lid edge
            local.append((pr, pf, pu))
            cols.append((color[0] * shade, color[1] * shade, color[2] * shade, 1.0))
        new_faces = [tuple(remap[i] for i in f) for f in faces_kept]
        lid_mat = bpy.data.materials.get("MikoLid") or material("MikoLid", roughness=0.8, vertex_color="Col")
        lid = build_object(f"MikoLid{kind}_{side}", local, new_faces, center, right, up, forward,
                           mat=lid_mat, colors=cols, world_rotation=(open_angle, right))
        solid = lid.modifiers.new("thickness", "SOLIDIFY")
        solid.thickness = 0.0025
        solid.offset = 1.0
    eyes_info[side] = {"center": center[:], "forward": forward[:], "right": right[:], "up": up[:], "radius": r}
    print(f"EYE {side}: center={tuple(round(c, 4) for c in center)} forward={tuple(round(c, 3) for c in forward)}")

# ------------------------------------------------------------------ mouth
mx, mz = to_mesh_xz(*cfg["mouth_px"])
mouth_hit, mouth_n = surface(mx, mz)
m_forward = mouth_n
m_right, m_up, m_forward = frame(m_forward)
W2, HM = cfg["mouth_width"] / 2, cfg["mouth_height"]
COLS, ROWS = 26, 14


def surface_point(local_x, local_y):
    guess = mouth_hit + m_right * local_x + m_up * local_y
    hit = bvh.ray_cast(guess + m_forward * 0.03, -m_forward)[0]
    return (hit if hit is not None else guess) + m_forward * 0.0016


def smile_line(s, curve):
    return curve * (s * s) * HM * 0.25


def mouth_shape(width, top, bottom, curve):
    """Grid points: s across (-1..1), t from top edge (1) to bottom edge (-1)."""
    pts = []
    for row in range(ROWS + 1):
        t = 1 - 2 * row / ROWS
        for col in range(COLS + 1):
            s = -1 + 2 * col / COLS
            envelope = math.sqrt(max(0.0, 1 - s * s))
            mid = smile_line(s, curve)
            y_top, y_bottom = mid + top * HM * envelope, mid - bottom * HM * envelope
            y = y_bottom + (y_top - y_bottom) * (t + 1) / 2
            pts.append(surface_point(s * W2 * width, y))
    return pts


faces = []
for row in range(ROWS):
    for col in range(COLS):
        a = row * (COLS + 1) + col
        faces.append((a, a + COLS + 1, a + COLS + 2, a + 1))
colors = []
for row in range(ROWS + 1):
    t = 1 - 2 * row / ROWS
    for col in range(COLS + 1):
        s = -1 + 2 * col / COLS
        tongue = max(0.0, min(1.0, (-t - 0.15) / 0.5)) * (1 - abs(s) ** 2)
        base = np.array([0.20, 0.035, 0.045]) * (1 - tongue) + np.array([0.80, 0.33, 0.36]) * tongue
        rim = max(0.0, (abs(s) - 0.85) / 0.15) + max(0.0, (abs(t) - 0.85) / 0.15)
        colors.append((*(base * (1 - 0.5 * min(1, rim))), 1.0))

closed = mouth_shape(width=0.55, top=0.0, bottom=0.0, curve=1.0)
me = bpy.data.meshes.new("MikoMouth")
me.from_pydata([p[:] for p in closed], [], faces)
me.update()
bm = bmesh.new(); bm.from_mesh(me)
for face in bm.faces:
    if face.normal.dot(m_forward) < 0:
        face.normal_flip()
bm.to_mesh(me); bm.free()
attr = me.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
attr.data.foreach_set("color", np.array(colors, dtype=np.float32).ravel())
for poly in me.polygons:
    poly.use_smooth = True
mouth = link(bpy.data.objects.new("MikoMouth", me))
me.materials.append(material("MikoMouth", roughness=0.6, vertex_color="Col"))
mouth.shape_key_add(name="Basis", from_mix=False)
SHAPES = {
    #  name         width  top   bottom  curve
    "open":       (0.80, 0.10, 0.75, 0.8),   # A
    "round":      (0.42, 0.22, 0.62, 0.0),   # O / U
    "wide":       (1.00, 0.06, 0.32, 0.6),   # E / I
    "smile":      (0.95, 0.08, 0.45, 2.2),   # happy talking / laugh
    "sad":        (0.70, 0.04, 0.22, -1.6),  # frown
    "surprised":  (0.50, 0.35, 0.80, 0.0),   # small round gasp
}
for name, (width, top, bottom, curve) in SHAPES.items():
    key = mouth.shape_key_add(name=name, from_mix=False)
    for i, p in enumerate(mouth_shape(width, top, bottom, curve)):
        key.data[i].co = p
print(f"MOUTH at {tuple(round(c, 4) for c in mouth_hit)} keys={list(SHAPES)}")

json.dump({"eyes": eyes_info, "mouth": {"center": mouth_hit[:], "normal": m_forward[:]},
           "lid_open_deg": lid_open_deg, "lid_low_open_deg": cfg.get("lid_low_open_deg", 55)},
          open(os.path.join(os.path.dirname(blend_out), "face_parts.json"), "w"), indent=2)
bpy.ops.wm.save_as_mainfile(filepath=blend_out)
print("SAVED", blend_out)
