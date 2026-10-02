"""Offline checks for Miko's local sight (miko_vision.py).

Synthetic video: a real face photo on a plain background, with a textured
"hand" that waves beside it, moves vertically, or stays still. Run:
python diagnostics/test_miko_vision.py   (needs opencv-python, numpy)
"""

from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import miko_vision  # noqa: E402

if not miko_vision.available():  # pragma: no cover
    raise SystemExit("OpenCV is required for this test")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

FACE = cv2.imread(str(Path(__file__).parent / "data" / "face_sample.jpg"))
RNG = np.random.default_rng(7)
HAND = (RNG.integers(60, 200, (70, 50, 3))).astype(np.uint8)
HAND = cv2.GaussianBlur(HAND, (5, 5), 0)


def frame(face_x=240, hand=None):
    img = np.full((480, 640, 3), (92, 104, 112), np.uint8)
    face = cv2.resize(FACE, (200, 200))
    img[120:320, face_x:face_x + 200] = face
    if hand is not None:
        hx, hy = int(hand[0]), int(hand[1])
        img[hy:hy + 70, hx:hx + 50] = HAND
    return img


def run(engine, frames, fps=11.0, start=0.0):
    events = []
    for i, f in enumerate(frames):
        events += [e["event"] for e in engine.process(f, start + i / fps)]
    return events


class VisionTests(unittest.TestCase):
    def test_face_arrives_and_position_is_reported_from_viewer_side(self):
        engine = miko_vision.VisionEngine()
        events = run(engine, [frame(face_x=60)] * 6)
        self.assertIn("arrived", events)
        state = engine.state()
        self.assertTrue(state["seen"])
        # Face on the image's left = owner's right = screen right (+x).
        self.assertGreater(state["x"], 0.2)
        self.assertEqual(engine.summary()["where"], "owner's right side")

    def test_wave_beside_face_is_detected_once(self):
        engine = miko_vision.VisionEngine()
        run(engine, [frame()] * 4)
        waving = [frame(hand=(470 + 45 * math.sin(i * 2 * math.pi / 5.0), 150)) for i in range(26)]
        events = run(engine, waving, start=1.0)
        self.assertEqual(events.count("wave"), 1, events)

    def test_still_hand_and_vertical_motion_are_not_waves(self):
        engine = miko_vision.VisionEngine()
        run(engine, [frame()] * 4)
        still = [frame(hand=(480, 150))] * 15
        vertical = [frame(hand=(480, 150 + 40 * math.sin(i * 2 * math.pi / 5.0))) for i in range(26)]
        events = run(engine, still + vertical, start=1.0)
        self.assertNotIn("wave", events)

    def test_whole_person_moving_is_not_a_wave(self):
        engine = miko_vision.VisionEngine()
        run(engine, [frame()] * 4)
        sway = [frame(face_x=int(240 + 60 * math.sin(i * 2 * math.pi / 5.0))) for i in range(26)]
        self.assertNotIn("wave", run(engine, sway, start=1.0))

    def test_leaving_and_returning(self):
        engine = miko_vision.VisionEngine()
        empty = np.full((480, 640, 3), (92, 104, 112), np.uint8)
        events = run(engine, [frame()] * 4 + [empty] * 150 + [frame()] * 4)
        self.assertEqual(events, ["arrived", "left", "arrived"])
        self.assertFalse(miko_vision.VisionEngine().state()["seen"])

    def test_service_emits_state_and_reacts_to_wave(self):
        emitted, reactions = [], []
        service = miko_vision.VisionService(emitted.append, reactions.append, settings_path="/nonexistent/x.json")
        service.engine = miko_vision.VisionEngine()
        for i in range(4):
            service.feed_frame(frame(), i / 11.0)
        for i in range(26):
            service.feed_frame(frame(hand=(470 + 45 * math.sin(i * 2 * math.pi / 5.0), 150)), 1.0 + i / 11.0)
        kinds = [e["type"] for e in emitted]
        self.assertIn("vision", kinds)
        self.assertEqual([r["event"] for r in reactions], ["arrived", "wave"])
        ok, jpeg = cv2.imencode(".jpg", frame(), [cv2.IMWRITE_JPEG_QUALITY, 70])
        service.feed_jpeg(jpeg.tobytes())
        self.assertEqual(service.source, "device")


class GreetingTests(unittest.TestCase):
    def _hub(self, history_at=0.0):
        import asyncio  # noqa: F401
        import threading
        import miko_realtime

        class Brain:
            state_lock = threading.Lock()
            miko = {"realtime_conversation_history": [{"role": "Owner", "text": "x", "at": history_at}]}

        class Native:
            def __init__(self):
                self.api, self.responding, self.input_bytes, self.calls = object(), False, 0, []

            async def request_response(self, response=None):
                self.calls.append(response)

        hub = miko_realtime.RealtimeHub.__new__(miko_realtime.RealtimeHub)
        hub.browser_id, hub.vision_spoken_at, hub.brain = None, 0.0, Brain()
        native = Native()
        hub.native = {native}
        return hub, native

    def test_wave_greets_once_in_open_idle_session(self):
        import asyncio
        hub, native = self._hub()
        asyncio.run(hub._vision_greeting({"event": "wave"}))
        asyncio.run(hub._vision_greeting({"event": "wave"}))     # cooldown
        self.assertEqual(len(native.calls), 1)
        self.assertEqual(native.calls[0]["tool_choice"], "none")
        self.assertIn("מנופף", native.calls[0]["instructions"])

    def test_no_greeting_while_busy_closed_or_mid_conversation(self):
        import asyncio
        import time
        hub, native = self._hub()
        native.responding = True
        asyncio.run(hub._vision_greeting({"event": "wave"}))
        native.responding, native.api = False, None
        asyncio.run(hub._vision_greeting({"event": "arrived"}))
        hub2, native2 = self._hub(history_at=time.time())
        asyncio.run(hub2._vision_greeting({"event": "wave"}))
        self.assertEqual(native.calls + native2.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
