"""Pose the robot rig with world-axis rotations and render (deformation check).

blender -b --python robot_pose_test.py -- <robot.blend> <out_prefix>
"""

import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

args = sys.argv[sys.argv.index("--") + 1:]
blend, prefix = os.path.abspath(args[0]), os.path.abspath(args[1])

POSES = {
    "wave": [("upperarm.R", "Y", -125), ("forearm.R", "Y", -35), ("head", "Y", 8), ("head", "Z", 12)],
    "walk": [("thigh.L", "X", -30), ("shin.L", "X", 25), ("thigh.R", "X", 22), ("shin.R", "X", 10),
             ("upperarm.L", "X", 20), ("upperarm.R", "X", -25), ("spine", "Z", 6)],
    "think": [("upperarm.L", "X", -70), ("forearm.L", "X", -95), ("head", "X", -10), ("head", "Y", -12)],
}


def pose(rig, name, axis, degrees):
    pb = rig.pose.bones[name]
    h = pb.head.copy()                                   # armature space
    r = Matrix.Rotation(math.radians(degrees), 4, axis)
    pb.matrix = Matrix.Translation(h) @ r @ Matrix.Translation(-h) @ pb.matrix
    bpy.context.view_layer.update()


for pose_name, moves in POSES.items():
    bpy.ops.wm.open_mainfile(filepath=blend)
    scene = bpy.context.scene
    rig = bpy.data.objects["RobotRig"]
    for name, axis, deg in moves:
        pose(rig, name, axis, deg)
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = scene.render.resolution_y = 520
    scene.view_settings.view_transform = "AgX"
    world = bpy.data.worlds.new("w"); scene.world = world; world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.55, 0.58, 0.62, 1)
    for n, e, rot in (("key", 3.0, (55, 0, -35)), ("rim", 2.0, (120, 0, 160))):
        light = bpy.data.objects.new(n, bpy.data.lights.new(n, "SUN")); light.data.energy = e
        light.rotation_euler = [math.radians(x) for x in rot]; scene.collection.objects.link(light)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam)
    scene.camera = cam; cam.data.lens = 70
    target = Vector((0, 0, 0.5))
    a = math.radians(30)
    cam.location = target + Vector((3.2 * math.sin(a), -3.2 * math.cos(a), 0.3))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{prefix}_{pose_name}.png"
    bpy.ops.render.render(write_still=True)
    print("RENDERED", scene.render.filepath)
