"""Check (and fix) Miko's camera on this computer.

1. Makes sure OpenCV is installed for the Python that runs Miko.
2. Opens the webcam the same way Miko does.
3. Shows a live preview with the detected face and prints what Miko notices
   (you arrived, you waved). Press Q or close the window to finish.
Nothing is recorded or sent anywhere.
"""

import importlib
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def ensure_opencv() -> bool:
    try:
        import cv2  # noqa: F401
        print("OK  OpenCV", cv2.__version__)
        return True
    except ImportError:
        pass
    print("..  OpenCV is missing - installing opencv-python (one time)...")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "opencv-python>=4.9", "numpy"])
    if result.returncode != 0:
        print("XX  Install failed. Check the internet connection and run this again.")
        return False
    importlib.invalidate_caches()
    try:
        import cv2  # noqa: F401
        print("OK  OpenCV installed", cv2.__version__)
        return True
    except ImportError:
        print("XX  OpenCV still cannot be imported by", sys.executable)
        return False


CHECK_VERSION = "17.4.3"   # loads the face model from memory (Hebrew paths OK)


def main() -> int:
    print("Miko camera check", CHECK_VERSION)
    print("Folder:", HERE)
    print("Python:", sys.executable, sys.version.split()[0])
    if not ensure_opencv():
        return 1
    import cv2
    import miko_vision
    importlib.reload(miko_vision)
    print("OK  face model:", "YuNet" if os.path.isfile(miko_vision.YUNET_PATH) else "built-in (Haar)")
    print("..  opening the camera...")
    capture, description = miko_vision.VisionService.open_webcam()
    if capture is None:
        print("XX  No camera image.")
        print("    - Close apps that may use the camera (Teams, Zoom, Camera, browser tabs).")
        print("    - Windows Settings > Privacy & security > Camera:")
        print("      turn on 'Camera access' and 'Let desktop apps access your camera'.")
        print("    - With several cameras, set MIKO_CAMERA_INDEX=1 (or 2) and try again.")
        return 1
    print("OK ", description)
    engine = miko_vision.VisionEngine()
    print("\nLook at the camera, then wave hello beside your face. Press Q to finish.\n")
    seen_once = waved = False
    started = time.monotonic()
    try:
        while time.monotonic() - started < 120:
            ok, frame = capture.read()
            if not ok:
                continue
            for event in engine.process(frame):
                name = event["event"]
                print({"arrived": "**  I see you!", "left": "..  you left the frame",
                       "wave": "**  you waved - Miko waves back", "approached": "**  you came closer"}.get(name, name))
                seen_once |= name == "arrived"
                waved |= name == "wave"
            view = frame.copy()
            if engine.seen and engine.face is not None:
                h, w = view.shape[:2]
                f = engine.face
                x0, y0 = int((f.cx - f.w / 2) * w), int((f.cy - f.h / 2) * h)
                cv2.rectangle(view, (x0, y0), (x0 + int(f.w * w), y0 + int(f.h * h)), (80, 220, 120), 2)
            cv2.putText(view, "Miko camera check - Q to finish", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.imshow("Miko camera check", cv2.flip(view, 1))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27) or cv2.getWindowProperty("Miko camera check", cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        capture.release()
        cv2.destroyAllWindows()
    print()
    print("Face seen:", "yes" if seen_once else "no", "| wave detected:", "yes" if waved else "no")
    print("Camera works. Restart Miko (Stop Miko.cmd, then Start Miko.cmd) so it uses it.")
    return 0


if __name__ == "__main__":
    code = main()
    input("\nPress Enter to close...")
    raise SystemExit(code)
