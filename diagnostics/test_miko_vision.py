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


# A room-like textured background: real scenes have texture, which is what
# lets the engine tell a moving camera from a moving hand.
_ROOM = cv2.GaussianBlur(RNG.integers(40, 170, (480, 640, 3)).astype(np.uint8), (0, 0), 1.5)
_ROOM = cv2.addWeighted(_ROOM, 0.6, np.full_like(_ROOM, (92, 104, 112)), 0.4, 0)
# Furniture-like structure (shelves, frames, a door edge): real rooms have
# edges that survive downscaling, which is what camera-motion checks rely on.
for _x, _y, _w, _h, _c in [(10, 20, 120, 40, (40, 60, 90)), (500, 30, 110, 150, (200, 190, 170)),
                            (20, 300, 90, 160, (60, 40, 30)), (530, 330, 90, 120, (30, 90, 60)),
                            (150, 10, 60, 90, (180, 200, 210)), (460, 220, 40, 80, (20, 20, 30)),
                            (5, 150, 70, 60, (150, 120, 60)), (580, 200, 50, 90, (210, 210, 220))]:
    cv2.rectangle(_ROOM, (_x, _y), (_x + _w, _y + _h), _c, -1)
    cv2.rectangle(_ROOM, (_x, _y), (_x + _w, _y + _h), (15, 15, 15), 3)
for _y in (120, 260, 420):
    cv2.line(_ROOM, (0, _y), (640, _y + 6), (35, 30, 25), 4)


def frame(face_x=240, hand=None):
    img = _ROOM.copy()
    face = cv2.resize(FACE, (200, 200))
    img[120:320, face_x:face_x + 200] = face
    if hand is not None:
        hx, hy = int(hand[0]), int(hand[1])
        img[hy:hy + 70, hx:hx + 50] = HAND
    return img


def opencv_engine():
    """The OpenCV-only path (no MediaPipe): the synthetic textured "hand" is
    not a hand to a landmark model, so flow evidence is what is tested."""
    return miko_vision.VisionEngine(use_mediapipe=False)


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
        engine = opencv_engine()
        run(engine, [frame()] * 4)
        waving = [frame(hand=(470 + 45 * math.sin(i * 2 * math.pi / 5.0), 150)) for i in range(26)]
        events = run(engine, waving, start=1.0)
        self.assertEqual(events.count("wave"), 1, events)

    def test_big_hand_close_to_the_camera_is_a_wave_not_a_shake(self):
        engine = opencv_engine()
        run(engine, [frame()] * 4)
        big = cv2.resize(HAND, (200, 260))
        frames = []
        for i in range(26):
            img = frame()
            x = int(380 + 60 * math.sin(i * 2 * math.pi / 5.0))
            img[60:320, x:x + 200] = big
            frames.append(img)
        events = run(engine, frames, start=1.0)
        self.assertIn("wave", events)
        self.assertNotIn("shaken", events)

    def test_something_new_in_the_room_is_noticed(self):
        engine = miko_vision.VisionEngine()
        run(engine, [frame()] * 30)
        with_box = frame()
        with_box[330:450, 40:170] = (30, 160, 220)       # a box put on the desk
        events = run(engine, [with_box] * 30, start=3.0)
        self.assertIn("scene_changed", events)
        self.assertEqual(events.count("scene_changed"), 1)

    def test_still_hand_and_vertical_motion_are_not_waves(self):
        engine = opencv_engine()
        run(engine, [frame()] * 4)
        still = [frame(hand=(480, 150))] * 15
        vertical = [frame(hand=(480, 150 + 40 * math.sin(i * 2 * math.pi / 5.0))) for i in range(26)]
        events = run(engine, still + vertical, start=1.0)
        self.assertNotIn("wave", events)

    def test_whole_person_moving_is_not_a_wave(self):
        engine = opencv_engine()
        run(engine, [frame()] * 4)
        sway = [frame(face_x=int(240 + 60 * math.sin(i * 2 * math.pi / 5.0))) for i in range(26)]
        self.assertNotIn("wave", run(engine, sway, start=1.0))

    def test_wave_detected_even_while_the_head_moves(self):
        engine = opencv_engine()
        run(engine, [frame()] * 4)
        waving = [frame(face_x=int(240 + 6 * math.sin(i)), hand=(470 + 45 * math.sin(i * 2 * math.pi / 5.0), 150))
                  for i in range(26)]
        self.assertIn("wave", run(engine, waving, start=1.0))

    def test_shaking_the_camera_is_noticed_and_is_not_a_wave(self):
        engine = opencv_engine()
        run(engine, [frame()] * 4)
        base = frame()
        shaken = []
        for i in range(20):
            dx = int(14 * math.sin(i * 2 * math.pi / 4.0))
            dy = int(6 * math.cos(i * 2 * math.pi / 4.0))
            shaken.append(np.roll(np.roll(base, dx, axis=1), dy, axis=0))
        events = run(engine, shaken, start=1.0)
        self.assertEqual(events.count("shake_started"), 1, events)
        self.assertNotIn("wave", events)
        self.assertFalse(engine.camera_stable)          # waves/room changes are suspended meanwhile

    @unittest.skipUnless(miko_vision.miko_perception and miko_vision.miko_perception.available(), "MediaPipe not installed")
    def test_with_a_hand_model_motion_alone_needs_stronger_evidence(self):
        # MediaPipe often loses a fast, blurred hand (or runs slowly): motion
        # evidence may still confirm a clear wave, but with a higher bar.
        engine = miko_vision.VisionEngine()
        run(engine, [frame()] * 4)
        brief = [frame(hand=(470 + 45 * math.sin(i * 2 * math.pi / 5.0), 150)) for i in range(5)]
        self.assertNotIn("wave", run(engine, brief, start=1.0))         # one back-and-forth is not enough
        self.assertEqual(engine.flow_wave.confirm_at, 0.86)
        waving = [frame(hand=(470 + 45 * math.sin(i * 2 * math.pi / 5.0), 150)) for i in range(26)]
        self.assertEqual(run(engine, waving, start=2.0).count("wave"), 1)

    def test_covering_and_uncovering_the_lens(self):
        engine = miko_vision.VisionEngine()
        dark = np.full((480, 640, 3), 8, np.uint8)
        events = run(engine, [frame()] * 4 + [dark] * 12 + [frame()] * 6)
        self.assertEqual(events, ["arrived", "covered", "uncovered"])
        self.assertTrue(engine.seen)                 # covering is not leaving

    def test_lights_turning_off_is_a_light_change(self):
        engine = miko_vision.VisionEngine()
        dim = (frame().astype(np.float32) * 0.45).astype(np.uint8)
        events = run(engine, [frame()] * 25 + [dim] * 15)
        self.assertIn("light_changed", events)
        self.assertNotIn("covered", events)

    def test_sitting_still_with_sensor_noise_triggers_nothing(self):
        engine = miko_vision.VisionEngine()
        noisy = []
        for i in range(80):
            f = frame(face_x=240 + (i % 3) - 1).astype(np.int16)
            f += RNG.normal(0, 5, f.shape).astype(np.int16)
            noisy.append(np.clip(f, 0, 255).astype(np.uint8))
        self.assertEqual(run(engine, noisy), ["arrived"])

    def test_leaving_and_returning(self):
        engine = miko_vision.VisionEngine()
        empty = _ROOM.copy()
        events = run(engine, [frame()] * 4 + [empty] * 150 + [frame()] * 4)
        self.assertEqual(events, ["arrived", "left", "arrived"])
        self.assertFalse(miko_vision.VisionEngine().state()["seen"])

    def test_service_emits_state_and_reacts_to_wave(self):
        emitted, reactions = [], []
        service = miko_vision.VisionService(emitted.append, reactions.append, settings_path="/nonexistent/x.json")
        service.engine = opencv_engine()
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


class VisionProcessTests(unittest.TestCase):
    """Sight in its own process: events still reach the host's reaction
    first, state reaches Godot, and a crashed worker is restarted."""

    def wait(self, predicate, seconds=40.0):
        import time
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_device_frames_through_the_worker_process(self):
        import os
        import time
        emitted, reactions = [], []

        def react(message):
            message["level"] = "SHORT_VOCAL"      # the host annotates before Godot sees it
            reactions.append(dict(message))
        service = miko_vision.VisionProcess(emitted.append, react, settings_path="/nonexistent/x.json")
        os.environ["MIKO_CAMERA_INDEX"] = "99"      # no webcam in tests
        service.start()
        try:
            self.assertTrue(self.wait(lambda: any(e.get("type") == "vision_status" for e in emitted)))
            ok, jpeg = cv2.imencode(".jpg", frame(), [cv2.IMWRITE_JPEG_QUALITY, 80])
            for _ in range(40):
                service.feed_jpeg(jpeg.tobytes())
                time.sleep(0.07)
            self.assertTrue(self.wait(lambda: [r["event"] for r in reactions] == ["arrived"]), reactions)
            godot = [e for e in emitted if e.get("type") == "vision_event"]
            self.assertEqual(godot[0].get("level"), "SHORT_VOCAL")
            self.assertTrue(any(e.get("type") == "vision" and e.get("seen") for e in emitted))
            self.assertTrue(self.wait(lambda: service.summary().get("seen") is True))
            # A crashed worker comes back by itself.
            first = service._proc.pid
            service._proc.kill()
            self.assertTrue(self.wait(lambda: service._proc.pid != first and service._proc.poll() is None))
        finally:
            service.stop()
            os.environ.pop("MIKO_CAMERA_INDEX", None)


class GreetingTests(unittest.TestCase):
    def _hub(self, history_at=0.0):
        import threading
        import miko_realtime
        from miko_behavior import ResponsePolicy

        class Brain:
            state_lock = threading.Lock()
            miko = {"realtime_conversation_history": [{"role": "Owner", "text": "x", "at": history_at}]}

        class Native:
            def __init__(self):
                self.api, self.responding, self.input_bytes, self.calls = object(), False, 0, []
                self.ptt_active, self.active_response_id, self.pending_responses = False, "", {}

            async def spontaneous(self, note):
                self.calls.append(note)
                return True

            async def ensure_session(self):
                self.api = object()

        class AlwaysTalk:
            def random(self):
                return 0.0

        hub = miko_realtime.RealtimeHub.__new__(miko_realtime.RealtimeHub)
        hub.browser_id, hub.vision_spoken_at, hub.brain = None, 0.0, Brain()
        hub.perception_policy = ResponsePolicy(rng=AlwaysTalk())
        hub.loop = None
        native = Native()
        hub.native = {native}
        return hub, native

    def react(self, hub, event):
        import asyncio
        decision = hub.vision_reaction(dict(event))
        asyncio.run(hub._vision_greeting(dict(event), decision))
        return decision

    def test_wave_greets_once_then_the_body_alone_reacts(self):
        hub, native = self._hub()
        first = self.react(hub, {"event": "wave", "confidence": 0.93})
        second = self.react(hub, {"event": "wave", "confidence": 0.95})      # cooldown
        self.assertEqual(first.name, "SHORT_VOCAL")
        self.assertEqual(second.name, "ANIMATION_ONLY")
        self.assertEqual(len(native.calls), 1)
        self.assertTrue(native.calls[0].startswith("[perception] "))
        self.assertNotIn("נופף", native.calls[0])                    # nothing Hebrew to echo

    def test_low_confidence_wave_gets_no_reaction_beyond_a_glance(self):
        hub, native = self._hub()
        decision = self.react(hub, {"event": "wave", "confidence": 0.55})
        self.assertEqual(decision.name, "MICRO")
        self.assertEqual(native.calls, [])

    def test_no_words_while_busy_closed_or_mid_conversation(self):
        import time
        hub, native = self._hub()
        native.responding = True
        self.assertEqual(self.react(hub, {"event": "wave", "confidence": 0.9}).name, "ANIMATION_ONLY")
        native.responding, native.api = False, None
        self.react(hub, {"event": "arrived"})          # quiet observation never opens a session
        hub2, native2 = self._hub(history_at=time.time())
        self.assertLess(self.react(hub2, {"event": "wave", "confidence": 0.9}).level, 4)
        self.assertEqual(native.calls + native2.calls, [])

    def test_minor_sensor_events_never_speak(self):
        hub, native = self._hub()
        for kind in ("device_moved", "device_nudged", "shake_active", "motion", "scene_changed", "light_changed",
                     "looked_away", "tilted_head"):
            self.assertLessEqual(self.react(hub, {"event": kind}).level, 1, kind)
        self.assertEqual(native.calls, [])


class PerceptionContextTests(unittest.TestCase):
    def test_facts_are_added_silently_before_the_owner_speaks(self):
        import asyncio
        import miko_realtime
        hub = miko_realtime.RealtimeHub.__new__(miko_realtime.RealtimeHub)
        hub.vision_context({"event": "smiled"})
        hub.vision_context({"event": "gesture", "gesture": "thumbs_up"})
        hub.vision_context({"event": "unknown_thing"})

        class Native:
            api = object()
            sent = []

            async def api_send(self, event):
                self.sent.append(event)
        native = Native()
        asyncio.run(hub.flush_vision_context(native))
        self.assertEqual(len(native.sent), 1)
        text = native.sent[0]["item"]["content"][0]["text"]
        self.assertEqual(native.sent[0]["item"]["role"], "system")
        self.assertIn("smiled", text)
        self.assertIn("thumbs up", text)
        asyncio.run(hub.flush_vision_context(native))     # nothing new: nothing sent
        self.assertEqual(len(native.sent), 1)


class PlayfulReactionTests(GreetingTests):
    def test_covering_and_a_real_shake_get_spoken_reactions(self):
        hub, native = self._hub()
        self.react(hub, {"event": "covered"})
        hub.perception_policy.last_any_vocal = -1e9        # past the social budget
        self.react(hub, {"event": "shake_started", "confidence": 0.9})
        self.react(hub, {"event": "shake_ended", "duration": 2.0})
        self.assertEqual(len(native.calls), 2)
        self.assertIn("covered", native.calls[0])
        self.assertIn("shook", native.calls[1])

    def test_a_brief_bump_shake_gets_body_only(self):
        hub, native = self._hub()
        self.assertEqual(self.react(hub, {"event": "shake_ended", "duration": 0.6}).name, "ANIMATION_ONLY")
        self.assertEqual(native.calls, [])

    def test_closed_session_opens_once_for_a_wave(self):
        hub, native = self._hub()
        native.api = None
        self.react(hub, {"event": "wave", "confidence": 0.9})
        self.assertEqual(len(native.calls), 1)          # opened, then spoke


class SpontaneousSpeechTests(unittest.TestCase):
    def _session(self):
        import threading
        import miko_realtime

        class Brain:
            state_lock = threading.Lock()
            miko = {"realtime_conversation_history": [{"role": "Miko", "text": "ברור. אני כאן, רגוע וזמין.", "at": 1.0}]}

        Brain.owner_request_active = threading.Event()

        class Hub:
            brain = Brain()

            async def flush_vision_context(self, native):
                pass

        session = miko_realtime.NativeSession(Hub(), ws=None)
        session.api = object()
        sent = []

        async def api_send(event):
            sent.append(event)
        session.api_send = api_send
        return session, sent

    def test_note_is_a_system_item_and_reply_has_its_own_auto_turn(self):
        import asyncio
        session, sent = self._session()
        session.state.turn_id = "turn_owner"
        self.assertTrue(asyncio.run(session.spontaneous("[perception] The owner waved hello to you just now.")))
        self.assertEqual(sent[0]["item"]["role"], "system")
        self.assertEqual(sent[1]["type"], "response.create")
        self.assertEqual(sent[1]["response"]["tool_choice"], "none")
        self.assertIn("רגוע וזמין", sent[1]["response"]["instructions"])   # told not to repeat it
        origin = next(iter(session.pending_responses.values()))[0]
        self.assertTrue(origin.startswith("auto_"))
        self.assertEqual(session.state.turn_id, "turn_owner")           # owner turn untouched

    def test_never_speaks_over_a_response_or_the_owner(self):
        import asyncio
        session, sent = self._session()
        session.responding = True
        self.assertFalse(asyncio.run(session.spontaneous("x")))
        session.responding, session.input_bytes = False, 4800
        self.assertFalse(asyncio.run(session.spontaneous("x")))
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
