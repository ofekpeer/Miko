"""Close-up renders of face states to verify eyes, lids and mouth shapes.

blender -b --python render_face_states.py -- <stage3.blend> <face_parts.json> <out_prefix> [yaw_deg]
"""

import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

args = sys.argv[sys.argv.index("--") + 1:]
blend, parts_path, prefix = (os.path.abspath(a) for a in args[:3])
yaw = math.radians(float(args[3])) if len(args) > 3 else 0.0
parts = json.loads(open(parts_path, encoding="utf-8").read())

bpy.ops.wm.open_mainfile(filepath=blend)
scene = bpy.context.scene
engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 560
scene.view_settings.view_transform = "AgX"
world = bpy.data.worlds.new("preview"); scene.world = world; world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.62, 0.64, 0.68, 1)
for name, energy, rot, color in (("key", 3.2, (55, 0, -35), (1, 0.96, 0.9)), ("rim", 2.0, (120, 0, 160), (0.8, 0.9, 1))):
    light = bpy.data.objects.new(name, bpy.data.lights.new(name, "SUN"))
    light.data.energy, light.data.color = energy, color
    light.rotation_euler = [math.radians(x) for x in rot]
    scene.collection.objects.link(light)

eyes = parts["eyes"]
face_center = (Vector(eyes["L"]["center"]) + Vector(eyes["R"]["center"])) / 2 + Vector((0, 0, -0.04))
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam)
scene.camera = cam
cam.data.lens = 85
cam.location = face_center + Vector((0.95 * math.sin(yaw), -0.95 * math.cos(yaw), 0.05))
cam.rotation_euler = (face_center - cam.location).to_track_quat("-Z", "Y").to_euler()

lids = {o.name: o for o in scene.objects if o.name.startswith("MikoLid")}
eyeballs = {o.name: o for o in scene.objects if o.name.startswith("MikoEye_")}
mouth = scene.objects["MikoMouth"]


def pose(lid_up=0.0, lid_low=0.0, mouth_keys=None, look=(0.0, 0.0)):
    """lid_* in 0..1 of the closing rotation; look = (yaw, pitch) degrees."""
    for name, obj in lids.items():
        side = name[-1]
        info = eyes[side]
        center, right = Vector(info["center"]), Vector(info["right"])
        if "Up" in name:
            angle = math.radians(parts.get('lid_open_deg', 96)) * lid_up
        else:
            angle = -math.radians(parts.get('lid_low_open_deg', 62)) * lid_low
        rot = Matrix.Translation(center) @ Matrix.Rotation(angle, 4, right) @ Matrix.Translation(-center)
        obj.matrix_world = rot
    for name, obj in eyeballs.items():
        info = eyes[name[-1]]
        center, up, right = Vector(info["center"]), Vector(info["up"]), Vector(info["right"])
        rot = Matrix.Rotation(math.radians(look[0]), 4, up) @ Matrix.Rotation(math.radians(look[1]), 4, right)
        obj.matrix_world = Matrix.Translation(center) @ rot @ Matrix.Translation(-center)
    for key in mouth.data.shape_keys.key_blocks[1:]:
        key.value = (mouth_keys or {}).get(key.name, 0.0)


states = {
    "neutral": {},
    "blink": {"lid_up": 1.0, "lid_low": 0.35},
    "sleepy": {"lid_up": 0.55},
    "happy": {"lid_low": 0.75, "mouth_keys": {"smile": 1.0}},
    "talk_a": {"mouth_keys": {"open": 1.0}},
    "talk_o": {"mouth_keys": {"round": 1.0}},
    "talk_e": {"mouth_keys": {"wide": 1.0}},
    "look_left": {"look": (-25, 0)},
}
wanted = os.environ.get("STATES", ",".join(states)).split(",")
for name in wanted:
    pose(**states[name])
    scene.render.filepath = f"{prefix}_{name}.png"
    bpy.ops.render.render(write_still=True)
    print("RENDERED", scene.render.filepath)
