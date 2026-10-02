"""Locate painted eyes, nose and mouth in the concept image (dark blobs).

blender -b --python find_face.py -- <concept.png>
Prints blob centroids/sizes in image pixels (x right, y from top).
"""

import os
import sys
from collections import deque

import bpy
import numpy as np

path = os.path.abspath(sys.argv[sys.argv.index("--") + 1])
img = bpy.data.images.load(path)
W, H = img.size
pix = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)[::-1, :, :3]  # row 0 = top
dark = pix.max(axis=2) < 0.33

# Face region: middle of the image, between the ears and the arms.
y0, y1, x0, x1 = int(0.28 * H), int(0.48 * H), int(0.30 * W), int(0.72 * W)
seen = np.zeros_like(dark)
blobs = []
for y in range(y0, y1):
    for x in range(x0, x1):
        if dark[y, x] and not seen[y, x]:
            queue, cells = deque([(y, x)]), []
            seen[y, x] = True
            while queue:
                cy, cx = queue.popleft()
                cells.append((cy, cx))
                for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                    if y0 <= ny < y1 and x0 <= nx < x1 and dark[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
            if len(cells) >= 12:
                ys, xs = np.array(cells).T
                blobs.append((len(cells), xs.mean(), ys.mean(), xs.min(), xs.max(), ys.min(), ys.max()))
for area, cx, cy, xmin, xmax, ymin, ymax in sorted(blobs, key=lambda b: -b[0]):
    print(f"BLOB area={area:5d} center=({cx:6.1f},{cy:6.1f}) x=[{xmin},{xmax}] y=[{ymin},{ymax}]")
