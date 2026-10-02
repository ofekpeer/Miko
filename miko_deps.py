"""Install Miko's optional sight packages for the Python that runs Miko.

MediaPipe (expressions, hand gestures, gaze) brings opencv-contrib-python.
Two different OpenCV wheels side by side share one "cv2" folder and can
break each other, so the plain/headless wheels are removed first. Without
internet or MediaPipe, plain OpenCV still gives Miko basic sight.
"""

from __future__ import annotations

import subprocess
import sys


def _can_import(module: str) -> bool:
    # A fresh interpreter: the current one may hold a stale half-installed cv2.
    probe = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True)
    return probe.returncode == 0


def _pip(*args: str) -> bool:
    return subprocess.run([sys.executable, "-m", "pip", *args], capture_output=True, text=True).returncode == 0


def ensure_vision_packages(log=print) -> str:
    """Returns 'mediapipe', 'opencv' or 'none' (what Miko will see with)."""
    if _can_import("mediapipe") and _can_import("cv2"):
        return "mediapipe"
    log("Installing Miko's sight (MediaPipe + OpenCV), one time, a minute or two...")
    _pip("uninstall", "-y", "opencv-python", "opencv-python-headless")
    if _pip("install", "--user", "--upgrade", "mediapipe>=1.0", "opencv-contrib-python") \
            and _can_import("mediapipe") and _can_import("cv2"):
        return "mediapipe"
    log("MediaPipe could not be installed now; installing basic OpenCV sight instead.")
    if not _can_import("cv2"):
        _pip("install", "--user", "--force-reinstall", "opencv-contrib-python")
    return "opencv" if _can_import("cv2") else "none"


if __name__ == "__main__":
    print("Miko sight:", ensure_vision_packages())
