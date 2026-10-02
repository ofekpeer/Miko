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
    import miko_deps
    sight = miko_deps.ensure_vision_packages()
    importlib.invalidate_caches()
    try:
        import cv2  # noqa: F401
    except ImportError:
        print("XX  OpenCV cannot be imported by", sys.executable)
        return False
    print("OK  OpenCV", cv2.__version__, "| MediaPipe:", "yes" if sight == "mediapipe" else "no (basic sight)")
    return True


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
    print("OK  perception:", engine.detector)
    print("\nTry: smile, laugh, nod, shake your head, wink, thumbs up, peace sign, wave,")
    print("cover the lens, look away and back, yawn. Press Q to finish.\n")
    seen_once = waved = False
    frames = 0
    gui = True               # opencv-python-headless has no preview window
    last_report = 0.0
    started = time.monotonic()
    try:
        while time.monotonic() - started < (120 if gui else 30):
            ok, frame = capture.read()
            if not ok:
                continue
            for event in engine.process(frame):
                name = event["event"]
                print({"arrived": "**  I see you!", "left": "..  you left the frame",
                       "wave": "**  you waved - Miko waves back", "approached": "**  you came closer",
                       "covered": "**  camera covered - Miko can't see", "uncovered": "**  peekaboo - Miko sees again",
                       "shaken": "**  the camera shook - Miko wobbles", "light_changed": "**  the light changed",
                       "motion": "**  something moved", "scene_changed": "**  something in the room changed",
                       "looked_at_miko": "**  you looked at Miko", "looked_away": "..  you looked away",
                       "smiled": "**  you smiled", "laughing": "**  you laughed", "yawned": "**  you yawned",
                       "surprised": "**  you look surprised", "frowned": "**  you look sad", "winked": "**  you winked",
                       "eyes_closed": "..  your eyes are closed", "eyes_opened": "..  eyes open again",
                       "nodded": "**  you nodded yes", "shook_head": "**  you shook your head no",
                       "tilted_head": "**  you tilted your head", "looked_somewhere": "..  you looked somewhere else",
                       "someone_joined": "**  someone joined you", "someone_left": "..  someone left"}.get(name, name)
                      + (f" ({event['gesture']})" if name == "gesture" else ""))
                seen_once |= name == "arrived"
                waved |= name == "wave"
            now = time.monotonic()
            if not gui and now - last_report >= 2.0:
                last_report = now
                summary = engine.summary()
                print("    face:", (summary.get("where", "") + ", " + summary.get("distance", "")) if summary["seen"] else "not visible",
                      "| %ds left" % max(0, 30 - int(now - started)))
            if not gui:
                continue
            view = frame.copy()
            if engine.seen and engine.face is not None:
                h, w = view.shape[:2]
                f = engine.face
                x0, y0 = int((f.cx - f.w / 2) * w), int((f.cy - f.h / 2) * h)
                cv2.rectangle(view, (x0, y0), (x0 + int(f.w * w), y0 + int(f.h * h)), (80, 220, 120), 2)
            cv2.putText(view, "Miko camera check - Q to finish", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            try:
                cv2.imshow("Miko camera check", cv2.flip(view, 1))
                key = cv2.waitKey(1) & 0xFF
            except cv2.error:
                gui = False
                started = time.monotonic()
                print("..  (no preview window in this OpenCV build - checking in text mode for 30 seconds)")
                continue
            frames += 1
            # Closing the preview window also finishes (checked once it is up).
            closed = frames > 15 and cv2.getWindowProperty("Miko camera check", cv2.WND_PROP_VISIBLE) < 1
            if key in (ord("q"), ord("Q"), 27) or closed:
                break
    finally:
        capture.release()
        if gui:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass
    print()
    print("Face seen:", "yes" if seen_once else "no", "| wave detected:", "yes" if waved else "no")
    print("Camera works. Restart Miko (Stop Miko.cmd, then Start Miko.cmd) so it uses it.")
    return 0


class _Tee:
    """Mirror everything printed into check_miko_camera_log.txt (easy to send)."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        self.stream.write(text)
        self.log.write(text)
        self.log.flush()

    def flush(self):
        self.stream.flush()


if __name__ == "__main__":
    import traceback
    code = 1
    try:
        log = open(os.path.join(HERE, "check_miko_camera_log.txt"), "w", encoding="utf-8")
        sys.stdout = _Tee(sys.stdout, log)
        sys.stderr = _Tee(sys.stderr, log)
    except OSError:
        pass
    try:
        code = main()
    except BaseException:
        print("\nXX  The camera check stopped with an error:")
        traceback.print_exc()
    print("\nA copy of this text is saved in check_miko_camera_log.txt")
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass
    raise SystemExit(code)
