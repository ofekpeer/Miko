"""Offline integration checks for the native Miko function-tool adapter.

Run: python work/test_miko_realtime_tools.py --brain work/miko_next/miko_brain.py
The shared fixture copies the brain to a temporary sandbox and fakes OpenAI
and SMTP. These tests never import the live brain or read live credentials.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
import sys
import threading
import unittest

import test_miko_conversation as fixture

# When copied into a release's diagnostics/ folder, the adapter lives beside
# that folder rather than on Python's initial script import path.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if (PACKAGE_ROOT / "miko_realtime_tools.py").is_file():
    sys.path.insert(0, str(PACKAGE_ROOT))

from miko_realtime_tools import MikoRealtimeTools


class RealtimeToolsRegression(fixture.MikoFixture):
    def setUp(self):
        super().setUp()
        self.tools = MikoRealtimeTools(self.brain)

    def prepare(self, *, mode="new", recipient_name="דנה", to_address="dana@example.com", body="שלום", source_quote="dana@example.com", reference=""):
        return self.tools.call("miko_prepare_email", {
            "mode": mode, "recipient_name": recipient_name, "to_address": to_address,
            "subject": "", "body": body, "source_quote": source_quote, "reference": reference,
        })

    def test_native_tool_specs_return_facts_and_context_without_credentials(self):
        specs = self.tools.tool_definitions()
        names = {item["name"] for item in specs}
        self.assertIn("miko_prepare_email", names)
        self.assertIn("miko_send_email", names)
        self.assertIn("miko_remember", names)
        self.assertIn("miko_set_expression", names)
        self.assertTrue(all(item["type"] == "function" for item in specs))
        context = self.tools.call("miko_get_context", {})
        self.assertEqual(context["status"], "context")
        self.assertIn("זיכרון בדיקה שאסור למחוק", context["memories"])
        self.assertNotIn("password", str(context).lower())
        self.assertNotIn("test-placeholder-only", str(context))

    def test_spoken_address_candidate_survives_bad_auxiliary_stt_then_sends_after_readback(self):
        self.tools.begin_turn("u1", user_text="אני לא רעב", input_kind="audio", has_user_audio=True)
        draft = self.prepare(
            recipient_name="אופק", to_address="ofekpeer3030@gmail.com", body="אני בדרך",
            source_quote="אופק פאר שלושים שלושים שטרודל ג׳ימייל נקודה קום",
        )
        self.assertEqual(draft["status"], "draft_ready")
        self.assertEqual(draft["draft"]["address_basis"], "tentative_from_audio")
        draft_id = draft["draft"]["id"]
        self.assertEqual(self.sent, [])
        self.assertEqual(self.brain.miko["email_contacts"], {})
        self.assertEqual(self.tools.send_email(draft_id, "כן, תשלח")["status"], "readback_required")

        presented = self.tools.mark_draft_presented(draft_id)
        self.assertEqual(presented["status"], "presented")
        self.tools.begin_turn("u2", user_text="כן, תשלח", input_kind="audio", has_user_audio=True)
        sent = self.tools.call("miko_send_email", {"draft_id": draft_id, "confirmation_quote": "כן, תשלח"})
        self.assertEqual(sent["status"], "sent")
        self.assertTrue(sent["smtp_confirmed"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["to"], "ofekpeer3030@gmail.com")
        self.assertEqual(self.brain.find_email_contact("אופק"), "ofekpeer3030@gmail.com")
        self.assert_persistent_identity()

    def test_spoken_semantic_correction_updates_tentative_draft(self):
        self.tools.begin_turn("u1", user_text="ofekper3030@gmail.com", input_kind="audio", has_user_audio=True)
        first = self.prepare(recipient_name="אופק", to_address="ofekper3030@gmail.com", body="", source_quote="ofekper3030@gmail.com")
        self.assertEqual(first["status"], "awaiting_body")
        self.tools.begin_turn("u2", user_text="ב-PER חסר E", input_kind="audio", has_user_audio=True)
        corrected = self.prepare(
            mode="edit", recipient_name="", to_address="ofekpeer3030@gmail.com", body="אני בדרך",
            source_quote="ב-PER חסר E",
        )
        self.assertEqual(corrected["status"], "draft_ready")
        self.assertEqual(corrected["draft"]["to"], "ofekpeer3030@gmail.com")
        self.assertEqual(self.sent, [])

    def test_topic_turn_expires_confirmation_until_new_readback(self):
        self.tools.begin_turn("u1", user_text="שלח מייל לדנה dana@example.com וכתוב שלום")
        draft = self.prepare()
        self.assertEqual(draft["status"], "draft_ready")
        draft_id = draft["draft"]["id"]
        self.tools.mark_draft_presented(draft_id)

        self.tools.begin_turn("u2", user_text="איך היה היום שלך?")
        self.tools.begin_turn("u3", user_text="כן, תשלח")
        stale = self.tools.send_email(draft_id, "כן, תשלח")
        self.assertEqual(stale["status"], "readback_required")
        self.assertEqual(self.sent, [])

        resumed = self.tools.resume_email()
        self.assertEqual(resumed["status"], "draft_ready")
        self.tools.mark_draft_presented(draft_id)
        self.tools.begin_turn("u4", user_text="כן, תשלח")
        sent = self.tools.send_email(draft_id, "כן, תשלח")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(self.sent), 1)

    def test_negative_transcript_blocks_positive_audio_quote(self):
        self.tools.begin_turn("u1", user_text="dana@example.com", input_kind="audio", has_user_audio=True)
        draft = self.prepare()
        self.tools.mark_draft_presented(draft["draft"]["id"])
        self.tools.begin_turn("u2", user_text="לא תשלח", input_kind="audio", has_user_audio=True)
        blocked = self.tools.send_email(draft["draft"]["id"], "כן שלח")
        self.assertEqual(blocked["status"], "confirmation_ambiguous")
        self.assertEqual(self.sent, [])

    def test_new_topic_with_yes_does_not_confirm_email(self):
        self.tools.begin_turn("u1", user_text="dana@example.com", input_kind="audio", has_user_audio=True)
        draft = self.prepare()
        self.tools.mark_draft_presented(draft["draft"]["id"])
        self.tools.begin_turn("u2", user_text="כן אני רוצה לשחק", input_kind="audio", has_user_audio=True)
        blocked = self.tools.send_email(draft["draft"]["id"], "כן")
        self.assertEqual(blocked["status"], "confirmation_ambiguous")
        self.assertEqual(self.sent, [])

    def test_unrelated_trusted_audio_transcript_overrides_model_yes_quote(self):
        self.tools.begin_turn("u1", user_text="שלח לדנה dana@example.com: אני מגיע",
                              input_kind="audio", has_user_audio=True)
        draft = self.prepare(body="אני מגיע")
        self.assertEqual(draft["status"], "draft_ready")
        draft_id = draft["draft"]["id"]
        self.assertEqual(self.tools.mark_draft_presented(draft_id)["status"], "presented")

        # The model can claim it heard "yes" from audio, but the completed
        # auxiliary transcript is a clear unrelated question. A nonempty
        # trusted transcript must decide whether this turn authorizes send.
        self.tools.begin_turn("u2", user_text="מה השעה?", input_kind="audio", has_user_audio=True)
        blocked = self.tools.send_email(draft_id, "כן")
        self.assertIn(blocked["status"], ("confirmation_ambiguous", "confirmation_not_grounded"))
        self.assertEqual(self.sent, [])
        self.assertTrue(self.brain.miko["pending_external_action"]["confirmation_active"])

    def test_clear_spoken_send_confirmation_still_sends_once(self):
        self.tools.begin_turn("u1", user_text="שלח לדנה dana@example.com: אני מגיע",
                              input_kind="audio", has_user_audio=True)
        draft = self.prepare(body="אני מגיע")
        draft_id = draft["draft"]["id"]
        self.assertEqual(self.tools.mark_draft_presented(draft_id)["status"], "presented")
        self.tools.begin_turn("u2", user_text="תשלח", input_kind="audio", has_user_audio=True)
        result = self.tools.send_email(draft_id, "תשלח")
        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(self.sent), 1)

    def test_hebrew_approved_word_sends_after_fresh_readback(self):
        self.tools.begin_turn("u1", user_text="dana@example.com")
        draft = self.prepare()
        draft_id = draft["draft"]["id"]
        self.tools.mark_draft_presented(draft_id)
        self.tools.begin_turn("u2", user_text="מאושר")
        sent = self.tools.send_email(draft_id, "מאושר")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.tools._confirmation_signal("מאושרת"))

    def test_discourse_no_just_send_is_narrow_positive_confirmation(self):
        self.tools.begin_turn("u1", user_text="dana@example.com")
        draft = self.prepare()
        draft_id = draft["draft"]["id"]
        self.tools.mark_draft_presented(draft_id)
        self.tools.begin_turn("u2", user_text="לא, פשוט תשלח")
        sent = self.tools.send_email(draft_id, "לא, פשוט תשלח")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.tools._negative_signal("לא פשוט תשלח"))
        self.assertTrue(self.tools._confirmation_signal("לא פשוט תשלח"))
        for negative in (
            "לא תשלח", "אל תשלח", "לא מאשר", "לא, תשלח",
            "לא, פשוט תשלח אבל תשנה את הגוף", "לא, פשוט תשלח למישהו אחר",
        ):
            with self.subTest(negative=negative):
                self.assertTrue(self.tools._negative_signal(negative))
                self.assertFalse(self.tools._confirmation_signal(negative))

    def test_changing_recipient_name_does_not_keep_old_address(self):
        self.tools.begin_turn("u1", user_text="dana@example.com")
        draft = self.prepare()
        self.assertEqual(draft["status"], "draft_ready")
        self.tools.begin_turn("u2", user_text="בעצם לאופק")
        changed = self.prepare(mode="edit", recipient_name="אופק", to_address="", body="", source_quote="בעצם לאופק")
        self.assertEqual(changed["status"], "needs_recipient")
        self.assertIsNone(self.brain.miko["pending_external_action"])
        self.assertEqual(self.brain.miko["email_compose"]["recipient_name"], "אופק")
        self.assertEqual(self.brain.miko["email_compose"].get("to", ""), "")

    def test_late_transcript_keeps_same_audio_turn(self):
        self.tools.begin_turn("u1", input_kind="audio", has_user_audio=True)
        self.assertTrue(self.tools.update_turn_transcript("u1", "אמרתי לשלוח לדנה"))
        self.tools.begin_turn("u1", user_text="אמרתי לשלוח לדנה", input_kind="audio", has_user_audio=True)
        self.assertEqual(self.tools._turn_sequence, 1)
        self.assertTrue(self.tools._has_user_audio)
        self.assertFalse(self.tools.update_turn_transcript("other", "כן"))

    def test_text_candidate_needs_exact_address_or_verified_edit(self):
        self.tools.begin_turn("u1", user_text="שלח מייל לאופק", input_kind="text")
        guessed = self.prepare(recipient_name="אופק", to_address="ofak3030@gmail.com", source_quote="שלח מייל לאופק")
        self.assertEqual(guessed["status"], "address_unverified")
        self.assertIsNone(self.brain.miko["pending_external_action"])

        self.tools.begin_turn("u2", user_text="ofekper3030@gmail.com", input_kind="text")
        first = self.prepare(recipient_name="אופק", to_address="ofekper3030@gmail.com", body="", source_quote="ofekper3030@gmail.com")
        self.assertEqual(first["status"], "awaiting_body")
        self.tools.begin_turn("u3", user_text="במקום per זה peer", input_kind="text")
        fixed = self.prepare(mode="edit", recipient_name="", to_address="ofekpeer3030@gmail.com", body="שלום", source_quote="במקום per זה peer")
        self.assertEqual(fixed["status"], "draft_ready")
        self.assertEqual(fixed["draft"]["to"], "ofekpeer3030@gmail.com")

    def test_live_text_correction_adds_missing_e_with_literal_peer_evidence(self):
        self.tools.begin_turn("u1", user_text="ofekper3030@example.com", input_kind="text")
        first = self.prepare(recipient_name="אופק", to_address="ofekper3030@example.com", body="", source_quote="ofekper3030@example.com")
        self.assertEqual(first["status"], "awaiting_body")
        correction = "לא, בכתובת חסרה E. זה peer, שתי E לפני ה-R."
        self.tools.begin_turn("u2", user_text=correction, input_kind="text")
        fixed = self.prepare(mode="edit", recipient_name="", to_address="ofekpeer3030@example.com", body="שלום", source_quote=correction)
        self.assertEqual(fixed["status"], "draft_ready")
        self.assertEqual(fixed["draft"]["address_basis"], "grounded_text_correction")
        self.assertEqual(fixed["draft"]["to"], "ofekpeer3030@example.com")

    def test_unrelated_or_large_text_address_change_is_not_grounded(self):
        self.tools.begin_turn("u1", user_text="ofekper3030@example.com", input_kind="text")
        first = self.prepare(recipient_name="אופק", to_address="ofekper3030@example.com", body="", source_quote="ofekper3030@example.com")
        self.assertEqual(first["status"], "awaiting_body")
        self.tools.begin_turn("u2", user_text="תשנה את הכתובת", input_kind="text")
        guessed = self.prepare(mode="edit", to_address="ofekpeer3030@example.com", body="שלום", source_quote="תשנה את הכתובת")
        self.assertEqual(guessed["status"], "address_unverified")
        self.tools.begin_turn("u3", user_text="חסר E, זה peer", input_kind="text")
        wrong_domain = self.prepare(mode="edit", to_address="ofekpeer3030@gmail.com", body="שלום", source_quote="חסר E, זה peer")
        self.assertEqual(wrong_domain["status"], "address_unverified")
        guessed_large = self.prepare(mode="edit", to_address="totallydifferent@example.com", body="שלום", source_quote="חסר E, זה peer")
        self.assertEqual(guessed_large["status"], "address_unverified")

    def test_memory_is_additive_and_legacy_memory_is_untouched(self):
        original = copy.deepcopy(self.brain.miko["memories"])
        self.tools.begin_turn("u1", user_text="אני אוהב קפה בבוקר", input_kind="audio", has_user_audio=True)
        added = self.tools.call("miko_remember", {
            "fact": "אופק אוהב קפה בבוקר", "source_quote": "אני אוהב קפה בבוקר", "replace_index": -1,
        })
        self.assertEqual(added["status"], "remembered")
        self.assertEqual(self.brain.miko["memories"], original)
        recalled = self.tools.call("miko_recall_memory", {"query": "קפה"})
        self.assertTrue(any("קפה" in m["fact"] for m in recalled["matches"]))
        self.assertEqual(len(self.brain.miko["realtime_memories"]), 1)
        self.assert_persistent_identity()

    def test_no_smtp_confirmation_when_gmail_disconnected(self):
        self.tools.begin_turn("u1", user_text="dana@example.com")
        draft = self.prepare()
        draft_id = draft["draft"]["id"]
        self.tools.mark_draft_presented(draft_id)
        self.smtp_configured = False
        self.tools.begin_turn("u2", user_text="כן, תשלח")
        failed = self.tools.send_email(draft_id, "כן, תשלח")
        self.assertEqual(failed["status"], "email_not_configured")
        self.assertEqual(failed["reason"], "gmail_not_connected")
        self.assertFalse(failed["smtp_confirmed"])
        self.assertEqual(self.sent, [])

    def test_concurrent_confirmation_calls_send_only_once(self):
        self.tools.begin_turn("u1", user_text="dana@example.com")
        draft = self.prepare()
        draft_id = draft["draft"]["id"]
        self.tools.mark_draft_presented(draft_id)
        self.tools.begin_turn("u2", user_text="כן תשלח")
        entered = threading.Event()
        release = threading.Event()

        def hold_smtp():
            entered.set()
            self.assertTrue(release.wait(3))

        self.smtp_send_hook = hold_smtp
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.tools.send_email, draft_id, "כן תשלח")
            self.assertTrue(entered.wait(3))
            second = pool.submit(self.tools.send_email, draft_id, "כן תשלח")
            blocked = second.result(timeout=3)
            self.assertNotEqual(blocked["status"], "sent")
            release.set()
            accepted = first.result(timeout=3)
        self.assertEqual(accepted["status"], "sent")
        self.assertEqual(len(self.sent), 1)

    def test_expression_returns_godot_cue_without_spoken_template(self):
        cue = self.tools.call("miko_set_expression", {"emotion": "happy", "action": "bounce"})
        self.assertEqual(cue, {"ok": True, "status": "expression_set", "emotion": "happy", "action": "bounce"})


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain", type=Path, default=fixture.DEFAULT_BRAIN)
    args = parser.parse_args()
    fixture.SOURCE = args.brain.expanduser().resolve()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RealtimeToolsRegression))
    raise SystemExit(0 if result.wasSuccessful() else 1)
