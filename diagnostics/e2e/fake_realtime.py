"""A fake OpenAI Realtime server for end-to-end tests (no network, no key).

It follows the rules and timing of the real API that matter for Miko:
- one active response at a time: a second response.create is rejected with
  conversation_already_has_active_response (carrying the client event_id);
- audio is streamed in real time (24 kHz PCM16 chunks), with transcript
  deltas, then output_audio.done / output_audio_transcript.done / response.done;
- response.cancel stops the stream (no audio.done for the cut item) and ends
  it with status "cancelled"; cancelling nothing is an error;
- committed input gets a transcript after a delay; empty commits fail;
- some owner turns call the miko_perform_action tool first.
Latencies are randomised so races appear the way they do in real use.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random

from websockets.asyncio.server import serve

PCM_CHUNK = b"\x00\x00" * 2400            # 100 ms at 24 kHz


class FakeRealtimeConnection:
    def __init__(self, ws, stats, rng):
        self.ws = ws
        self.stats = stats
        self.rng = rng
        self.audio = 0
        self.active = None
        self.counter = 0
        self.turns = 0
        self.pending_tool = {}
        self.tool_turn = -1

    def _id(self, prefix):
        self.counter += 1
        return f"{prefix}_{self.counter}"

    async def emit(self, event):
        try:
            await self.ws.send(json.dumps(event, ensure_ascii=False))
        except Exception:
            pass

    async def run(self):
        await self.emit({"type": "session.created", "session": {}})
        async for raw in self.ws:
            event = json.loads(raw)
            await self.handle(event)

    async def handle(self, event):
        kind = event.get("type")
        self.stats["client_events"][kind] = self.stats["client_events"].get(kind, 0) + 1
        if kind == "session.update":
            await self.emit({"type": "session.updated", "session": {}})
        elif kind == "input_audio_buffer.append":
            self.audio += len(base64.b64decode(event.get("audio", "")))
        elif kind == "input_audio_buffer.clear":
            self.audio = 0
            await self.emit({"type": "input_audio_buffer.cleared"})
        elif kind == "input_audio_buffer.commit":
            if self.audio < 3200:
                await self.emit({"type": "error", "error": {"code": "input_audio_buffer_commit_empty",
                                                             "message": "buffer too small", "event_id": event.get("event_id")}})
                return
            self.audio = 0
            item = self._id("item_user")
            self.turns += 1
            await self.emit({"type": "input_audio_buffer.committed", "item_id": item})
            asyncio.create_task(self._transcribe(item, self.turns))
        elif kind == "conversation.item.create":
            item = event.get("item", {})
            if item.get("type") == "function_call_output":
                self.pending_tool.pop(item.get("call_id"), None)
            await self.emit({"type": "conversation.item.created", "item": {"id": self._id("item"), **item}})
        elif kind in ("conversation.item.truncate", "conversation.item.delete"):
            # Like the real API, operations on items the server no longer
            # has fail with a per-request error.
            if self.rng.random() < 0.5:
                await self.emit({"type": "error", "error": {"type": "invalid_request_error", "code": "item_not_found",
                                                             "message": "Item not found", "event_id": event.get("event_id")}})
            else:
                await self.emit({"type": kind + "d", "item_id": event.get("item_id")})
        elif kind == "response.create":
            await asyncio.sleep(self.rng.uniform(0.0, 0.08))
            if self.active:
                self.stats["collisions"] += 1
                await self.emit({"type": "error", "error": {
                    "code": "conversation_already_has_active_response",
                    "message": "Conversation already has an active response in progress: " + self.active,
                    "event_id": event.get("event_id")}})
                return
            response_id = self._id("resp")
            self.active = response_id
            asyncio.create_task(self._respond(response_id, event.get("response", {})))
        elif kind == "response.cancel":
            await asyncio.sleep(self.rng.uniform(0.02, 0.15))
            target = event.get("response_id") or self.active
            if not self.active or (target and target != self.active):
                await self.emit({"type": "error", "error": {"code": "response_cancel_not_active",
                                                             "message": "no active response", "event_id": event.get("event_id")}})
                return
            cancelled, self.active = self.active, None
            self.stats["cancelled"] += 1
            await self.emit({"type": "response.done", "response": {"id": cancelled, "status": "cancelled", "output": []}})

    async def _transcribe(self, item, number):
        await asyncio.sleep(self.rng.uniform(0.15, 0.9))
        text = "תקפוץ" if number % 5 == 0 else f"משפט {number} של הבעלים"
        await self.emit({"type": "conversation.item.input_audio_transcription.completed", "item_id": item, "transcript": text})

    async def _respond(self, response_id, options):
        metadata = options.get("metadata") or {}
        spontaneous = "instructions" in options
        await asyncio.sleep(self.rng.uniform(0.05, 0.35))
        if self.active != response_id:
            return
        await self.emit({"type": "response.created", "response": {"id": response_id, "metadata": metadata, "status": "in_progress"}})
        # Every fifth owner turn: a tool call first (the host answers it and
        # asks for a new response).
        if (not spontaneous and self.turns % 5 == 0 and options.get("tool_choice") != "none"
                and not self.pending_tool and self.tool_turn != self.turns):
            self.tool_turn = self.turns
            call_id = self._id("call")
            self.pending_tool[call_id] = True
            await asyncio.sleep(self.rng.uniform(0.1, 0.4))
            if self.active != response_id:
                return
            self.active = None
            await self.emit({"type": "response.done", "response": {"id": response_id, "status": "completed", "output": [
                {"type": "function_call", "call_id": call_id, "name": "miko_perform_action",
                 "arguments": json.dumps({"action": "jump", "times": 2})}]}})
            self.stats["tool_calls"] += 1
            return
        item = self._id("item_assistant")
        text = ("אוי, מה זה היה" if spontaneous else f"תשובה {response_id}")
        chunks = self.rng.randint(4, 8) if spontaneous else self.rng.randint(10, 30)
        for index in range(chunks):
            await asyncio.sleep(0.1 * self.rng.uniform(0.3, 0.9))
            if self.active != response_id:
                return              # cancelled: no audio.done for this item
            await self.emit({"type": "response.output_audio.delta", "response_id": response_id, "item_id": item,
                             "content_index": 0, "delta": base64.b64encode(PCM_CHUNK).decode("ascii")})
            if index % 3 == 0:
                await self.emit({"type": "response.output_audio_transcript.delta", "response_id": response_id,
                                 "item_id": item, "delta": text[:1 + index]})
        if self.active != response_id:
            return
        await self.emit({"type": "response.output_audio.done", "response_id": response_id, "item_id": item, "content_index": 0})
        await self.emit({"type": "response.output_audio_transcript.done", "response_id": response_id, "item_id": item, "transcript": text})
        self.active = None
        self.stats["completed_spontaneous" if spontaneous else "completed_owner"] += 1
        await self.emit({"type": "response.done", "response": {"id": response_id, "status": "completed", "output": [
            {"type": "message", "id": item}]}})


async def serve_fake(port, stats, seed=1):
    rng = random.Random(seed)

    async def handler(ws):
        stats["connections"] += 1
        await FakeRealtimeConnection(ws, stats, rng).run()

    return await serve(handler, "127.0.0.1", port, max_size=8 * 1024 * 1024)


def new_stats():
    return {"connections": 0, "collisions": 0, "cancelled": 0, "tool_calls": 0,
            "completed_owner": 0, "completed_spontaneous": 0, "client_events": {}}
