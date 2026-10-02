"""Render landmark markers over the body (front + side) to validate rig placement.

blender -b --python debug_landmarks.py -- <stage3.blend> <face_parts.json> <out_prefix>
"""

import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from landmarks import find_landmarks  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
blend, parts_path, prefix = (os.path.abspath(a) for a in args[:3])
parts = json.loads(open(parts_path, encoding="utf-8").read())
bpy.ops.wm.open_mainfile(filepath=blend)
body = bpy.data.objects["MikoBody"]
verts = np.array([v.co[:] for v in body.data.vertices])
face_x = (parts["eyes"]["L"]["center"][0] + parts["eyes"]["R"]["center"][0]) / 2
lm = find_landmarks(verts, face_x)
for name, p in lm.items():
    print(f"LM {name:16s} {np.round(p, 3)}")

scene = bpy.context.scene
mat = bpy.data.materials.new("marker"); mat.use_nodes = True
mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.0, 0.9, 0.3, 1)
mat.node_tree.nodes["Principled BSDF"].inputs["Emission Color"].default_value = (0.0, 1.0, 0.3, 1)
mat.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 3.0
for name, p in lm.items():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.018, location=Vector(p))
    bpy.context.active_object.data.materials.append(mat)
body.data.materials[0].diffuse_color = (1, 1, 1, 0.35)

engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "MATERIAL"
scene.display.shading.show_xray = True
scene.display.shading.xray_alpha = 0.55
scene.render.resolution_x = scene.render.resolution_y = 560
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam)
scene.camera = cam
cam.data.type = "ORTHO"; cam.data.ortho_scale = 1.25
for view, loc in (("front", (face_x, -4, 0.5)), ("side", (4, 0.0, 0.5))):
    cam.location = Vector(loc)
    cam.rotation_euler = (Vector((face_x if view == "front" else 0, 0, 0.5)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{prefix}_{view}.png"
    bpy.ops.render.render(write_still=True)
    print("RENDERED", scene.render.filepath)
