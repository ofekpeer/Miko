"""Stage 1: normalize, decimate and UV-unwrap the generated shape.

blender -b --python stage1_prepare.py -- <shape.glb> <out.blend> [target_faces]

Result: one object "MikoBody", facing -Y, feet on z=0, height 1.0, smooth
shaded, with a UV map ready for baking.
"""

import os
import sys

import bmesh
import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
source, target = os.path.abspath(args[0]), os.path.abspath(args[1])
target_faces = int(args[2]) if len(args) > 2 else 40000

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=source)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
for o in bpy.context.scene.objects:
    o.select_set(o in meshes)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
body = bpy.context.view_layer.objects.active
bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
for o in list(bpy.context.scene.objects):
    if o is not body:
        bpy.data.objects.remove(o, do_unlink=True)
body.name = body.data.name = "MikoBody"

# Normalize: feet on z=0, centered in x/y, height 1.0.
coords = [v.co for v in body.data.vertices]
lo = Vector((min(c.x for c in coords), min(c.y for c in coords), min(c.z for c in coords)))
hi = Vector((max(c.x for c in coords), max(c.y for c in coords), max(c.z for c in coords)))
scale = 1.0 / (hi.z - lo.z)
offset = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
for v in body.data.vertices:
    v.co = (v.co - offset) * scale
print(f"NORMALIZE scale={scale:.5f} offset={tuple(round(x, 4) for x in offset)}")

before = len(body.data.polygons)
mod = body.modifiers.new("decimate", "DECIMATE")
mod.ratio = min(1.0, target_faces / before)
mod.use_collapse_triangulate = True
bpy.ops.object.modifier_apply(modifier=mod.name)
print(f"DECIMATE faces {before} -> {len(body.data.polygons)}")

bm = bmesh.new()
bm.from_mesh(body.data)
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
loose = [v for v in bm.verts if not v.link_faces]
bmesh.ops.delete(bm, geom=loose, context="VERTS")
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
bm.to_mesh(body.data)
bm.free()
print(f"CLEAN non_manifold_edges={non_manifold}")

bpy.ops.object.shade_smooth()

# UV unwrap for baking.
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.004, area_weight=0.0, scale_to_bounds=True)
bpy.ops.object.mode_set(mode="OBJECT")
print(f"UV layers={[uv.name for uv in body.data.uv_layers]}")

bpy.ops.wm.save_as_mainfile(filepath=target)
print("SAVED", target)
