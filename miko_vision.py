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

Interpretation is temporal, not per frame (see miko_physical.py and
miko_gestures.py): whole-image motion feeds a PhysicalInteractionDetector
(nudge / move / shake episodes with start, active and end), and while the
camera or device moves, scene-relative perception (waves, room changes, head
gestures) is suspended because every pixel moves. Waves need evidence over
time (open raised hand, sustained sideways swings, steady camera, the head
not doing the same) and are confirmed only at high confidence. With
MediaPipe the evidence is hand landmarks; without it, optical flow beside
the face is used with the same gates.
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

from miko_gestures import FaceSample, HandSample, WaveDetector
from miko_log import debug, log, throttled
from miko_physical import CAMERA, ImuInterpreter, PhysicalInteractionDetector

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
STATE_INTERVAL_S = 0.2         # vision state broadcast rate (5 Hz)
# Perception that is only meaningful while the camera is still.
POSE_EVENTS = {"nodded", "shook_head", "tilted_head", "looked_away", "looked_at_miko", "looked_somewhere"}


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


class VisionEngine:
    """Turns frames into presence, position, gesture and scene-change events.

    Events: arrived, left, approached, wave, covered, uncovered,
    shake_started / shake_active / shake_ended, device_moved, device_nudged,
    light_changed, scene_changed, motion, plus MediaPipe face/hand events.
    Pure computation, no I/O.
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
        self._raw_face: _Face | None = None
        self._prev_raw_face: _Face | None = None
        self._smooth: _Face | None = None
        self._streak = 0
        self._last_face_at = -1e9
        self._absent_since = -1e9
        self._ever_seen = False
        self._size_hist: deque = deque(maxlen=30)
        self._last_approach = -1e9
        # Scene / motion state.
        self._prev_gray = None
        self.physical = PhysicalInteractionDetector(CAMERA, source="camera")
        self.camera_stable = True
        self.imu_unstable_until = -1e9      # set by the device IMU when it moves
        self.imu_active_until = -1e9        # IMU is the shake source while it streams
        # Hand landmarks are the wave evidence when MediaPipe is present;
        # optical flow may then only report candidates, never confirm.
        self.hand_wave = WaveDetector("hand")
        self.flow_wave = WaveDetector("flow", can_confirm=self.mp is None)
        self._last_hand: HandSample | None = None
        self._hand_seen_at = -1e9
        self._jolt_flip = False
        self._hand_boxes: list = []
        self._blob_box = None
        self._prev_t: float | None = None
        self.fast_frames = 0
        self.slow_frames = 0
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
    #
    # Two lanes. The fast lane (cover/light, device motion, shakes, motion
    # waves, room changes) is cheap and runs on every camera frame. The slow
    # lane (faces, MediaPipe expressions and hands) is heavy and runs as fast
    # as the computer allows on the newest frame. Before, everything ran in
    # one chain at the slow lane's pace (4-6 fps on many laptops), and a
    # 2-4 per second wave or shake simply fell between frames.

    def _gray(self, frame):
        h, w = frame.shape[:2]
        return cv2.cvtColor(cv2.resize(frame, (FLOW_WIDTH, max(1, int(round(h * FLOW_WIDTH / float(w))))),
                                       interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)

    def process(self, frame, now: float | None = None) -> list[dict[str, Any]]:
        """Both lanes on one frame, in order (device frames, tests)."""
        now = time.monotonic() if now is None else now
        gray = self._gray(frame)
        out = self._scene_step(gray, now)
        if self._covered_reset(now):
            return out
        out.extend(self.apply_slow(self.analyze_slow(frame, now), now))
        out.extend(self._fast_motion(gray, now))
        return out

    def process_fast(self, frame, now: float | None = None) -> list[dict[str, Any]]:
        """Fast lane only (call on every camera frame)."""
        now = time.monotonic() if now is None else now
        self.fast_frames += 1
        gray = self._gray(frame)
        out = self._scene_step(gray, now)
        if self._covered_reset(now):
            return out
        out.extend(self._fast_motion(gray, now))
        return out

    def _covered_reset(self, now: float) -> bool:
        if not self.covered:
            return False
        # A hand over the lens is not the owner leaving.
        self._last_face_at = now if self.seen else self._last_face_at
        self._prev_gray = None
        self._bg = None
        return True

    def analyze_slow(self, frame, now: float) -> dict[str, Any]:
        """Heavy detection (YuNet, MediaPipe). Touches only the detectors, so
        the live service runs it outside the engine lock."""
        h, w = frame.shape[:2]
        scale = PROCESS_WIDTH / float(w)
        small = cv2.resize(frame, (PROCESS_WIDTH, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
        detected = self._detect(small)
        obs = None
        if self.mp is not None:
            started = time.perf_counter()
            obs = self.mp.process(frame, now)
            self._cost.append(time.perf_counter() - started)
            # Slow machine: look at hands every other frame to keep the pace.
            if len(self._cost) == self._cost.maxlen:
                average = sum(self._cost) / len(self._cost)
                self.mp.hand_every = 2 if average > 0.075 else (1 if average < 0.045 else self.mp.hand_every)
        return {"detected": detected, "obs": obs}

    def apply_slow(self, result: dict[str, Any], now: float) -> list[dict[str, Any]]:
        """Presence, expressions, hand signs and hand waves from a slow-lane
        detection (call under the engine lock)."""
        self.slow_frames += 1
        out: list[dict[str, Any]] = []
        if self.covered:
            self._last_face_at = now if self.seen else self._last_face_at
            return out
        detected, obs = result["detected"], result["obs"]
        rich: list[dict[str, Any]] = []
        if obs is not None:
            if obs.hands_checked:
                # Hands are not the room either: a hand waving close to the
                # camera must not look like the camera moving.
                self._hand_boxes = [self._hand_box(h) for h in obs.hands]
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
        self._prev_raw_face, self._raw_face = self._raw_face, face
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
                away = now - self._absent_since if self._ever_seen else 1e6
                if not self._ever_seen or away >= ARRIVE_AFTER_ABSENCE_S:
                    out.append(self._event(now, "arrived", away=round(min(away, 1e6))))
                self._ever_seen = True
        else:
            self._streak = 0
            if self.seen and now - self._last_face_at >= ABSENT_AFTER_S:
                self.seen = False
                self._absent_since = now
                self.face = None
                self._smooth = None
                out.append(self._event(now, "left"))
        stable = self.camera_stable
        if self.seen and self.face is not None and self.mp is None and stable:
            out.extend(self._attention_step(now))
            self._size_hist.append((now, self.face.w))
            old = [s for t, s in self._size_hist if now - t >= 1.2]
            if old and self.face.w > old[-1] * 1.45 and now - self._last_approach > 6.0:
                self._last_approach = now
                out.append(self._event(now, "approached"))
        elif not stable:
            self._size_hist.clear()
        if obs is not None and obs.hands_checked:
            out.extend(self._hand_wave_step(obs.hands, now, stable))
        for event in rich:
            name = event.pop("event")
            if not stable and name in POSE_EVENTS:
                throttled("pose_" + name, 3.0, "PERCEPTION", name, decision="suppressed",
                          suppression="camera_or_device_moving")
                continue
            out.append(self._event(now, name, **event))
        return out

    def _fast_motion(self, gray, now: float) -> list[dict[str, Any]]:
        # Device / camera motion first: it decides whether scene-relative
        # perception can be trusted.
        out = self._motion_step(gray, now)
        out.extend(self._scene_change_step(gray, now))
        if not self.camera_stable and self.face_analyzer is not None:
            self.face_analyzer.camera_moved()
        return out

    @staticmethod
    def _hand_box(h) -> tuple[float, float, float, float]:
        points = [h.wrist] + ([h.tips] if h.tips else []) + ([h.center] if h.center else [])
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        half = max(0.07, 1.8 * float(h.size or 0.0))
        return cx - half, cy - half * 1.3, cx + half, cy + half * 1.3

    # ------------------------------------------------------------ waves
    def _face_sample(self, now: float) -> FaceSample | None:
        if not self.seen or self.face is None:
            return None
        f = self.face
        return FaceSample(now, f.cx, f.cy, f.w, f.h)

    def _hand_wave_step(self, hands: list, now: float, stable: bool) -> list[dict[str, Any]]:
        """Wave evidence from MediaPipe hand landmarks."""
        sample = None
        if hands:
            # Follow the same hand between frames; else the most certain one.
            def position(h):
                return h.tips if h.tips else (h.center if h.center else h.wrist)
            if self._last_hand is not None:
                last = self._last_hand
                hand = min(hands, key=lambda h: (position(h)[0] - last.x) ** 2 + (position(h)[1] - last.y) ** 2)
            else:
                hand = max(hands, key=lambda h: h.score * max(h.size, 0.05))
            x, y = hand.tips if hand.tips else position(hand)
            sample = HandSample(now, float(x), float(y), float(hand.size or 0.1), float(hand.openness), hand.gesture)
            self._hand_seen_at = now
        self._last_hand = sample
        return [self._event(now, e.pop("event"), **e)
                for e in self.hand_wave.update(now, sample, self._face_sample(now), stable)]

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
        if (self._bg is None or self._bg.shape != g.shape or now - self._last_light < 3.0
                or not self.camera_stable):
            # The camera moved: the old background no longer lines up.
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
        for box in self._hand_boxes:                # a raised hand is not the room
            bx0, by0, bx1, by1 = box
            mask[max(0, int(by0 * H)):max(0, int(by1 * H)), max(0, int(bx0 * W)):max(0, int(bx1 * W))] = False
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
                self.hand_wave.reset("lens covered")
                self.flow_wave.reset("lens covered")
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
                if older and now - self._last_light > 8.0 and self.camera_stable:
                    before = sum(older) / len(older)
                    if abs(mean - before) > max(30.0, 0.35 * before):
                        self._last_light = now
                        self._light_hist.clear()
                        out.append(self._event(now, "light_changed", brighter=mean > before))
        return out

    # ------------------------------------------------------------ motion
    def _background_mask(self, shape):
        """Pixels that belong to the room, not to the person: the face box
        widened to the shoulders and everything below it (the body)."""
        H, W = shape
        mask = np.ones((H, W), np.float32)
        # The raw detection now and one frame ago (the diff compares both
        # frames) plus the smoothed track: a fast head lags the smoothed box.
        for f in (self.face if self.seen else None, self._raw_face, self._prev_raw_face):
            if f is None:
                continue
            # Detectors box the inner face; hair, ears and shoulders reach
            # well beyond it.
            x0, x1 = int((f.cx - 1.4 * f.w) * W), int((f.cx + 1.4 * f.w) * W)
            y0 = int((f.cy - 1.6 * f.h) * H)
            mask[max(0, y0):, max(0, x0):max(0, x1)] = 0.0
        # Hands seen by the hand model, and the moving blob of the last steady
        # frame (a hand the model missed).
        for box in list(self._hand_boxes) + ([self._blob_box] if self._blob_box else []):
            x0, y0, x1, y1 = box
            mask[max(0, int(y0 * H)):max(0, int(y1 * H)), max(0, int(x0 * W)):max(0, int(x1 * W))] = 0.0
        return mask

    def _global_motion(self, prev, gray) -> tuple[tuple[float, float], float]:
        """How far the room moved since the last frame (camera or device
        motion), and how much to trust it (0..1).

        Measured on the background only (the person is masked) and checked
        zone by zone: when the camera moves, the left, right and top parts of
        the room all move the same way; a head moving in front of a plain
        wall, or a hand over a still room, changes only the area next to it.
        A fast shake blurs everything, so "most zones changed at once with
        steady brightness" also counts, with an uncertain direction."""
        H, W = gray.shape
        if self._hann is None or self._hann.shape != gray.shape:
            self._hann = cv2.createHanningWindow((W, H), cv2.CV_32F)
        mask = self._background_mask(gray.shape)
        room = mask > 0.5
        if float(room.mean()) < 0.15:
            return (0.0, 0.0), 0.0                 # the person fills the view: unknowable
        # Six regions: left/right halves of the left and right thirds, and the
        # top band. Camera motion moves all of them; a hand or head only some.
        zones = []
        regions = [("L", np.s_[: H // 2, : W // 3]), ("L", np.s_[H // 2:, : W // 3]),
                   ("R", np.s_[: H // 2, 2 * W // 3:]), ("R", np.s_[H // 2:, 2 * W // 3:]),
                   ("T", np.s_[: H // 3, W // 3: W // 2]), ("T", np.s_[: H // 3, W // 2: 2 * W // 3])]
        for side, region in regions:
            z = np.zeros_like(room)
            z[region] = True
            z &= room
            if float(z.mean()) >= 0.015:
                zones.append((side, z))
        if len({side for side, _ in zones}) < 2:
            return (0.0, 0.0), 0.0
        diff = cv2.absdiff(prev, gray).astype(np.float32)
        window = self._hann * cv2.blur(mask, (9, 9))
        (sx, sy), response = cv2.phaseCorrelate(prev.astype(np.float32), gray.astype(np.float32), window)
        shift = math.hypot(sx, sy)
        if shift >= 0.5 and response > 0.04 and shift < min(H, W) / 4:
            moved = cv2.warpAffine(prev, np.float32([[1, 0, sx], [0, 1, sy]]), (W, H), borderMode=cv2.BORDER_REPLICATE)
            aligned = cv2.absdiff(moved, gray).astype(np.float32)
            m = int(math.ceil(shift)) + 2
            edge = np.zeros_like(room)
            edge[m:-m, m:-m] = True
            agreeing, still, sides = 0, 0, set()
            for side, z in zones:
                zz = z & edge
                if not zz.any():
                    continue
                raw = float(diff[zz].mean())
                if raw > 1.0 and float(aligned[zz].mean()) < 0.7 * raw:
                    agreeing += 1
                    sides.add(side)
                elif raw < 0.5 and float(gray[zz].std()) > 8.0:
                    still += 1                       # textured and unchanged: the camera did not move
            if agreeing >= 3 and len(sides) >= 2 and agreeing > still:
                return (sx, sy), 1.0
        # Blur: most zones changed a lot at once while the brightness stayed
        # (a light switching or the lens being covered changes brightness).
        if abs(float(gray.mean()) - float(prev.mean())) > 12.0 or float(gray.std()) < 10.0 or float(prev.std()) < 10.0:
            return (0.0, 0.0), 0.0
        busy_sides = {side for side, z in zones if float(diff[z].mean()) > 4.0}
        busy = sum(1 for side, z in zones if float(diff[z].mean()) > 4.0)
        if busy >= 3 and len(busy_sides) >= 2 and busy >= len(zones) - 1:
            if shift < 0.5:
                sx, sy = (3.0, 0.0) if self._jolt_flip else (-3.0, 0.0)
                self._jolt_flip = not self._jolt_flip
            return (sx, sy), 0.6
        return (0.0, 0.0), 0.0

    def _motion_step(self, gray, now: float) -> list[dict[str, Any]]:
        """Device/camera motion (physical episodes), then waves and other
        movement relative to a steady background."""
        prev, self._prev_gray = self._prev_gray, gray
        prev_t, self._prev_t = self._prev_t, now
        if prev is None or prev.shape != gray.shape:
            return []
        H, W = gray.shape
        out = []
        # Thresholds are in "pixels per 1/15 s": scale by the real frame
        # interval so 30 fps and 6 fps cameras measure the same motion.
        dt = (now - prev_t) if prev_t is not None else 1.0 / 15.0
        per15 = max(0.25, min(4.0, (1.0 / 15.0) / max(dt, 1e-3)))
        vector, quality = self._global_motion(prev, gray)
        vector = (vector[0] * per15, vector[1] * per15)
        physical = self.physical.update(now, vector, quality)
        if now < self.imu_active_until:
            # The device IMU measures its own motion better than the camera;
            # the camera episode then only gates perception.
            for event in physical:
                debug("PHYSICAL", "camera episode left to the IMU", event=event["event"])
        else:
            for event in physical:
                name = event.pop("event")
                out.append(self._event(now, name, **event))
        was_stable = self.camera_stable
        self.camera_stable = self.physical.stable(now) and now >= self.imu_unstable_until
        if was_stable != self.camera_stable:
            debug("PERCEPTION", "camera " + ("steady" if self.camera_stable else "moving"),
                  energy=self.physical.energy)
        if not self.camera_stable:
            self._quiet = False
            self.flow_wave.update(now, None, None, camera_stable=False)
            return out                          # local motion is meaningless now

        flow = cv2.calcOpticalFlowFarneback(prev, gray, None, 0.5, 2, 11, 2, 5, 1.1, 0)
        gx = float(np.median(flow[..., 0]))
        gy = float(np.median(flow[..., 1]))
        # Local motion relative to the background.
        local_x = flow[..., 0] - gx
        local_y = flow[..., 1] - gy
        local = np.hypot(local_x, local_y)
        moving = local > 1.2 / per15
        f = self.face if self.seen else None
        if f is not None:
            # Head movement is not a gesture: ignore the face box.
            x0, x1 = int((f.cx - 0.7 * f.w) * W), int((f.cx + 0.7 * f.w) * W)
            y0, y1 = int((f.cy - 0.8 * f.h) * H), int((f.cy + 0.9 * f.h) * H)
            moving[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = False
        area = float(moving.mean())
        self._quiet = area < 0.02
        self._blob_box = None
        sample = None
        cx = 0.5
        if area >= 0.004:
            ys, xs = np.nonzero(moving)
            cx = float(xs.mean()) / W
            # The hand is the largest moving blob outside the face; its
            # position over time is the evidence (velocity integration
            # aliases at webcam frame rates).
            blobs = cv2.dilate(moving.astype(np.uint8), np.ones((3, 3), np.uint8))
            count, labels, stats, centers = cv2.connectedComponentsWithStats(blobs, connectivity=8)
            if count > 1:
                index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
                blob_area = stats[index, cv2.CC_STAT_AREA] / float(W * H)
                if 0.004 <= blob_area <= 0.5:
                    blob = (labels == index) & moving
                    if blob.any():
                        vx = float(np.median(local_x[blob]))
                        vy = float(np.median(np.abs(local_y[blob])))
                        left, top = stats[index, cv2.CC_STAT_LEFT], stats[index, cv2.CC_STAT_TOP]
                        bw, bh = stats[index, cv2.CC_STAT_WIDTH], stats[index, cv2.CC_STAT_HEIGHT]
                        self._blob_box = ((left - 4) / W, (top - 4) / H, (left + bw + 4) / W, (top + bh + 4) / H)
                        if abs(vx) > 0.9 * vy:            # mostly sideways
                            bx, by = float(centers[index][0]) / W, float(centers[index][1]) / H
                            sample = HandSample(now, bx, by, 0.1, -1.0, "", bx)
        # Fast waves blur, and the hand model then loses the hand (or runs
        # slowly on this computer). Flow may confirm; without a hand seen just
        # now it needs stronger evidence.
        self.flow_wave.can_confirm = True
        self.flow_wave.confirm_at = 0.8 if (self.mp is None or now - self._hand_seen_at < 2.5) else 0.86
        events = self.flow_wave.update(now, sample, self._face_sample(now), camera_stable=True)
        for event in events:
            out.append(self._event(now, event.pop("event"), **event))
        if (not events and area > 0.12 and now - self._last_motion > 6.0
                and now - self.flow_wave._last_confirm > 2.0 and now - self._face_change_at > 1.5):
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
        self._imu: ImuInterpreter | None = None
        self._slow_frame = None
        self._slow_ready = threading.Event()
        self.preview_enabled = False
        self._preview_at = 0.0
        self._fast_at = 0.0
        self._slow_at = 0.0
        self._fps_fast = 0.0
        self._fps_slow = 0.0

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
        video = os.environ.get("MIKO_CAMERA_FILE", "").strip()
        if video:                                  # diagnostics: a recorded video as the webcam
            capture = cv2.VideoCapture(video)
            if capture.isOpened():
                return capture, f"video file {os.path.basename(video)}"
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
        errors = 0
        frames = 0
        beat_at = time.monotonic()
        lane_stop = threading.Event()
        threading.Thread(target=self._slow_lane, args=(lane_stop,), name="MikoVisionSlowLane", daemon=True).start()
        try:
            next_at = 0.0
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    failures += 1
                    if failures > 50:                   # camera went away: reopen
                        log("RECOVERY", "webcam stopped delivering frames; reopening")
                        break
                    time.sleep(0.2)
                    continue
                failures = 0
                if description.startswith("video file"):
                    time.sleep(1.0 / 15.0)               # play a recorded video at camera pace
                if time.monotonic() - beat_at >= 30.0:
                    self._heartbeat(frames, time.monotonic() - beat_at, errors)
                    beat_at, frames, errors = time.monotonic(), 0, 0
                # A paired device camera takes over while it streams.
                if time.monotonic() - self._device_frames_at < 2.0:
                    continue
                now = time.monotonic()
                if now < next_at:
                    continue
                next_at = now + 0.03                    # fast lane: up to ~30 fps (cheap)
                try:
                    self._fast_lane(frame, now)
                    frames += 1
                except Exception as error:
                    # One bad frame must never kill sight for the rest of the
                    # session (before, the camera thread died silently here).
                    errors += 1
                    import traceback
                    throttled("vision_frame_error", 10.0, "RECOVERY", "frame analysis failed; camera keeps running",
                              error=type(error).__name__, where=traceback.format_exc().strip().splitlines()[-2].strip()[:120])
        finally:
            lane_stop.set()
            self._slow_ready.set()
            capture.release()
            if self.source == "webcam":
                self.source = "off"
        if not self._stop.is_set():
            self._thread = None
            self._start_webcam()

    def _fast_lane(self, frame, now: float) -> None:
        if self._fast_at:
            self._fps_fast += (1.0 / max(now - self._fast_at, 1e-3) - self._fps_fast) * 0.1
        self._fast_at = now
        with self.lock:
            engine = self.engine
            if engine is None:
                return
            events = engine.process_fast(frame, now)
            state = engine.state()
        # Hand the newest frame to the slow lane (older ones are dropped).
        self._slow_frame = (frame, now)
        self._slow_ready.set()
        self._publish(events)
        self._emit_state(state, now)
        self._maybe_preview(frame, now)

    def _slow_lane(self, stop: threading.Event) -> None:
        """Faces, expressions and hands on the newest frame, as fast as the
        computer allows, without ever holding up the fast lane."""
        while not stop.is_set() and not self._stop.is_set():
            self._slow_ready.wait(1.0)
            self._slow_ready.clear()
            item, self._slow_frame = self._slow_frame, None
            if item is None:
                continue
            frame, now = item
            engine = self.engine
            if engine is None:
                continue
            try:
                result = engine.analyze_slow(frame, now)          # heavy, outside the lock
                done = time.monotonic()
                if self._slow_at:
                    self._fps_slow += (1.0 / max(done - self._slow_at, 1e-3) - self._fps_slow) * 0.2
                self._slow_at = done
                with self.lock:
                    if self.engine is not engine:
                        continue
                    events = engine.apply_slow(result, now)
                self._publish(events)
            except Exception as error:
                import traceback
                throttled("vision_slow_error", 10.0, "RECOVERY", "face/hand analysis failed; sight keeps running",
                          error=type(error).__name__, where=traceback.format_exc().strip().splitlines()[-2].strip()[:120])

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

    def _heartbeat(self, frames: int, seconds: float, errors: int) -> None:
        """Every 30 s: is sight actually working? (frames, faces, camera state)."""
        with self.lock:
            engine = self.engine
            seen = bool(engine and engine.seen)
            stable = bool(engine.camera_stable) if engine else False
            mp = bool(engine and engine.mp)
            energy = round(engine.physical.energy, 2) if engine else 0.0
        log("PERCEPTION", "sight heartbeat", fps=round(frames / max(seconds, 0.1), 1),
            fast_fps=round(self._fps_fast, 1), slow_fps=round(self._fps_slow, 1), face_seen=seen,
            camera_steady=stable, motion_energy=energy, mediapipe=mp, errors=errors, source=self.source)

    def feed_imu(self, samples, now: float | None = None) -> None:
        """Motion samples from the paired device's IMU: (t, ax, ay, az, gx,
        gy, gz) with accel in g and gyro in deg/s. Works with the camera off:
        it is the device's sense of being moved, not sight."""
        now = time.monotonic() if now is None else now
        events: list[dict[str, Any]] = []
        with self.lock:
            if self._imu is None:
                self._imu = ImuInterpreter()
                log("PHYSICAL", "device IMU connected; it is now the shake source")
            last_t = None
            for sample in samples:
                events.extend(self._imu.update(sample))
                last_t = float(sample[0])
            if self.engine is not None and last_t is not None:
                self.engine.imu_active_until = now + 2.0
                if not self._imu.detector.stable(last_t):
                    self.engine.imu_unstable_until = now + 0.3
        self._publish(events)

    def _publish(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            message = {"type": "vision_event", **event}
            log("PERCEPTION", "event " + str(event.get("event")),
                **{k: v for k, v in event.items() if k != "event" and isinstance(v, (int, float, str))})
            # The host decides the response level first and annotates the
            # event ("level"), so the body in Godot reacts at the same level.
            if self.react:
                try:
                    self.react(message)
                except Exception as error:  # never let a reaction kill the camera loop
                    log("RECOVERY", "vision reaction failed", error=type(error).__name__)
            self.emit(message)

    def feed_frame(self, frame, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.engine is None:
                return
            events = self.engine.process(frame, now)
            state = self.engine.state()
        self._publish(events)
        self._emit_state(state, now)
        self._maybe_preview(frame, now)

    def _emit_state(self, state: dict[str, Any], now: float) -> None:
        changed = state.get("seen") != self._last_state.get("seen")
        if changed or now - self._last_state_at >= STATE_INTERVAL_S:
            self._last_state_at = now
            self._last_state = state
            self.emit(state)

    def set_preview(self, enabled: bool) -> None:
        self.preview_enabled = bool(enabled)
        log("PERCEPTION", "live view " + ("on" if self.preview_enabled else "off"))

    def _maybe_preview(self, frame, now: float) -> None:
        """'What Miko sees' (F6): the camera picture with what was measured on
        it, ~5 times a second, as a small JPEG for Godot. Local only."""
        if not self.preview_enabled or now - self._preview_at < 0.2:
            return
        self._preview_at = now
        try:
            with self.lock:
                engine = self.engine
                if engine is None:
                    return
                info = self._preview_info(engine, now)
            image = self._draw_preview(frame, info)
            ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 62])
            if ok:
                import base64
                self.emit({"type": "vision_preview", "jpeg": base64.b64encode(jpeg.tobytes()).decode("ascii")})
        except Exception as error:
            throttled("vision_preview_error", 10.0, "RECOVERY", "live view failed", error=type(error).__name__)

    def _preview_info(self, engine, now: float) -> dict[str, Any]:
        hand_e, flow_e = engine.hand_wave.last_evidence, engine.flow_wave.last_evidence
        wave = max((e for e in (hand_e, flow_e) if e), key=lambda e: e.get("confidence", 0.0), default={})
        p = engine.physical
        return {
            "face": engine.face if engine.seen else None, "hands": list(engine._hand_boxes),
            "blob": engine._blob_box, "covered": engine.covered, "steady": engine.camera_stable,
            "energy": p.energy, "episode": bool(p.episode), "shaking": p.shaking,
            "start": p.p.start, "peak": p.p.shake_peak, "wave": dict(wave),
            "fast": self._fps_fast, "slow": self._fps_slow, "mediapipe": engine.mp is not None,
            "events": [(round(now - t, 1), n) for t, n, _ in list(engine.events)[-4:] if now - t < 8.0],
        }

    @staticmethod
    def _draw_preview(frame, info: dict[str, Any]):
        h, w = frame.shape[:2]
        W = 320
        H = int(round(h * W / float(w)))
        img = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
        img = cv2.flip(img, 1)                         # mirror, like a selfie camera

        def rect(box, color, thick=2):
            x0, y0, x1, y1 = box
            cv2.rectangle(img, (int((1 - x1) * W), int(y0 * H)), (int((1 - x0) * W), int(y1 * H)), color, thick)
        f = info["face"]
        if f is not None:
            rect((f.cx - f.w / 2, f.cy - f.h / 2, f.cx + f.w / 2, f.cy + f.h / 2), (90, 220, 90))
        for box in info["hands"]:
            rect(box, (40, 200, 255))
        if info["blob"]:
            rect(info["blob"], (255, 200, 60), 1)
        # Top bar: lanes and camera state.
        cv2.rectangle(img, (0, 0), (W, 18), (20, 20, 20), -1)
        state = "COVERED" if info["covered"] else ("steady" if info["steady"] else "CAMERA MOVING")
        text = f"fast {info['fast']:.0f}fps  slow {info['slow']:.0f}fps  {'MP' if info['mediapipe'] else 'cv'}  {state}"
        cv2.putText(img, text, (4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (235, 235, 235), 1, cv2.LINE_AA)
        # Motion meter (device / camera shake).
        cv2.rectangle(img, (0, H - 34), (W, H), (20, 20, 20), -1)
        level = min(1.0, info["energy"] / max(info["peak"] * 1.5, 1e-3))
        color = (60, 60, 255) if info["shaking"] else ((0, 170, 255) if info["episode"] else (120, 200, 120))
        cv2.rectangle(img, (4, H - 30), (4 + int((W - 8) * level), H - 22), color, -1)
        mark = 4 + int((W - 8) * min(1.0, info["start"] / max(info["peak"] * 1.5, 1e-3)))
        cv2.line(img, (mark, H - 32), (mark, H - 20), (255, 255, 255), 1)
        wave = info["wave"]
        wtext = "wave: -" if not wave else (f"wave {wave.get('confidence', 0):.2f} swings {wave.get('swings', 0)} "
                                             f"open {wave.get('openness', 0):.1f}")
        cv2.putText(img, ("SHAKING  " if info["shaking"] else "") + wtext, (4, H - 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.38, (235, 235, 235), 1, cv2.LINE_AA)
        for i, (age, name) in enumerate(reversed(info["events"])):
            cv2.putText(img, f"{name} {age}s", (W - 120, 34 + 14 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        (90, 255, 255), 1, cv2.LINE_AA)
        return img


class VisionProcess:
    """Same interface as VisionService, but the camera, OpenCV and MediaPipe
    run in a separate process (miko_vision_worker.py), so perception can never
    starve the real-time voice relay. The worker is restarted if it dies.
    Perception events still pass through ``react`` here first (the response
    level is decided in the host), then go to Godot via ``emit``."""

    WORKER = os.path.join(BASE_DIR, "miko_vision_worker.py")

    def __init__(self, emit: Callable[[dict], None], react: Callable[[dict], None] | None = None,
                 settings_path: str = SETTINGS_PATH) -> None:
        self.emit = emit
        self.react = react
        self.settings_path = settings_path
        self.enabled = VisionService._load_enabled(self)
        self.source = "off"
        self.error = ""
        self.engine = None                      # lives in the worker
        self._proc = None
        self._write_lock = threading.Lock()
        self._stopping = False
        self._summary: dict[str, Any] = {"status": "watching", "seen": False}
        self._imu: ImuInterpreter | None = None
        self._restarts = 0

    # ------------------------------------------------------------ process
    def start(self) -> None:
        if not available():
            self.error = "opencv_missing"
            print("MIKO VISION: OpenCV not installed (pip install opencv-python) - sight disabled")
            self.emit(self.status_event())
            return
        self._stopping = False
        self._spawn()

    def _spawn(self) -> None:
        import subprocess
        import sys
        env = dict(os.environ, MIKO_LOG_STDERR="1", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self._proc = subprocess.Popen([sys.executable, "-X", "utf8", self.WORKER], cwd=BASE_DIR, env=env,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                                      text=True, encoding="utf-8", errors="replace", bufsize=1,
                                      creationflags=flags)
        log("PERCEPTION", "vision worker started", pid=self._proc.pid)
        threading.Thread(target=self._read, args=(self._proc,), name="MikoVisionReader", daemon=True).start()

    def _read(self, proc) -> None:
        for line in proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            kind, body = message.get("t"), message.get("m") or {}
            try:
                if kind == "event":
                    if self.react:
                        self.react(body)            # annotates the response level
                    self.emit(body)
                elif kind == "emit":
                    if body.get("type") == "vision_status":
                        self.enabled = bool(body.get("enabled", self.enabled))
                        self.source = str(body.get("source", self.source))
                        self.error = str(body.get("error", ""))
                    self.emit(body)
                elif kind == "summary":
                    self._summary = body
            except Exception as error:              # never let a reaction kill the reader
                log("RECOVERY", "vision message failed", kind=kind, error=type(error).__name__)
        code = proc.wait()
        if self._stopping or proc is not self._proc:
            return
        self._restarts += 1
        delay = min(30.0, 2.0 * self._restarts)
        log("RECOVERY", "vision worker exited; restarting", code=code, delay=delay, count=self._restarts)
        self.source = "off"
        self.emit(self.status_event())
        time.sleep(delay)
        if not self._stopping:
            self._spawn()

    def _send(self, command: dict) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        with self._write_lock:
            try:
                proc.stdin.write(json.dumps(command) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                pass

    def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc is None:
            return
        self._send({"c": "stop"})
        try:
            proc.wait(timeout=4)
        except Exception:
            proc.kill()
        self.source = "off"

    # ------------------------------------------------------------ interface
    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._send({"c": "enable", "v": self.enabled})
        if not self.enabled:
            self.emit({"type": "vision", "seen": False})

    def status_event(self) -> dict[str, Any]:
        return {"type": "vision_status", "enabled": self.enabled, "source": self.source,
                "available": available(), "error": self.error}

    def set_preview(self, enabled: bool) -> None:
        self._send({"c": "preview", "v": bool(enabled)})

    def summary(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "camera_off", "seen": False}
        return dict(self._summary)

    def feed_jpeg(self, jpeg: bytes) -> None:
        if self.enabled:
            import base64
            self._send({"c": "jpeg", "b": base64.b64encode(jpeg).decode("ascii")})

    def feed_imu(self, samples, now: float | None = None) -> None:
        if self._imu is None:
            self._imu = ImuInterpreter()
            log("PHYSICAL", "device IMU connected; it is now the shake source")
        events: list[dict[str, Any]] = []
        last_t = None
        for sample in samples:
            events.extend(self._imu.update(sample))
            last_t = float(sample[0])
        if last_t is not None:
            self._send({"c": "imu", "active": 2.0, "unstable": 0.0 if self._imu.detector.stable(last_t) else 0.3})
        for event in events:
            message = {"type": "vision_event", **event}
            log("PERCEPTION", "event " + str(event.get("event")), source="imu")
            if self.react:
                try:
                    self.react(message)
                except Exception as error:
                    log("RECOVERY", "vision reaction failed", error=type(error).__name__)
            self.emit(message)
