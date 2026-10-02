"""Offline lifecycle regression tests for Miko's Realtime host.

Run: python work/test_miko_realtime_host.py --brain work/miko_next/miko_brain.py

The shared fixture imports a copy of the brain under a temporary state root,
blocks sockets, and substitutes fake OpenAI and SMTP. This module never starts
the Realtime server, uses real credentials, or sends a real email.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

import test_miko_conversation as fixture

# A packaged diagnostic script is launched from diagnostics/ while the host
# modules are in the package root.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if (PACKAGE_ROOT / "miko_realtime.py").is_file():
    sys.path.insert(0, str(PACKAGE_ROOT))

from miko_realtime import NativeSession, RealtimeHub, VoiceState


class RealtimeHostLifecycle(fixture.MikoFixture):
    @classmethod
    def setUpClass(cls):
        # Windows creates its loopback wakeup socket with the event loop. Do
        # this before the fixture blocks all network connections.
        cls.loop = asyncio.new_event_loop()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.loop.close()

    def run_async(self, coroutine):
        return self.loop.run_until_complete(coroutine)

    def setUp(self):
        super().setUp()
        self.hub = RealtimeHub(self.brain)
        self.state = VoiceState(self.hub, "offline-session")

    def prepare_draft(self, *, turn_id="u1", name="דנה", address="dana@example.com", body="שלום"):
        self.state.begin_turn(turn_id, audio=False, text=f"שלח ל{name} {address} וכתוב {body}")
        result = self.state.tool_call("prepare-" + turn_id, "miko_prepare_email", {
            "mode": "new", "recipient_name": name, "to_address": address,
            "subject": "", "body": body, "source_quote": address, "reference": "",
        }, turn_id)
        self.assertEqual(result["status"], "draft_ready")
        return result["draft"]["id"]

    def play_readback(self, draft_id, *, item="reply", address="dana@example.com", body="שלום"):
        self.state.assistant_text(item, f"המייל אל {address}: {body}. לשלוח?")
        self.state.playback_complete(item)
        pending = self.brain.miko["pending_external_action"]
        self.assertEqual(pending["id"], draft_id)
        return pending

    def test_late_previous_transcript_does_not_override_current_turn(self):
        self.state.begin_turn("speech-1", audio=True)
        self.state.committed("item-1")
        self.state.begin_turn("speech-2", audio=True)
        self.state.committed("item-2")
        self.state.user_transcript("item-1", "כן תשלח")
        self.assertEqual(self.state.last_user, "")
        self.assertEqual(self.state.tools._user_text, "")
        self.state.user_transcript("item-2", "לא תשלח")
        self.assertEqual(self.state.last_user, "לא תשלח")
        self.assertEqual(self.state.tools._user_text, "לא תשלח")

    def test_committed_item_maps_to_current_audio_turn(self):
        self.state.begin_turn("speech-start", audio=True)
        self.state.committed("committed-item")
        self.state.user_transcript("committed-item", "שמעתי את הכתובת")
        self.assertEqual(self.state.input_item_turns["committed-item"], "speech-start")
        self.assertEqual(self.state.tools._turn_id, "speech-start")
        self.assertTrue(self.state.tools._has_user_audio)
        self.assertEqual(self.state.tools._user_text, "שמעתי את הכתובת")

    def test_late_commit_does_not_reassign_known_earlier_item(self):
        self.state.begin_turn("speech-1", audio=True)
        self.state.begin_turn("speech-2", audio=True)
        # A browser event report can arrive after the newer speech_started.
        # speech-1 already identifies the first turn and must keep that owner.
        self.state.committed("speech-1")
        self.state.user_transcript("speech-1", "כן תשלח")
        self.assertEqual(self.state.last_user, "")
        self.assertEqual(self.state.tools._user_text, "")

    def test_transcript_arriving_before_commit_is_attached_when_commit_arrives(self):
        self.state.begin_turn("speech-start", audio=True)
        # Browser HTTP event reports are asynchronous and may cross in flight.
        self.state.user_transcript("committed-item", "לא תשלח")
        self.state.committed("committed-item")
        self.assertEqual(self.state.tools._user_text, "לא תשלח")

    def test_readback_requires_address_and_body_after_playback(self):
        draft_id = self.prepare_draft()
        self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.state.assistant_text("reply-1", "לדנה: שלום. לשלוח?")
        self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.state.playback_complete("reply-1")
        self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.state.assistant_text("reply-2", "לדנה, dana@example.com. לשלוח?")
        self.state.playback_complete("reply-2")
        self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])
        pending = self.play_readback(draft_id, item="reply-3")
        self.assertTrue(pending["confirmation_active"])

    def test_exact_live_browser_readback_marks_presented(self):
        address = "ofekpeer3030@gmail.com"
        draft_id = self.prepare_draft(name="אופק", address=address, body="אני בדרך")
        transcript = (
            "טיוטה מיועדת לאופק בכתובת ofekpeer3030@gmail.com. "
            "התוכן הוא: אני בדרך. תאשר שוב כדי שאשלח."
        )
        self.hub.handle_browser_event(self.state, {
            "type": "response.output_audio_transcript.done", "item_id": "assistant-item",
            "transcript": transcript,
        })
        self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.hub.handle_browser_event(self.state, {
            "type": "output_audio_buffer.stopped", "item_id": "assistant-item", "audible": True,
        })
        pending = self.brain.miko["pending_external_action"]
        self.assertEqual(pending["id"], draft_id)
        self.assertTrue(pending["confirmation_active"])

    def test_playback_report_before_transcript_still_marks_presented(self):
        address = "ofekpeer3030@gmail.com"
        self.prepare_draft(name="אופק", address=address, body="אני בדרך")
        self.hub.handle_browser_event(self.state, {
            "type": "output_audio_buffer.stopped", "item_id": "assistant-item", "audible": True,
        })
        self.hub.handle_browser_event(self.state, {
            "type": "response.output_audio_transcript.done", "item_id": "assistant-item",
            "transcript": "לאופק בכתובת ofekpeer3030@gmail.com: אני בדרך. לשלוח?",
        })
        self.assertTrue(self.brain.miko["pending_external_action"]["confirmation_active"])

    def test_late_readback_transcript_after_next_speech_keeps_audible_draft(self):
        address = "ofekpeer3030@gmail.com"
        self.prepare_draft(name="אופק", address=address, body="אני בדרך")
        # The browser has heard the complete playback. Independent HTTP event
        # reports can let the next speech-start overtake the transcript report.
        self.hub.handle_browser_event(self.state, {
            "type": "output_audio_buffer.stopped", "item_id": "assistant-item", "audible": True,
        })
        self.state.begin_turn("confirmation", audio=True, text="כן תשלח")
        self.hub.handle_browser_event(self.state, {
            "type": "response.output_audio_transcript.done", "item_id": "assistant-item",
            "transcript": "טיוטה מיועדת לאופק בכתובת ofekpeer3030@gmail.com. התוכן הוא: אני בדרך. תאשר שוב כדי שאשלח.",
        })
        self.assertTrue(self.brain.miko["pending_external_action"]["confirmation_active"])

    def test_browser_normal_stop_after_next_speech_allows_confirmed_send(self):
        address = "ofekpeer3030@gmail.com"
        draft_id = self.prepare_draft(name="אופק", address=address, body="אני בדרך")
        self.hub.handle_browser_event(self.state, {
            "type": "response.output_audio_transcript.done", "item_id": "assistant-item",
            "transcript": "לאופק בכתובת ofekpeer3030@gmail.com: אני בדרך. לשלוח?",
        })
        self.hub.handle_browser_event(self.state, {
            "type": "input_audio_buffer.speech_started", "item_id": "approval-speech",
        })
        self.hub.handle_browser_event(self.state, {
            "type": "output_audio_buffer.stopped", "item_id": "assistant-item", "audible": True,
        })
        self.hub.handle_browser_event(self.state, {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "approval-speech", "transcript": "כן תשלח",
        })
        sent = self.state.tool_call("send-after-normal-stop", "miko_send_email", {
            "draft_id": draft_id, "confirmation_quote": "כן תשלח",
        }, "approval-speech")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(self.sent), 1)

    def test_response_id_recovers_late_transcript_and_stop_after_next_turn(self):
        draft_id = self.prepare_draft(name="אופק", address="ofekpeer3030@gmail.com", body="אני בדרך")
        self.hub.handle_browser_event(self.state, {
            "type": "response.created", "response": {"id": "response-u1"},
        })
        self.hub.handle_browser_event(self.state, {
            "type": "input_audio_buffer.speech_started", "item_id": "approval-speech",
        })
        self.hub.handle_browser_event(self.state, {
            "type": "response.output_audio_transcript.done", "response_id": "response-u1",
            "item_id": "assistant-u1",
            "transcript": "לאופק בכתובת ofekpeer3030@gmail.com: אני בדרך. לשלוח?",
        })
        self.hub.handle_browser_event(self.state, {
            "type": "output_audio_buffer.stopped", "item_id": "assistant-u1", "audible": True,
        })
        self.hub.handle_browser_event(self.state, {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "approval-speech", "transcript": "כן תשלח",
        })
        sent = self.state.tool_call("late-response-send", "miko_send_email", {
            "draft_id": draft_id, "confirmation_quote": "כן תשלח",
        }, "approval-speech")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(self.sent), 1)

    def test_clearing_old_item_keeps_new_draft_readback_association(self):
        old_id = self.prepare_draft(body="ישן")
        self.state.assistant_text("old-item", "לדנה בכתובת dana@example.com: ישן. לשלוח?")
        self.state.begin_turn("u2", audio=False, text="מייל חדש לדנה dana@example.com, כתוב חדש")
        new_result = self.state.tool_call("new-prepare", "miko_prepare_email", {
            "mode": "new", "recipient_name": "דנה", "to_address": "dana@example.com",
            "subject": "", "body": "חדש", "source_quote": "dana@example.com", "reference": "",
        }, "u2")
        self.assertEqual(new_result["status"], "draft_ready")
        new_id = new_result["draft"]["id"]
        self.assertNotEqual(old_id, new_id)
        self.state.assistant_text("new-item", "לדנה בכתובת dana@example.com: חדש. לשלוח?")
        self.hub.handle_browser_event(self.state, {
            "type": "output_audio_buffer.cleared", "item_id": "old-item",
        })
        self.assertEqual(self.state.output_draft.get("new-item"), new_id)
        self.state.playback_complete("new-item")
        self.assertTrue(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.state.begin_turn("u3", audio=False, text="כן תשלח")
        sent = self.state.tool_call("new-send", "miko_send_email", {
            "draft_id": new_id, "confirmation_quote": "כן תשלח",
        }, "u3")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(self.sent), 1)

    def test_browser_actual_clear_after_next_speech_discards_unplayed_readback(self):
        draft_id = self.prepare_draft()
        self.hub.handle_browser_event(self.state, {
            "type": "response.output_audio_transcript.done", "item_id": "assistant-item",
            "transcript": "לדנה בכתובת dana@example.com: שלום. לשלוח?",
        })
        self.hub.handle_browser_event(self.state, {
            "type": "input_audio_buffer.speech_started", "item_id": "new-speech",
        })
        self.hub.handle_browser_event(self.state, {
            "type": "output_audio_buffer.cleared", "item_id": "assistant-item",
        })
        self.hub.handle_browser_event(self.state, {
            "type": "output_audio_buffer.stopped", "item_id": "assistant-item", "audible": True,
        })
        self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.hub.handle_browser_event(self.state, {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "new-speech", "transcript": "כן תשלח",
        })
        blocked = self.state.tool_call("blocked-after-clear", "miko_send_email", {
            "draft_id": draft_id, "confirmation_quote": "כן תשלח",
        }, "new-speech")
        self.assertNotEqual(blocked["status"], "sent")
        self.assertEqual(self.sent, [])

    def test_complete_visible_readback_survives_early_audio_cut_and_fresh_yes(self):
        draft_id = self.prepare_draft()
        self.hub.handle_browser_event(self.state, {
            "type": "response.created", "response": {"id": "readback-response"},
        })
        self.hub.handle_browser_event(self.state, {
            "type": "response.output_audio_transcript.done", "response_id": "readback-response",
            "item_id": "visible-item", "transcript": "לדנה בכתובת dana@example.com: שלום. לשלוח?",
        })
        self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.hub.handle_browser_event(self.state, {
            "type": "output_text_presented", "item_id": "visible-item",
        })
        self.assertTrue(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.assertNotIn("visible-item", self.state.played_items)
        self.hub.handle_browser_event(self.state, {
            "type": "input_audio_buffer.speech_started", "item_id": "approval-speech",
        })
        self.hub.handle_browser_event(self.state, {
            "type": "output_audio_buffer.cleared", "item_id": "visible-item",
        })
        self.hub.handle_browser_event(self.state, {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "approval-speech", "transcript": "כן תשלח",
        })
        sent = self.state.tool_call("visible-send", "miko_send_email", {
            "draft_id": draft_id, "confirmation_quote": "כן תשלח",
        }, "approval-speech")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(self.sent), 1)

    def test_visible_readback_without_address_or_body_cannot_authorize_send(self):
        draft_id = self.prepare_draft()
        self.hub.handle_browser_event(self.state, {
            "type": "response.created", "response": {"id": "incomplete-response"},
        })
        for item_id, transcript in (
            ("missing-address", "לדנה: שלום. לשלוח?"),
            ("missing-body", "לדנה בכתובת dana@example.com. לשלוח?"),
        ):
            self.hub.handle_browser_event(self.state, {
                "type": "response.output_audio_transcript.done", "response_id": "incomplete-response",
                "item_id": item_id, "transcript": transcript,
            })
            self.hub.handle_browser_event(self.state, {
                "type": "output_text_presented", "item_id": item_id,
            })
            self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.state.begin_turn("u2", audio=False, text="כן תשלח")
        blocked = self.state.tool_call("incomplete-send", "miko_send_email", {
            "draft_id": draft_id, "confirmation_quote": "כן תשלח",
        }, "u2")
        self.assertEqual(blocked["status"], "readback_required")
        self.assertEqual(self.sent, [])

    def test_new_turn_reread_of_existing_draft_without_prepare_can_be_confirmed(self):
        draft_id = self.prepare_draft()
        self.state.begin_turn("reread-turn", audio=False, text="תקריא לי את הטיוטה")
        self.assertIsNone(self.state.awaiting_draft)
        self.state.assistant_text("reread-item", "הטיוטה אל dana@example.com: שלום. לשלוח?")
        self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.state.playback_complete("reread-item")
        self.assertTrue(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.state.begin_turn("confirm-turn", audio=False, text="כן תשלח")
        sent = self.state.tool_call("send-after-reread", "miko_send_email", {
            "draft_id": draft_id, "confirmation_quote": "כן תשלח",
        }, "confirm-turn")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(self.sent), 1)

    def test_name_only_tentative_readback_does_not_authorize_send(self):
        self.state.begin_turn("u1", audio=True, text="אני רוצה לשלוח לדנה")
        draft = self.state.tool_call("prepare-audio", "miko_prepare_email", {
            "mode": "new", "recipient_name": "דנה", "to_address": "dana@example.com",
            "subject": "", "body": "שלום", "source_quote": "דנה שטרודל example נקודה com", "reference": "",
        }, "u1")
        self.assertEqual(draft["draft"]["address_basis"], "tentative_from_audio")
        draft_id = draft["draft"]["id"]
        self.state.assistant_text("name-only", "לדנה: שלום. לשלוח?")
        self.state.playback_complete("name-only")
        self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])
        self.play_readback(draft_id, item="complete")
        self.assertTrue(self.brain.miko["pending_external_action"]["confirmation_active"])

    def test_interrupted_audio_cannot_be_marked_presented_later(self):
        draft_id = self.prepare_draft()
        self.state.assistant_text("interrupted-reply", "המייל אל dana@example.com: שלום. לשלוח?")
        self.state.interrupted()
        self.state.playback_complete("interrupted-reply")
        self.assertEqual(self.brain.miko["pending_external_action"]["id"], draft_id)
        self.assertFalse(self.brain.miko["pending_external_action"]["confirmation_active"])

    def test_duplicate_call_id_even_concurrently_sends_once(self):
        draft_id = self.prepare_draft()
        self.play_readback(draft_id)
        self.state.begin_turn("u2", audio=False, text="כן תשלח")
        entered = threading.Event()
        release = threading.Event()

        def hold_smtp():
            entered.set()
            self.assertTrue(release.wait(3))

        self.smtp_send_hook = hold_smtp
        args = {"draft_id": draft_id, "confirmation_quote": "כן תשלח"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.state.tool_call, "send-same", "miko_send_email", args, "u2")
            self.assertTrue(entered.wait(3))
            second = pool.submit(self.state.tool_call, "send-same", "miko_send_email", args, "u2")
            release.set()
            first_result = first.result(timeout=4)
            second_result = second.result(timeout=4)
        self.assertEqual(first_result["status"], "sent")
        self.assertEqual(second_result, first_result)
        self.assertEqual(len(self.sent), 1)

    def test_slow_smtp_does_not_block_new_user_turn(self):
        draft_id = self.prepare_draft()
        self.play_readback(draft_id)
        self.state.begin_turn("u2", audio=False, text="כן תשלח")
        entered = threading.Event()
        release = threading.Event()

        def hold_smtp():
            entered.set()
            self.assertTrue(release.wait(3))

        self.smtp_send_hook = hold_smtp
        with ThreadPoolExecutor(max_workers=2) as pool:
            send = pool.submit(self.state.tool_call, "slow-send", "miko_send_email",
                               {"draft_id": draft_id, "confirmation_quote": "כן תשלח"}, "u2")
            self.assertTrue(entered.wait(3))
            try:
                next_turn = pool.submit(self.state.begin_turn, "u3", False, "מה השעה?")
                next_turn.result(timeout=1.5)
                self.assertEqual(self.state.turn_id, "u3")
            finally:
                release.set()
            self.assertEqual(send.result(timeout=4)["status"], "sent")
        self.assertEqual(len(self.sent), 1)

    def test_native_stale_generation_and_turn_do_not_run_tool(self):
        class FakeWebSocket:
            async def send(self, _payload):
                pass

        class FakeAPI:
            def __init__(self):
                self.events = []

            async def send(self, payload):
                self.events.append(payload)

        session = NativeSession(self.hub, FakeWebSocket())
        session.api = FakeAPI()
        self.state = session.state
        self.state.begin_turn("u1", audio=False, text="dana@example.com")
        call = {"call_id": "stale-prepare", "name": "miko_prepare_email", "arguments": {
            "mode": "new", "recipient_name": "דנה", "to_address": "dana@example.com",
            "subject": "", "body": "שלום", "source_quote": "dana@example.com", "reference": "",
        }}
        session.generation = 2
        # This stale branch returns before its first await, so drive it without
        # creating an asyncio loopback socket in the network-blocked fixture.
        stale = session.finish_calls([call], 1, "u1")
        with self.assertRaises(StopIteration):
            stale.send(None)
        self.assertIsNone(self.brain.miko["pending_external_action"])
        self.assertEqual(session.api.events, [])
        self.state.begin_turn("u2", audio=False, text="נושא חדש")
        result = self.state.tool_call("stale-prepare", "miko_prepare_email", call["arguments"], "u1")
        self.assertIsNone(self.brain.miko["pending_external_action"])
        self.assertEqual(result["status"], "turn_changed")

    def test_native_visible_readback_survives_next_ptt_and_sends_once(self):
        """Visible final text can confirm a draft even if audio ack is late."""
        async def scenario():
            client_events = []
            model_events = []

            class FakeSocket:
                async def send(self, raw):
                    client_events.append(json.loads(raw))

            class FakeModel:
                def __init__(self):
                    self.incoming = asyncio.Queue()

                async def send(self, raw):
                    model_events.append(json.loads(raw))

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    event = await self.incoming.get()
                    if event is None:
                        raise StopAsyncIteration
                    return json.dumps(event)

            async def until(predicate):
                for _ in range(300):
                    if predicate():
                        return
                    await asyncio.sleep(.002)
                self.fail('Native Realtime event was not handled promptly')

            async def speak_ptt(pcm_byte):
                await native.client_event({'type':'start'})
                await native.client_event({'type':'audio','audio':base64.b64encode(bytes([pcm_byte,0])*4800).decode('ascii')})
                await native.client_event({'type':'stop'})

            native = NativeSession(self.hub, FakeSocket())
            model = FakeModel()
            native.api = model
            reader = asyncio.create_task(native.api_events(model))
            try:
                # First spoken turn requests a draft. The model's function
                # call is driven through the same response.done path as live.
                await speak_ptt(17)
                first_request = next(e for e in model_events if e['type']=='response.create')
                first_turn = next(e for e in client_events if e['type']=='turn_started')['turn_id']
                await model.incoming.put({'type':'input_audio_buffer.committed','item_id':'draft-input'})
                await model.incoming.put({'type':'conversation.item.input_audio_transcription.completed',
                                          'item_id':'draft-input','transcript':'שלח לדנה הודעה שאני מגיע'})
                await model.incoming.put({'type':'response.created','response':{
                    'id':'draft-response','metadata':first_request['response']['metadata']}})
                await model.incoming.put({'type':'response.done','response':{
                    'id':'draft-response','status':'completed','output':[{
                        'type':'function_call','call_id':'prepare-one','name':'miko_prepare_email',
                        'arguments':json.dumps({'mode':'new','recipient_name':'דנה',
                            'to_address':'dana@example.com','subject':'','body':'אני מגיע',
                            'source_quote':'dana@example.com','reference':''})}]}})
                # queue_email_action exposes the pending object before the
                # prepare tool finishes clearing confirmation_active. The
                # follow-up response is requested only after tool_call returns.
                await until(lambda: len([e for e in model_events if e['type']=='response.create']) >= 2)
                self.assertTrue(any(e['type']=='conversation.item.create'
                    and e['item'].get('call_id')=='prepare-one' for e in model_events))
                draft = self.brain.miko['pending_external_action']
                draft_id = draft['id']
                self.assertFalse(draft.get('confirmation_active'))

                readback_request = [e for e in model_events if e['type']=='response.create'][1]
                await model.incoming.put({'type':'response.created','response':{
                    'id':'readback-response','metadata':readback_request['response']['metadata']}})
                await model.incoming.put({'type':'response.output_audio_transcript.done',
                    'response_id':'readback-response','item_id':'readback-item',
                    'transcript':'לדנה בכתובת dana@example.com: אני מגיע. לשלוח?'})
                await model.incoming.put({'type':'response.done','response':{
                    'id':'readback-response','status':'completed','output':[]}})
                await until(lambda: any(e['type']=='transcript' and e.get('item_id')=='readback-item'
                                        for e in client_events))
                self.assertEqual(native.state.output_turns['readback-item'],first_turn)
                self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
                self.assertNotIn('readback-item',native.state.played_items)

                # The actual native panel rendered the final caption. No fake
                # audio-complete report is supplied; the owner can press SPACE
                # while the playback tail is still draining.
                await native.client_event({'type':'output_text_presented',
                    'session_id':native.state.id,'item_id':'readback-item'})
                self.assertTrue(self.brain.miko['pending_external_action']['confirmation_active'])
                await speak_ptt(18)
                confirmation_turn = [e for e in client_events if e['type']=='turn_started'][-1]['turn_id']
                self.assertNotEqual(confirmation_turn,first_turn)
                await model.incoming.put({'type':'input_audio_buffer.committed','item_id':'approval-input'})
                await model.incoming.put({'type':'conversation.item.input_audio_transcription.completed',
                                          'item_id':'approval-input','transcript':'כן, תשלח'})
                await until(lambda: native.state.tools._user_text == 'כן, תשלח')
                approval_request = [e for e in model_events if e['type']=='response.create'][-1]
                await model.incoming.put({'type':'response.created','response':{
                    'id':'send-response','metadata':approval_request['response']['metadata']}})
                await model.incoming.put({'type':'response.done','response':{
                    'id':'send-response','status':'completed','output':[{
                        'type':'function_call','call_id':'send-once','name':'miko_send_email',
                        'arguments':json.dumps({'draft_id':draft_id,'confirmation_quote':'כן, תשלח'})}]}})
                await until(lambda: len(self.sent)==1)
                self.assertEqual(len(self.sent),1)
                self.assertIsNone(self.brain.miko.get('pending_external_action'))
                self.assertEqual(self.brain.miko['email_history'][-1]['to'],'dana@example.com')
            finally:
                await model.incoming.put(None)
                await reader
                for task in list(native.tasks):
                    await task

        self.run_async(scenario())

    def test_native_visible_readback_requires_final_full_text_and_current_session(self):
        class FakeSocket:
            async def send(self, _raw):
                pass

        native = NativeSession(self.hub, FakeSocket())
        native.state.begin_turn('draft-turn',audio=False,text='שלח לדנה dana@example.com: אני מגיע')
        draft = native.state.tool_call('prepare-native','miko_prepare_email',{
            'mode':'new','recipient_name':'דנה','to_address':'dana@example.com',
            'subject':'','body':'אני מגיע','source_quote':'dana@example.com','reference':''},'draft-turn')
        self.assertEqual(draft['status'],'draft_ready')
        draft_id = draft['draft']['id']
        native.state.response_started('readback-response','draft-turn')
        native.state.caption_delta('assistant','readback-item',
            'לדנה בכתובת dana@example.com: אני מגיע. לשלוח?','readback-response')
        self.run_async(native.client_event({'type':'output_text_presented',
            'session_id':native.state.id,'item_id':'readback-item'}))
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
        native.state.assistant_text('readback-item','לדנה: אני מגיע. לשלוח?','readback-response')
        self.run_async(native.client_event({'type':'output_text_presented',
            'session_id':native.state.id,'item_id':'readback-item'}))
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
        native.state.assistant_text('readback-item',
            'לדנה בכתובת dana@example.com: אני מגיע. לשלוח?','readback-response')
        self.run_async(native.client_event({'type':'output_text_presented',
            'session_id':'other-session','item_id':'readback-item'}))
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
        self.run_async(native.client_event({'type':'output_text_presented',
            'session_id':native.state.id,'item_id':'wrong-item'}))
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
        self.assertEqual(self.sent,[])
        native.state.begin_turn('approval-turn',audio=True,text='כן תשלח')
        blocked = native.state.tool_call('blocked-native','miko_send_email',{
            'draft_id':draft_id,'confirmation_quote':'כן תשלח'},'approval-turn')
        self.assertEqual(blocked['status'],'readback_required')
        self.assertEqual(self.sent,[])

    def test_native_late_visible_old_draft_cannot_present_replacement(self):
        class FakeSocket:
            async def send(self, _raw):
                pass

        class FakeModel:
            async def send(self, _raw):
                pass

        native = NativeSession(self.hub, FakeSocket())
        native.api = FakeModel()
        self.state = native.state
        old_id = self.prepare_draft(turn_id='old-turn',body='ישן')
        native.state.response_started('old-response','old-turn')
        native.state.assistant_text('old-item',
            'לדנה בכתובת dana@example.com: ישן. לשלוח?','old-response')
        native.state.transcript_event('assistant','old-item',
            'לדנה בכתובת dana@example.com: ישן. לשלוח?','old-response')
        self.run_async(native.interrupt({}))
        self.assertIn('old-item',native.state.late_visible_drafts)

        new_id = self.prepare_draft(turn_id='new-turn',body='חדש')
        self.assertNotEqual(old_id,new_id)
        self.run_async(native.client_event({'type':'output_text_presented',
            'session_id':native.state.id,'item_id':'old-item'}))
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
        self.assertEqual(self.brain.miko['pending_external_action']['id'],new_id)
        self.assertEqual(self.sent,[])

    def test_browser_raw_commit_and_transcript_events_preserve_audio_identity(self):
        state = VoiceState(self.hub, "browser-offline")
        self.hub.handle_browser_event(state, {"type": "input_audio_buffer.speech_started", "item_id": "speech-1"})
        self.hub.handle_browser_event(state, {"type": "input_audio_buffer.committed", "item_id": "item-1"})
        self.hub.handle_browser_event(state, {"type": "conversation.item.input_audio_transcription.completed",
                                              "item_id": "item-1", "transcript": "לא תשלח"})
        self.assertEqual(state.tools._turn_id, "speech-1")
        self.assertTrue(state.tools._has_user_audio)
        self.assertEqual(state.tools._user_text, "לא תשלח")

    def test_state_save_retries_windows_replace_and_preserves_memories(self):
        state_path = Path(self.brain.STATE_FILE)
        before = json.loads(state_path.read_text(encoding="utf-8"))
        original_memories = list(before["memories"])
        self.brain.miko["memories"].append("זיכרון אחרי נעילה זמנית")
        real_replace = self.brain.os.replace
        attempts = []

        def locked_twice_then_replace(source, destination):
            attempts.append((source, destination))
            if len(attempts) <= 2:
                still_saved = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(still_saved["memories"], original_memories)
                raise PermissionError(5, "synthetic Windows sharing violation")
            return real_replace(source, destination)

        with mock.patch.object(self.brain.os, "replace", side_effect=locked_twice_then_replace), mock.patch.object(
            self.brain.time, "sleep", return_value=None
        ):
            self.brain.save_state()
        self.assertEqual(len(attempts), 3)
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["memories"], original_memories + ["זיכרון אחרי נעילה זמנית"])
        self.assert_persistent_identity()

    def test_finalize_response_preserves_all_existing_conversation_history(self):
        original = [f"Owner: historical turn {index:03d}" for index in range(85)]
        self.brain.miko["conversation_history"] = list(original)
        result = self.brain.finalize_action_response("שאלה חדשה", {"message": "תשובה חדשה", "emotion": "curious"})
        expected = original + ["Owner: שאלה חדשה", "Miko: תשובה חדשה"]
        self.assertEqual(self.brain.miko["conversation_history"], expected)
        self.assertEqual(result["message"], "תשובה חדשה")
        saved = json.loads(Path(self.brain.STATE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(saved["conversation_history"], expected)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain", type=Path, default=fixture.DEFAULT_BRAIN)
    args = parser.parse_args()
    fixture.SOURCE = args.brain.expanduser().resolve()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RealtimeHostLifecycle)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
