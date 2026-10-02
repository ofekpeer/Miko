"""Offline native readback and confirmation lifecycle regression.

Run: python diagnostics/test_miko_confirm_flow.py
The shared fixture uses a temporary state file, fake OpenAI and fake SMTP.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import unittest

import test_miko_conversation as fixture


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from miko_realtime import NativeSession, RealtimeHub


def run_immediate(coroutine):
    """Drive fake-only awaits without opening an asyncio socketpair."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    else:
        coroutine.close()
        raise AssertionError('The offline native event unexpectedly suspended')


class FakeWebSocket:
    def __init__(self):
        self.events = []

    async def send(self, payload):
        self.events.append(json.loads(payload))


class FakeAPI:
    def __init__(self):
        self.events = []

    async def send(self, payload):
        self.events.append(json.loads(payload))


class NativeConfirmationFlow(fixture.MikoFixture):
    def setUp(self):
        super().setUp()
        self.hub = RealtimeHub(self.brain)
        self.session = NativeSession(self.hub, FakeWebSocket())
        self.session.api = FakeAPI()
        self.state = self.session.state

    def prepare_and_readback(self):
        self.state.begin_turn('dictation', audio=True, text='שלח לדנה dana@example.com אני בדרך')
        result = self.state.tool_call('prepare', 'miko_prepare_email', {
            'mode':'new', 'recipient_name':'דנה', 'to_address':'dana@example.com',
            'subject':'', 'body':'אני בדרך', 'source_quote':'dana@example.com', 'reference':'',
        }, 'dictation')
        self.assertEqual(result['status'], 'draft_ready')
        draft_id = result['draft']['id']
        self.state.response_started('readback-response', 'dictation', copy.copy(self.state.tools))
        text = 'הטיוטה מיועדת לדנה בכתובת dana@example.com. התוכן הוא: אני בדרך. לשלוח?'
        self.state.assistant_text('readback-item', text, 'readback-response')
        self.state.transcript_event('assistant', 'readback-item', text, 'readback-response')
        return draft_id

    def report_visible(self, *, session_id=None, item_id='readback-item'):
        run_immediate(self.session.client_event({
            'type':'output_text_presented',
            'session_id':self.state.id if session_id is None else session_id,
            'item_id':item_id,
        }))

    def test_late_reviewable_caption_allows_fresh_confirmation(self):
        draft_id = self.prepare_and_readback()
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])

        # The user presses SPACE at the end of the readback, before playback
        # reports `finished`. UI rendering acknowledges the complete caption
        # two frames later, after start/interrupt has already reached Python.
        run_immediate(self.session.client_event({'type':'start'}))
        self.assertNotIn('readback-item', self.state.output_draft)
        self.assertIn('readback-item', self.state.late_visible_drafts)
        self.report_visible()
        self.assertTrue(self.brain.miko['pending_external_action']['confirmation_active'])

        approval_turn = self.state.turn_id
        self.state.user_transcript(approval_turn, 'תשלח')
        sent = self.state.tool_call('send-once', 'miko_send_email', {
            'draft_id':draft_id, 'confirmation_quote':'תשלח',
        }, approval_turn)
        self.assertEqual(sent['status'], 'sent')
        self.assertEqual(len(self.sent), 1)
        duplicate = self.state.tool_call('send-once', 'miko_send_email', {
            'draft_id':draft_id, 'confirmation_quote':'תשלח',
        }, approval_turn)
        self.assertEqual(duplicate, sent)
        self.assertEqual(len(self.sent), 1)

    def test_interrupt_and_stale_playback_without_visible_ack_cannot_send(self):
        draft_id = self.prepare_and_readback()
        run_immediate(self.session.client_event({'type':'start'}))
        # A delayed or fabricated `finished` report must not turn an
        # interrupted, unreviewed item into send permission.
        self.state.playback_complete('readback-item')
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
        approval_turn = self.state.turn_id
        self.state.user_transcript(approval_turn, 'תשלח')
        blocked = self.state.tool_call('blocked', 'miko_send_email', {
            'draft_id':draft_id, 'confirmation_quote':'תשלח',
        }, approval_turn)
        self.assertEqual(blocked['status'], 'readback_required')
        self.assertEqual(self.sent, [])

    def test_ack_requires_final_owned_caption_and_desktop_session(self):
        self.prepare_and_readback()
        self.state.final_captions.discard(('assistant','readback-item'))
        self.report_visible()
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
        self.state.final_captions.add(('assistant','readback-item'))
        self.report_visible(session_id='another-session')
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
        self.report_visible(item_id='unknown-item')
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
        self.session.device_id = 'paired-device'
        self.report_visible()
        self.assertFalse(self.brain.miko['pending_external_action']['confirmation_active'])
        self.session.device_id = ''
        self.report_visible()
        self.assertTrue(self.brain.miko['pending_external_action']['confirmation_active'])


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brain', type=Path, default=fixture.DEFAULT_BRAIN)
    args = parser.parse_args()
    fixture.SOURCE = args.brain.expanduser().resolve()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(NativeConfirmationFlow)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
