"""Miko's local sight: notices the owner, where they are, and when they wave.

Frames come from the PC webcam today and from the paired device's camera
later (device/bridge.py feeds JPEG frames through ``VisionService.feed_jpeg``).
Everything runs in memory on this computer: frames are never stored, logged
or sent to OpenAI. Only small derived facts leave this module, e.g.
``{"type": "vision", "seen": True, "x": -0.3, ...}`` and events such as
``wave``, ``arrived``, ``left`` and ``approached``.

Dependencies: ``opencv-python`` and ``numpy``. Without them the service stays
off and Miko works as before. Face detection uses OpenCV's YuNet model
(vision_models/face_detection_yunet_2023mar.onnx, MIT); if the file is
missing a Haar cascade bundled with OpenCV is used instead.

Waves are detected without a hand model: dense optical flow beside and above
the face, looking for repeated left-right reversals while the face itself
stays fairly still. That is cheap enough for a laptop and for the
low-resolution frames a small device camera can send.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import math
import os
import threading
import time
from typing import Any, Callable

try:  # Optional richer perception (MediaPipe): expressions, gestures, gaze.
    import miko_perception
except Exception:  # pragma: no cover
    miko_perception = None

try:  # Optional: the rest of Miko must keep working without OpenCV.
    import cv2
    import numpy as np
except Exception:  # pragma: no cover - exercised on machines without OpenCV
    cv2 = None
    np = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YUNET_PATH = os.path.join(BASE_DIR, "vision_models", "face_detection_yunet_2023mar.onnx")
SETTINGS_PATH = os.path.join(BASE_DIR, "miko_vision_settings.json")

PROCESS_WIDTH = 320            # detection frame width
FLOW_WIDTH = 160               # optical-flow frame width
PRESENT_AFTER = 2              # consecutive face frames before "seen"
ABSENT_AFTER_S = 3.5           # no face for this long -> "left"
ARRIVE_AFTER_ABSENCE_S = 8.0   # re-greet only after a real absence
WAVE_WINDOW_S = 2.2
WAVE_MIN_REVERSALS = 2         # left-right-left is a wave
WAVE_COOLDOWN_S = 4.0
STATE_INTERVAL_S = 0.2         # vision state broadcast rate (5 Hz)


def available() -> bool:
    return cv2 is not None and np is not None


@dataclass
class _Face:
    cx: float                  # normalized image coords 0..1
    cy: float
    w: float                   # width / frame width
    h: float
    score: float
    frontal: bool


class _Oscillation:
    """Counts direction reversals of a signed velocity within a time window."""

    def __init__(self, window: float) -> None:
        self.window = window
        self.samples: deque = deque(maxlen=60)   # (t, v)

    def add(self, t: float, v: float) -> None:
        self.samples.append((t, v))

    def reversals(self, now: float, threshold: float) -> int:
        signs = []
        for t, v in self.samples:
            if now - t <= self.window and abs(v) >= threshold:
                sign = 1 if v > 0 else -1
                if not signs or signs[-1] != sign:
                    signs.append(sign)
        return max(0, len(signs) - 1)

    def clear(self) -> None:
        self.samples.clear()


class VisionEngine:
    """Turns frames into presence, position, gesture and scene-change events.

    Events: arrived, left, approached, wave, covered, uncovered, shaken,
    light_changed, motion. Pure computation, no I/O.
    """

    def __init__(self, model_path: str = YUNET_PATH, use_mediapipe: bool = True) -> None:
        if not available():
            raise RuntimeError("OpenCV is not installed")
        self._yunet = None
        self._haar = None
        if os.path.isfile(model_path) and hasattr(cv2, "FaceDetectorYN"):
            # Load from memory: OpenCV cannot open file paths with non-ASCII
            # characters on Windows (e.g. a Hebrew Desktop folder name).
            try:
                with open(model_path, "rb") as handle:
                    model = np.frombuffer(handle.read(), np.uint8)
                self._yunet = cv2.FaceDetectorYN.create("onnx", model, np.array([], np.uint8),
                                                        (PROCESS_WIDTH, 240), 0.72, 0.3, 5)
            except (cv2.error, OSError, TypeError) as error:
                print("MIKO VISION: YuNet unavailable, using the built-in face detector:", type(error).__name__)
                self._yunet = None
        if self._yunet is None:
            self._haar = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
        self.detector = "yunet" if self._yunet is not None else "haar"
        self.mp = None
        if use_mediapipe and miko_perception is not None and miko_perception.available():
            try:
                self.mp = miko_perception.MediaPipePerception()
                self.detector = "mediapipe"
            except Exception as error:
                print("MIKO VISION: MediaPipe unavailable, using OpenCV:", type(error).__name__)
        self.face_analyzer = miko_perception.FaceAnalyzer() if self.mp else None
        self.hand_analyzer = miko_perception.HandAnalyzer() if self.mp else None
        self.crowd = miko_perception.CrowdAnalyzer() if self.mp else None
        self.face_obs = None
        self.people = 0
        self._cost: deque = deque(maxlen=30)
        self.seen = False
        self.face: _Face | None = None
        self._smooth: _Face | None = None
        self._streak = 0
        self._last_face_at = -1e9
        self._absent_since = -1e9
        self._ever_seen = False
        self._size_hist: deque = deque(maxlen=30)
        self._last_approach = -1e9
        # Scene / motion state.
        self._prev_gray = None
        self._wave = _Oscillation(WAVE_WINDOW_S)
        self._shake = _Oscillation(1.4)
        self._last_wave = -1e9
        self._last_shake = -1e9
        self._last_motion = -1e9
        self._last_light = -1e9
        self._face_change_at = -1e9
        self._hann = None
        self._bg = None
        self._scene_since: float | None = None
        self._last_scene = -1e9
        self._quiet = True
        self._frontal_since: float | None = None
        self._away_since: float | None = None
        self._looking = None              # None unknown, True at Miko, False away
        self._last_attention = -1e9
        self._jolts: deque = deque(maxlen=20)
        self._had_face = False
        self.covered = False
        self._dark_since: float | None = None
        self._bright_since: float | None = None
        self._light_hist: deque = deque(maxlen=40)    # (t, mean brightness)
        self.events: deque = deque(maxlen=16)  # (t, name, detail)

    # ------------------------------------------------------------ detection
    def _detect(self, small) -> list[_Face]:
        h, w = small.shape[:2]
        faces: list[_Face] = []
        if self._yunet is not None:
            self._yunet.setInputSize((w, h))
            _, rows = self._yunet.detect(small)
            for row in rows if rows is not None else []:
                x, y, fw, fh = (float(v) for v in row[:4])
                # Landmarks: right eye, left eye, nose, mouth corners.
                rex, lex, nx = float(row[4]), float(row[6]), float(row[8])
                span = max(abs(lex - rex), 1.0)
                frontal = abs((nx - (rex + lex) / 2.0) / span) < 0.28
                faces.append(_Face((x + fw / 2) / w, (y + fh / 2) / h, fw / w, fh / h, float(row[14]), frontal))
        else:
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            for (x, y, fw, fh) in self._haar.detectMultiScale(gray, 1.15, 5, minSize=(24, 24)):
                faces.append(_Face((x + fw / 2) / w, (y + fh / 2) / h, fw / w, fh / h, 0.8, True))
        return faces

    def _pick(self, faces: list[_Face]) -> _Face | None:
        if not faces:
            return None
        if self._smooth is None:
            return max(faces, key=lambda f: f.w)
        # Stay with the same person: nearest to the last position, then size.
        s = self._smooth
        return min(faces, key=lambda f: (f.cx - s.cx) ** 2 + (f.cy - s.cy) ** 2 - 0.05 * f.w)

    # ------------------------------------------------------------ main step
    def process(self, frame, now: float | None = None) -> list[dict[str, Any]]:
        """Analyse one BGR frame; returns new events (dicts with 'event')."""
        now = time.monotonic() if now is None else now
        out: list[dict[str, Any]] = []
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(cv2.resize(frame, (FLOW_WIDTH, max(1, int(round(h * FLOW_WIDTH / float(w))))),
                                       interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        out.extend(self._scene_step(gray, now))
        if self.covered:
            # A hand over the lens is not the owner leaving.
            self._last_face_at = now if self.seen else self._last_face_at
            self._prev_gray = None
            self._bg = None
            return out

        rich: list[dict[str, Any]] = []
        # Presence and position: the OpenCV detector finds faces at any
        # distance. MediaPipe adds expressions/gaze/hands when it sees them.
        scale = PROCESS_WIDTH / float(w)
        small = cv2.resize(frame, (PROCESS_WIDTH, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
        detected = self._detect(small)
        if self.mp is not None:
            started = time.perf_counter()
            obs = self.mp.process(frame, now)
            self._cost.append(time.perf_counter() - started)
            # Slow machine: look at hands every other frame to keep the pace.
            if len(self._cost) == self._cost.maxlen:
                average = sum(self._cost) / len(self._cost)
                self.mp.hand_every = 2 if average > 0.075 else (1 if average < 0.045 else self.mp.hand_every)
            if not detected:
                detected = [_Face(f.cx, f.cy, f.w, f.h, 1.0, abs(f.yaw) < 18) for f in obs.faces]
            face = self._pick(detected)
            self.face_obs = None
            if face is not None and obs.faces:
                nearest = min(obs.faces, key=lambda f: (f.cx - face.cx) ** 2 + (f.cy - face.cy) ** 2)
                if abs(nearest.cx - face.cx) < 0.15 and abs(nearest.cy - face.cy) < 0.18:
                    self.face_obs = nearest
            if self.face_obs is not None and self.seen:
                rich += self.face_analyzer.update(self.face_obs, now)
            rich += self.hand_analyzer.update(obs.hands, now)
            rich += self.crowd.update(max(len(detected), len(obs.faces)), now)
            self.people = self.crowd.count
        else:
            face = self._pick(detected)
        if (face is not None) != self._had_face:
            self._had_face = face is not None
            self._face_change_at = now
        if face is not None:
            self._streak += 1
            self._last_face_at = now
            if self._smooth is None:
                self._smooth = face
            else:
                k = 0.45
                s = self._smooth
                self._smooth = _Face(s.cx + (face.cx - s.cx) * k, s.cy + (face.cy - s.cy) * k,
                                     s.w + (face.w - s.w) * k, s.h + (face.h - s.h) * k, face.score, face.frontal)
            self.face = self._smooth
            if not self.seen and self._streak >= PRESENT_AFTER:
                self.seen = True
                if not self._ever_seen or now - self._absent_since >= ARRIVE_AFTER_ABSENCE_S:
                    out.append(self._event(now, "arrived"))
                self._ever_seen = True
        else:
            self._streak = 0
            if self.seen and now - self._last_face_at >= ABSENT_AFTER_S:
                self.seen = False
                self._absent_since = now
                self.face = None
                self._smooth = None
                out.append(self._event(now, "left"))

        if self.seen and self.face is not None and self.mp is None:
            out.extend(self._attention_step(now))
            self._size_hist.append((now, self.face.w))
            old = [s for t, s in self._size_hist if now - t >= 1.2]
            if old and self.face.w > old[-1] * 1.45 and now - self._last_approach > 6.0:
                self._last_approach = now
                out.append(self._event(now, "approached"))
        out.extend(self._motion_step(gray, now))
        out.extend(self._scene_change_step(gray, now))
        for event in rich:
            name = event.pop("event")
            if name == "wave":
                if now - self._last_wave < WAVE_COOLDOWN_S or any(e["event"] == "wave" for e in out):
                    continue
                self._last_wave = now
                self._wave.clear()
            out.append(self._event(now, name, **event))
        return out

    # ------------------------------------------------------------ attention
    def _attention_step(self, now: float) -> list[dict[str, Any]]:
        """The owner starts looking at Miko, or looks away for a while."""
        frontal = bool(self.face.frontal)
        if frontal:
            self._away_since = None
            self._frontal_since = self._frontal_since if self._frontal_since is not None else now
        else:
            self._frontal_since = None
            self._away_since = self._away_since if self._away_since is not None else now
        out = []
        if now - self._last_attention < 6.0:
            return out
        if frontal and self._looking is False and now - self._frontal_since >= 0.8:
            self._looking = True
            self._last_attention = now
            out.append(self._event(now, "looked_at_miko"))
        elif not frontal and self._looking is not False and self._away_since is not None and now - self._away_since >= 2.5:
            first = self._looking is None
            self._looking = False
            if not first:
                self._last_attention = now
                out.append(self._event(now, "looked_away"))
        elif frontal and self._looking is None and now - self._frontal_since >= 0.8:
            self._looking = True
        return out

    # ------------------------------------------------------------ scene change
    def _scene_change_step(self, gray, now: float) -> list[dict[str, Any]]:
        """Something in the room changed and stayed changed (an object put
        down or taken away, a chair moved, a door opened)."""
        g = gray.astype(np.float32)
        if self._bg is None or self._bg.shape != g.shape or now - self._last_light < 3.0:
            self._bg = g
            self._scene_since = None
            return []
        mask = np.abs(g - self._bg) > 28
        H, W = mask.shape
        if self.face is not None and self.seen:
            # The owner's own head and body are not "the room".
            f = self.face
            x0, x1 = int((f.cx - 1.8 * f.w) * W), int((f.cx + 1.8 * f.w) * W)
            y0 = int((f.cy - 1.2 * f.h) * H)
            mask[max(0, y0):, max(0, x0):max(0, x1)] = False
        area = float(mask.mean())
        out = []
        if area > 0.03 and self._quiet:
            self._scene_since = self._scene_since if self._scene_since is not None else now
            if now - self._scene_since >= 1.0:
                if now - self._last_scene > 10.0:
                    self._last_scene = now
                    ys, xs = np.nonzero(mask)
                    cx = float(xs.mean()) / W if len(xs) else 0.5
                    out.append(self._event(now, "scene_changed", x=round(-(cx * 2 - 1), 2)))
                self._bg = g                     # the new arrangement is normal now
                self._scene_since = None
        else:
            if area <= 0.03:
                self._scene_since = None
            if self._quiet:
                cv2.accumulateWeighted(g, self._bg, 0.03)
        return out

    def _event(self, now: float, name: str, **detail: Any) -> dict[str, Any]:
        self.events.append((now, name, detail))
        return {"event": name, **detail}

    # ------------------------------------------------------------ scene
    def _scene_step(self, gray, now: float) -> list[dict[str, Any]]:
        """Lens covered / uncovered and sudden lighting changes."""
        out = []
        mean = float(gray.mean())
        spread = float(gray.std())
        dark = mean < 28 or (spread < 7 and mean < 60)
        if dark:
            self._bright_since = None
            self._dark_since = self._dark_since if self._dark_since is not None else now
            if not self.covered and now - self._dark_since >= 0.5:
                self.covered = True
                self._wave.clear()
                self._shake.clear()
                out.append(self._event(now, "covered"))
        else:
            self._dark_since = None
            if self.covered:
                self._bright_since = self._bright_since if self._bright_since is not None else now
                if now - self._bright_since >= 0.3:
                    self.covered = False
                    self._light_hist.clear()
                    out.append(self._event(now, "uncovered"))
            else:
                self._light_hist.append((now, mean))
                older = [m for t, m in self._light_hist if 0.8 <= now - t <= 2.0]
                if older and now - self._last_light > 8.0:
                    before = sum(older) / len(older)
                    if abs(mean - before) > max(30.0, 0.35 * before):
                        self._last_light = now
                        self._light_hist.clear()
                        out.append(self._event(now, "light_changed", brighter=mean > before))
        return out

    # ------------------------------------------------------------ motion
    def _motion_step(self, gray, now: float) -> list[dict[str, Any]]:
        """Camera shake (whole image moves), waves and other movement."""
        prev, self._prev_gray = self._prev_gray, gray
        if prev is None or prev.shape != gray.shape:
            return []
        H, W = gray.shape
        out = []
        # Global motion (computer/camera moved): phase correlation measures
        # how far the whole image shifted, even for large, fast jumps; a big
        # share of changed pixels catches blurry shakes it cannot lock onto.
        if self._hann is None or self._hann.shape != gray.shape:
            self._hann = cv2.createHanningWindow((W, H), cv2.CV_32F)
        (sx, sy), response = cv2.phaseCorrelate(prev.astype(np.float32), gray.astype(np.float32), self._hann)
        shift = math.hypot(sx, sy)
        global_move = False
        if response > 0.08 and shift > 1.2:
            # Does shifting the whole previous frame explain the new one? It
            # does when the camera moved; it does not when only a hand moved
            # over a still room (shifting the room then makes things worse).
            m = max(2, int(math.ceil(shift)) + 2)
            moved = cv2.warpAffine(prev, np.float32([[1, 0, sx], [0, 1, sy]]), (W, H), borderMode=cv2.BORDER_REPLICATE)
            raw = float(cv2.absdiff(prev, gray)[m:-m, m:-m].mean())
            aligned = float(cv2.absdiff(moved, gray)[m:-m, m:-m].mean())
            global_move = raw > 2.0 and aligned < 0.6 * raw
        if global_move:
            axis = sx if abs(sx) >= abs(sy) else sy
            self._shake.add(now, axis if shift > 1.2 else 0.0)
            self._jolts.append(now)
            jolts = sum(1 for t in self._jolts if now - t <= 1.2)
            if (self._shake.reversals(now, 1.2) >= 2 or jolts >= 5) and now - self._last_shake > 4.0:
                self._last_shake = now
                self._shake.clear()
                self._jolts.clear()
                self._wave.clear()
                out.append(self._event(now, "shaken"))
            self._quiet = False
            return out                          # local motion is meaningless now
        self._shake.add(now, 0.0)
        flow = cv2.calcOpticalFlowFarneback(prev, gray, None, 0.5, 2, 11, 2, 5, 1.1, 0)
        gx = float(np.median(flow[..., 0]))
        gy = float(np.median(flow[..., 1]))

        # Local motion relative to the background.
        local_x = flow[..., 0] - gx
        local_y = flow[..., 1] - gy
        local = np.hypot(local_x, local_y)
        moving = local > 1.2
        if self.face is not None and self.seen:
            # Head movement is not a gesture: ignore the face box.
            f = self.face
            x0, x1 = int((f.cx - 0.7 * f.w) * W), int((f.cx + 0.7 * f.w) * W)
            y0, y1 = int((f.cy - 0.8 * f.h) * H), int((f.cy + 0.9 * f.h) * H)
            moving[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = False
        area = float(moving.mean())
        self._quiet = area < 0.02
        if area < 0.006:
            self._wave.add(now, 0.0)
            return out
        vx = float(np.median(local_x[moving]))
        vy = float(np.median(np.abs(local_y[moving])))
        ys, xs = np.nonzero(moving)
        cx = float(xs.mean()) / W
        # A hand: a modest area moving mostly sideways, back and forth.
        if 0.006 <= area <= 0.65 and abs(vx) > 0.9 * vy:
            self._wave.add(now, vx)
        else:
            self._wave.add(now, 0.0)
        if self._wave.reversals(now, 1.0) >= WAVE_MIN_REVERSALS and now - self._last_wave >= WAVE_COOLDOWN_S:
            self._last_wave = now
            self._wave.clear()
            # Image left is the owner's right (the camera faces them).
            out.append(self._event(now, "wave", hand="right" if cx < 0.5 else "left"))
        elif (area > 0.12 and now - self._last_motion > 6.0 and now - self._last_wave > 2.0
              and now - self._face_change_at > 1.5):
            # Something big moved (someone walked by, the owner stood up).
            self._last_motion = now
            out.append(self._event(now, "motion", x=round(-(cx * 2 - 1), 2)))
        return out

    # ------------------------------------------------------------ outputs
    def state(self) -> dict[str, Any]:
        """Godot-facing state. x/y are from the viewer's side: +x = owner's
        right (screen right), +y = up; size ~ face width / frame width."""
        if not self.seen or self.face is None:
            return {"type": "vision", "seen": False, "covered": self.covered}
        f = self.face
        state = {"type": "vision", "seen": True, "x": round(-(f.cx * 2 - 1), 3), "y": round(-(f.cy * 2 - 1), 3),
                 "size": round(f.w, 3), "facing": bool(f.frontal)}
        if self.face_analyzer is not None and self.face_obs is not None:
            state.update({"expression": self.face_analyzer.expression,
                          "looking": bool(self.face_analyzer.looking.state),
                          "roll": round(self.face_obs.roll, 1), "people": self.people})
        return state

    def summary(self, now: float | None = None) -> dict[str, Any]:
        """Model-facing facts (miko_get_vision). No image content."""
        now = time.monotonic() if now is None else now
        recent = [{"event": n, "seconds_ago": round(now - t, 1), **d} for t, n, d in self.events if now - t <= 120]
        if self.covered:
            return {"status": "camera_covered", "seen": False, "recent_events": recent}
        if not self.seen or self.face is None:
            return {"status": "watching", "seen": False, "recent_events": recent}
        f = self.face
        x = -(f.cx * 2 - 1)
        where = "center" if abs(x) < 0.33 else ("owner's right side" if x > 0 else "owner's left side")
        distance = "close" if f.w > 0.32 else ("far" if f.w < 0.12 else "normal distance")
        facts = {"status": "watching", "seen": True, "where": where, "distance": distance,
                 "looking_at_miko": bool(f.frontal), "recent_events": recent}
        if self.face_analyzer is not None:
            facts.update({"looking_at_miko": bool(self.face_analyzer.looking.state),
                          "expression": self.face_analyzer.expression, "people_in_view": self.people})
        return facts


class VisionService:
    """Owns the frame source and broadcasts events through the Realtime hub.

    ``emit`` receives Godot-facing dicts ({'type': 'vision'} state and
    {'type': 'vision_event'}); ``react`` gets significant events (wave,
    arrived) so the host can greet back.
    """

    def __init__(self, emit: Callable[[dict], None], react: Callable[[dict], None] | None = None,
                 settings_path: str = SETTINGS_PATH) -> None:
        self.emit = emit
        self.react = react
        self.settings_path = settings_path
        self.engine: VisionEngine | None = None
        self.lock = threading.Lock()
        self.enabled = self._load_enabled()
        self.source = "off"
        self.error = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_state_at = 0.0
        self._last_state: dict[str, Any] = {}
        self._device_frames_at = 0.0

    def _load_enabled(self) -> bool:
        if os.environ.get("MIKO_VISION", "").strip() == "0":
            return False
        try:
            with open(self.settings_path, encoding="utf-8") as handle:
                return bool(json.load(handle).get("enabled", True))
        except (OSError, ValueError):
            return True

    def _save_enabled(self) -> None:
        try:
            with open(self.settings_path, "w", encoding="utf-8") as handle:
                json.dump({"enabled": self.enabled}, handle)
        except OSError:
            pass

    # ------------------------------------------------------------ control
    def start(self) -> None:
        if not available():
            self.error = "opencv_missing"
            print("MIKO VISION: OpenCV not installed (pip install opencv-python) - sight disabled")
            self._status()
            return
        with self.lock:
            if self.engine is None:
                self.engine = VisionEngine()
        if self.enabled:
            self._start_webcam()
        self._status()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None
        self.source = "off"

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._save_enabled()
        if not self.enabled:
            self.stop()
            with self.lock:
                if self.engine is not None:
                    self.engine = VisionEngine()        # forget the last sighting
            self.emit({"type": "vision", "seen": False})
        elif available():
            self.start()
        self._status()

    def status_event(self) -> dict[str, Any]:
        return {"type": "vision_status", "enabled": self.enabled, "source": self.source,
                "available": available(), "error": self.error}

    def _status(self) -> None:
        self.emit(self.status_event())

    def summary(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "camera_off", "seen": False}
        with self.lock:
            if self.engine is None:
                return {"status": "vision_unavailable", "seen": False}
            return self.engine.summary()

    # ------------------------------------------------------------ sources
    def _start_webcam(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._webcam_loop, name="MikoVisionWebcam", daemon=True)
        self._thread.start()

    @staticmethod
    def open_webcam():
        """Try the usual Windows backends and the first camera indices; return
        (capture, description) for the first one that delivers a frame."""
        wanted = os.environ.get("MIKO_CAMERA_INDEX", "").strip()
        indices = [int(wanted)] if wanted.isdigit() else [0, 1, 2]
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY] if os.name == "nt" else [cv2.CAP_ANY]
        for index in indices:
            for backend in backends:
                capture = cv2.VideoCapture(index, backend)
                if capture.isOpened():
                    capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    for _ in range(10):                 # some cameras need a few frames
                        ok, frame = capture.read()
                        if ok and frame is not None and frame.size:
                            return capture, f"camera {index} ({capture.getBackendName()})"
                        time.sleep(0.05)
                capture.release()
        return None, ""

    def _webcam_loop(self) -> None:
        capture = None
        while not self._stop.is_set():
            capture, description = self.open_webcam()
            if capture is not None:
                break
            # Busy (another app uses it), blocked by Windows privacy settings,
            # or unplugged: say so, then keep trying quietly.
            if self.error != "webcam_unavailable":
                self.error = "webcam_unavailable"
                self.source = "off"
                print("MIKO VISION: no webcam image (busy, blocked in Windows camera privacy settings, or missing); retrying")
                self._status()
            self._stop.wait(15)
        if capture is None:
            return
        self.source = "webcam"
        self.error = ""
        self._status()
        print("MIKO VISION: webcam on,", description, "- frames stay in memory on this computer")
        failures = 0
        try:
            next_at = 0.0
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    failures += 1
                    if failures > 50:                   # camera went away: reopen
                        break
                    time.sleep(0.2)
                    continue
                failures = 0
                # A paired device camera takes over while it streams.
                if time.monotonic() - self._device_frames_at < 2.0:
                    continue
                now = time.monotonic()
                if now < next_at:
                    continue
                next_at = now + 0.06                    # ~15 fps analysis (waves are fast)
                self.feed_frame(frame, now)
        finally:
            capture.release()
            if self.source == "webcam":
                self.source = "off"
        if not self._stop.is_set():
            self._thread = None
            self._start_webcam()

    def feed_jpeg(self, jpeg: bytes) -> None:
        """Frame from the paired device camera (in memory only)."""
        if not self.enabled or not available():
            return
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return
        if self.source != "device":
            self.source = "device"
            self._status()
        self._device_frames_at = time.monotonic()
        self.feed_frame(frame, self._device_frames_at)

    def feed_frame(self, frame, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.engine is None:
                return
            events = self.engine.process(frame, now)
            state = self.engine.state()
        for event in events:
            message = {"type": "vision_event", **event}
            self.emit(message)
            print("MIKO VISION EVENT:", event["event"], flush=True)
            if self.react:
                try:
                    self.react(message)
                except Exception as error:  # never let a reaction kill the camera loop
                    print("MIKO VISION: reaction failed:", type(error).__name__)
        changed = state.get("seen") != self._last_state.get("seen")
        if changed or now - self._last_state_at >= STATE_INTERVAL_S:
            self._last_state_at = now
            self._last_state = state
            self.emit(state)
