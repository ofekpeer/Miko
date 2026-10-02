"""Long voice session stress test: many push-to-talk turns with spontaneous
perception reactions interleaved at awkward moments. Every owner turn must
get an answer and the session must never end up stuck.

The fake model follows the Realtime API's rules that matter here: one active
response at a time (a second response.create fails with
conversation_already_has_active_response), cancel of an inactive response
fails, audio streams over time.

Run: python diagnostics/test_miko_long_session.py --brain miko_brain.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path
import random
import sys
import unittest

import test_miko_conversation as fixture

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if (PACKAGE_ROOT / "miko_realtime.py").is_file():
    sys.path.insert(0, str(PACKAGE_ROOT))

from miko_realtime import NativeSession, RealtimeHub  # noqa: E402


class FakeRealtimeModel:
    """Asynchronous stand-in for the OpenAI Realtime socket."""

    def __init__(self, stream_seconds: float = 0.25):
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.active = None              # response id
        self.counter = 0
        self.stream_seconds = stream_seconds
        self.errors: list[str] = []
        self.created_requests: list[dict] = []
        self.tasks: set = set()

    def _id(self, prefix):
        self.counter += 1
        return f"{prefix}_{self.counter}"

    async def send(self, raw):
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "input_audio_buffer.commit":
            item = self._id("user_item")
            await self.incoming.put({"type": "input_audio_buffer.committed", "item_id": item})
            await self.incoming.put({"type": "conversation.item.input_audio_transcription.completed",
                                     "item_id": item, "transcript": "משפט מספר " + str(self.counter)})
        elif kind == "response.create":
            if self.active:
                self.errors.append("conversation_already_has_active_response")
                await self.incoming.put({"type": "error", "error": {
                    "code": "conversation_already_has_active_response",
                    "message": "Conversation already has an active response in progress"}})
                return
            response_id = self._id("resp")
            self.active = response_id
            self.created_requests.append(event.get("response", {}))
            task = asyncio.create_task(self._stream(response_id, event.get("response", {})))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        elif kind == "response.cancel":
            if not self.active:
                await self.incoming.put({"type": "error", "error": {"code": "response_cancel_not_active",
                                                                     "message": "no active response"}})
                return
            response_id, self.active = self.active, None
            await self.incoming.put({"type": "response.done", "response": {"id": response_id, "status": "cancelled", "output": []}})

    async def _stream(self, response_id, options):
        metadata = options.get("metadata") or {}
        item = self._id("assistant_item")
        await self.incoming.put({"type": "response.created", "response": {"id": response_id, "metadata": metadata}})
        for _ in range(3):
            await asyncio.sleep(self.stream_seconds / 3)
            if self.active != response_id:
                return                    # cancelled: no more audio, no done marker
            await self.incoming.put({"type": "response.output_audio.delta", "response_id": response_id,
                                     "item_id": item, "content_index": 0,
                                     "delta": base64.b64encode(b"\0\0" * 480).decode("ascii")})
        if self.active != response_id:
            return
        await self.incoming.put({"type": "response.output_audio.done", "response_id": response_id, "item_id": item, "content_index": 0})
        await self.incoming.put({"type": "response.output_audio_transcript.done", "response_id": response_id,
                                 "item_id": item, "transcript": "תשובה " + response_id})
        self.active = None
        await self.incoming.put({"type": "response.done", "response": {"id": response_id, "status": "completed", "output": []}})

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.incoming.get()
        if event is None:
            raise StopAsyncIteration
        return json.dumps(event)

    async def close(self):
        await self.incoming.put(None)


class FakeGodot:
    def __init__(self):
        self.events: list[dict] = []

    async def send(self, raw):
        self.events.append(json.loads(raw))


class LongSessionStress(fixture.MikoFixture):
    def test_many_turns_with_spontaneous_reactions_never_get_stuck(self):
        asyncio.run(self._scenario(seed=7))

    def test_spontaneous_reaction_right_before_owner_speaks(self):
        asyncio.run(self._scenario(seed=11, reaction_before_start=True))

    async def _scenario(self, seed: int, turns: int = 25, reaction_before_start: bool = False):
        rng = random.Random(seed)
        hub = RealtimeHub(self.brain)
        hub.loop = asyncio.get_running_loop()
        hub.VISION_COOLDOWN = {k: 0 for k in RealtimeHub.VISION_COOLDOWN}
        hub.vision_talk_chance = {k: 1.0 for k in RealtimeHub.VISION_TALK_CHANCE}
        godot = FakeGodot()
        session = NativeSession(hub, godot)
        hub.native.add(session)
        model = FakeRealtimeModel()
        session.api = model
        session.reader = asyncio.create_task(session.api_events(model))
        events = ["wave", "covered", "uncovered", "shaken"]

        async def react():
            hub.vision_spoken_at = 0.0
            await hub._vision_greeting({"event": rng.choice(events)})

        async def until(predicate, seconds=5.0):
            for _ in range(int(seconds / 0.01)):
                if predicate():
                    return True
                await asyncio.sleep(0.01)
            return False

        try:
            for turn in range(turns):
                # Miko may speak up on its own between turns (or right before).
                if reaction_before_start or rng.random() < 0.5:
                    await react()
                    if not reaction_before_start:
                        await asyncio.sleep(rng.choice([0.0, 0.05, 0.2, 0.4]))
                done_before = sum(1 for e in godot.events if e.get("type") == "response_done" and e.get("status") == "completed")
                await session.client_event({"type": "start"})
                if rng.random() < 0.4:
                    await react()           # a wave while the owner is talking
                for _ in range(4):
                    await session.client_event({"type": "audio", "audio": base64.b64encode(b"\1\0" * 1200).decode("ascii")})
                await session.client_event({"type": "stop"})
                if rng.random() < 0.3:
                    await asyncio.sleep(0.05)
                    await react()           # a reaction while Miko answers
                answered = await until(lambda: sum(1 for e in godot.events if e.get("type") == "response_done"
                                                   and e.get("status") == "completed") > done_before
                                       and not session.responding and model.active is None)
                self.assertTrue(answered, f"turn {turn} got no completed answer; model errors={model.errors[-3:]} "
                                          f"responding={session.responding} active={model.active}")
                # Acknowledge playback like the real client does.
                for item in list(session.output_complete):
                    await session.client_event({"type": "playback", "item_id": item, "finished": True})
            # Collisions may happen (timing), but each owner turn is answered
            # exactly once and the window never sees an error for it.
            owner_answers = [r for r in model.created_requests if "instructions" not in r]
            self.assertEqual(len(owner_answers), turns)
            self.assertEqual([e for e in godot.events if e.get("type") == "error"], [])
            self.assertFalse(session.deferred_requests)
            print(f"collisions recovered: {len(model.errors)}")
        finally:
            await model.close()
            session.reader.cancel()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain", type=Path, default=fixture.DEFAULT_BRAIN)
    args = parser.parse_args()
    fixture.SOURCE = args.brain.expanduser().resolve()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LongSessionStress))
    raise SystemExit(0 if result.wasSuccessful() else 1)
