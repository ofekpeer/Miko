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
import os
import threading
import time
from typing import Any, Callable

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
WAVE_MIN_REVERSALS = 3
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


@dataclass
class _WaveTrack:
    samples: deque = field(default_factory=lambda: deque(maxlen=40))   # (t, vx)

    def add(self, t: float, vx: float) -> None:
        self.samples.append((t, vx))

    def reversals(self, now: float, threshold: float) -> int:
        signs = [1 if v > 0 else -1 for t, v in self.samples if now - t <= WAVE_WINDOW_S and abs(v) >= threshold]
        return sum(1 for a, b in zip(signs, signs[1:]) if a != b)

    def clear(self) -> None:
        self.samples.clear()


class VisionEngine:
    """Turns frames into presence, position and gesture events (no I/O)."""

    def __init__(self, model_path: str = YUNET_PATH) -> None:
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
        self.seen = False
        self.face: _Face | None = None
        self._smooth: _Face | None = None
        self._streak = 0
        self._last_face_at = -1e9
        self._absent_since = -1e9
        self._ever_seen = False
        self._prev_flow_gray = None
        self._prev_face_c: tuple[float, float] | None = None
        self._tracks = {"left": _WaveTrack(), "right": _WaveTrack()}
        self._last_wave = -1e9
        self._size_hist: deque = deque(maxlen=30)
        self._last_approach = -1e9
        self.events: deque = deque(maxlen=12)  # (t, name, detail)

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
        scale = PROCESS_WIDTH / float(w)
        small = cv2.resize(frame, (PROCESS_WIDTH, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
        face = self._pick(self._detect(small))
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
                for track in self._tracks.values():
                    track.clear()
                out.append(self._event(now, "left"))

        if self.seen and self.face is not None:
            self._size_hist.append((now, self.face.w))
            old = [s for t, s in self._size_hist if now - t >= 1.2]
            if old and self.face.w > old[-1] * 1.45 and now - self._last_approach > 6.0:
                self._last_approach = now
                out.append(self._event(now, "approached"))
        out.extend(self._wave_step(frame, now, face))
        return out

    def _event(self, now: float, name: str, **detail: Any) -> dict[str, Any]:
        self.events.append((now, name, detail))
        return {"event": name, **detail}

    def _wave_step(self, frame, now: float, face: _Face | None) -> list[dict[str, Any]]:
        h, w = frame.shape[:2]
        fh_px = max(1, int(round(h * FLOW_WIDTH / float(w))))
        gray = cv2.cvtColor(cv2.resize(frame, (FLOW_WIDTH, fh_px), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        prev, self._prev_flow_gray = self._prev_flow_gray, gray
        prev_c = self._prev_face_c
        self._prev_face_c = (face.cx, face.cy) if face is not None else None
        if prev is None or prev.shape != gray.shape or face is None or not self.seen:
            return []
        # Ignore whole-body or camera motion: the face must be fairly still.
        if prev_c is not None and abs(face.cx - prev_c[0]) + abs(face.cy - prev_c[1]) > 0.06:
            for track in self._tracks.values():
                track.clear()
            return []
        flow = cv2.calcOpticalFlowFarneback(prev, gray, None, 0.5, 2, 9, 2, 5, 1.1, 0)
        H, W = gray.shape
        fx, fy, fw, fhh = face.cx * W, face.cy * H, face.w * W, face.h * H
        top = int(max(0, fy - 2.2 * fhh))
        bottom = int(min(H, fy + 0.9 * fhh))
        regions = {
            "left": (int(max(0, fx - 4.0 * fw)), int(max(0, fx - 0.8 * fw))),
            "right": (int(min(W, fx + 0.8 * fw)), int(min(W, fx + 4.0 * fw))),
        }
        threshold = max(0.6, 0.12 * fw)            # px/frame, scales with distance
        events = []
        for side, (x0, x1) in regions.items():
            if x1 - x0 < 4 or bottom - top < 4:
                continue
            patch = flow[top:bottom, x0:x1]
            mag = np.hypot(patch[..., 0], patch[..., 1])
            moving = mag > threshold
            if moving.mean() < 0.02:
                self._tracks[side].add(now, 0.0)
                continue
            vx = float(np.median(patch[..., 0][moving]))
            vy = float(np.median(np.abs(patch[..., 1][moving])))
            self._tracks[side].add(now, vx if abs(vx) > 1.1 * vy else 0.0)
            if (self._tracks[side].reversals(now, threshold) >= WAVE_MIN_REVERSALS
                    and now - self._last_wave >= WAVE_COOLDOWN_S):
                self._last_wave = now
                for track in self._tracks.values():
                    track.clear()
                # Image left is the owner's right hand side (camera faces them).
                events.append(self._event(now, "wave", hand="right" if side == "left" else "left"))
        return events

    # ------------------------------------------------------------ outputs
    def state(self) -> dict[str, Any]:
        """Godot-facing state. x/y are from the viewer's side: +x = owner's
        right (screen right), +y = up; size ~ face width / frame width."""
        if not self.seen or self.face is None:
            return {"type": "vision", "seen": False}
        f = self.face
        return {"type": "vision", "seen": True, "x": round(-(f.cx * 2 - 1), 3), "y": round(-(f.cy * 2 - 1), 3),
                "size": round(f.w, 3), "facing": bool(f.frontal)}

    def summary(self, now: float | None = None) -> dict[str, Any]:
        """Model-facing facts (miko_get_vision). No image content."""
        now = time.monotonic() if now is None else now
        recent = [{"event": n, "seconds_ago": round(now - t, 1), **d} for t, n, d in self.events if now - t <= 120]
        if not self.seen or self.face is None:
            return {"status": "watching", "seen": False, "recent_events": recent}
        f = self.face
        x = -(f.cx * 2 - 1)
        where = "center" if abs(x) < 0.33 else ("owner's right side" if x > 0 else "owner's left side")
        distance = "close" if f.w > 0.32 else ("far" if f.w < 0.12 else "normal distance")
        return {"status": "watching", "seen": True, "where": where, "distance": distance,
                "looking_at_miko": bool(f.frontal), "recent_events": recent}


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
                next_at = now + 0.09                    # ~11 fps analysis
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
            if self.react and event["event"] in ("wave", "arrived"):
                try:
                    self.react(message)
                except Exception as error:  # never let a reaction kill the camera loop
                    print("MIKO VISION: reaction failed:", type(error).__name__)
        changed = state.get("seen") != self._last_state.get("seen")
        if changed or now - self._last_state_at >= STATE_INTERVAL_S:
            self._last_state_at = now
            self._last_state = state
            self.emit(state)
