"""Pose the rig and render, to verify skin deformation.

blender -b --python pose_test.py -- <stage4.blend> <out_prefix> <pose_name>
"""

import math
import os
import sys

import bpy
from mathutils import Euler, Vector

args = sys.argv[sys.argv.index("--") + 1:]
blend, prefix, pose_name = os.path.abspath(args[0]), os.path.abspath(args[1]), args[2]
bpy.ops.wm.open_mainfile(filepath=blend)
scene = bpy.context.scene
rig = bpy.data.objects["MikoRig"]
P = rig.pose.bones

POSES = {
    "tilt_droop_wag": {
        "head": (0, 14, 0), "ear.L.1": (32, 0, 0), "ear.L.2": (20, 0, 0),
        "ear.R.1": (-12, 0, 0), "tail.1": (0, 0, 38), "tail.2": (0, 0, 25),
        "arm.R": (-55, 0, 0), "lid_up.L": (60, 0, 0), "lid_up.R": (60, 0, 0),
    },
    "happy_bounce": {
        "body": (-6, 0, 0), "head": (-8, 0, -10), "ear.L.1": (-18, 0, 0), "ear.R.1": (-18, 0, 0),
        "arm.L": (-70, 0, 0), "arm.R": (-70, 0, 0), "tail.1": (0, 0, -30),
        "lid_low.L": (-40, 0, 0), "lid_low.R": (-40, 0, 0),
    },
}
for name, (rx, ry, rz) in POSES[pose_name].items():
    P[name].rotation_mode = "XYZ"
    P[name].rotation_euler = Euler((math.radians(rx), math.radians(ry), math.radians(rz)))
bpy.context.view_layer.update()

engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 600
scene.view_settings.view_transform = "AgX"
world = bpy.data.worlds.new("w"); scene.world = world; world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.62, 0.64, 0.68, 1)
for n, e, rot in (("key", 3.2, (55, 0, -35)), ("rim", 2.0, (120, 0, 160))):
    light = bpy.data.objects.new(n, bpy.data.lights.new(n, "SUN")); light.data.energy = e
    light.rotation_euler = [math.radians(x) for x in rot]; scene.collection.objects.link(light)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam)
scene.camera = cam; cam.data.lens = 85
target = Vector((0.0, 0.0, 0.5))
for view, yaw in (("front", 0), ("threequarter", 35)):
    a = math.radians(yaw)
    cam.location = target + Vector((4.2 * math.sin(a), -4.2 * math.cos(a), 0.35))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{prefix}_{pose_name}_{view}.png"
    bpy.ops.render.render(write_still=True)
    print("RENDERED", scene.render.filepath)
