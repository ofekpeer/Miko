"""Language, repetition and truthfulness checks (acceptance O, P, Q).

  O  Hebrew quality: known broken forms, canned assistant phrases and sensor
     narration are flagged; natural short lines pass; perception notes are
     neutral English facts with nothing Hebrew for the model to echo.
  P  No repeated phrases: repeated lines/openers are flagged and fed back
     into the next spontaneous directive; repeated events lose their words.
  Q  Email: a spoken "sent" without a successful executor result is caught
     and corrected deterministically; a real success is not second-guessed.

python diagnostics/test_miko_language.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import miko_language as L  # noqa: E402
import miko_realtime  # noqa: E402
from miko_behavior import Context, ResponsePolicy  # noqa: E402

NATURAL = [
    "היי! מה קורה?", "אוי, מה זה היה?", "חחח די, אני מסוחרר", "איזה כיף שחזרת", "סבבה, אני על זה",
    "רגע, תן לי לבדוק", "וואלה, לא ידעתי", "אוקיי, מוכן", "הא, תפסת אותי", "יאללה, בוא נתחיל",
]
BROKEN = {
    "איזה נופף חמוד!": "invented_word",
    "אני כאן בשבילך, איך אוכל לעזור?": "canned_phrase",
    "זיהיתי שנופפת לי": "narration",
    "אני רואה שאתה מנופף": "narration",
    "קיבלתי הודעת מערכת": "narration",
}


class HebrewQuality(unittest.TestCase):
    def test_O_natural_lines_pass(self):
        for line in NATURAL:
            self.assertEqual(L.check(line), [], line)

    def test_O_broken_lines_are_flagged(self):
        for line, kind in BROKEN.items():
            problems = L.check(line)
            self.assertTrue(any(p.startswith(kind) for p in problems), (line, problems))

    def test_O_perception_notes_are_neutral_english_facts(self):
        hebrew = re.compile(r"[֐-׿]")
        for kind, note in miko_realtime.RealtimeHub.VISION_NOTES.items():
            self.assertIsNone(hebrew.search(note), f"{kind}: Hebrew in a note invites echoing: {note}")
        for kind, words in miko_realtime.RealtimeHub.CONTEXT_WORDS.items():
            self.assertIsNone(hebrew.search(words), kind)

    def test_O_style_rules_are_in_the_instructions(self):
        text = miko_realtime.INSTRUCTIONS + miko_realtime.SPONTANEOUS_STYLE
        self.assertIn("נופף", miko_realtime.INSTRUCTIONS)            # the concrete counter-example
        self.assertIn("never translate or echo", text)
        self.assertIn("invent", text)
        self.assertIn("Israeli Hebrew", text)


class Repetition(unittest.TestCase):
    def test_P_repeated_lines_and_openers_are_flagged(self):
        monitor = L.SpeechMonitor()
        self.assertEqual(monitor.observe("היי אופק, מה שלומך היום?"), [])
        self.assertIn("repeated_line", monitor.observe("היי אופק, מה שלומך היום?"))
        problems = monitor.observe("היי אופק, איזה יופי שחזרת")
        self.assertTrue(any(p.startswith("repeated_opener") for p in problems), problems)
        self.assertIn("היי אופק", monitor.avoid_openers())

    def test_P_spontaneous_directive_lists_openers_to_avoid(self):
        class Brain:
            state_lock = threading.Lock()
            miko = {"realtime_conversation_history": []}
            owner_request_active = threading.Event()

        class Hub:
            brain = Brain()
            speech = L.SpeechMonitor()

            async def flush_vision_context(self, native):
                pass

        Hub.speech.observe("וואו, איזה כיף לראות אותך")
        session = miko_realtime.NativeSession(Hub(), ws=None)
        session.api = object()
        sent = []

        async def api_send(event):
            sent.append(event)
        session.api_send = api_send
        self.assertTrue(asyncio.run(session.spontaneous("[perception] The owner waved hello to you just now.")))
        instructions = sent[-1]["response"]["instructions"]
        self.assertIn("Do not start with", instructions)
        self.assertIn("וואו איזה", instructions)

    def test_P_the_same_event_again_loses_its_words(self):
        class AlwaysTalk:
            def random(self):
                return 0.0
        policy = ResponsePolicy(rng=AlwaysTalk())
        levels = []
        for i in range(4):
            levels.append(policy.decide({"event": "gesture", "gesture": "thumbs_up"}, Context(now=1000.0 + i * 100)).level)
        self.assertEqual(levels[0], 4)
        self.assertTrue(all(level < 4 for level in levels[1:]), levels)


class SendClaims(unittest.TestCase):
    def session(self):
        class Brain:
            state_lock = threading.Lock()
            miko = {"realtime_conversation_history": []}
            owner_request_active = threading.Event()

        class Hub:
            brain = Brain()

        session = miko_realtime.NativeSession(Hub(), ws=None)
        session.api = object()
        session.state.turn_id = "turn_1"
        sent = []

        async def api_send(event):
            sent.append(event)
        session.api_send = api_send
        return session, sent

    def run_claim(self, session, text):
        async def go():
            result = await session._check_send_claim(text)
            for task in list(session.tasks):
                task.cancel()
            return result
        return asyncio.run(go())

    def test_Q_sent_without_executor_success_is_corrected(self):
        session, sent = self.session()
        session.state.last_send_status = "readback_required"
        self.assertTrue(self.run_claim(session, "נשלח!"))
        note = sent[0]["item"]["content"][0]["text"]
        self.assertIn("Nothing was sent", note)
        self.assertIn("readback_required", note)
        self.assertEqual(sent[1]["type"], "response.create")
        self.assertFalse(self.run_claim(session, "נשלח."))           # one correction per turn

    def test_Q_real_success_and_negations_are_left_alone(self):
        session, sent = self.session()
        self.assertFalse(self.run_claim(session, "עוד לא נשלח, לשלוח?"))
        self.assertFalse(self.run_claim(session, "ברגע שתאשר זה יישלח"))
        session.state.last_send_ok_at = __import__("time").time()
        self.assertFalse(self.run_claim(session, "נשלח."))
        self.assertEqual(sent, [])

    def test_Q_send_tool_records_only_executor_success(self):
        class Tools:
            def __init__(self, result):
                self.result = result

            def call(self, name, arguments):
                return dict(self.result)

            def begin_turn(self, *a, **k):
                pass

        class Brain:
            state_lock = threading.Lock()
            miko = {}
            owner_request_active = threading.Event()

            def mark_owner_interaction(self):
                pass

            def wake_miko(self):
                pass

        class Hub:
            brain = Brain()

            def broadcast(self, event):
                pass

        state = miko_realtime.VoiceState.__new__(miko_realtime.VoiceState)
        real = miko_realtime.VoiceState.__init__
        try:
            miko_realtime.MikoRealtimeTools.__init__  # noqa: B018
        except AttributeError:
            pass
        original = miko_realtime.MikoRealtimeTools
        miko_realtime.MikoRealtimeTools = lambda brain: Tools({"ok": False, "status": "send_failed"})
        try:
            real(state, Hub(), "s")
            state.turn_id = "t"
            state.tool_call("c1", "miko_send_email", {"draft_id": "d"}, "t")
            self.assertEqual(state.last_send_status, "send_failed")
            self.assertFalse(getattr(state, "last_send_ok_at", 0))
            state.tools = Tools({"ok": True, "status": "sent"})
            state.tool_call("c2", "miko_send_email", {"draft_id": "d"}, "t")
            self.assertGreater(state.last_send_ok_at, 0)
        finally:
            miko_realtime.MikoRealtimeTools = original


if __name__ == "__main__":
    unittest.main(verbosity=1)
