"""Offline, multi-turn regression tests for Miko's /think email conversation.

Run from this projectless workspace:
    python work/test_miko_conversation.py
    python work/test_miko_conversation.py --brain path/to/miko_brain.py

The brain source is copied into a temporary folder before import. The real
state, integration settings, and credential files are never opened by it.
OpenAI and SMTP are fake, and socket connections are blocked during tests.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


WORKSPACE = Path(__file__).resolve().parent.parent


def _default_brain_source() -> Path:
    # diagnostics/ inside a packaged update; work/ during development; then
    # the earlier output layout. Tests still copy the selected source to a
    # temporary state root before importing it.
    candidates = (
        WORKSPACE / "miko_brain.py",
        Path(__file__).resolve().parent / "miko_brain_16.py",
        WORKSPACE / "outputs" / "Miko_Update" / "miko_brain.py",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


DEFAULT_BRAIN = _default_brain_source()
SOURCE: Path


class _FakeOpenAIResponses:
    def __init__(self, client: "_FakeOpenAI"):
        self.client = client

    def create(self, **kwargs):
        format_name = kwargs.get("text", {}).get("format", {}).get("name", "")
        self.client.calls.append((format_name, kwargs.get("input", "")))
        queue = self.client.script.setdefault(format_name, [])
        if not queue:
            self.client.unscripted.append(format_name)
            raise AssertionError(f"Unscripted fake OpenAI response: {format_name}")
        return types.SimpleNamespace(output_text=json.dumps(queue.pop(0), ensure_ascii=False))


class _FakeOpenAI:
    instances: list["_FakeOpenAI"] = []

    def __init__(self, **_kwargs):
        self.script: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, str]] = []
        self.unscripted: list[str] = []
        self.responses = _FakeOpenAIResponses(self)
        type(self).instances.append(self)


class _FakeAPITimeoutError(Exception):
    pass


def _fake_openai_module():
    module = types.ModuleType("openai")
    module.OpenAI = _FakeOpenAI
    module.APITimeoutError = _FakeAPITimeoutError
    return module


class MikoFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.is_file():
            raise FileNotFoundError(f"Brain source does not exist: {SOURCE}")

        work_dir = WORKSPACE / "work"
        work_dir.mkdir(exist_ok=True)
        cls.sandbox = tempfile.TemporaryDirectory(prefix="miko-regression-", dir=work_dir)
        cls.sandbox_path = Path(cls.sandbox.name)
        cls.copy_path = cls.sandbox_path / "miko_brain.py"
        shutil.copyfile(SOURCE, cls.copy_path)

        # A same-directory, synthetic state prevents legacy state discovery.
        (cls.sandbox_path / "miko_brain_state.json").write_text(
            json.dumps({"last_seen": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )

        cls.socket_patch = mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("Network access is forbidden in regression tests"),
        )
        cls.socket_patch.start()

        # Source import constructs OpenAI clients. These clients never reach an API.
        cls.fake_openai = _fake_openai_module()
        spec = importlib.util.spec_from_file_location("miko_brain_regression_copy", cls.copy_path)
        cls.brain = importlib.util.module_from_spec(spec)
        try:
            with mock.patch.dict(sys.modules, {"openai": cls.fake_openai}), mock.patch(
                "os.path.expanduser", return_value=str(cls.sandbox_path)
            ), mock.patch.object(
                Path, "home", return_value=cls.sandbox_path
            ), mock.patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-only"}):
                spec.loader.exec_module(cls.brain)
        except BaseException:
            cls.socket_patch.stop()
            cls.sandbox.cleanup()
            raise

        for name in ("STATE_FILE", "LEGACY_STATE_FILE", "CREDENTIALS_FILE", "INTEGRATIONS_FILE"):
            location = Path(getattr(cls.brain, name)).resolve()
            if not location.is_relative_to(cls.sandbox_path.resolve()):
                cls.socket_patch.stop()
                cls.sandbox.cleanup()
                raise AssertionError(f"{name} escaped the isolated test sandbox: {location}")

        cls.client = cls.brain.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.socket_patch.stop()
        cls.sandbox.cleanup()

    def setUp(self):
        brain = self.brain
        brain.miko = copy.deepcopy(brain.DEFAULT_STATE)
        brain.miko["bond"] = 47
        brain.miko["memories"] = ["זיכרון בדיקה שאסור למחוק"]
        brain.miko["last_seen"] = time.time()
        brain.save_state()

        self.ai = brain.foreground_client
        self.ai.script.clear()
        self.ai.calls.clear()
        self.ai.unscripted.clear()
        self.agent_script: list[dict] = []
        self.unscripted_general = 0
        self.require_agent_script = False
        self.sent: list[dict[str, str]] = []
        self.smtp_failure = False
        self.smtp_exception = None
        self.smtp_configured = True
        self.smtp_send_hook = None
        self.real_generate = brain._generate_general_miko_response

        def fake_settings():
            return {
                "host": "smtp.invalid",
                "port": 465,
                "user": "sender@example.invalid",
                "password": "test-placeholder-only",
                "from_email": "sender@example.invalid",
                "use_ssl": True,
                "enabled": True,
                "configured": self.smtp_configured,
            }

        test_case = self

        class FakeSMTP:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def login(self, *_args):
                pass

            def send_message(self, message):
                if test_case.smtp_send_hook is not None:
                    test_case.smtp_send_hook()
                if test_case.smtp_failure:
                    raise test_case.smtp_exception or RuntimeError("fake SMTP rejection")
                test_case.sent.append(
                    {
                        "to": message["To"],
                        "subject": message["Subject"],
                        "body": message.get_content().strip(),
                    }
                )

        self.patchers = [
            mock.patch.object(brain, "load_saved_integration_credentials", return_value={}),
            mock.patch.object(brain, "email_settings", side_effect=fake_settings),
            mock.patch.object(brain, "open_email_connect_page", return_value=None),
            mock.patch.object(brain.smtplib, "SMTP_SSL", FakeSMTP),
            mock.patch.object(brain.smtplib, "SMTP", FakeSMTP),
            mock.patch.object(
                brain,
                "_generate_general_miko_response",
                side_effect=self.fake_general_response,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.assertFalse(self.ai.unscripted, f"Unexpected AI calls: {self.ai.unscripted}")
        self.assertEqual(self.unscripted_general, 0, "Unexpected general AI call")
        self.assertEqual(self.agent_script, [], "Unused scripted agent decisions")

    def fake_general_response(self, _prompt):
        if self.agent_script:
            return self.agent_script.pop(0)
        if self.require_agent_script:
            self.unscripted_general += 1
            raise AssertionError("Agent decision was not scripted for this turn")
        return self.brain.action_response_template("אני איתך בנושא החדש.", "happy")

    def agent_turn(
        self, message, intent="none", *, to="", recipient="", body="", subject="", query="", reply="אני איתך בנושא החדש."
    ):
        decision = self.brain.action_response_template(reply, "happy")
        decision.update({
            "email_intent": intent,
            "email_to": to,
            "email_recipient_name": recipient,
            "email_body": body,
            "email_subject": subject,
            "email_query": query,
        })
        self.agent_script.append(decision)
        return self.turn(message)

    def script_draft(self, *, recipient_name="", to="", body="", subject=""):
        self.ai.script.setdefault("miko_email_draft", []).append(
            {
                "intent": "draft" if to else "needs_recipient",
                "recipient_name": recipient_name,
                "to": to,
                "subject": subject,
                "body": body,
                "reply": "",
            }
        )

    def script_dialogue(self, intent, *, body="", email="", updated_email=""):
        self.ai.script.setdefault("miko_email_dialogue_turn", []).append(
            {
                "intent": intent,
                "recipient_name": "",
                "email": email,
                "subject": "",
                "body": body,
                "updated_email": updated_email,
                "send_after_update": False,
                "confidence": "high",
                "reply_hint": "",
            }
        )

    def turn(self, message: str):
        response = self.client.post("/think", json={"message": message})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertIsInstance(payload, dict)
        self.assertIn("message", payload)
        return payload

    def assert_persistent_identity(self):
        saved = json.loads((self.sandbox_path / "miko_brain_state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["bond"], 47)
        self.assertIn("זיכרון בדיקה שאסור למחוק", saved["memories"])


class LegacyConversationRegression(MikoFixture):

    def test_misheard_address_correction_body_prefix_and_successful_send(self):
        self.script_draft(recipient_name="אופק")
        first = self.turn("אני צריך שתשלח מייל לאופק")
        self.assertEqual(first["external_action"]["status"], "needs_recipient")

        heard = self.turn("ofekper3030@gmail.com")
        self.assertEqual(heard["external_action"]["status"], "confirm_recipient")
        self.assertEqual(self.brain.miko["email_compose"]["to"], "ofekper3030@gmail.com")

        corrected = self.turn("במקום per זה peer")
        self.assertEqual(corrected["external_action"]["status"], "recipient_edited")
        self.assertEqual(self.brain.miko["email_compose"]["to"], "ofekpeer3030@gmail.com")

        self.turn("כן")
        draft = self.turn("תכתוב לו, אני אגיע עוד עשר דקות.")
        self.assertEqual(draft["external_action"]["status"], "awaiting_confirmation")
        pending = self.brain.miko["pending_external_action"]
        self.assertEqual(pending["args"]["to"], "ofekpeer3030@gmail.com")
        self.assertEqual(pending["args"]["body"], "אני אגיע עוד עשר דקות.")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.brain.miko["email_contacts"], {})

        sent = self.turn("שלח")
        self.assertTrue(sent["external_action"]["ok"])
        self.assertEqual(sent["external_action"]["status"], "sent")
        self.assertIn("נשלח", sent["message"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["to"], "ofekpeer3030@gmail.com")
        self.assertEqual(self.sent[0]["body"], "אני אגיע עוד עשר דקות.")
        self.assertEqual(len(self.brain.miko["email_history"]), 1)
        self.assertEqual(self.brain.find_email_contact("אופק"), "ofekpeer3030@gmail.com")
        self.assert_persistent_identity()

    def test_smtp_failure_never_claims_sent_or_trusts_contact_then_retry(self):
        self.script_draft(recipient_name="נועה", to="noa@example.com", body="אני בדרך")
        self.turn("שלח מייל לנועה ב-noa@example.com וכתוב שאני בדרך")
        self.turn("כן")
        self.assertIsNotNone(self.brain.miko["pending_external_action"])

        self.smtp_failure = True
        failure_log = io.StringIO()
        with contextlib.redirect_stdout(failure_log):
            failed = self.turn("שלח")
        self.assertFalse(failed["external_action"]["ok"])
        self.assertEqual(failed["external_action"]["status"], "send_failed")
        self.assertNotIn("נשלח", failed["message"])
        self.assertNotIn("STATUS: SENT OK", failure_log.getvalue())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.brain.miko["email_history"], [])
        self.assertIsNone(self.brain.find_email_contact("נועה"))
        self.assertIsNotNone(self.brain.miko["pending_external_action"])

        self.smtp_failure = False
        success_log = io.StringIO()
        with contextlib.redirect_stdout(success_log):
            retried = self.turn("שלח")
        self.assertEqual(retried["external_action"]["status"], "sent")
        self.assertIn("נשלח", retried["message"])
        self.assertIn("STATUS: SENT OK", success_log.getvalue())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.brain.miko["email_history"]), 1)
        self.assertEqual(self.brain.find_email_contact("נועה"), "noa@example.com")
        self.assert_persistent_identity()

    def test_topic_switch_preserves_email_task_and_returns_to_general_chat(self):
        self.script_draft(recipient_name="דנה", to="dana@example.com")
        self.turn("תשלח מייל לדנה, dana@example.com")
        self.turn("כן")
        before = copy.deepcopy(self.brain.miko["email_compose"])
        self.assertEqual(before["stage"], "awaiting_body")

        self.script_dialogue("pause")
        chat = self.turn("רגע, איך היה היום שלך?")
        self.assertEqual(chat["message"], "אני איתך בנושא החדש.")
        paused = self.brain.miko["email_compose"]
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["to"], before["to"])
        self.assertEqual(paused["stage"], before["stage"])
        self.assertIsNone(self.brain.miko["pending_external_action"])
        self.assertEqual(self.sent, [])

        resumed = self.turn("נחזור למייל")
        self.assertEqual(resumed["external_action"]["status"], "resumed")
        draft = self.turn("תגיד לה שאני בדרך")
        self.assertEqual(draft["external_action"]["status"], "awaiting_confirmation")
        self.assertEqual(self.brain.miko["pending_external_action"]["args"]["body"], "אני בדרך")
        self.assertEqual(self.sent, [])
        self.assert_persistent_identity()

    def test_unrelated_yes_after_topic_switch_does_not_send_draft(self):
        self.script_draft(recipient_name="דנה", to="dana@example.com", body="נתראה מחר")
        self.turn("תשלח מייל לדנה dana@example.com וכתוב נתראה מחר")
        self.turn("כן")
        self.script_dialogue("pause")
        self.turn("רגע, בוא נדבר על משהו אחר")
        self.script_dialogue("none")
        reply = self.turn("כן")
        self.assertNotIn("נשלח", reply["message"])
        self.assertEqual(self.sent, [])
        self.assertIsNotNone(self.brain.miko["pending_external_action"])

    def test_missing_gmail_connection_keeps_draft_and_never_claims_sent(self):
        self.script_draft(recipient_name="דנה", to="dana@example.com", body="שלום")
        self.turn("תשלח מייל לדנה dana@example.com וכתוב שלום")
        self.turn("כן")
        self.smtp_configured = False
        blocked = self.turn("שלח")
        self.assertNotIn("נשלח", blocked["message"])
        self.assertEqual(self.sent, [])
        self.assertIsNotNone(self.brain.miko["pending_external_action"])
        self.assertEqual(self.brain.miko["email_history"], [])

    def test_new_email_request_supersedes_old_draft_without_sending_it(self):
        self.script_draft(recipient_name="דנה", to="dana@example.com", body="הטיוטה הישנה")
        self.turn("תשלח מייל לדנה dana@example.com וכתוב הטיוטה הישנה")
        self.turn("כן")
        previous_id = self.brain.miko["pending_external_action"]["id"]

        self.script_draft(recipient_name="נועה")
        result = self.turn("אני צריך שתשלח מייל לנועה")
        self.assertEqual(result["external_action"]["status"], "needs_recipient")
        self.assertIsNone(self.brain.miko["pending_external_action"])
        self.assertEqual(self.brain.miko["email_compose"]["recipient_name"], "נועה")
        self.assertTrue(any(
            item.get("id") == previous_id and item.get("status") == "superseded"
            for item in self.brain.miko["action_history"]
        ))
        self.assertEqual(self.sent, [])

    def test_recipient_confirmation_with_body_in_same_turn_advances(self):
        self.script_draft(recipient_name="אופק")
        self.turn("אני צריך שתשלח מייל לאופק")
        self.turn("ofekpeer3030@gmail.com")
        self.ai.script.setdefault("miko_email_address", []).append({"email": ""})
        self.script_dialogue("provide_body", body="אני אגיע בעוד שעה")
        response = self.turn("נכון, תכתוב לו שאני אגיע בעוד שעה")
        pending = self.brain.miko.get("pending_external_action") or {}
        self.assertEqual(pending.get("args", {}).get("body"), "אני אגיע בעוד שעה", response)
        self.assertEqual(pending.get("args", {}).get("to"), "ofekpeer3030@gmail.com")
        self.assertEqual(self.sent, [])

    def test_address_history_never_accepts_a_hallucinated_reconstruction(self):
        # The prior real address exists in history. A fake parser supplies the
        # specific wrong reconstruction seen in the handoff log.
        self.brain.miko["email_history"] = [
            {"to": "ofekpeer3030@gmail.com", "recipient_name": "אופק", "sent_at": 1.0, "action_id": "old-1"},
            {"to": "ofak93@gmail.com", "recipient_name": "אופק", "sent_at": 2.0, "action_id": "new-2"},
        ]
        self.brain.save_state()
        self.script_draft(recipient_name="אופק", to="ofak3030@gmail.com", body="אני בדרך")
        result = self.turn("שלח מייל לכתובת הקודמת של אופק שלושים שלושים וכתוב שאני בדרך")
        compose = self.brain.miko.get("email_compose") or {}
        pending = self.brain.miko.get("pending_external_action") or {}
        proposed = compose.get("to") or pending.get("args", {}).get("to")
        self.assertNotEqual(proposed, "ofak3030@gmail.com", result)
        self.assertEqual(self.sent, [])
        self.assert_persistent_identity()


class UnifiedAgentRegression(MikoFixture):
    def setUp(self):
        super().setUp()
        self.require_agent_script = True

    def test_agent_multiturn_correction_body_cleanup_and_smtp_success(self):
        first = self.agent_turn("אני צריך שתשלח מייל לאופק", "compose", recipient="אופק")
        self.assertEqual(first["external_action"]["status"], "needs_recipient")

        heard = self.agent_turn("ofekper3030@gmail.com", "edit", to="ofekper3030@gmail.com")
        self.assertEqual(heard["external_action"]["status"], "awaiting_body")
        self.assertEqual(self.brain.miko["email_compose"]["to"], "ofekper3030@gmail.com")

        corrected = self.agent_turn("במקום per זה peer", "edit", to="ofekpeer3030@gmail.com")
        self.assertEqual(corrected["external_action"]["status"], "awaiting_body")
        self.assertEqual(self.brain.miko["email_compose"]["to"], "ofekpeer3030@gmail.com")

        draft = self.agent_turn(
            "תכתוב לו, אני אגיע עוד עשר דקות.", "edit", body="לו, אני אגיע עוד עשר דקות."
        )
        self.assertEqual(draft["external_action"]["status"], "awaiting_confirmation")
        pending = self.brain.miko["pending_external_action"]
        self.assertEqual(pending["args"]["to"], "ofekpeer3030@gmail.com")
        self.assertEqual(pending["args"]["body"], "אני אגיע עוד עשר דקות.")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.brain.miko["email_contacts"], {})

        sent_log = io.StringIO()
        with contextlib.redirect_stdout(sent_log):
            result = self.agent_turn("שלח", "send")
        self.assertEqual(result["external_action"]["status"], "sent")
        self.assertIn("נשלח", result["message"])
        self.assertIn("STATUS: SENT OK", sent_log.getvalue())
        self.assertEqual(self.sent[0]["to"], "ofekpeer3030@gmail.com")
        self.assertEqual(self.sent[0]["body"], "אני אגיע עוד עשר דקות.")
        self.assertEqual(len(self.brain.miko["email_history"]), 1)
        self.assertEqual(self.brain.find_email_contact("אופק"), "ofekpeer3030@gmail.com")
        self.assert_persistent_identity()

    def test_agent_smtp_failure_and_retry_preserve_untrusted_contact(self):
        draft = self.agent_turn(
            "שלח מייל לנועה ב-noa@example.com וכתוב שאני בדרך", "compose",
            to="noa@example.com", recipient="נועה", body="אני בדרך",
        )
        self.assertEqual(draft["external_action"]["status"], "awaiting_confirmation")

        self.smtp_failure = True
        failure_log = io.StringIO()
        with contextlib.redirect_stdout(failure_log):
            failed = self.agent_turn("שלח", "send")
        self.assertEqual(failed["external_action"]["status"], "send_failed")
        self.assertNotIn("נשלח", failed["message"])
        self.assertNotIn("STATUS: SENT OK", failure_log.getvalue())
        self.assertEqual(self.brain.miko["email_history"], [])
        self.assertEqual(self.brain.miko["email_contacts"], {})
        self.assertFalse(any(e.get("verified") for e in self.brain.miko.get("email_contact_entities", [])))
        self.assertEqual(self.sent, [])

        self.smtp_failure = False
        success_log = io.StringIO()
        with contextlib.redirect_stdout(success_log):
            retried = self.agent_turn("שלח", "send")
        self.assertEqual(retried["external_action"]["status"], "sent")
        self.assertIn("STATUS: SENT OK", success_log.getvalue())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.brain.miko["email_history"]), 1)
        self.assertEqual(self.brain.find_email_contact("נועה"), "noa@example.com")
        self.assertTrue(any(
            e.get("address") == "noa@example.com" and e.get("verified")
            for e in self.brain.miko.get("email_contact_entities", [])
        ))
        self.assert_persistent_identity()

    def test_agent_timeout_marks_delivery_uncertain_without_retry(self):
        self.agent_turn(
            "שלח מייל לדנה dana@example.com וכתוב שלום", "compose",
            to="dana@example.com", recipient="דנה", body="שלום",
        )
        self.smtp_failure = True
        self.smtp_exception = TimeoutError("fake timeout after SMTP handoff")
        first = self.agent_turn("שלח", "send")
        self.assertEqual(first["external_action"]["status"], "delivery_uncertain")
        self.assertNotIn("נשלח", first["message"])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.brain.miko["email_history"], [])

        second = self.agent_turn("שלח", "send")
        self.assertEqual(second["external_action"]["status"], "delivery_uncertain")
        self.assertEqual(self.sent, [])

    def test_agent_cancel_while_waiting_for_address_returns_to_chat(self):
        self.agent_turn("אני צריך שתשלח מייל לאופק", "compose", recipient="אופק")
        cancelled = self.agent_turn("התחרטתי, אני לא רוצה לשלוח כרגע מייל", "cancel")
        self.assertEqual(cancelled["external_action"]["status"], "cancelled")
        self.assertIsNone(self.brain.miko["email_compose"])
        self.assertIsNone(self.brain.miko["pending_external_action"])
        chat = self.agent_turn("מה קורה?", "none")
        self.assertEqual(chat["message"], "אני איתך בנושא החדש.")
        self.assertEqual(self.sent, [])
        self.assert_persistent_identity()

    def test_agent_pending_only_legacy_draft_can_be_edited(self):
        self.agent_turn(
            "תשלח מייל לדנה dana@example.com וכתוב שלום", "compose",
            to="dana@example.com", recipient="דנה", body="שלום",
        )
        old_pending = self.brain.miko["pending_external_action"]
        old_id = old_pending["id"]
        # 14.4 often cleared compose once a ready action was queued.
        self.brain.miko["email_compose"] = None
        self.brain.save_state()

        edited = self.agent_turn("תשנה את התוכן לאני אגיע מחר", "edit", body="אני אגיע מחר")
        self.assertEqual(edited["external_action"]["status"], "awaiting_confirmation")
        new_pending = self.brain.miko["pending_external_action"]
        self.assertNotEqual(new_pending["id"], old_id)
        self.assertEqual(new_pending["args"]["to"], "dana@example.com")
        self.assertEqual(new_pending["args"]["recipient_name"], "דנה")
        self.assertEqual(new_pending["args"]["body"], "אני אגיע מחר")
        self.assertTrue(any(
            item.get("id") == old_id and item.get("status") == "superseded"
            for item in self.brain.miko["action_history"]
        ))
        self.assertEqual(self.sent, [])

    def test_agent_non_loopback_requests_are_rejected_before_processing(self):
        for path, method in (("/health", "GET"), ("/think", "POST")):
            with self.subTest(path=path):
                kwargs = {"environ_overrides": {"REMOTE_ADDR": "203.0.113.9"}}
                if method == "POST":
                    kwargs["json"] = {"message": "שלח"}
                response = self.client.open(path, method=method, **kwargs)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json().get("status"), "local_only")
        self.assertEqual(self.ai.calls, [])
        self.assertEqual(self.sent, [])
        self.assert_persistent_identity()

    def test_agent_restart_marks_interrupted_send_uncertain(self):
        queued = self.brain.queue_email_action(
            "dana@example.com", "בדיקה", "שלום", "synthetic test", "דנה"
        )
        self.assertTrue(queued["ok"])
        self.brain.miko["pending_external_action"]["status"] = "executing"
        self.brain.save_state()

        spec = importlib.util.spec_from_file_location("miko_recovery_regression_copy", self.copy_path)
        restarted = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"openai": self.fake_openai}), mock.patch(
            "os.path.expanduser", return_value=str(self.sandbox_path)
        ), mock.patch.object(Path, "home", return_value=self.sandbox_path):
            spec.loader.exec_module(restarted)

        recovered = restarted.miko["pending_external_action"]
        self.assertEqual(recovered["status"], "delivery_uncertain")
        self.assertFalse(recovered["confirmation_active"])
        self.assertEqual(restarted.execute_pending_external_action()["status"], "delivery_uncertain")
        self.assertEqual(self.sent, [])
        self.assertEqual(restarted.miko["email_history"], [])
        self.assert_persistent_identity()

    def test_agent_modified_send_phrase_cannot_send_old_pending(self):
        self.agent_turn(
            "תשלח מייל לדנה dana@example.com וכתוב שלום", "compose",
            to="dana@example.com", recipient="דנה", body="שלום",
        )
        old_id = self.brain.miko["pending_external_action"]["id"]
        response = self.agent_turn("שלח לאופק", "send", recipient="אופק")
        self.assertEqual(response["external_action"]["status"], "awaiting_confirmation")
        self.assertNotIn("נשלח", response["message"])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.brain.miko["pending_external_action"]["id"], old_id)

    def test_agent_named_lookup_never_falls_back_to_unrelated_last_sent(self):
        self.brain.miko["email_history"] = [
            {"to": "dana@example.com", "recipient_name": "דנה", "sent_at": 1.0, "action_id": "dana-1"},
        ]
        self.brain.save_state()
        response = self.agent_turn(
            "מה כתובת המייל של אופק?", "lookup", recipient="אופק", query="כתובת המייל של אופק"
        )
        self.assertEqual(response["external_action"]["status"], "contact_not_found")
        self.assertNotIn("dana@example.com", response["message"])
        self.assertEqual(self.sent, [])

    def test_agent_cross_origin_form_post_cannot_confirm_pending(self):
        self.agent_turn(
            "תשלח מייל לדנה dana@example.com וכתוב שלום", "compose",
            to="dana@example.com", recipient="דנה", body="שלום",
        )
        pending_id = self.brain.miko["pending_external_action"]["id"]
        response = self.client.post(
            "/actions/confirm",
            data={"confirm": "true"},
            headers={"Origin": "https://other.example", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get("status"), "cross_origin_blocked")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.brain.miko["pending_external_action"]["id"], pending_id)

    def assert_models_unavailable_turn(self, message):
        with mock.patch.object(self.brain, "_generate_general_miko_response", self.real_generate), mock.patch.object(
            self.ai.responses, "create", side_effect=RuntimeError("synthetic model outage")
        ) as create, contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            response = self.turn(message)
        self.assertEqual(create.call_count, 2, "Expected both main and fast models to fail")
        return response

    def test_agent_offline_exact_send_still_uses_smtp(self):
        self.agent_turn(
            "תשלח מייל לדנה dana@example.com וכתוב שלום", "compose",
            to="dana@example.com", recipient="דנה", body="שלום",
        )
        sent = self.assert_models_unavailable_turn("שלח")
        self.assertEqual(sent["external_action"]["status"], "sent")
        self.assertIn("נשלח", sent["message"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.brain.miko["email_history"]), 1)

    def test_agent_offline_exact_cancel_still_clears_task(self):
        self.agent_turn("אני צריך שתשלח מייל לאופק", "compose", recipient="אופק")
        cancelled = self.assert_models_unavailable_turn("בטל את המייל")
        self.assertEqual(cancelled["external_action"]["status"], "cancelled")
        self.assertIsNone(self.brain.miko["email_compose"])
        self.assertEqual(self.sent, [])

    def test_agent_offline_exact_resume_still_recovers_task(self):
        self.agent_turn(
            "תשלח מייל לדנה dana@example.com", "compose", to="dana@example.com", recipient="דנה"
        )
        self.agent_turn("רגע, בוא נדבר על משהו אחר", "none")
        self.assertTrue(self.brain.miko["email_compose"]["paused"])
        resumed = self.assert_models_unavailable_turn("נחזור למייל")
        self.assertEqual(resumed["external_action"]["status"], "awaiting_body")
        self.assertFalse(self.brain.miko["email_compose"]["paused"])
        self.assertEqual(self.sent, [])

    def test_agent_topic_switch_resume_and_unrelated_yes(self):
        first = self.agent_turn(
            "תשלח מייל לדנה, dana@example.com", "compose", to="dana@example.com", recipient="דנה"
        )
        self.assertEqual(first["external_action"]["status"], "awaiting_body")
        before = copy.deepcopy(self.brain.miko["email_compose"])

        chat = self.agent_turn("רגע, איך היה היום שלך?", "none")
        self.assertEqual(chat["message"], "אני איתך בנושא החדש.")
        paused = self.brain.miko["email_compose"]
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["to"], before["to"])
        self.assertEqual(self.sent, [])

        self.agent_turn("כן", "none")
        self.assertEqual(self.sent, [])
        resumed = self.agent_turn("נחזור למייל", "resume")
        self.assertEqual(resumed["external_action"]["status"], "awaiting_body")
        draft = self.agent_turn("תגיד לה שאני בדרך", "edit", body="אני בדרך")
        self.assertEqual(draft["external_action"]["status"], "awaiting_confirmation")
        self.assertEqual(self.brain.miko["pending_external_action"]["args"]["body"], "אני בדרך")
        self.assertEqual(self.sent, [])
        self.assert_persistent_identity()

    def test_agent_missing_connection_keeps_draft(self):
        self.agent_turn(
            "תשלח מייל לדנה dana@example.com וכתוב שלום", "compose",
            to="dana@example.com", recipient="דנה", body="שלום",
        )
        self.smtp_configured = False
        blocked = self.agent_turn("שלח", "send")
        self.assertEqual(blocked["external_action"]["status"], "needs_email_connection")
        self.assertNotIn("נשלח", blocked["message"])
        self.assertEqual(self.sent, [])
        self.assertIsNotNone(self.brain.miko["pending_external_action"])
        self.assertEqual(self.brain.miko["email_history"], [])

    def test_agent_new_request_supersedes_old_draft(self):
        self.agent_turn(
            "תשלח מייל לדנה dana@example.com וכתוב הטיוטה הישנה", "compose",
            to="dana@example.com", recipient="דנה", body="הטיוטה הישנה",
        )
        previous_id = self.brain.miko["pending_external_action"]["id"]
        result = self.agent_turn("אני צריך שתשלח מייל לנועה", "compose", recipient="נועה")
        self.assertEqual(result["external_action"]["status"], "needs_recipient")
        self.assertIsNone(self.brain.miko["pending_external_action"])
        self.assertEqual(self.brain.miko["email_compose"]["recipient_name"], "נועה")
        self.assertTrue(any(
            item.get("id") == previous_id and item.get("status") == "superseded"
            for item in self.brain.miko["action_history"]
        ))
        self.assertEqual(self.sent, [])

    def test_agent_combined_recipient_body_turn_advances_without_loop(self):
        self.agent_turn("אני צריך שתשלח מייל לאופק", "compose", recipient="אופק")
        result = self.agent_turn(
            "נכון, תכתוב לו שאני אגיע בעוד שעה ל-ofekpeer3030@gmail.com", "edit",
            to="ofekpeer3030@gmail.com", body="אני אגיע בעוד שעה",
        )
        self.assertEqual(result["external_action"]["status"], "awaiting_confirmation")
        pending = self.brain.miko["pending_external_action"]
        self.assertEqual(pending["args"]["to"], "ofekpeer3030@gmail.com")
        self.assertEqual(pending["args"]["body"], "אני אגיע בעוד שעה")
        self.assertEqual(self.sent, [])

    def test_agent_previous_address_rejects_model_reconstruction(self):
        self.brain.miko["email_history"] = [
            {"to": "ofekpeer3030@gmail.com", "recipient_name": "אופק", "sent_at": 1.0, "action_id": "old-1"},
            {"to": "ofak93@gmail.com", "recipient_name": "אופק", "sent_at": 2.0, "action_id": "new-2"},
        ]
        self.brain.save_state()
        response = self.agent_turn(
            "שלח מייל לכתובת הקודמת של אופק שלושים שלושים וכתוב שאני בדרך", "compose",
            to="ofak3030@gmail.com", recipient="אופק", body="אני בדרך",
        )
        compose = self.brain.miko.get("email_compose") or {}
        pending = self.brain.miko.get("pending_external_action") or {}
        proposed = compose.get("to") or pending.get("args", {}).get("to")
        self.assertNotEqual(proposed, "ofak3030@gmail.com", response)
        self.assertIn(proposed, ("ofekpeer3030@gmail.com", None, ""))
        self.assertEqual(self.sent, [])
        self.assert_persistent_identity()

    def test_agent_cannot_claim_sent_without_smtp_success(self):
        # The language model may emit a delivery claim despite choosing no
        # email tool. The final response must still be gated on SMTP success.
        response = self.agent_turn("מה קורה?", "none", reply="נשלח לאופק.")
        self.assertNotIn("נשלח", response["message"])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.brain.miko["email_history"], [])

    def test_agent_concurrent_confirmations_send_once(self):
        self.agent_turn(
            "שלח מייל לדנה dana@example.com וכתוב שלום", "compose",
            to="dana@example.com", recipient="דנה", body="שלום",
        )
        entered = threading.Event()
        release = threading.Event()
        results = {}

        def block_send():
            entered.set()
            if not release.wait(timeout=3):
                raise RuntimeError("fake SMTP synchronization timed out")

        def confirm(label):
            results[label] = self.brain.execute_pending_external_action()

        self.smtp_send_hook = block_send
        first = threading.Thread(target=confirm, args=("first",))
        second = threading.Thread(target=confirm, args=("second",))
        first.start()
        self.assertTrue(entered.wait(timeout=3), "First fake SMTP send did not start")
        try:
            second.start()
            second.join(timeout=2)
            self.assertFalse(second.is_alive(), "Second confirmation blocked behind SMTP")
        finally:
            release.set()
            first.join(timeout=3)
            second.join(timeout=3)
        self.assertFalse(first.is_alive())
        self.assertEqual(results["first"]["status"], "sent")
        self.assertEqual(results["second"]["status"], "already_executing")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.brain.miko["email_history"]), 1)


if __name__ == "__main__":
    # Windows terminals may default to cp1252, while the brain logs Hebrew.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain", type=Path, default=DEFAULT_BRAIN, help="Brain source to copy into an isolated test sandbox")
    args = parser.parse_args()
    SOURCE = args.brain.expanduser().resolve()
    source_text = SOURCE.read_text(encoding="utf-8")
    suite_class = UnifiedAgentRegression if "def dispatch_agent_email(" in source_text else LegacyConversationRegression
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(suite_class))
    raise SystemExit(0 if result.wasSuccessful() else 1)
