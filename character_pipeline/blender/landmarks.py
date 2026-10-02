"""Find skeleton landmarks on MikoBody (shared by stage4_rig and its debug render).

All coordinates are Blender space: character faces -Y, feet on z=0, height 1.
"L"/"R" are screen sides (-X / +X), matching the eye naming of stage 3.
"""

import numpy as np


def _mean(points):
    return points.mean(axis=0) if len(points) else None


def find_landmarks(verts: np.ndarray, face_center_x: float) -> dict:
    x, y, z = verts[:, 0], verts[:, 1], verts[:, 2]
    cx = face_center_x
    # Body depth center: midpoint of the central column's front and back surfaces
    # (the face is denser in vertices, so a median would drift forward).
    column = verts[(np.abs(x - cx) < 0.10) & (z > 0.30) & (z < 0.70)]
    front_y, back_y = float(column[:, 1].min()), float(column[:, 1].max())
    cy = (front_y + back_y) / 2
    lm = {"center": np.array([cx, cy, 0.0])}
    lm["crown"] = np.array([cx, cy, float(z[np.abs(x - cx) < 0.06].max())])

    for side, sign in (("L", -1), ("R", 1)):
        ear = verts[(sign * (x - cx) > 0.12) & (z > 0.80)]
        tip = ear[np.argmax(ear[:, 2])]
        base = _mean(ear[ear[:, 2] < 0.84])
        lm[f"ear_{side}_base"], lm[f"ear_{side}_tip"] = base, tip
        lm[f"ear_{side}_mid"] = base + (tip - base) * 0.5

        feet = verts[(sign * (x - cx) > 0.02) & (z < 0.05)]
        fx = float(np.median(feet[:, 0]))
        lm[f"foot_{side}_ankle"] = np.array([fx, float(np.median(feet[:, 1])) + 0.03, 0.13])
        lm[f"foot_{side}_toe"] = np.array([fx, float(feet[:, 1].min()) + 0.02, 0.02])

        # Paw resting on the belly: center of the forward-most vertices on this side.
        paw = verts[(y < front_y + 0.02) & (z > 0.38) & (z < 0.54) & (sign * (x - cx) > 0.08) & (sign * (x - cx) < 0.30)]
        hand = _mean(paw) if len(paw) else np.array([cx + sign * 0.18, front_y - 0.05, 0.46])
        lm[f"arm_{side}_hand"] = hand
        lm[f"arm_{side}_shoulder"] = np.array([cx + sign * 0.22, front_y + 0.12, 0.53])

    # Tail: the fluffy lobe low on the +X side (screen right in the concept),
    # outside the body's own width. Tip = its farthest part from the body axis;
    # root sits inside the body, most of the way back toward the axis.
    body_half_width = 0.26
    tail = verts[(x - cx > body_half_width) & (z < 0.36)]
    axis_point = np.array([cx, cy, 0.18])
    order = np.argsort(np.linalg.norm(tail - axis_point, axis=1))
    tip = tail[order[-max(8, len(order) // 10):]].mean(axis=0)
    near = tail[order[:max(8, len(order) // 10)]].mean(axis=0)
    root = near + (np.array([cx, cy, near[2]]) - near) * 0.45
    lm["tail_base"], lm["tail_tip"] = root, tip
    lm["tail_mid"] = root + (tip - root) * 0.5

    lm["pelvis"] = np.array([cx, cy, 0.14])
    lm["chest"] = np.array([cx, cy, 0.46])
    lm["head_top"] = np.array([cx, cy, 0.86])
    return lm
