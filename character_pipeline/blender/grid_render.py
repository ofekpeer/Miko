"""Orthographic front/side renders of a GLB with a 0.1-unit coordinate grid.

blender -b --python grid_render.py -- <model.glb> <out_prefix> [markers.json]
Red lines: every 0.1 (thick every 0.5). Markers: optional {"name": [x,y,z]} spheres.
"""

import json
import os
import sys

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
model, prefix = os.path.abspath(args[0]), os.path.abspath(args[1])
markers = json.load(open(args[2])) if len(args) > 2 else {}
bpy.ops.wm.read_factory_settings(use_empty=True)
if model.endswith(".blend"):
    bpy.ops.wm.open_mainfile(filepath=model)
else:
    bpy.ops.import_scene.gltf(filepath=model)
scene = bpy.context.scene


def mat(name, color):
    m = bpy.data.materials.new(name)
    m.diffuse_color = color
    return m


red, dark, green = mat("grid", (1, 0.1, 0.1, 1)), mat("grid5", (0.6, 0, 0, 1)), mat("mark", (0, 1, 0.2, 1))


def line(a, b, thick, material):
    bpy.ops.mesh.primitive_cylinder_add(radius=thick, depth=(Vector(b) - Vector(a)).length, location=(Vector(a) + Vector(b)) / 2)
    o = bpy.context.active_object
    o.rotation_euler = (Vector(b) - Vector(a)).to_track_quat("Z", "Y").to_euler()
    o.data.materials.append(material)
    return o


grid = []
for i in range(-10, 11):
    v = i / 10
    m, t = (dark, 0.004) if i % 5 == 0 else (red, 0.0015)
    grid.append(line((-1.0, -0.6, v), (1.0, -0.6, v), t, m))    # horizontal (z) lines in front
    grid.append(line((v, -0.6, -1.0), (v, -0.6, 1.0), t, m))    # vertical (x) lines in front
    grid.append(line((0.7, -0.6, v), (0.7, 0.6, v), t, m))      # z lines on +X side plane
    grid.append(line((0.7, v * 0.6, -1.0), (0.7, v * 0.6, 1.0), t, m))  # y lines (every 0.06)
for name, p in markers.items():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.022, location=Vector(p))
    bpy.context.active_object.data.materials.append(green)

scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "MATERIAL"
scene.display.shading.show_xray = bool(markers)
scene.display.shading.xray_alpha = 0.6
scene.render.resolution_x = scene.render.resolution_y = 900
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(cam)
scene.camera = cam
cam.data.type = "ORTHO"
cam.data.ortho_scale = 2.1
for view, loc, rot in (("front", (0, -5, 0), (1.5708, 0, 0)), ("side", (5, 0, 0), (1.5708, 0, 1.5708))):
    cam.location = loc
    cam.rotation_euler = rot
    scene.render.filepath = f"{prefix}_{view}.png"
    bpy.ops.render.render(write_still=True)
    print("RENDERED", scene.render.filepath)
