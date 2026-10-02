"""Render quick turnaround previews of a GLB and print mesh statistics.

blender -b --python inspect_model.py -- <model.glb> <out_prefix> [workbench|eevee]
"""

import math
import os
import sys

import bmesh
import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
model, prefix = os.path.abspath(args[0]), os.path.abspath(args[1])
engine = args[2] if len(args) > 2 else "workbench"

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=model)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]

corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
center, size = (lo + hi) / 2, hi - lo
print(f"BOUNDS lo={tuple(round(v, 3) for v in lo)} hi={tuple(round(v, 3) for v in hi)} size={tuple(round(v, 3) for v in size)}")
for o in meshes:
    bm = bmesh.new()
    bm.from_mesh(o.data)
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    print(f"MESH {o.name}: verts={len(bm.verts)} faces={len(bm.faces)} non_manifold_edges={non_manifold} "
          f"materials={[m.name for m in o.data.materials]} uv_layers={len(o.data.uv_layers)}")
    bm.free()

scene = bpy.context.scene
scene.render.resolution_x = scene.render.resolution_y = 640
scene.render.film_transparent = False
if engine == "eevee":
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items] else "BLENDER_EEVEE"
    world = bpy.data.worlds.new("w"); scene.world = world; world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.8, 0.8, 0.82, 1)
    sun = bpy.data.objects.new("key", bpy.data.lights.new("key", "SUN")); scene.collection.objects.link(sun)
    sun.data.energy = 3; sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(-30))
else:
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "TEXTURE" if any(o.data.uv_layers for o in meshes) else "MATERIAL"
    scene.display.shading.show_cavity = True

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(cam)
scene.camera = cam
cam.data.lens = 70
radius = max(size) * 3.2
# glTF imports Y-up as Blender Z-up; the generated character faces -Y.
views = {"front": 0, "threequarter": 35, "side": 90, "back": 180}
for name, yaw in views.items():
    angle = math.radians(yaw)
    cam.location = center + Vector((radius * math.sin(angle), -radius * math.cos(angle), size.z * 0.08))
    direction = center - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{prefix}_{name}.png"
    bpy.ops.render.render(write_still=True)
    print("RENDERED", scene.render.filepath)
