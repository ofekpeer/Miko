"""Render lit turnaround previews of a .blend character with Eevee.

blender -b --python render_blend.py -- <file.blend> <out_prefix> [views=front,threequarter,side,back] [size=640]
"""

import math
import os
import sys

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
blend, prefix = os.path.abspath(args[0]), os.path.abspath(args[1])
views = (args[2] if len(args) > 2 else "front,threequarter,side,back").split(",")
size = int(args[3]) if len(args) > 3 else 640

bpy.ops.wm.open_mainfile(filepath=blend)
scene = bpy.context.scene
for o in [o for o in scene.objects if o.type in {"CAMERA", "LIGHT"}]:
    bpy.data.objects.remove(o, do_unlink=True)

meshes = [o for o in scene.objects if o.type == "MESH" and o.visible_get()]
corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
center, extent = (lo + hi) / 2, hi - lo

engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = size
scene.view_settings.view_transform = "AgX" if "AgX" in [i.identifier for i in scene.view_settings.bl_rna.properties["view_transform"].enum_items] else "Filmic"
world = bpy.data.worlds.new("preview"); scene.world = world; world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.62, 0.64, 0.68, 1); bg.inputs[1].default_value = 0.9


def light(name, kind, energy, rot, color=(1, 1, 1), size_=1.0):
    data = bpy.data.lights.new(name, kind); data.energy = energy; data.color = color
    if kind == "AREA":
        data.size = size_
    obj = bpy.data.objects.new(name, data); scene.collection.objects.link(obj)
    obj.rotation_euler = [math.radians(x) for x in rot]
    return obj


key = light("key", "SUN", 3.2, (55, 0, -35), (1.0, 0.96, 0.9))
rim = light("rim", "SUN", 2.0, (120, 0, 160), (0.8, 0.9, 1.0))

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(cam); scene.camera = cam
cam.data.lens = 85
radius = max(extent) * 3.6
angles = {"front": 0, "threequarter": 35, "side": 90, "back": 180, "left": -40}
for name in views:
    yaw = math.radians(angles[name])
    cam.location = center + Vector((radius * math.sin(yaw), -radius * math.cos(yaw), extent.z * 0.12))
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{prefix}_{name}.png"
    bpy.ops.render.render(write_still=True)
    print("RENDERED", scene.render.filepath)
