"""Stage 4: skeleton, skin weights, export to GLB + controller metadata.

blender -b --python stage4_rig.py -- <stage3.blend> <face_parts.json> <out.blend> <out.glb> <out_meta.json>

Bones (names are the contract with the Godot controller):
  root > body > head > ear.{L,R}.{1,2}, eye.{L,R}, lid_up.{L,R}, lid_low.{L,R}
  body > arm.{L,R}, tail.1 > tail.2 ;  root > foot.{L,R}
Body skin uses Blender's heat weights for the deforming body bones; eyes,
lids and mouth are rigid parts weighted 100% to their bone.
"""

import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from landmarks import find_landmarks  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
blend_in, parts_path, blend_out, glb_out, meta_out = (os.path.abspath(a) for a in args[:5])
parts = json.loads(open(parts_path, encoding="utf-8").read())

bpy.ops.wm.open_mainfile(filepath=blend_in)
scene = bpy.context.scene
body = bpy.data.objects["MikoBody"]
verts = np.array([v.co[:] for v in body.data.vertices])
eyes = parts["eyes"]
face_x = (eyes["L"]["center"][0] + eyes["R"]["center"][0]) / 2
lm = {k: Vector(v) for k, v in find_landmarks(verts, face_x).items()}

# Apply lid thickness so the parts are plain meshes before skinning.
for obj in [o for o in scene.objects if o.type == "MESH" and o.modifiers]:
    bpy.context.view_layer.objects.active = obj
    for mod in list(obj.modifiers):
        bpy.ops.object.modifier_apply(modifier=mod.name)

# ------------------------------------------------------------------ armature
arm_data = bpy.data.armatures.new("MikoRig")
rig = bpy.data.objects.new("MikoRig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones


def bone(name, head, tail, parent=None, connect=False, roll_z=None):
    b = eb.new(name)
    b.head, b.tail = Vector(head), Vector(tail)
    if parent:
        b.parent = eb[parent]
        b.use_connect = connect
    if roll_z is not None:
        b.align_roll(Vector(roll_z))
    return b


center = lm["center"]
bone("root", center, center + Vector((0, 0, 0.08)))
bone("body", lm["pelvis"], lm["chest"], "root")
bone("head", lm["chest"], lm["head_top"], "body", connect=True)
for s in ("L", "R"):
    bone(f"ear.{s}.1", lm[f"ear_{s}_base"], lm[f"ear_{s}_mid"], "head")
    bone(f"ear.{s}.2", lm[f"ear_{s}_mid"], lm[f"ear_{s}_tip"], f"ear.{s}.1", connect=True)
    bone(f"arm.{s}", lm[f"arm_{s}_shoulder"], lm[f"arm_{s}_hand"], "body")
    bone(f"foot.{s}", lm[f"foot_{s}_ankle"], lm[f"foot_{s}_toe"], "root")
    e = eyes[s]
    c, f, up = Vector(e["center"]), Vector(e["forward"]), Vector(e["up"])
    # Roll so local X == eye right: rotating about +X closes the upper lid.
    bone(f"eye.{s}", c, c + f * 0.045, "head", roll_z=-up)
    bone(f"lid_up.{s}", c, c + f * 0.035, "head", roll_z=-up)
    bone(f"lid_low.{s}", c, c + f * 0.030, "head", roll_z=-up)
bone("tail.1", lm["tail_base"], lm["tail_mid"], "body")
bone("tail.2", lm["tail_mid"], lm["tail_tip"], "tail.1", connect=True)
bpy.ops.object.mode_set(mode="OBJECT")

FACE_BONES = [b.name for b in arm_data.bones if b.name.startswith(("eye.", "lid_"))]
for b in arm_data.bones:
    b.use_deform = b.name not in FACE_BONES and b.name != "root"

# ------------------------------------------------------------------ body skin
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.parent_set(type="ARMATURE_AUTO")

groups = {g.index: g.name for g in body.vertex_groups}
deform_heads = {b.name: (b.head_local, b.tail_local) for b in arm_data.bones if b.use_deform}
unweighted = 0
for v in body.data.vertices:
    if sum(g.weight for g in v.groups) < 1e-4:
        unweighted += 1
        best, best_d = None, 1e9
        for name, (h, t) in deform_heads.items():
            seg = t - h
            k = max(0.0, min(1.0, (v.co - h).dot(seg) / seg.length_squared))
            d = (v.co - (h + seg * k)).length
            if d < best_d:
                best, best_d = name, d
        group = body.vertex_groups.get(best) or body.vertex_groups.new(name=best)
        group.add([v.index], 1.0, "REPLACE")
print(f"SKIN groups={len(body.vertex_groups)} unweighted_fixed={unweighted}")

for b in arm_data.bones:
    if b.name in FACE_BONES:
        b.use_deform = True   # face parts deform rigidly with their own bones

# ------------------------------------------------------------------ rigid parts
RIGID = {}
for s in ("L", "R"):
    RIGID[f"MikoEye_{s}"] = f"eye.{s}"
    RIGID[f"MikoLidUp_{s}"] = f"lid_up.{s}"
    RIGID[f"MikoLidLow_{s}"] = f"lid_low.{s}"
RIGID["MikoMouth"] = "head"
for obj_name, bone_name in RIGID.items():
    obj = bpy.data.objects[obj_name]
    group = obj.vertex_groups.new(name=bone_name)
    group.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    obj.parent = rig
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = rig

bpy.ops.wm.save_as_mainfile(filepath=blend_out)
print("SAVED", blend_out)

# ------------------------------------------------------------------ export
# Exported node names use a Fox prefix: the Godot controller searches the
# scene for robot parts named Miko* (e.g. MikoMouth) and would hijack them.
# The body's color attributes only fed the texture bake; glTF COLOR_0 would
# multiply the base color in Godot, so drop them from the exported mesh.
for attribute_name in [a.name for a in body.data.color_attributes]:
    body.data.attributes.remove(body.data.attributes[attribute_name])
exported = [rig, body, *[bpy.data.objects[n] for n in RIGID]]
for obj in exported:
    obj.name = obj.name.replace("Miko", "Fox", 1)
    if obj.data is not None:
        obj.data.name = obj.data.name.replace("Miko", "Fox", 1)
bpy.ops.object.select_all(action="DESELECT")
for obj in exported:
    obj.select_set(True)
bpy.ops.export_scene.gltf(
    filepath=glb_out, export_format="GLB", use_selection=True,
    export_skins=True, export_morph=True, export_morph_normal=False,
    export_apply=False, export_animations=False, export_yup=True,
    export_image_format="JPEG", export_jpeg_quality=92,
)
print("EXPORTED", glb_out, os.path.getsize(glb_out) // 1024, "KB")


def to_gltf(v):
    v = Vector(v)
    return [round(v.x, 5), round(v.z, 5), round(-v.y, 5)]


meta = {
    "units": "height 1.0, feet at y=0, faces +Z in glTF/Godot",
    "eyes": {s: {"center": to_gltf(eyes[s]["center"]), "forward": to_gltf(eyes[s]["forward"]),
                 "right": to_gltf(eyes[s]["right"]), "up": to_gltf(eyes[s]["up"]),
                 "radius": eyes[s]["radius"]} for s in ("L", "R")},
    "lid_close_deg": {"up": parts.get("lid_open_deg", 96), "low": parts.get("lid_low_open_deg", 62)},
    "mouth_shapes": [k.name for k in bpy.data.objects["FoxMouth"].data.shape_keys.key_blocks[1:]],
    "bones": [b.name for b in arm_data.bones],
    "landmarks": {k: to_gltf(v) for k, v in lm.items()},
}
with open(meta_out, "w", encoding="utf-8") as fh:
    json.dump(meta, fh, indent=2)
print("META", meta_out)
