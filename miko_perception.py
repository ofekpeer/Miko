"""Rich perception for Miko with MediaPipe (optional): expressions, head
gestures, gaze, hand gestures and people count.

``MediaPipePerception`` wraps Google's Face Landmarker (478-point mesh with
52 expression blendshapes and head pose) and Gesture Recognizer (hand
landmarks + 7 gestures). ``FaceAnalyzer``, ``HandAnalyzer`` and
``CrowdAnalyzer`` turn those per-frame measurements into human-level events
with timing and hysteresis, so a fleeting twitch is not a "smile" and one
smile is one event, not thirty.

Everything is local and in memory. Without MediaPipe, miko_vision.py keeps
using its OpenCV engine and these analyzers are simply not used.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import os
from typing import Any

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:  # Optional dependency.
    import mediapipe as mp
    from mediapipe.tasks import python as mp_tasks
    from mediapipe.tasks.python import vision as mp_vision
except Exception:  # pragma: no cover - machines without MediaPipe
    mp = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FACE_MODEL = os.path.join(BASE_DIR, "vision_models", "face_landmarker.task")
GESTURE_MODEL = os.path.join(BASE_DIR, "vision_models", "gesture_recognizer.task")

GESTURE_NAMES = {
    "Thumb_Up": "thumbs_up", "Thumb_Down": "thumbs_down", "Victory": "peace",
    "Open_Palm": "open_palm", "Pointing_Up": "pointing", "Closed_Fist": "fist", "ILoveYou": "love",
}


def available() -> bool:
    return mp is not None and np is not None and os.path.isfile(FACE_MODEL)


# ---------------------------------------------------------------- data

@dataclass
class FaceObs:
    cx: float                     # 0..1 image coordinates
    cy: float
    w: float
    h: float
    yaw: float = 0.0              # degrees, + = the person turns to THEIR left (image right)
    pitch: float = 0.0            # degrees, + = looking up
    roll: float = 0.0             # degrees, + = head tilted toward their left shoulder
    blend: dict = field(default_factory=dict)

    def b(self, name: str) -> float:
        return float(self.blend.get(name, 0.0))


@dataclass
class HandObs:
    gesture: str                  # Miko name ('open_palm', ...) or ''
    score: float
    wrist: tuple                  # (x, y) 0..1
    index_tip: tuple
    index_base: tuple
    handedness: str = ""


@dataclass
class Observation:
    faces: list = field(default_factory=list)
    hands: list = field(default_factory=list)


# ---------------------------------------------------------------- MediaPipe

def _euler_from_matrix(matrix) -> tuple[float, float, float]:
    """Yaw, pitch, roll (degrees) from MediaPipe's facial transformation."""
    r = np.asarray(matrix, dtype=np.float64)[:3, :3]
    sy = math.hypot(r[0, 0], r[1, 0])
    pitch = math.degrees(math.atan2(r[2, 1], r[2, 2]))
    yaw = math.degrees(math.atan2(-r[2, 0], sy))
    roll = math.degrees(math.atan2(r[1, 0], r[0, 0]))
    return yaw, -pitch, roll


class MediaPipePerception:
    """Runs the two MediaPipe tasks on BGR frames (VIDEO mode)."""

    def __init__(self, face_model: str = FACE_MODEL, gesture_model: str = GESTURE_MODEL) -> None:
        if mp is None:
            raise RuntimeError("MediaPipe is not installed")
        # Models are passed as bytes: MediaPipe, like OpenCV, cannot open
        # files under non-ASCII Windows paths (e.g. a Hebrew Desktop name).
        with open(face_model, "rb") as handle:
            face_bytes = handle.read()
        self.face = mp_vision.FaceLandmarker.create_from_options(mp_vision.FaceLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_buffer=face_bytes),
            running_mode=mp_vision.RunningMode.VIDEO, num_faces=4,
            output_face_blendshapes=True, output_facial_transformation_matrixes=True,
            min_face_detection_confidence=0.5, min_tracking_confidence=0.5))
        self.hands = None
        if os.path.isfile(gesture_model):
            with open(gesture_model, "rb") as handle:
                gesture_bytes = handle.read()
            self.hands = mp_vision.GestureRecognizer.create_from_options(mp_vision.GestureRecognizerOptions(
                base_options=mp_tasks.BaseOptions(model_asset_buffer=gesture_bytes),
                running_mode=mp_vision.RunningMode.VIDEO, num_hands=2,
                min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))
        self._last_ms = -1
        self._frame = 0
        self.hand_every = 1           # raised automatically on slow machines

    def close(self) -> None:
        for task in (self.face, self.hands):
            try:
                if task is not None:
                    task.close()
            except Exception:
                pass

    def process(self, frame_bgr, now: float) -> Observation:
        import cv2
        ms = max(self._last_ms + 1, int(now * 1000))
        self._last_ms = ms
        self._frame += 1
        h, w = frame_bgr.shape[:2]
        scale = 480.0 / max(w, 1)
        small = cv2.resize(frame_bgr, (480, max(1, int(round(h * scale))))) if w > 480 else frame_bgr
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        obs = Observation()
        result = self.face.detect_for_video(image, ms)
        for index, landmarks in enumerate(result.face_landmarks or []):
            xs = [p.x for p in landmarks]
            ys = [p.y for p in landmarks]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            face = FaceObs((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0)
            if result.facial_transformation_matrixes and index < len(result.facial_transformation_matrixes):
                face.yaw, face.pitch, face.roll = _euler_from_matrix(result.facial_transformation_matrixes[index])
            if result.face_blendshapes and index < len(result.face_blendshapes):
                face.blend = {c.category_name: float(c.score) for c in result.face_blendshapes[index]}
            obs.faces.append(face)
        if self.hands is not None and self._frame % self.hand_every == 0:
            hands = self.hands.recognize_for_video(image, ms)
            for index, landmarks in enumerate(hands.hand_landmarks or []):
                gesture, score = "", 0.0
                if hands.gestures and index < len(hands.gestures) and hands.gestures[index]:
                    top = hands.gestures[index][0]
                    gesture, score = GESTURE_NAMES.get(top.category_name, ""), float(top.score)
                handed = ""
                if hands.handedness and index < len(hands.handedness) and hands.handedness[index]:
                    handed = hands.handedness[index][0].category_name
                obs.hands.append(HandObs(gesture, score, (landmarks[0].x, landmarks[0].y),
                                         (landmarks[8].x, landmarks[8].y), (landmarks[5].x, landmarks[5].y), handed))
        obs.faces.sort(key=lambda f: -f.w)            # nearest (largest) first
        return obs


# ---------------------------------------------------------------- analyzers

class _Hold:
    """True after a condition held for `on` seconds; false after it has been
    gone for `off` seconds (hysteresis against flicker)."""

    def __init__(self, on: float, off: float) -> None:
        self.on, self.off = on, off
        self.state = False
        self._since: float | None = None

    def update(self, condition: bool, now: float) -> bool | None:
        """Returns True on rising edge, False on falling edge, else None."""
        if condition != self.state:
            self._since = self._since if self._since is not None else now
            if now - self._since >= (self.on if condition else self.off):
                self.state = condition
                self._since = None
                return condition
        else:
            self._since = None
        return None


class _Oscillation:
    def __init__(self, window: float) -> None:
        self.window = window
        self.samples: deque = deque(maxlen=90)

    def add(self, t: float, v: float) -> None:
        self.samples.append((t, v))

    def swings(self, now: float, amplitude: float) -> int:
        """Direction reversals whose peak-to-peak exceeds `amplitude`."""
        values = [v for t, v in self.samples if now - t <= self.window]
        if len(values) < 4:
            return 0
        swings, direction, extreme = 0, 0, values[0]
        for v in values[1:]:
            if direction >= 0 and v > extreme:
                extreme = v
                direction = 1 if direction == 0 and v - values[0] > amplitude / 2 else direction
            elif direction <= 0 and v < extreme:
                extreme = v
                direction = -1 if direction == 0 and values[0] - v > amplitude / 2 else direction
            if direction == 1 and extreme - v >= amplitude:
                swings, direction, extreme = swings + 1, -1, v
            elif direction == -1 and v - extreme >= amplitude:
                swings, direction, extreme = swings + 1, 1, v
        return swings

    def clear(self) -> None:
        self.samples.clear()


class FaceAnalyzer:
    """Expressions, head gestures and attention of the main person."""

    def __init__(self) -> None:
        self.smile = _Hold(0.35, 0.8)
        self.laugh = _Hold(0.3, 0.9)
        self.yawn = _Hold(1.0, 0.6)
        self.surprise = _Hold(0.2, 0.8)
        self.frown = _Hold(0.8, 1.0)
        self.eyes_closed = _Hold(2.0, 0.4)
        self.tilt = _Hold(0.7, 0.6)
        self.looking = _Hold(0.6, 1.2)
        self.looking.state = True
        self.away_dir = _Hold(1.2, 0.5)
        self._nod = _Oscillation(1.6)
        self._shake = _Oscillation(1.6)
        self._jaw = _Oscillation(1.2)
        self._wink_start: float | None = None
        self._wink_side = ""
        self._last: dict[str, float] = {}
        self.expression = "neutral"
        self.away_direction = ""

    def _cool(self, name: str, now: float, seconds: float) -> bool:
        if now - self._last.get(name, -1e9) < seconds:
            return False
        self._last[name] = now
        return True

    def reset(self) -> None:
        self.__init__()

    def update(self, f: FaceObs, now: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        smile = (f.b("mouthSmileLeft") + f.b("mouthSmileRight")) / 2
        jaw = f.b("jawOpen")
        blink_l, blink_r = f.b("eyeBlinkLeft"), f.b("eyeBlinkRight")
        brows_up = f.b("browInnerUp")
        eyes_wide = (f.b("eyeWideLeft") + f.b("eyeWideRight")) / 2
        frown = (f.b("mouthFrownLeft") + f.b("mouthFrownRight")) / 2
        brows_down = (f.b("browDownLeft") + f.b("browDownRight")) / 2
        squint = (f.b("eyeSquintLeft") + f.b("eyeSquintRight")) / 2
        self._jaw.add(now, jaw)

        laughing = smile > 0.45 and (jaw > 0.25 or self._jaw.swings(now, 0.12) >= 2)
        edge = self.laugh.update(laughing, now)
        if edge and self._cool("laughing", now, 6.0):
            out.append({"event": "laughing"})
        edge = self.smile.update(smile > 0.5, now)
        if edge and not self.laugh.state and self._cool("smiled", now, 8.0):
            out.append({"event": "smiled"})
        yawning = jaw > 0.55 and smile < 0.3 and (squint > 0.25 or (blink_l + blink_r) / 2 > 0.3 or brows_up < 0.3)
        edge = self.yawn.update(yawning, now)
        if edge and self._cool("yawned", now, 15.0):
            out.append({"event": "yawned"})
        surprised = (brows_up > 0.45 and eyes_wide > 0.3) or (brows_up > 0.55 and 0.15 < jaw < 0.55 and smile < 0.3)
        edge = self.surprise.update(surprised and not yawning, now)
        if edge and self._cool("surprised", now, 6.0):
            out.append({"event": "surprised"})
        edge = self.frown.update((frown > 0.35 or (brows_down > 0.5 and smile < 0.1)) and smile < 0.2, now)
        if edge and self._cool("frowned", now, 12.0):
            out.append({"event": "frowned"})
        closed = blink_l > 0.6 and blink_r > 0.6
        edge = self.eyes_closed.update(closed, now)
        if edge is True:
            out.append({"event": "eyes_closed"})
        elif edge is False and self._cool("eyes_opened", now, 3.0):
            out.append({"event": "eyes_opened"})
        # Wink: one eye shut briefly while the other stays open.
        one = (blink_l > 0.65 and blink_r < 0.35) or (blink_r > 0.65 and blink_l < 0.35)
        if one and self._wink_start is None:
            self._wink_start, self._wink_side = now, "left" if blink_l > blink_r else "right"
        elif not one and self._wink_start is not None:
            duration = now - self._wink_start
            self._wink_start = None
            if 0.12 <= duration <= 0.8 and not closed and self._cool("winked", now, 4.0):
                out.append({"event": "winked"})

        # Head gestures from pose over time.
        self._nod.add(now, f.pitch)
        self._shake.add(now, f.yaw)
        nod_swings = self._nod.swings(now, 7.0)
        shake_swings = self._shake.swings(now, 9.0)
        if nod_swings >= 2 and shake_swings == 0 and self._cool("nodded", now, 3.0):
            self._nod.clear()
            out.append({"event": "nodded"})
        elif shake_swings >= 2 and nod_swings == 0 and self._cool("shook_head", now, 3.0):
            self._shake.clear()
            out.append({"event": "shook_head"})
        edge = self.tilt.update(abs(f.roll) > 14.0, now)
        if edge and self._cool("tilted_head", now, 8.0):
            out.append({"event": "tilted_head", "side": "left" if f.roll > 0 else "right"})

        # Attention: facing the camera (head pose) and eyes not turned away.
        look_out = max(f.b("eyeLookOutLeft"), f.b("eyeLookOutRight"), f.b("eyeLookInLeft"), f.b("eyeLookInRight"))
        facing = abs(f.yaw) < 18 and abs(f.pitch) < 16 and look_out < 0.6
        edge = self.looking.update(facing, now)
        if edge is True and self._cool("looked_at_miko", now, 5.0):
            out.append({"event": "looked_at_miko"})
        elif edge is False and self._cool("looked_away", now, 5.0):
            out.append({"event": "looked_away"})
        direction = ""
        if not facing:
            if abs(f.yaw) >= abs(f.pitch):
                # Their left is the image right; report it from the viewer's side.
                direction = "right" if f.yaw > 0 else "left"
            else:
                direction = "up" if f.pitch > 0 else "down"
        edge = self.away_dir.update(bool(direction), now)
        if edge and direction and self._cool("looked_somewhere", now, 6.0):
            out.append({"event": "looked_somewhere", "direction": direction})
        self.away_direction = direction

        if self.laugh.state:
            self.expression = "laughing"
        elif self.smile.state:
            self.expression = "smiling"
        elif self.yawn.state or self.eyes_closed.state:
            self.expression = "sleepy"
        elif self.surprise.state:
            self.expression = "surprised"
        elif self.frown.state:
            self.expression = "sad"
        else:
            self.expression = "neutral"
        return out


class HandAnalyzer:
    """Stable hand gestures, landmark-based waves and pointing direction."""

    def __init__(self) -> None:
        self._gesture = ""
        self._gesture_since = 0.0
        self._last: dict[str, float] = {}
        self._wrist = _Oscillation(2.0)
        self._open_recent = -1e9

    def update(self, hands: list, now: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        best = max(hands, key=lambda h: h.score) if hands else None
        gesture = best.gesture if best is not None and best.score >= 0.6 else ""
        if gesture != self._gesture:
            self._gesture, self._gesture_since = gesture, now
        elif gesture and now - self._gesture_since >= 0.35 and now - self._last.get(gesture, -1e9) > 4.0:
            self._last[gesture] = now
            event = {"event": "gesture", "gesture": gesture}
            if gesture == "pointing" and best is not None:
                # Where the index finger points, from the viewer's side.
                dx = best.index_tip[0] - best.index_base[0]
                event["x"] = round(max(-1.0, min(1.0, -dx * 8.0)), 2)
            out.append(event)
        # Wave: an open hand swinging sideways (works even when the
        # recognizer calls a blurry hand "None").
        if best is not None:
            if best.gesture in ("open_palm", "") or best.score < 0.6:
                self._wrist.add(now, best.wrist[0])
            if best.gesture == "open_palm":
                self._open_recent = now
            if (self._wrist.swings(now, 0.06) >= 2 and now - self._last.get("wave", -1e9) > 4.0):
                self._last["wave"] = now
                self._wrist.clear()
                out.append({"event": "wave", "hand": best.handedness.lower() or "unknown", "source": "hand"})
        return out


class CrowdAnalyzer:
    """Someone else joins or leaves the owner."""

    def __init__(self) -> None:
        self.count = 0
        self._candidate = 0
        self._since = 0.0

    def update(self, faces: int, now: float) -> list[dict[str, Any]]:
        if faces != self._candidate:
            self._candidate, self._since = faces, now
            return []
        if faces != self.count and now - self._since >= 1.2:
            before, self.count = self.count, faces
            if before >= 1 and faces > before:
                return [{"event": "someone_joined", "people": faces}]
            if before >= 2 and faces < before and faces >= 1:
                return [{"event": "someone_left", "people": faces}]
        return []
