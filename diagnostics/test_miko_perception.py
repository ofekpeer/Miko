"""Checks for the rich (MediaPipe) perception layer: real hand-gesture photos
through the whole engine, and expression/head-gesture state machines driven
by measured face values over time.

python diagnostics/test_miko_perception.py   (needs mediapipe, opencv-python)
"""

from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import miko_perception as P  # noqa: E402

DATA = Path(__file__).parent / "data"


def face(t=0.0, **values):
    pose = {k: values.pop(k) for k in ("yaw", "pitch", "roll") if k in values}
    return P.FaceObs(0.5, 0.45, 0.3, 0.4, blend=values, **pose)


def drive(analyzer, frames, fps=15.0, start=0.0):
    """frames: list of FaceObs; returns event names."""
    events = []
    for i, f in enumerate(frames):
        events += [e["event"] for e in analyzer.update(f, start + i / fps)]
    return events


class FaceAnalyzerTests(unittest.TestCase):
    def test_a_smile_is_one_event_not_a_flicker(self):
        a = P.FaceAnalyzer()
        neutral = [face()] * 15
        flicker = [face(mouthSmileLeft=0.8, mouthSmileRight=0.8)] * 2 + [face()] * 10
        smile = [face(mouthSmileLeft=0.8, mouthSmileRight=0.7)] * 30
        self.assertEqual(drive(a, neutral + flicker), [])
        self.assertEqual(drive(a, smile, start=2.0), ["smiled"])
        self.assertEqual(a.expression, "smiling")

    def test_laughing(self):
        a = P.FaceAnalyzer()
        frames = [face(mouthSmileLeft=0.8, mouthSmileRight=0.8, jawOpen=0.2 + 0.25 * (i % 2)) for i in range(30)]
        self.assertIn("laughing", drive(a, frames))
        self.assertEqual(a.expression, "laughing")

    def test_yawn_needs_a_long_wide_open_mouth(self):
        a = P.FaceAnalyzer()
        talk = [face(jawOpen=0.6 if i % 3 == 0 else 0.1) for i in range(30)]
        yawn = [face(jawOpen=0.8, eyeSquintLeft=0.4, eyeSquintRight=0.4)] * 30
        self.assertNotIn("yawned", drive(a, talk))
        self.assertIn("yawned", drive(a, yawn, start=3.0))

    def test_nod_yes_and_shake_no(self):
        a = P.FaceAnalyzer()
        nod = [face(pitch=10 * math.sin(i * 2 * math.pi / 7)) for i in range(24)]
        self.assertIn("nodded", drive(a, nod))
        b = P.FaceAnalyzer()
        shake = [face(yaw=14 * math.sin(i * 2 * math.pi / 7)) for i in range(24)]
        events = drive(b, shake)
        self.assertIn("shook_head", events)
        self.assertNotIn("nodded", events)

    def test_eyes_closed_then_open_and_a_wink(self):
        a = P.FaceAnalyzer()
        closed = [face(eyeBlinkLeft=0.9, eyeBlinkRight=0.9)] * 40
        events = drive(a, [face()] * 5 + closed + [face()] * 10)
        self.assertEqual([e for e in events if e.startswith("eyes")], ["eyes_closed", "eyes_opened"])
        b = P.FaceAnalyzer()
        wink = [face()] * 5 + [face(eyeBlinkLeft=0.9, eyeBlinkRight=0.1)] * 5 + [face()] * 5
        self.assertIn("winked", drive(b, wink))
        blink = [face(eyeBlinkLeft=0.9, eyeBlinkRight=0.9)] * 2 + [face()] * 5
        self.assertNotIn("winked", drive(P.FaceAnalyzer(), blink))

    def test_attention_and_where_they_look(self):
        a = P.FaceAnalyzer()
        events = drive(a, [face()] * 15 + [face(yaw=35)] * 40 + [face()] * 20)
        self.assertIn("looked_away", events)
        self.assertIn("looked_somewhere", events)
        self.assertIn("looked_at_miko", events)

    def test_surprise_frown_and_tilt(self):
        self.assertIn("surprised", drive(P.FaceAnalyzer(), [face(browInnerUp=0.7, eyeWideLeft=0.6, eyeWideRight=0.6)] * 15))
        self.assertIn("frowned", drive(P.FaceAnalyzer(), [face(mouthFrownLeft=0.6, mouthFrownRight=0.6)] * 30))
        self.assertIn("tilted_head", drive(P.FaceAnalyzer(), [face(roll=22)] * 20))


class HandAndCrowdTests(unittest.TestCase):
    def hand(self, x, gesture="open_palm", score=0.9, tip=(0.5, 0.3), base=(0.5, 0.4)):
        return P.HandObs(gesture, score, (x, 0.5), tip, base, "Right")

    def test_open_hand_swinging_is_a_wave(self):
        a = P.HandAnalyzer()
        events = []
        for i in range(30):
            events += [e["event"] for e in a.update([self.hand(0.6 + 0.08 * math.sin(i * 2 * math.pi / 6))], i / 15)]
        self.assertEqual(events.count("wave"), 1)

    def test_stable_gesture_once_and_pointing_direction(self):
        a = P.HandAnalyzer()
        out = []
        for i in range(20):
            out += a.update([self.hand(0.5, "thumbs_up")], i / 15)
        self.assertEqual([e.get("gesture") for e in out if e["event"] == "gesture"], ["thumbs_up"])
        b = P.HandAnalyzer()
        out = []
        for i in range(20):
            out += b.update([self.hand(0.5, "pointing", tip=(0.40, 0.4), base=(0.48, 0.42))], i / 15)
        point = [e for e in out if e.get("gesture") == "pointing"][0]
        self.assertGreater(point["x"], 0.3)           # image left = viewer's right

    def test_someone_joins_and_leaves(self):
        c = P.CrowdAnalyzer()
        events = []
        for i, n in enumerate([1] * 30 + [2] * 30 + [1] * 30):
            events += [e["event"] for e in c.update(n, i / 15)]
        self.assertEqual(events, ["someone_joined", "someone_left"])


@unittest.skipUnless(P.available(), "MediaPipe not installed")
class RealPhotoTests(unittest.TestCase):
    def test_gesture_photos_through_the_vision_engine(self):
        import cv2
        import numpy as np
        import miko_vision
        expected = {"thumbs_up": "thumbs_up", "thumbs_down": "thumbs_down", "victory": "peace", "pointing_up": "pointing"}
        for photo, name in expected.items():
            engine = miko_vision.VisionEngine()
            image = cv2.imread(str(DATA / f"gesture_{photo}.jpg"))
            frame = np.full((480, 640, 3), 100, np.uint8)
            hand = cv2.resize(image, (400, 400))
            frame[40:440, 120:520] = hand
            events = []
            for i in range(20):
                events += engine.process(frame, i / 15)
            gestures = [e.get("gesture") for e in events if e["event"] == "gesture"]
            self.assertEqual(gestures, [name], f"{photo}: {events}")


if __name__ == "__main__":
    unittest.main(verbosity=1)
