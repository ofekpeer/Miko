"""Physical interaction: what actually happened to Miko's device?

A stream of device-motion measurements becomes a few semantic events:

    noise / tiny movement      -> nothing
    short bump                 -> device_nudged     (low priority)
    one-way move / lid tilted  -> device_moved      (low priority)
    deliberate shake           -> shake_started, shake_active (<= 1/s), shake_ended

Measurements come from the camera today (global image shift between frames,
see miko_vision.py) and from the device IMU later (gyro, deg/s). The same
episode logic serves both with different units/thresholds.

Signal processing: dead zone -> smoothed energy -> episode with hysteresis
(start/end thresholds and hold times) -> accumulated features (duration,
peak, path, net displacement, sustained direction reversals) -> one
classification, a confidence, and a refractory period after a shake. A lid
that wobbles after a touch has decaying swings and is a "move", not a shake:
a shake needs repeated swings that keep their strength.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from miko_log import debug, log


@dataclass
class EpisodeParams:
    unit: str
    dead_zone: float          # magnitudes below this are noise
    start: float              # smoothed energy that opens an episode
    end: float                # ...and below which it can close
    start_hold: float         # seconds above `start` before it counts
    end_hold: float           # seconds below `end` before it closes
    reversal: float           # minimum signed speed for a direction to count
    shake_peak: float         # a shake must reach this peak
    shake_min_duration: float
    shake_swings: int         # sustained direction reversals needed...
    shake_window: float       # ...within this many seconds (a shake is brisk)
    sustain: float            # a swing counts only if >= sustain * peak
    nudge_max_duration: float
    moved_min_path: float     # total motion for "moved"
    refractory: float         # after a shake, thresholds are raised for this long
    refractory_gain: float
    settle: float             # after any episode the scene is "unstable" this long
    smoothing: float          # EMA time constant (s)


# Camera: global image shift in pixels per frame at 160 px width (~15 fps).
CAMERA = EpisodeParams(unit="px/frame", dead_zone=0.6, start=1.1, end=0.7, start_hold=0.12, end_hold=0.4,
                       reversal=1.0, shake_peak=2.4, shake_min_duration=0.45, shake_swings=3, shake_window=1.3, sustain=0.45,
                       nudge_max_duration=0.45, moved_min_path=0.6, refractory=2.5, refractory_gain=1.6,
                       settle=1.2, smoothing=0.12)
# IMU: angular velocity in deg/s.
IMU = EpisodeParams(unit="deg/s", dead_zone=8.0, start=30.0, end=18.0, start_hold=0.08, end_hold=0.35,
                    reversal=40.0, shake_peak=170.0, shake_min_duration=0.4, shake_swings=3, shake_window=1.3, sustain=0.45,
                    nudge_max_duration=0.4, moved_min_path=25.0, refractory=2.5, refractory_gain=1.6,
                    settle=0.8, smoothing=0.06)


@dataclass
class _Episode:
    started: float
    peak: float = 0.0
    path: float = 0.0
    net: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    signs: list = field(default_factory=lambda: [0, 0, 0])
    extremes: list = field(default_factory=lambda: [0.0, 0.0, 0.0])   # current half-swing
    previous: list = field(default_factory=lambda: [0.0, 0.0, 0.0])   # last completed half-swing
    swings: int = 0               # sustained reversals (strong both ways)
    swing_times: list = field(default_factory=list)
    jolts: list = field(default_factory=list)       # (t, sign) of blurred, direction-uncertain frames
    weak_swings: int = 0
    shaking: bool = False
    last_active: float = 0.0
    below_since: float | None = None


class PhysicalInteractionDetector:
    """Feed `update(t, vector)` with one motion measurement per sample."""

    def __init__(self, params: EpisodeParams = CAMERA, source: str = "camera") -> None:
        self.p = params
        self.source = source
        self.energy = 0.0
        self._last_t: float | None = None
        self._above_since: float | None = None
        self.episode: _Episode | None = None
        self._last_shake_end = -1e9
        self._last_episode_end = -1e9
        self.last_classification = ""

    # ------------------------------------------------------------ state
    @property
    def shaking(self) -> bool:
        return bool(self.episode and self.episode.shaking)

    def stable(self, now: float) -> bool:
        """True when the device/camera is still and has settled: scene-
        relative perception (waves, room changes, gaze) is trustworthy."""
        return self.episode is None and now - self._last_episode_end >= self.p.settle

    # ------------------------------------------------------------ main step
    def update(self, t: float, vector, quality: float = 1.0) -> list[dict[str, Any]]:
        vec = [float(v) for v in vector] + [0.0] * (3 - len(vector))
        dt = 0.066 if self._last_t is None else max(1e-3, min(0.5, t - self._last_t))
        self._last_t = t
        magnitude = math.sqrt(sum(v * v for v in vec)) * max(0.0, min(1.0, quality))
        if magnitude < self.p.dead_zone:
            magnitude = 0.0
            vec = [0.0, 0.0, 0.0]
        alpha = 1.0 - math.exp(-dt / self.p.smoothing)
        self.energy += (magnitude - self.energy) * alpha
        gain = self.p.refractory_gain if t - self._last_shake_end < self.p.refractory else 1.0
        events: list[dict[str, Any]] = []

        if self.episode is None:
            if self.energy > self.p.start * gain:
                self._above_since = self._above_since if self._above_since is not None else t
                if t - self._above_since >= self.p.start_hold:
                    self.episode = _Episode(started=self._above_since)
                    debug("PHYSICAL", "episode_start", source=self.source, energy=self.energy)
            else:
                self._above_since = None
            if self.episode is None:
                return events

        ep = self.episode
        ep.peak = max(ep.peak, magnitude)
        ep.path += magnitude * dt
        for axis in range(3):
            ep.net[axis] += vec[axis] * dt
            value = vec[axis]
            if abs(value) < self.p.reversal * gain:
                continue
            sign = 1 if value > 0 else -1
            if ep.signs[axis] and sign != ep.signs[axis]:
                # A half-swing just ended. The reversal is a sustained swing
                # when it and the half-swing before it were both strong
                # relative to the episode peak (a fading wobble is not).
                done = abs(ep.extremes[axis])
                before = ep.previous[axis]
                if before > 0.0:
                    if min(done, before) >= self.p.sustain * ep.peak:
                        ep.swings += 1
                        ep.swing_times.append(t)
                    else:
                        ep.weak_swings += 1
                ep.previous[axis] = done
                ep.extremes[axis] = value
            elif abs(value) > abs(ep.extremes[axis]):
                ep.extremes[axis] = value
            ep.signs[axis] = sign

        duration = t - ep.started
        if quality < 1.0 and magnitude >= self.p.reversal * gain:
            axis = 0 if abs(vec[0]) >= abs(vec[1]) else 1
            ep.jolts.append((t, 1 if vec[axis] >= 0 else -1))
        brisk = sum(1 for st in ep.swing_times if t - st <= self.p.shake_window)
        # A real shake blurs the picture; many blurred jolts with changing
        # direction inside the window are a shake even when swings cannot be
        # measured cleanly.
        recent = [sign for jt, sign in ep.jolts if t - jt <= self.p.shake_window]
        flips = sum(1 for a, b in zip(recent, recent[1:]) if a != b)
        blurry_shake = len(recent) >= 6 and (flips >= 2 or len(recent) >= 9)
        if not ep.shaking and (brisk >= self.p.shake_swings or blurry_shake) \
                and duration >= self.p.shake_min_duration and ep.peak >= self.p.shake_peak * gain * (0.6 if blurry_shake else 1.0):
            ep.shaking = True
            ep.last_active = t
            confidence = self._shake_confidence(ep, duration, gain)
            events.append({"event": "shake_started", "confidence": round(confidence, 2),
                           "intensity": round(ep.peak / self.p.shake_peak, 2), "source": self.source})
            log("PHYSICAL", "shake_started", source=self.source, peak=ep.peak, swings=ep.swings,
                duration=duration, confidence=confidence)
        elif ep.shaking and t - ep.last_active >= 1.0 and self.energy > self.p.end:
            ep.last_active = t
            events.append({"event": "shake_active", "duration": round(duration, 1),
                           "intensity": round(self.energy / self.p.shake_peak, 2), "source": self.source})

        if self.energy < self.p.end:
            ep.below_since = ep.below_since if ep.below_since is not None else t
            if t - ep.below_since >= self.p.end_hold:
                events.extend(self._finish(ep, ep.below_since))
        else:
            ep.below_since = None
        return events

    def _shake_confidence(self, ep: _Episode, duration: float, gain: float) -> float:
        swings = min(1.0, (ep.swings - self.p.shake_swings + 1) / 3.0)
        strength = min(1.0, (ep.peak / (self.p.shake_peak * gain) - 1.0) / 1.5)
        length = min(1.0, duration / 1.2)
        return max(0.0, min(1.0, 0.55 + 0.2 * swings + 0.15 * strength + 0.1 * length))

    def _finish(self, ep: _Episode, ended: float) -> list[dict[str, Any]]:
        self.episode = None
        self._above_since = None
        self._last_episode_end = ended
        duration = ended - ep.started
        net = math.sqrt(sum(v * v for v in ep.net))
        if ep.shaking:
            self._last_shake_end = ended
            self.last_classification = "shake"
            log("PHYSICAL", "shake_ended", source=self.source, duration=duration, peak=ep.peak, swings=ep.swings)
            return [{"event": "shake_ended", "duration": round(duration, 1),
                     "intensity": round(ep.peak / self.p.shake_peak, 2), "swings": ep.swings, "source": self.source}]
        if duration <= self.p.nudge_max_duration and ep.path < self.p.moved_min_path * 2:
            self.last_classification = "nudge"
            log("PHYSICAL", "episode -> device_nudged (no reaction)", source=self.source, peak=ep.peak,
                duration=duration, swings=ep.swings, weak_swings=ep.weak_swings)
            return [{"event": "device_nudged", "confidence": 0.6, "source": self.source}]
        if ep.path >= self.p.moved_min_path:
            self.last_classification = "moved"
            reason = "fading wobble" if ep.weak_swings else "one-way"
            log("PHYSICAL", f"episode -> device_moved ({reason}, not a shake)", source=self.source, peak=ep.peak,
                duration=duration, swings=ep.swings, weak_swings=ep.weak_swings, net=net)
            return [{"event": "device_moved", "confidence": 0.7, "source": self.source}]
        self.last_classification = "noise"
        debug("PHYSICAL", "episode ignored", reason="too small", peak=ep.peak)
        return []


class ImuInterpreter:
    """Device IMU samples -> physical events (+ orientation changes).

    samples: (t, ax, ay, az, gx, gy, gz) with accel in g and gyro in deg/s.
    """

    def __init__(self) -> None:
        self.detector = PhysicalInteractionDetector(IMU, source="imu")
        self._gravity: list[float] | None = None
        self._settled_gravity: list[float] | None = None
        self._tilt_since: float | None = None

    def update(self, sample) -> list[dict[str, Any]]:
        t, ax, ay, az, gx, gy, gz = (float(v) for v in sample[:7])
        events = self.detector.update(t, (gx, gy, gz))
        g = [ax, ay, az]
        if self._gravity is None:
            self._gravity = g
            self._settled_gravity = g
            return events
        self._gravity = [a + (b - a) * 0.1 for a, b in zip(self._gravity, g)]
        if self.detector.stable(t) and self._settled_gravity is not None:
            angle = _angle(self._gravity, self._settled_gravity)
            if angle > 50.0:
                self._tilt_since = self._tilt_since if self._tilt_since is not None else t
                if t - self._tilt_since > 0.8:
                    self._settled_gravity = list(self._gravity)
                    self._tilt_since = None
                    log("PHYSICAL", "orientation_changed", source="imu", angle=angle)
                    events.append({"event": "orientation_changed", "angle": round(angle), "source": "imu"})
            else:
                self._tilt_since = None
                self._settled_gravity = [a + (b - a) * 0.02 for a, b in zip(self._settled_gravity, self._gravity)]
        return events


def _angle(a, b) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    cos = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / (na * nb)))
    return math.degrees(math.acos(cos))
