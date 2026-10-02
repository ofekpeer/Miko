"""Stage 2: color the body from the concept image and bake a texture.

blender -b --python stage2_texture.py -- <stage1.blend> <concept.png> <out.blend> <out_texture.png> [size]

* Aligns the concept's silhouette to the mesh's front silhouette (IoU search).
* Front-facing, unoccluded surface takes the concept pixels directly
  (projection inside the bake shader, so the texture keeps the image detail).
* Hidden/back surface gets plausible colors: back-facing seeds use the
  concept's orange fur color, everything else is diffused over the mesh from
  reliable front colors.
* Bakes the result into one UV texture and assigns a fur-like material.
"""

import os
import sys

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

args = sys.argv[sys.argv.index("--") + 1:]
blend_in, concept_path, blend_out, texture_out = (os.path.abspath(a) for a in args[:4])
tex_size = int(args[4]) if len(args) > 4 else 2048

bpy.ops.wm.open_mainfile(filepath=blend_in)
body = bpy.data.objects["MikoBody"]
mesh = body.data

# ---------------------------------------------------------------- concept image
concept = bpy.data.images.load(concept_path)
W, H = concept.size
pix = np.array(concept.pixels[:], dtype=np.float32).reshape(H, W, 4)[:, :, :3]  # row 0 = bottom
r, g, b = pix[..., 0], pix[..., 1], pix[..., 2]
warm = (r - b) > 0.045
cool = (b - r) > 0.045
dark = pix.max(axis=2) < 0.45
fg = warm | cool | dark
# Remove isolated specks: keep rows/columns with enough foreground.
rows = np.where(fg.sum(axis=1) > 3)[0]
cols = np.where(fg.sum(axis=0) > 3)[0]
uy0, uy1, ux0, ux1 = rows.min(), rows.max(), cols.min(), cols.max()
print(f"CONCEPT size={W}x{H} fg_bbox x=[{ux0},{ux1}] y_from_bottom=[{uy0},{uy1}]")

orange_mask = fg & ((r - b) > 0.30) & (r > 0.6)
orange = np.median(pix[orange_mask], axis=0)
cream_mask = fg & ((r - b) > 0.045) & ((r - b) < 0.16) & (pix.min(axis=2) > 0.70)
cream = np.median(pix[cream_mask], axis=0) if cream_mask.any() else np.array([0.95, 0.9, 0.82])
print(f"COLORS orange={np.round(orange, 3)} cream={np.round(cream, 3)}")

# ---------------------------------------------------------------- mesh silhouette
verts = np.array([v.co[:] for v in mesh.vertices], dtype=np.float64)
normals = np.array([v.normal[:] for v in mesh.vertices], dtype=np.float64)
bvh = BVHTree.FromObject(body, bpy.context.evaluated_depsgraph_get())
mx0, mx1 = verts[:, 0].min(), verts[:, 0].max()
mz0, mz1 = verts[:, 2].min(), verts[:, 2].max()
G = 160
gx = np.linspace(mx0 - 0.05, mx1 + 0.05, G)
gz = np.linspace(mz0 - 0.05, mz1 + 0.05, G)
sil = np.zeros((G, G), dtype=bool)
for i, z in enumerate(gz):
    for j, x in enumerate(gx):
        sil[i, j] = bvh.ray_cast(Vector((x, -5.0, z)), Vector((0, 1, 0)))[0] is not None

a0 = (uy1 - uy0) / (mz1 - mz0)          # pixels per unit (vertical extent)
ucx, ucy = (ux0 + ux1) / 2, (uy0 + uy1) / 2
mcx, mcz = (mx0 + mx1) / 2, (mz0 + mz1) / 2
XX, ZZ = np.meshgrid(gx, gz)
best = (-1.0, None)
for k in np.arange(0.90, 1.101, 0.01):
    for dx in np.arange(-0.05, 0.0501, 0.005):
        for dz in np.arange(-0.05, 0.0501, 0.005):
            px = np.rint(a0 * k * (XX - mcx) + ucx + dx * a0).astype(int)
            py = np.rint(a0 * k * (ZZ - mcz) + ucy + dz * a0).astype(int)
            inside = (px >= 0) & (px < W) & (py >= 0) & (py < H)
            img = np.zeros_like(sil)
            img[inside] = fg[py[inside], px[inside]]
            union = (img | sil).sum()
            iou = (img & sil).sum() / union if union else 0
            if iou > best[0]:
                best = (iou, (k, dx, dz))
iou, (k, dx, dz) = best
A = a0 * k
BX = ucx + dx * a0 - A * mcx      # U_px = A*x + BX
BZ = ucy + dz * a0 - A * mcz      # V_px(from bottom) = A*z + BZ
print(f"ALIGN iou={iou:.3f} k={k:.2f} dx={dx:.3f} dz={dz:.3f} A={A:.2f} BX={BX:.1f} BZ={BZ:.1f}")

# ---------------------------------------------------------------- per-vertex weights
facing = -normals[:, 1]                       # 1 = faces the concept camera (-Y)
visible = np.zeros(len(verts), dtype=bool)
for i, (co, n) in enumerate(zip(verts, normals)):
    origin = Vector(co) + Vector(n) * 1e-4 + Vector((0, -1e-4, 0))
    visible[i] = bvh.ray_cast(origin, Vector((0, -1, 0)))[0] is None


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


front_w = smoothstep(0.10, 0.40, facing) * visible

U = A * verts[:, 0] + BX
V = A * verts[:, 2] + BZ
ui = np.clip(np.rint(U).astype(int), 0, W - 1)
vi = np.clip(np.rint(V).astype(int), 0, H - 1)
projected = pix[vi, ui]

# Diffuse reliable colors into hidden regions; back faces are seeded with fur color.
known = front_w > 0.6
colors = np.where(known[:, None], projected, 0.0)
back = facing < -0.25
colors[back] = orange
fixed = known | back
edges = np.array([e.vertices[:] for e in mesh.edges], dtype=np.int64)
n_vert = len(verts)
degree = np.bincount(edges.ravel(), minlength=n_vert).astype(np.float64)
filled = fixed.copy()
for _ in range(400):
    sums = np.zeros((n_vert, 3))
    np.add.at(sums, edges[:, 0], colors[edges[:, 1]] * filled[edges[:, 1], None])
    np.add.at(sums, edges[:, 1], colors[edges[:, 0]] * filled[edges[:, 0], None])
    counts = np.zeros(n_vert)
    np.add.at(counts, edges[:, 0], filled[edges[:, 1]])
    np.add.at(counts, edges[:, 1], filled[edges[:, 0]])
    update = ~fixed & (counts > 0)
    colors[update] = sums[update] / counts[update, None]
    filled |= update
print(f"WEIGHTS visible={visible.mean():.2f} front={(front_w > 0.5).mean():.2f} back={back.mean():.2f} unfilled={(~filled).sum()}")
colors[~filled] = orange
# Concept pixels are sRGB-encoded; color attributes are read as linear.
colors = np.where(colors <= 0.04045, colors / 12.92, ((colors + 0.055) / 1.055) ** 2.4)

# Store as attributes for the bake shader.
for name in ("fallback", "front_w"):
    if name in mesh.color_attributes:
        mesh.color_attributes.remove(mesh.color_attributes[name])
fallback_attr = mesh.color_attributes.new("fallback", "FLOAT_COLOR", "POINT")
fallback_attr.data.foreach_set("color", np.hstack([colors, np.ones((n_vert, 1))]).astype(np.float32).ravel())
weight_attr = mesh.color_attributes.new("front_w", "FLOAT_COLOR", "POINT")
weight_attr.data.foreach_set("color", np.repeat(front_w[:, None], 4, axis=1).astype(np.float32).ravel())

# ---------------------------------------------------------------- bake material
mat = bpy.data.materials.new("MikoBake")
mat.use_nodes = True
nt = mat.node_tree
nt.nodes.clear()
N = nt.nodes.new
coord = N("ShaderNodeTexCoord")
sep = N("ShaderNodeSeparateXYZ")
nt.links.new(coord.outputs["Object"], sep.inputs[0])


def affine(socket, scale, offset):
    mul = N("ShaderNodeMath"); mul.operation = "MULTIPLY_ADD"
    nt.links.new(socket, mul.inputs[0]); mul.inputs[1].default_value = scale; mul.inputs[2].default_value = offset
    return mul.outputs[0]


u = affine(sep.outputs["X"], A / W, BX / W)
v = affine(sep.outputs["Z"], A / H, BZ / H)
comb = N("ShaderNodeCombineXYZ")
nt.links.new(u, comb.inputs["X"]); nt.links.new(v, comb.inputs["Y"])
tex = N("ShaderNodeTexImage"); tex.image = concept; tex.extension = "EXTEND"; tex.interpolation = "Cubic"
nt.links.new(comb.outputs[0], tex.inputs["Vector"])
fallback_node = N("ShaderNodeVertexColor"); fallback_node.layer_name = "fallback"
weight_node = N("ShaderNodeVertexColor"); weight_node.layer_name = "front_w"
# Hidden/back surface: livelier fur color plus a fine strand-like noise so it
# matches the painted fur detail of the projected front.
hsv = N("ShaderNodeHueSaturation")
hsv.inputs["Saturation"].default_value = 1.18
hsv.inputs["Value"].default_value = 1.06
nt.links.new(fallback_node.outputs["Color"], hsv.inputs["Color"])
strands = N("ShaderNodeMapping")
strands.inputs["Scale"].default_value = (90.0, 90.0, 26.0)
nt.links.new(coord.outputs["Object"], strands.inputs["Vector"])
noise = N("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = 1.0
noise.inputs["Detail"].default_value = 8.0
noise.inputs["Roughness"].default_value = 0.65
nt.links.new(strands.outputs["Vector"], noise.inputs["Vector"])
ramp = N("ShaderNodeMapRange")
ramp.inputs["From Min"].default_value = 0.3; ramp.inputs["From Max"].default_value = 0.7
ramp.inputs["To Min"].default_value = 0.86; ramp.inputs["To Max"].default_value = 1.08
nt.links.new(noise.outputs["Fac"], ramp.inputs["Value"])
furred = N("ShaderNodeMix"); furred.data_type = "RGBA"; furred.blend_type = "MULTIPLY"
furred.inputs["Factor"].default_value = 1.0
nt.links.new(hsv.outputs["Color"], furred.inputs[6])
nt.links.new(ramp.outputs["Result"], furred.inputs[7])
mix = N("ShaderNodeMix"); mix.data_type = "RGBA"
nt.links.new(weight_node.outputs["Color"], mix.inputs["Factor"])
nt.links.new(furred.outputs[2], mix.inputs[6])
nt.links.new(tex.outputs["Color"], mix.inputs[7])
emit = N("ShaderNodeEmission")
nt.links.new(mix.outputs[2], emit.inputs["Color"])
out = N("ShaderNodeOutputMaterial")
nt.links.new(emit.outputs[0], out.inputs["Surface"])
baked = bpy.data.images.new("MikoBody_BaseColor", tex_size, tex_size, alpha=False, float_buffer=False)
target_node = N("ShaderNodeTexImage"); target_node.image = baked
nt.nodes.active = target_node
mesh.materials.clear()
mesh.materials.append(mat)

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 4
scene.render.bake.margin = 12
bpy.context.view_layer.objects.active = body
body.select_set(True)
bpy.ops.object.bake(type="EMIT", margin=12, use_clear=True)
baked.filepath_raw = texture_out
baked.file_format = "PNG"
baked.save()
print("BAKED", texture_out)

# ---------------------------------------------------------------- final material
final = bpy.data.materials.new("MikoFur")
final.use_nodes = True
bsdf = final.node_tree.nodes["Principled BSDF"]
img_node = final.node_tree.nodes.new("ShaderNodeTexImage")
img_node.image = bpy.data.images.load(texture_out)
final.node_tree.links.new(img_node.outputs["Color"], bsdf.inputs["Base Color"])
bsdf.inputs["Roughness"].default_value = 0.85
if "Sheen Weight" in bsdf.inputs:
    bsdf.inputs["Sheen Weight"].default_value = 0.6
    bsdf.inputs["Sheen Roughness"].default_value = 0.4
mesh.materials.clear()
mesh.materials.append(final)
bpy.ops.wm.save_as_mainfile(filepath=blend_out)
print("SAVED", blend_out)
