"""Event interpretation: one physical action -> one semantic event, and no
hallucinated waves. Covers the owner's acceptance list A-H:

  A tiny movement -> no shake          E camera/device moving, head visible -> no wave
  B one shake -> one event             F head moving left/right -> no wave
  C 3 s of shaking -> one episode      G a real wave -> recognized once, high confidence
  D shaking stops -> back to steady    H repeated waving -> cooldown

python diagnostics/test_miko_events.py   (vision-engine cases need opencv-python)
"""

from __future__ import annotations

import math
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import miko_physical  # noqa: E402
from miko_gestures import CONFIRM, FaceSample, HandSample, WaveDetector  # noqa: E402

FPS = 15.0


def feed(detector, velocities, start=0.0, fps=FPS):
    """velocities: list of (vx, vy) per frame; returns event names."""
    events = []
    for i, v in enumerate(velocities):
        events += [e["event"] for e in detector.update(start + i / fps, v)]
    return events


def still(seconds, fps=FPS, noise=0.0, seed=1):
    rng = random.Random(seed)
    return [(rng.uniform(-noise, noise), rng.uniform(-noise, noise)) for _ in range(int(seconds * fps))]


def shake(seconds, amplitude=5.0, hz=3.0, fps=FPS):
    return [(amplitude * math.sin(2 * math.pi * hz * i / fps + 0.4), 0.3 * amplitude * math.cos(2 * math.pi * hz * i / fps))
            for i in range(int(seconds * fps))]


class PhysicalEpisodes(unittest.TestCase):
    def test_A_tiny_movement_and_sensor_noise_give_nothing(self):
        d = miko_physical.PhysicalInteractionDetector()
        self.assertEqual(feed(d, still(10, noise=0.5)), [])
        # Typing on the laptop: small irregular jolts.
        rng = random.Random(3)
        typing = [(rng.choice([0, 0, 0, 0.9, -0.8]), rng.choice([0, 0, 0.7])) for _ in range(150)]
        events = feed(d, typing, start=10)
        self.assertNotIn("shake_started", events)
        self.assertTrue(set(events) <= {"device_nudged"}, events)

    def test_B_one_shake_is_one_event_pair(self):
        d = miko_physical.PhysicalInteractionDetector()
        events = feed(d, still(1) + shake(1.0) + still(3))
        self.assertEqual(events.count("shake_started"), 1, events)
        self.assertEqual(events.count("shake_ended"), 1, events)
        self.assertNotIn("device_moved", events)

    def test_C_three_seconds_of_shaking_is_one_episode(self):
        d = miko_physical.PhysicalInteractionDetector()
        events = feed(d, still(1) + shake(3.0) + still(3))
        self.assertEqual(events.count("shake_started"), 1, events)
        self.assertEqual(events.count("shake_ended"), 1, events)
        self.assertLessEqual(events.count("shake_active"), 3)      # progress, at most 1/s
        reactions = [e for e in events if e in ("shake_started", "shake_ended")]
        self.assertEqual(len(reactions), 2)

    def test_D_settles_back_to_steady_and_stays_quiet(self):
        d = miko_physical.PhysicalInteractionDetector()
        feed(d, still(1) + shake(2.0))
        self.assertTrue(d.shaking)
        self.assertFalse(d.stable(3.0))
        events = feed(d, still(6), start=3.0)
        self.assertEqual(events, ["shake_ended"])
        self.assertFalse(d.shaking)
        self.assertTrue(d.stable(9.0))
        self.assertLess(d.energy, 0.1)

    def test_lid_wobble_after_a_touch_is_a_move_not_a_shake(self):
        d = miko_physical.PhysicalInteractionDetector()
        wobble = [(4.0 * math.exp(-(i / FPS) / 0.3) * math.sin(2 * math.pi * 4 * i / FPS + 0.5), 0.0) for i in range(20)]
        events = feed(d, still(1) + wobble + still(3))
        self.assertNotIn("shake_started", events)
        self.assertEqual(len(events), 1, events)
        self.assertIn(events[0], ("device_moved", "device_nudged"))

    def test_sliding_the_laptop_is_a_move(self):
        d = miko_physical.PhysicalInteractionDetector()
        events = feed(d, still(1) + [(2.2, 0.3)] * 12 + still(3))
        self.assertEqual(events, ["device_moved"])

    def test_refractory_after_a_shake(self):
        d = miko_physical.PhysicalInteractionDetector()
        events = feed(d, still(1) + shake(1.0) + still(1.0) + shake(0.8, amplitude=3.0) + still(3))
        self.assertEqual(events.count("shake_started"), 1, events)   # the weak aftershock is not a new shake


class ImuEpisodes(unittest.TestCase):
    def samples(self, seconds, gyro=lambda t: (0, 0, 0), accel=lambda t: (0, 0, 1.0), start=0.0, hz=100):
        return [(start + i / hz, *accel(start + i / hz), *gyro(start + i / hz)) for i in range(int(seconds * hz))]

    def run_imu(self, imu, samples):
        events = []
        for s in samples:
            events += [e["event"] for e in imu.update(s)]
        return events

    def test_resting_device_gives_nothing(self):
        rng = random.Random(2)
        imu = miko_physical.ImuInterpreter()
        noise = self.samples(5, gyro=lambda t: (rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-3, 3)))
        self.assertEqual(self.run_imu(imu, noise), [])

    def test_shaking_the_device_is_one_episode(self):
        imu = miko_physical.ImuInterpreter()
        events = self.run_imu(imu, self.samples(1) + self.samples(1.5, gyro=lambda t: (400 * math.sin(2 * math.pi * 4 * t), 0, 80), start=1)
                              + self.samples(3, start=2.5))
        self.assertEqual(events.count("shake_started"), 1, events)
        self.assertEqual(events.count("shake_ended"), 1, events)

    def test_turning_the_device_over_is_an_orientation_change(self):
        imu = miko_physical.ImuInterpreter()
        upright = self.samples(2)
        turn = self.samples(0.5, gyro=lambda t: (180, 0, 0), accel=lambda t: (0, math.sin((t - 2) * math.pi), math.cos((t - 2) * math.pi)), start=2)
        lying = self.samples(4, accel=lambda t: (0, 1.0, 0.0), start=2.5)
        events = self.run_imu(imu, upright + turn + lying)
        self.assertEqual(events.count("orientation_changed"), 1, events)
        self.assertNotIn("shake_started", events)


FACE = FaceSample(0, 0.45, 0.45, 0.3, 0.32)


def wave_frames(detector, seconds, start=0.0, x0=0.72, amp=0.07, hz=2.2, y=0.36, openness=1.0, size=0.08,
                face=FACE, face_motion=0.0, stable=True, vertical=False, present=lambda i: True):
    events = []
    for i in range(int(seconds * FPS)):
        t = start + i / FPS
        phase = math.sin(2 * math.pi * hz * t)
        fx = face.cx + face_motion * phase
        f = FaceSample(t, fx, face.cy, face.w, face.h) if face is not None else None
        hand = None
        if present(i):
            hx = x0 if vertical else x0 + amp * phase + (fx - face.cx if face is not None else 0.0)
            hy = y + (amp * phase if vertical else 0.0)
            hand = HandSample(t, hx, hy, size, openness, "open_palm" if openness >= 0.75 else "")
        events += detector.update(t, hand, f, stable)
    return events


class WaveEvidence(unittest.TestCase):
    def test_G_a_real_wave_is_recognized_once_with_high_confidence(self):
        d = WaveDetector("hand")
        events = wave_frames(d, 2.0)
        self.assertEqual([e["event"] for e in events], ["wave"], events)
        self.assertGreaterEqual(events[0]["confidence"], CONFIRM)
        self.assertEqual(events[0]["hand"], "left")             # image right = the owner's left

    def test_G_a_short_clear_hi_wave_counts(self):
        d = WaveDetector("hand")
        events = wave_frames(d, 0.8, hz=1.8)                     # left-right-left, open raised hand
        self.assertEqual([e["event"] for e in events], ["wave"], d.last_evidence)

    def test_two_swings_of_a_half_open_hand_do_not_count(self):
        self.assertEqual(wave_frames(WaveDetector("hand"), 0.8, hz=1.8, openness=0.5), [])

    def test_G_a_wave_with_tracking_dropouts_still_counts(self):
        d = WaveDetector("hand")
        events = wave_frames(d, 2.0, present=lambda i: i % 4 != 0)
        self.assertEqual([e["event"] for e in events], ["wave"])

    def test_F_head_moving_left_right_is_not_a_wave(self):
        # The head moves; the hand rests (or is absent).
        self.assertEqual(wave_frames(WaveDetector("hand"), 3.0, amp=0.0, face_motion=0.08), [])
        self.assertEqual(wave_frames(WaveDetector("hand"), 3.0, face_motion=0.08, present=lambda i: False), [])
        # The whole body sways with the hand in it: the hand moves only with the head.
        d = WaveDetector("hand")
        events = wave_frames(d, 3.0, amp=0.0, face_motion=0.07, x0=0.75)
        self.assertEqual(events, [])

    def test_hand_moving_with_the_head_is_rejected(self):
        d = WaveDetector("hand")
        events = []
        for i in range(45):
            t = i / FPS
            dx = 0.07 * math.sin(2 * math.pi * 2.0 * t)
            events += d.update(t, HandSample(t, 0.72 + dx, 0.36, 0.08, 1.0, "open_palm"), FaceSample(t, 0.45 + dx, 0.45, 0.3, 0.32))
        self.assertEqual(events, [])
        self.assertTrue(d.last_evidence["co_motion"])

    def test_E_no_wave_while_the_camera_or_device_moves(self):
        self.assertEqual(wave_frames(WaveDetector("hand"), 3.0, stable=False), [])

    def test_swinging_a_fist_or_a_low_hand_is_not_a_wave(self):
        self.assertEqual(wave_frames(WaveDetector("hand"), 2.5, openness=0.0), [])
        self.assertEqual(wave_frames(WaveDetector("hand"), 2.5, y=0.95), [])         # resting on the desk

    def test_vertical_or_tiny_or_brief_motion_is_not_a_wave(self):
        self.assertEqual(wave_frames(WaveDetector("hand"), 2.5, vertical=True), [])
        self.assertEqual(wave_frames(WaveDetector("hand"), 2.5, amp=0.008), [])
        self.assertEqual(wave_frames(WaveDetector("hand"), 0.35), [])

    def test_H_continuous_waving_is_one_wave_and_repeats_need_a_pause_and_cooldown(self):
        d = WaveDetector("hand", cooldown=6.0)
        self.assertEqual(len(wave_frames(d, 6.0)), 1)                   # one long wave
        d = WaveDetector("hand", cooldown=6.0)
        first = wave_frames(d, 1.6)
        rest = wave_frames(d, 1.5, start=1.6, amp=0.0)
        early = wave_frames(d, 1.6, start=3.1)                          # within the cooldown
        later = wave_frames(d, 1.5, start=4.7, amp=0.0) + wave_frames(d, 2.0, start=7.0)
        self.assertEqual((len(first), len(rest), len(early), len(later)), (1, 0, 0, 1))

    def test_flow_evidence_without_permission_never_confirms(self):
        d = WaveDetector("flow", can_confirm=False)
        self.assertEqual(wave_frames(d, 2.0, openness=-1.0), [])


try:
    import miko_vision
    HAVE_CV = miko_vision.available()
except Exception:  # pragma: no cover
    HAVE_CV = False


@unittest.skipUnless(HAVE_CV, "OpenCV not installed")
class CameraScenes(unittest.TestCase):
    """Synthetic camera video through the real vision engine."""

    @classmethod
    def setUpClass(cls):
        import test_miko_vision as tv
        cls.tv = tv

    def run_frames(self, engine, frames, fps=15.0, start=0.0):
        events = []
        for i, f in enumerate(frames):
            events += [e["event"] for e in engine.process(f, start + i / fps)]
        return events

    def engines(self):
        yield self.tv.opencv_engine()
        if miko_vision.miko_perception is not None and miko_vision.miko_perception.available():
            yield miko_vision.VisionEngine()

    def test_E_laptop_moved_around_with_the_owner_in_view_is_no_wave(self):
        import numpy as np
        for engine in self.engines():
            base = self.tv.frame()
            self.run_frames(engine, [base] * 5)
            frames = []
            for i in range(60):                                      # 4 s of uneven moving
                t = i / 15.0
                dx = int(30 * math.sin(2 * math.pi * 0.9 * t) + 8 * math.sin(2 * math.pi * 2.3 * t))
                dy = int(10 * math.sin(2 * math.pi * 0.6 * t))
                frames.append(np.roll(np.roll(base, dx, axis=1), dy, axis=0))
            events = self.run_frames(engine, frames + [base] * 30, start=1.0)
            self.assertNotIn("wave", events, events)
            self.assertNotIn("scene_changed", events)
            self.assertNotIn("motion", events)

    def test_F_head_moving_left_right_is_no_wave(self):
        for engine in self.engines():
            self.run_frames(engine, [self.tv.frame()] * 5)
            frames = [self.tv.frame(face_x=int(240 + 28 * math.sin(2 * math.pi * 1.4 * i / 15.0))) for i in range(60)]
            events = self.run_frames(engine, frames, start=1.0)
            self.assertNotIn("wave", events)
            self.assertNotIn("shake_started", events)      # the head moved, not Miko

    def test_shaking_the_head_no_in_front_of_a_plain_wall_is_not_a_device_shake(self):
        import numpy as np
        import cv2
        face = cv2.resize(self.tv.FACE, (200, 200))

        def wall(x):
            img = np.full((480, 640, 3), (190, 196, 200), np.uint8)
            img[120:320, x:x + 200] = face
            return img
        engine = self.tv.opencv_engine()
        self.run_frames(engine, [wall(240)] * 5)
        frames = [wall(int(240 + 22 * math.sin(2 * math.pi * 2.5 * i / 15.0))) for i in range(45)]
        events = self.run_frames(engine, frames, start=1.0)
        self.assertEqual([e for e in events if e.startswith(("shake", "device"))], [], events)

    def test_B_a_real_blurred_laptop_shake_is_one_shake(self):
        import numpy as np
        import cv2
        for engine in self.engines():
            base = self.tv.frame()
            self.run_frames(engine, [base] * 6)
            frames = []
            for i in range(24):                                      # ~1.6 s at 15 fps, 4 Hz shake
                dx = int(36 * math.sin(2 * math.pi * 4.0 * i / 15.0))
                moved = np.roll(base, dx, axis=1)
                k = max(3, abs(int(36 * math.cos(2 * math.pi * 4.0 * i / 15.0))) // 2 * 2 + 1)
                frames.append(cv2.blur(moved, (k, 3)))               # motion blur, like a real webcam
            events = self.run_frames(engine, frames + [base] * 30, start=1.0)
            self.assertEqual(events.count("shake_started"), 1, events)
            self.assertNotIn("wave", events)

    @unittest.skipUnless(miko_vision.miko_perception is not None and miko_vision.miko_perception.available(),
                         "MediaPipe not installed")
    def test_G_a_real_hand_waving_near_the_camera_is_a_wave_not_a_shake(self):
        """Real photos through MediaPipe: a big hand close to the lens used
        to look like the camera moving (false shake, wave suppressed)."""
        import cv2
        data = Path(__file__).parent / "data"
        face = cv2.resize(cv2.imread(str(data / "face_sample.jpg")), (180, 180))
        for photo in ("victory", "pointing_up"):
            for size in (170, 240):
                hand = cv2.resize(cv2.imread(str(data / f"gesture_{photo}.jpg")), (size, size))
                engine = miko_vision.VisionEngine()

                def scene(hx=None):
                    img = self.tv._ROOM.copy()
                    img[150:330, 120:300] = face
                    if hx is not None:
                        img[20:20 + size, int(hx):int(hx) + size] = hand
                    return img
                self.run_frames(engine, [scene()] * 8)
                frames = [scene(640 - size - 60 + 50 * math.sin(2 * math.pi * 2.0 * i / 15.0)) for i in range(36)]
                events = self.run_frames(engine, frames, start=1.0)
                self.assertEqual(events.count("wave"), 1, (photo, size, events))
                self.assertFalse([e for e in events if e.startswith("shake")], (photo, size, events))

    def test_A_still_camera_with_noise_gives_no_physical_events(self):
        import numpy as np
        rng = np.random.default_rng(5)
        engine = self.tv.opencv_engine()
        frames = []
        for i in range(90):
            f = self.tv.frame().astype(np.int16) + rng.normal(0, 4, (480, 640, 3)).astype(np.int16)
            frames.append(np.clip(f, 0, 255).astype(np.uint8))
        events = self.run_frames(engine, frames)
        self.assertEqual([e for e in events if e.startswith(("shake", "device"))], [])
        self.assertTrue(engine.camera_stable)


if __name__ == "__main__":
    unittest.main(verbosity=1)
