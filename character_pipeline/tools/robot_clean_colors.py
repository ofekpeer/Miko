"""Replace the robot's noisy baked atlas with clean per-part vertex colors.

The supplied model's base-color atlas is low resolution with tiny, tightly
packed UV islands; mip-mapping blends black and white islands, which shows up
in-engine as white speckles and shimmer on the face rim and body. Each
triangle is sampled from the atlas, snapped to a small toy palette, cleaned
with a neighbour vote and baked into a smooth per-vertex "whiteness" (COLOR_0) that robot_body.gdshader
thresholds with anti-aliasing. The visor screen is rebuilt as a clean grid.

python robot_clean_colors.py <in.glb> <out.glb>   (needs numpy, pillow, pygltflib)
"""
import io
import sys

import numpy as np
from PIL import Image
from pygltflib import GLTF2, Accessor, BufferView

SRC, DST = sys.argv[1], sys.argv[2]
g = GLTF2().load(SRC)
blob = bytearray(g.binary_blob())

COMP = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read(index):
    acc = g.accessors[index]
    view = g.bufferViews[acc.bufferView]
    dtype = np.dtype(COMP[acc.componentType])
    n = NCOMP[acc.type]
    start = (view.byteOffset or 0) + (acc.byteOffset or 0)
    stride = view.byteStride or dtype.itemsize * n
    raw = np.frombuffer(bytes(blob[start:start + stride * acc.count]), dtype=np.uint8)
    raw = raw.reshape(acc.count, stride)[:, : dtype.itemsize * n]
    return raw.copy().view(dtype).reshape(acc.count, n) if n > 1 else raw.copy().view(dtype).reshape(acc.count)


def write(array, target=None, ctype=5126, normalized=False):
    data = np.ascontiguousarray(array)
    while len(blob) % 4:
        blob.append(0)
    offset = len(blob)
    blob.extend(data.tobytes())
    g.bufferViews.append(BufferView(buffer=0, byteOffset=offset, byteLength=data.nbytes, target=target))
    kind = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[1 if data.ndim == 1 else data.shape[1]]
    acc = Accessor(bufferView=len(g.bufferViews) - 1, componentType=ctype, count=len(data), type=kind,
                   normalized=normalized or None)
    if ctype == 5126 and kind == "VEC3":
        acc.min = data.min(0).tolist()
        acc.max = data.max(0).tolist()
    g.accessors.append(acc)
    return len(g.accessors) - 1


def image(index):
    img = g.images[g.textures[index].source]
    view = g.bufferViews[img.bufferView]
    start = view.byteOffset or 0
    return np.asarray(Image.open(io.BytesIO(bytes(blob[start:start + view.byteLength]))).convert("RGB"), np.float32) / 255.0


body_mesh = next(m for m in g.meshes if m.name == "RobotBody")
prim = body_mesh.primitives[0]
mat = g.materials[prim.material]
tex = image(mat.pbrMetallicRoughness.baseColorTexture.index)
H, W, _ = tex.shape

pos = read(prim.attributes.POSITION)
uv = read(prim.attributes.TEXCOORD_0)
tri = read(prim.indices).reshape(-1, 3).astype(np.int64)
print("vertices", len(pos), "triangles", len(tri))

# Sample each triangle at 7 interior points; take the median (robust to bleed).
bary = np.array([[1, 1, 1], [4, 1, 1], [1, 4, 1], [1, 1, 4], [2, 2, 1], [2, 1, 2], [1, 2, 2]], np.float32)
bary /= bary.sum(1, keepdims=True)
tri_uv = uv[tri]                                    # T,3,2
pts = np.einsum("sk,tkc->tsc", bary, tri_uv)       # T,S,2
px = np.clip((pts[..., 0] % 1.0) * W, 0, W - 1).astype(int)
py = np.clip((pts[..., 1] % 1.0) * H, 0, H - 1).astype(int)
samples = tex[py, px]                               # T,S,3
lum = samples @ np.array([0.2126, 0.7152, 0.0722], np.float32)
lum_med = np.median(lum, axis=1)
col_med = np.median(samples, axis=1)

# Toy palette: white shell, graphite joints, near-black rubber/visor rim, and
# the dark-teal ear/accent the source paints (kept if present).
PALETTE = np.array([
    [0.93, 0.93, 0.91],   # 0 white plastic
    [0.46, 0.47, 0.50],   # 1 mid grey trim
    [0.11, 0.115, 0.13],  # 2 graphite
    [0.035, 0.11, 0.16],  # 3 dark teal accent
], np.float32)
chroma = col_med.max(1) - col_med.min(1)
cls = np.where(lum_med > 0.46, 0, 2)   # white shell or graphite; no mid tone
teal = (col_med[:, 2] > col_med[:, 0] + 0.05) & (lum_med < 0.35) & (chroma > 0.05)
cls[teal] = 3
print("initial classes", np.bincount(cls, minlength=4))

# Triangle adjacency through shared positions (welds UV seams for the vote).
key = np.round(pos * 20000).astype(np.int64)
_, weld = np.unique(key, axis=0, return_inverse=True)
weld = weld.reshape(-1)
wt = weld[tri]
edges = np.concatenate([np.sort(wt[:, [0, 1]], 1), np.sort(wt[:, [1, 2]], 1), np.sort(wt[:, [2, 0]], 1)])
owner = np.tile(np.arange(len(tri)), 3)
order = np.lexsort((edges[:, 1], edges[:, 0]))
e_sorted, o_sorted = edges[order], owner[order]
same = np.all(e_sorted[1:] == e_sorted[:-1], axis=1)
a, b = o_sorted[:-1][same], o_sorted[1:][same]
pairs = np.concatenate([np.stack([a, b], 1), np.stack([b, a], 1)])

area = 0.5 * np.linalg.norm(np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]]), axis=1)
# Majority vote with neighbours, a few passes: removes isolated speckles.
for _ in range(7):
    votes = np.zeros((len(tri), 4), np.float64)
    np.add.at(votes, (np.arange(len(tri)), cls), 1.5)
    np.add.at(votes, (pairs[:, 0], cls[pairs[:, 1]]), 1.0)
    cls = votes.argmax(1)
# Small islands of one class surrounded by another become their neighbour.
for _ in range(3):
    parent = np.arange(len(tri))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    same_cls = pairs[cls[pairs[:, 0]] == cls[pairs[:, 1]]]
    for u, v in same_cls[same_cls[:, 0] < same_cls[:, 1]]:
        ru, rv = find(u), find(v)
        if ru != rv:
            parent[ru] = rv
    roots = np.array([find(i) for i in range(len(tri))])
    comp_area = np.bincount(roots, weights=area, minlength=len(tri))
    small = comp_area[roots] < 6e-4
    if not small.any():
        break
    votes = np.zeros((len(tri), 4))
    big_pairs = pairs[~small[pairs[:, 1]]]
    np.add.at(votes, (big_pairs[:, 0], cls[big_pairs[:, 1]]), 1.0)
    fix = small & (votes.sum(1) > 0)
    cls[fix] = votes[fix].argmax(1)
print("final classes", np.bincount(cls, minlength=4))

# Per-vertex "whiteness" (1 white shell, 0 graphite) averaged over the welded
# neighbourhood and relaxed a little. The body shader thresholds the
# interpolated value with screen-space anti-aliasing, so part boundaries are
# smooth curves instead of triangle zig-zags.
tone = np.where(cls == 0, 1.0, np.where(cls == 1, 0.55, 0.0))
nweld = weld.max() + 1
acc_tone = np.zeros(nweld)
acc_w = np.zeros(nweld)
for k in range(3):
    np.add.at(acc_tone, wt[:, k], tone * area)
    np.add.at(acc_w, wt[:, k], area)
wtone = acc_tone / np.maximum(acc_w, 1e-12)
wedges = np.unique(np.concatenate([np.sort(wt[:, [0, 1]], 1), np.sort(wt[:, [1, 2]], 1), np.sort(wt[:, [2, 0]], 1)]), axis=0)
deg = np.bincount(wedges.ravel(), minlength=nweld).astype(np.float64)
for _ in range(2):
    s = np.zeros(nweld)
    np.add.at(s, wedges[:, 0], wtone[wedges[:, 1]])
    np.add.at(s, wedges[:, 1], wtone[wedges[:, 0]])
    wtone = 0.5 * wtone + 0.5 * s / np.maximum(deg, 1)

# Smooth shading: the decimated mesh has noisy normals that sparkle under
# highlights. Average each welded vertex's face normals within a crease angle.
fn = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
nrm = read(attrs.NORMAL).astype(np.float64) if (attrs := prim.attributes).NORMAL is not None else None
acc_n = np.zeros((nweld, 3))
for k in range(3):
    np.add.at(acc_n, wt[:, k], fn)
smooth_n = acc_n / np.maximum(np.linalg.norm(acc_n, axis=1, keepdims=True), 1e-12)
cand = smooth_n[weld]
keep = np.einsum("ij,ij->i", cand, nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)) > np.cos(np.radians(40))
new_n = np.where(keep[:, None], cand, nrm).astype(np.float32)
new_n /= np.maximum(np.linalg.norm(new_n, axis=1, keepdims=True), 1e-12)

colors = np.ones((len(pos), 4), np.float32)
colors[:, 0] = colors[:, 1] = colors[:, 2] = wtone[weld]
new_attrs = {"NORMAL": write(new_n, 34962), "COLOR_0": write(colors, 34962)}
for name, idx in new_attrs.items():
    setattr(attrs, name, idx)
attrs.TEXCOORD_0 = None
attrs.TANGENT = None

mat.pbrMetallicRoughness.baseColorTexture = None
mat.pbrMetallicRoughness.metallicRoughnessTexture = None
mat.pbrMetallicRoughness.baseColorFactor = [1, 1, 1, 1]
mat.pbrMetallicRoughness.metallicFactor = 0.0
mat.pbrMetallicRoughness.roughnessFactor = 0.45
mat.normalTexture = None
mat.name = "RobotPlastic"
# Rebuild the visor screen as a clean, regular grid. The split-out screen
# followed the painted outline, so it was ragged, with hairline cracks where
# the room showed through. The new sheet samples the old surface as a height
# field over the visor's planar FaceUV, extends slightly under the helmet rim
# and sits 1 cm proud of the old surface (no z-fighting).
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

screen = next(m for m in g.meshes if m.name == "RobotVisorScreen").primitives[0]
spos = read(screen.attributes.POSITION).astype(np.float64)
lo, hi = spos.min(0), spos.max(0)
lin = LinearNDInterpolator(spos[:, :2], spos[:, 2])
near = NearestNDInterpolator(spos[:, :2], spos[:, 2])
NX, NY, M = 72, 54, 0.035
us = np.linspace(-M, 1 + M, NX)
vs = np.linspace(-M, 1 + M, NY)
U, V = np.meshgrid(us, vs)
X = lo[0] + U * (hi[0] - lo[0])
Y = hi[1] - V * (hi[1] - lo[1])
Z = lin(X, Y)
Z = np.where(np.isnan(Z), near(X, Y), Z)
# Light smoothing removes the scan-like lumps of the source surface.
from scipy.ndimage import gaussian_filter
Z = gaussian_filter(Z, 1.6, mode="nearest")
grid = np.stack([X, Y, Z], -1)
dx = np.gradient(grid, axis=1)
dy = np.gradient(grid, axis=0)
gn = np.cross(dy, dx)
gn /= np.linalg.norm(gn, axis=-1, keepdims=True)
gn *= np.sign(gn[..., 2:3])                 # face forward (+Z)
grid = grid + gn * 0.010
idx = np.arange(NX * NY).reshape(NY, NX)
q = np.stack([idx[:-1, :-1], idx[1:, :-1], idx[1:, 1:], idx[:-1, 1:]], -1).reshape(-1, 4)
faces = np.concatenate([q[:, [0, 1, 2]], q[:, [0, 2, 3]]]).astype(np.uint32)
nv = NX * NY
jt = g.accessors[screen.attributes.JOINTS_0].componentType
head = [g.nodes[j].name for j in g.skins[0].joints].index("head")
joints = np.zeros((nv, 4), {5121: np.uint8, 5123: np.uint16}[jt]); joints[:, 0] = head
weights = np.zeros((nv, 4), np.float32); weights[:, 0] = 1.0
screen.attributes.POSITION = write(grid.reshape(-1, 3).astype(np.float32), 34962)
screen.attributes.NORMAL = write(gn.reshape(-1, 3).astype(np.float32), 34962)
screen.attributes.TEXCOORD_0 = write(np.stack([U, V], -1).reshape(-1, 2).astype(np.float32), 34962)
screen.attributes.JOINTS_0 = write(joints, 34962, jt)
screen.attributes.WEIGHTS_0 = write(weights, 34962)
screen.indices = write(faces.reshape(-1), 34963, 5125)

g.textures = []
g.images = []
g.samplers = []

# Repack: copy only the accessors still referenced into a fresh buffer.
used = set()
for m in g.meshes:
    for p in m.primitives:
        used.update(v for v in vars(p.attributes).values() if isinstance(v, int))
        used.add(p.indices)
for s in g.skins:
    if s.inverseBindMatrices is not None:
        used.add(s.inverseBindMatrices)
for an in g.animations:
    for smp in an.samplers:
        used.update([smp.input, smp.output])
old_blob = blob
packed = bytearray()
views = []
remap = {}
for i in sorted(used):
    acc = g.accessors[i]
    view = g.bufferViews[acc.bufferView]
    dtype = np.dtype(COMP[acc.componentType])
    n = NCOMP[acc.type]
    width = dtype.itemsize * n
    start = (view.byteOffset or 0) + (acc.byteOffset or 0)
    stride = view.byteStride or width
    raw = np.frombuffer(bytes(old_blob[start:start + stride * (acc.count - 1) + width]), np.uint8)
    rows = np.lib.stride_tricks.as_strided(raw, (acc.count, width), (stride, 1)).copy()
    while len(packed) % 4:
        packed.append(0)
    views.append(BufferView(buffer=0, byteOffset=len(packed), byteLength=rows.nbytes, target=view.target))
    packed.extend(rows.tobytes())
    acc.bufferView = len(views) - 1
    acc.byteOffset = None
accessor_map = {old: new for new, old in enumerate(sorted(used))}
g.accessors = [g.accessors[i] for i in sorted(used)]
g.bufferViews = views
for m in g.meshes:
    for p in m.primitives:
        for k, v in vars(p.attributes).items():
            if isinstance(v, int):
                setattr(p.attributes, k, accessor_map[v])
        p.indices = accessor_map[p.indices]
for s in g.skins:
    if s.inverseBindMatrices is not None:
        s.inverseBindMatrices = accessor_map[s.inverseBindMatrices]
for an in g.animations:
    for smp in an.samplers:
        smp.input, smp.output = accessor_map[smp.input], accessor_map[smp.output]
g.buffers[0].byteLength = len(packed)
g.set_binary_blob(bytes(packed))
g.save(DST)
print("wrote", DST, len(packed), "bytes")
