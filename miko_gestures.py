"""Gesture recognition with explicit evidence: is that really a wave?

A wave is a temporal pattern, not a frame: an open hand, raised to about
head height, swinging side to side a few times at a hand-like rhythm, while
the camera is still and the head is not doing the same motion. The detector
collects that evidence over a sliding window and decides in three levels:

    nothing          - not enough evidence (most of the time)
    POSSIBLE_WAVE    - partial evidence; logged, never acted on
    CONFIRMED_WAVE   - strong evidence (confidence >= 0.8); one event per wave

A false wave is worse than a missed one, so every hard gate must pass and
the reasons for rejecting a candidate are logged. After a confirmed wave
the same continuing motion is the same wave, and a new wave needs a pause
and a cooldown.

Sources: "hand" (MediaPipe hand landmarks; positions in image coordinates)
and "flow" (OpenCV optical flow when MediaPipe is not installed; the
position is the integrated sideways motion of the moving region). Flow
evidence is weaker: the vision engine lets it confirm only when there is no
hand model at all.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Any

from miko_log import debug, log, throttled

CONFIRM = 0.8
POSSIBLE = 0.45


@dataclass
class HandSample:
    t: float
    x: float                  # 0..1 image coords (or integrated flow position)
    y: float
    size: float = 0.1         # palm length relative to the frame width
    openness: float = -1.0    # 0..1 fingers extended, -1 unknown
    gesture: str = ""
    cx: float = -1.0          # image x of the hand when x is not a position (flow)


@dataclass
class FaceSample:
    t: float
    cx: float
    cy: float
    w: float
    h: float


def _zigzag(times, values, amplitude):
    """Direction reversals whose swing exceeds `amplitude`; returns the
    times of the turning points and the swing sizes."""
    turns, swings = [], []
    if len(values) < 3:
        return turns, swings
    direction, extreme, extreme_t = 0, values[0], times[0]
    anchor = values[0]
    for t, v in zip(times[1:], values[1:]):
        if direction == 0:
            if v - anchor >= amplitude:
                direction, extreme, extreme_t = 1, v, t
            elif anchor - v >= amplitude:
                direction, extreme, extreme_t = -1, v, t
            continue
        if direction == 1:
            if v > extreme:
                extreme, extreme_t = v, t
            elif extreme - v >= amplitude:
                turns.append(extreme_t)
                swings.append(extreme - v)
                direction, extreme, extreme_t = -1, v, t
        else:
            if v < extreme:
                extreme, extreme_t = v, t
            elif v - extreme >= amplitude:
                turns.append(extreme_t)
                swings.append(v - extreme)
                direction, extreme, extreme_t = 1, v, t
    return turns, swings


def _std(values):
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _corr(a, b):
    if len(a) < 4 or len(a) != len(b):
        return 0.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den > 1e-9 else 0.0


class WaveDetector:
    def __init__(self, source: str = "hand", window: float = 2.4, cooldown: float = 6.0,
                 release: float = 1.2, can_confirm: bool = True) -> None:
        self.source = source
        self.window = window
        self.cooldown = cooldown
        self.release = release
        self.can_confirm = can_confirm
        self.samples: deque = deque(maxlen=90)       # (t, HandSample | None)
        self.faces: deque = deque(maxlen=90)
        self._last_confirm = -1e9
        self._last_turn = -1e9
        self._paused = True
        self._possible_logged = False
        self.last_evidence: dict[str, Any] = {}

    def reset(self, reason: str = "") -> None:
        if self.samples and any(s for _, s in self.samples):
            debug("GESTURE", "wave evidence cleared", source=self.source, reason=reason)
        self.samples.clear()
        self.faces.clear()
        self._possible_logged = False

    # ------------------------------------------------------------ main step
    def update(self, t: float, hand: HandSample | None, face: FaceSample | None,
               camera_stable: bool = True) -> list[dict[str, Any]]:
        if not camera_stable:
            if any(s for _, s in self.samples):
                throttled("wave_camera_" + self.source, 3.0, "GESTURE", "wave evidence dropped",
                          source=self.source, suppression="camera_or_device_moving")
            self.reset("camera moving")
            return []
        self.samples.append((t, hand))
        if face is not None:
            self.faces.append(face)
        while self.samples and t - self.samples[0][0] > self.window:
            self.samples.popleft()
        while self.faces and t - self.faces[0].t > self.window:
            self.faces.popleft()
        if t - self._last_turn >= self.release:
            self._paused = True                         # the hand rested: a later wave is a new one
        present = [s for _, s in self.samples if s is not None]
        if len(present) < 5:
            self._possible_logged = False
            return []
        evidence = self._evidence(present)
        self.last_evidence = evidence
        if evidence["last_turn"] > self._last_turn:
            self._last_turn = evidence["last_turn"]
        confidence = evidence["confidence"]

        # The same continuing motion after a confirmed wave is the same wave;
        # a new wave needs a pause and the cooldown.
        if t - self._last_confirm < self.cooldown or not self._paused:
            if confidence >= POSSIBLE:
                reason = "cooldown" if self._paused else "same_wave_continuing"
                throttled("wave_repeat_" + self.source, 2.0, "GESTURE", "wave repeat suppressed",
                          source=self.source, confidence=confidence, suppression=reason)
            return []

        gates = self._failed_gates(evidence)
        if confidence >= CONFIRM and not gates and self.can_confirm:
            self._last_confirm = t
            self._paused = False
            self.samples.clear()
            self._possible_logged = False
            log("GESTURE", "wave", decision="CONFIRMED_WAVE", source=self.source, confidence=confidence,
                swings=evidence["swings"], duration=evidence["duration"], coverage=evidence["coverage"],
                openness=evidence["openness"], raised=evidence["raised"])
            return [{"event": "wave", "confidence": round(confidence, 2), "hand": evidence["hand"],
                     "source": self.source}]
        if confidence >= POSSIBLE and not self._possible_logged:
            self._possible_logged = True
            why = ",".join(gates) if gates else ("flow_cannot_confirm" if not self.can_confirm else "low_confidence")
            log("GESTURE", "wave candidate", decision="POSSIBLE_WAVE", source=self.source,
                confidence=confidence, swings=evidence["swings"], suppression=why)
        return []

    # ------------------------------------------------------------ evidence
    def _evidence(self, present: list[HandSample]) -> dict[str, Any]:
        times = [s.t for s in present]
        xs = [s.x for s in present]
        ys = [s.y for s in present]
        palm = sorted(s.size for s in present)[len(present) // 2]
        amplitude = max(0.025, 0.35 * palm) if self.source == "hand" else 0.04
        turns, swings = _zigzag(times, xs, amplitude)
        n = len(turns)
        span_start = times[0]
        window_samples = [s for t, s in self.samples if t >= span_start]
        coverage = len(present) / max(1, len(window_samples))
        duration = (turns[-1] - turns[0]) if n >= 2 else 0.0
        intervals = [b - a for a, b in zip(turns, turns[1:])]
        half_period = sorted(intervals)[len(intervals) // 2] if intervals else 0.0
        rhythm = 0.09 <= half_period <= 0.9 if intervals else False
        x_range = (max(xs) - min(xs)) if xs else 0.0
        y_range = (max(ys) - min(ys)) if ys else 0.0
        horizontal = x_range >= 1.3 * y_range
        known = [s for s in present if s.openness >= 0.0 or s.gesture]
        if known:
            openness = sum(1.0 if (s.openness >= 0.75 or s.gesture == "open_palm") else 0.0 for s in known) / len(known)
        else:
            openness = 0.5                                  # unknown (flow)
        faces = list(self.faces)
        if faces:
            f = faces[-1]
            raised = sum(1 for s in present if s.y <= f.cy + 1.1 * f.h) / len(present)
        else:
            raised = sum(1 for s in present if s.y <= 0.7) / len(present)
        # Moves with the head/body (or a camera pan the motion gate missed):
        # the face swings in step with the "hand".
        co_motion = False
        if len(faces) >= 5:
            face_x = [f.cx for f in faces]
            hand_at = [min(present, key=lambda s, ft=f.t: abs(s.t - ft)).x for f in faces]
            if self.source == "flow":
                co_motion = _corr(face_x, hand_at) > 0.6 and _std(face_x) > 0.012
            else:
                co_motion = _std(face_x) > 0.5 * max(_std(hand_at), 1e-6) and _corr(face_x, hand_at) > 0.6
        s_swings = max(0.0, min(1.0, (n - 1) / 3.0))
        s_duration = max(0.0, min(1.0, duration / 0.9))
        s_coverage = max(0.0, min(1.0, (coverage - 0.3) / 0.45))
        confidence = (0.32 * s_swings + 0.14 * s_duration + 0.14 * s_coverage + 0.14 * openness
                      + 0.1 * raised + 0.1 * (1.0 if horizontal else 0.2) + 0.06 * (1.0 if rhythm else 0.0))
        if co_motion:
            confidence *= 0.4
        last = present[-1]
        image_x = last.cx if last.cx >= 0.0 else last.x
        hand = "right" if image_x < 0.5 else "left"   # image left = the owner's right
        return {"confidence": round(confidence, 3), "swings": n, "duration": round(duration, 2),
                "coverage": round(coverage, 2), "openness": round(openness, 2), "raised": round(raised, 2),
                "horizontal": horizontal, "rhythm": rhythm, "co_motion": co_motion,
                "last_turn": turns[-1] if turns else -1e9, "hand": hand}

    def _failed_gates(self, e: dict[str, Any]) -> list[str]:
        failed = []
        if e["swings"] < 3:
            failed.append("too_few_swings")
        if e["duration"] < 0.5:
            failed.append("too_short")
        if e["coverage"] < 0.5:
            failed.append("hand_not_tracked")
        if e["openness"] < 0.5:
            failed.append("hand_not_open")
        if e["raised"] < 0.5:
            failed.append("hand_low")
        if not e["horizontal"]:
            failed.append("not_sideways")
        if not e["rhythm"]:
            failed.append("rhythm")
        if e["co_motion"]:
            failed.append("moves_with_head")
        return failed
