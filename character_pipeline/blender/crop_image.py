"""Crop a region of an image (fractions, origin top-left) and save PNG.
blender -b --python crop_image.py -- <in.png> <out.png> x0 y0 x1 y1"""
import bpy, sys, os, numpy as np
a = sys.argv[sys.argv.index("--") + 1:]
img = bpy.data.images.load(os.path.abspath(a[0])); W, H = img.size
p = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)[::-1]
x0, y0, x1, y1 = (float(v) for v in a[2:6])
c = p[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)][::-1]
out = bpy.data.images.new("crop", c.shape[1], c.shape[0], alpha=True)
out.pixels.foreach_set(c.ravel()); out.filepath_raw = os.path.abspath(a[1]); out.file_format = "PNG"; out.save()
print("CROPPED", c.shape)
