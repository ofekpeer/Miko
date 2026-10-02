"""List the robot's loose parts (rigid pieces) with position and size.

blender -b --python robot_islands.py -- <model.glb>
Coordinates after import: Blender Z-up, character faces -Y.
"""

import os
import sys

import bmesh
import bpy
import numpy as np

path = os.path.abspath(sys.argv[sys.argv.index("--") + 1])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=path)
parts = []
for obj in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
    mw = obj.matrix_world
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    seen = set()
    for v in bm.verts:
        if v.index in seen:
            continue
        stack, island = [v], []
        seen.add(v.index)
        while stack:
            cur = stack.pop()
            island.append(cur.index)
            for e in cur.link_edges:
                o = e.other_vert(cur)
                if o.index not in seen:
                    seen.add(o.index)
                    stack.append(o)
        co = np.array([(mw @ bm.verts[i].co)[:] for i in island])
        parts.append((obj.name, len(island), co.min(0), co.max(0), co.mean(0)))
    bm.free()
print("PARTS", len(parts))
for name, n, lo, hi, c in sorted(parts, key=lambda p: -p[4][2]):
    if n < 30:
        continue
    print(f"{name:9s} n={n:6d} center=({c[0]:+.3f},{c[1]:+.3f},{c[2]:+.3f}) "
          f"x[{lo[0]:+.2f},{hi[0]:+.2f}] y[{lo[1]:+.2f},{hi[1]:+.2f}] z[{lo[2]:+.2f},{hi[2]:+.2f}]")
small = [p for p in parts if p[1] < 30]
print("SMALL parts (<30 verts):", len(small))
