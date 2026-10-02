"""Local function tools for a conversational Miko Realtime host.

This module has no import-time access to Miko's state, credentials, network, or
audio devices. Pass an already running brain module to ``MikoRealtimeTools``.
The host owns the Realtime connection and supplies trustworthy turn metadata:

    tools.begin_turn(turn_id, user_text=transcript, input_kind="audio", has_user_audio=True)
    result = tools.call(function_name, function_arguments)
    # Speak the prepared draft in the model's own words, then only after the
    # readback was actually delivered:
    tools.mark_draft_presented(result["draft"]["id"])

Model-visible tool results are facts and statuses, never lines for Miko to say.
The host sends each result as a Realtime ``function_call_output`` and requests
another model response. The model's language and turn-taking remain its own.
"""

from __future__ import annotations

import copy
from difflib import SequenceMatcher
import json
import re
import time
import uuid
from typing import Any, Mapping


# Physical actions Miko's body can perform on request (Godot robot and device).
BODY_ACTIONS = (
    "walk_forward", "walk_back", "walk_left", "walk_right", "come_here", "go_away",
    "turn_around", "spin", "jump", "wave", "dance", "nod", "shake_head", "sit",
    "stand_up", "stretch", "think", "laugh", "look_around", "sleep", "wake_up", "stop",
)


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function", "name": "miko_get_context",
        "description": "Get the current email task, verified contacts, tentative address mentions, recent confirmed sends, and saved memories. Use this to continue a task after a topic switch.",
        "parameters": _schema({}),
    },
    {
        "type": "function", "name": "miko_lookup_contact",
        "description": "Look up a person or a previous address. Returns factual candidates and verification status; ask the user naturally if several remain.",
        "parameters": _schema({
            "name": {"type": "string", "description": "Person's name, or empty if unknown."},
            "reference": {"type": "string", "description": "The user's own reference, such as 'the previous Ofek' or 'peer3030'."},
        }),
    },
    {
        "type": "function", "name": "miko_lookup_sent_email",
        "description": "Look up confirmed sent-email history. Does not treat a draft or failed send as sent.",
        "parameters": _schema({
            "name": {"type": "string", "description": "Recipient name or empty for the last sent email."},
            "reference": {"type": "string", "description": "The user's words describing which sent email."},
        }),
    },
    {
        "type": "function", "name": "miko_recall_memory",
        "description": "Retrieve saved personal memories to answer a conversational question. These are memory entries, not verified email addresses.",
        "parameters": _schema({
            "query": {"type": "string", "description": "The user's memory question or topic."},
        }),
    },
    {
        "type": "function", "name": "miko_remember",
        "description": "Add a stable user-provided fact to Miko's separate Realtime memory, or replace one Realtime memory entry. Never erases legacy memories.",
        "parameters": _schema({
            "fact": {"type": "string", "description": "Concise fact to keep."},
            "source_quote": {"type": "string", "description": "Short quote from the current user's own words supporting the fact."},
            "replace_index": {"type": "integer", "description": "Index in Realtime memories to replace, or -1 to append."},
        }),
    },
    {
        "type": "function", "name": "miko_get_status",
        "description": "Get Miko's current state and whether email is connected. Never returns credentials.",
        "parameters": _schema({}),
    },
    {
        "type": "function", "name": "miko_set_expression",
        "description": "Set a valid emotion and return a simple Godot action cue for animation. No spoken response is prescribed.",
        "parameters": _schema({
            "emotion": {"type": "string", "enum": ["happy", "sad", "angry", "sleepy", "excited", "curious", "shy"]},
            "action": {"type": "string", "enum": ["idle", "look", "bounce", "jump", "dance", "hide", "sleep"]},
        }),
    },
    {
        "type": "function", "name": "miko_perform_action",
        "description": (
            "Make Miko's body do something the owner asked for: walk, come closer, jump, wave, "
            "dance, sit, spin, nod, etc. Call it right away for any physical request "
            "('תלך', 'תבוא אליי', 'תקפוץ', 'תעשה שלום', 'שב', 'תסתובב', 'עצור'). "
            "Directions are from the owner's point of view. Returns when the motion has started."
        ),
        "parameters": _schema({
            "action": {"type": "string", "enum": list(BODY_ACTIONS)},
            "times": {"type": "integer", "minimum": 1, "maximum": 5,
                      "description": "Repetitions for jump/wave/nod/spin; 1 if not said."},
        }),
    },
    {
        "type": "function", "name": "miko_get_vision",
        "description": (
            "What Miko's camera perception currently knows about the owner: whether someone is "
            "in view, roughly where, how close, and recent events like a wave. Derived locally; "
            "no image is sent. Use it when asked 'do you see me', 'where am I', 'did you see me wave'."
        ),
        "parameters": _schema({}),
    },
    {
        "type": "function", "name": "miko_prepare_email",
        "description": "Start or edit an email task. A spoken address may be tentative; return a factual draft for natural readback. This tool never sends.",
        "parameters": _schema({
            "mode": {"type": "string", "enum": ["new", "edit"]},
            "recipient_name": {"type": "string"},
            "to_address": {"type": "string", "description": "Complete candidate address, or empty if unknown."},
            "subject": {"type": "string", "description": "Subject, or empty to preserve an existing subject."},
            "body": {"type": "string", "description": "The email text only, or empty to preserve an existing body."},
            "source_quote": {"type": "string", "description": "A short quote from this user turn grounding a spoken address or correction. It may differ from auxiliary STT."},
            "reference": {"type": "string", "description": "User's reference to a saved or previous address, if any."},
        }),
    },
    {
        "type": "function", "name": "miko_pause_email",
        "description": "Put the current email task in the background during a topic switch without deleting it.",
        "parameters": _schema({}),
    },
    {
        "type": "function", "name": "miko_resume_email",
        "description": "Return the saved email task for natural continuation or a fresh readback. Does not send.",
        "parameters": _schema({}),
    },
    {
        "type": "function", "name": "miko_cancel_email",
        "description": "Cancel an unfinished email task or draft. Does not affect confirmed sent history.",
        "parameters": _schema({}),
    },
    {
        "type": "function", "name": "miko_send_email",
        "description": "Send only a previously read-back draft after the user explicitly confirms it in a fresh turn. Returns actual SMTP outcome; never claim sent from model judgment alone.",
        "parameters": _schema({
            "draft_id": {"type": "string", "description": "Exact id returned by miko_prepare_email."},
            "confirmation_quote": {"type": "string", "description": "Short quote of the user's current approval, never the assistant's own words."},
        }),
    },
]


class MikoRealtimeTools:
    """Dependency-injected bridge from Realtime function calls to local brain tools.

    ``begin_turn`` must be called by trusted host code for each user turn. The
    model cannot set ``has_user_audio`` or ``turn_id`` through tool arguments.
    ``mark_draft_presented`` must be called by the host after a real readback.
    """

    CONFIRMATION_MAX_AGE_SECONDS = 90
    # Set by the host when local camera perception runs: () -> dict summary.
    vision_provider = None

    def __init__(self, brain: Any):
        self.brain = brain
        self._turn_id = ""
        self._turn_sequence = 0
        self._user_text = ""
        self._input_kind = "text"
        self._has_user_audio = False

    @staticmethod
    def tool_definitions() -> list[dict[str, Any]]:
        return copy.deepcopy(TOOL_DEFINITIONS)

    def begin_turn(
        self, turn_id: str, *, user_text: str = "", input_kind: str = "text", has_user_audio: bool = False
    ) -> None:
        if not str(turn_id).strip():
            raise ValueError("A trusted, unique turn_id is required")
        if input_kind not in {"audio", "text"}:
            raise ValueError("input_kind must be audio or text")
        if str(turn_id) == self._turn_id:
            if str(user_text or "").strip():
                self._user_text = str(user_text)[:4000]
            self._has_user_audio = self._has_user_audio or bool(has_user_audio and input_kind == "audio")
            return
        self._turn_sequence += 1
        self._turn_id = str(turn_id)
        self._user_text = str(user_text or "")[:4000]
        self._input_kind = input_kind
        self._has_user_audio = bool(has_user_audio and input_kind == "audio")
        with self.brain.state_lock:
            pending = self.brain.miko.get("pending_external_action")
            if isinstance(pending, dict):
                presented = pending.get("realtime_presented_sequence")
                if isinstance(presented, int) and self._turn_sequence > presented + 1 and pending.get("confirmation_active"):
                    pending["confirmation_active"] = False
                    self.brain.save_state()

    def update_turn_transcript(self, turn_id: str, text: str) -> bool:
        """Attach late auxiliary STT without changing the trusted audio turn."""
        if str(turn_id) != self._turn_id:
            return False
        if str(text or "").strip():
            self._user_text = str(text)[:4000]
        return True

    def call(self, name: str, arguments: Mapping[str, Any] | str | None = None) -> dict[str, Any]:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return {"ok": False, "status": "invalid_arguments"}
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            return {"ok": False, "status": "invalid_arguments"}
        methods = {
            "miko_get_context": self.get_context,
            "miko_lookup_contact": self.lookup_contact,
            "miko_lookup_sent_email": self.lookup_sent_email,
            "miko_recall_memory": self.recall_memory,
            "miko_remember": self.remember,
            "miko_get_status": self.get_status,
            "miko_set_expression": self.set_expression,
            "miko_perform_action": self.perform_action,
            "miko_get_vision": self.get_vision,
            "miko_prepare_email": self.prepare_email,
            "miko_pause_email": self.pause_email,
            "miko_resume_email": self.resume_email,
            "miko_cancel_email": self.cancel_email,
            "miko_send_email": self.send_email,
        }
        method = methods.get(name)
        if method is None:
            return {"ok": False, "status": "unknown_tool", "name": str(name)}
        try:
            return method(**dict(arguments))
        except (TypeError, ValueError):
            return {"ok": False, "status": "invalid_arguments"}

    def _snapshot(self) -> dict[str, Any]:
        with self.brain.state_lock:
            return copy.deepcopy(self.brain.miko)

    def _pending(self) -> dict[str, Any] | None:
        pending = self._snapshot().get("pending_external_action")
        return pending if isinstance(pending, dict) else None

    def _compose(self) -> dict[str, Any] | None:
        compose = self._snapshot().get("email_compose")
        return compose if isinstance(compose, dict) else None

    @staticmethod
    def _draft_facts(pending: dict[str, Any]) -> dict[str, Any]:
        args = pending.get("args", {}) if isinstance(pending.get("args"), dict) else {}
        return {
            "id": str(pending.get("id", "")),
            "status": str(pending.get("status", "")),
            "to": str(args.get("to", "")),
            "recipient_name": str(args.get("recipient_name", "")),
            "subject": str(args.get("subject", "")),
            "body": str(args.get("body", "")),
            "confirmation_active": bool(pending.get("confirmation_active", False)),
            "presented_at": float(pending.get("realtime_presented_at", 0) or 0),
            "address_basis": str(pending.get("realtime_address_basis", "")),
        }

    def get_context(self) -> dict[str, Any]:
        state = self._snapshot()
        with self.brain.state_lock:
            entities = copy.deepcopy(self.brain._agent_contact_entities())
            mentions = copy.deepcopy(self.brain._agent_address_mentions())
            history = copy.deepcopy(self.brain.all_sent_email_history())
            self.brain.save_state()
        pending = state.get("pending_external_action")
        compose = state.get("email_compose")
        return {
            "ok": True, "status": "context",
            "draft": self._draft_facts(pending) if isinstance(pending, dict) else None,
            "compose": {
                k: compose.get(k) for k in ("stage", "recipient_name", "to", "subject", "body", "paused", "updated_at")
            } if isinstance(compose, dict) else None,
            "verified_contacts": [
                {"id": e.get("id"), "address": e.get("address"), "names": e.get("names", []), "last_used_at": e.get("last_used_at", 0)}
                for e in entities if e.get("verified")
            ][-30:],
            "tentative_addresses": [
                {"address": e.get("address"), "recipient_name": e.get("recipient_name", ""), "rejected": bool(e.get("rejected"))}
                for e in mentions[-20:]
            ],
            "confirmed_sent": [
                {"to": h.get("to"), "recipient_name": h.get("recipient_name", ""), "subject": h.get("subject", ""), "sent_at": h.get("sent_at", 0)}
                for h in history[-15:]
            ],
            "memories": [str(m) for m in state.get("memories", [])[-50:]],
            "realtime_memories": [
                {"id": m.get("id"), "fact": m.get("fact"), "updated_at": m.get("updated_at")}
                for m in state.get("realtime_memories", [])[-50:] if isinstance(m, dict)
            ],
        }

    def lookup_contact(self, name: str = "", reference: str = "") -> dict[str, Any]:
        name = self.brain._clean_contact_name(name)
        reference = str(reference or "")[:500]
        with self.brain.state_lock:
            entities = copy.deepcopy(self.brain._agent_contact_entities())
            mentions = copy.deepcopy(self.brain._agent_address_mentions())
            if any(w in reference for w in ("קודם", "קודמ", "previous", "earlier")):
                previous = self.brain._agent_previous_address(name, query=reference)
            else:
                previous = ""
            self.brain.save_state()
        if previous:
            verified = any(e.get("address") == previous and e.get("verified") for e in entities)
            return {"ok": True, "status": "single", "candidate": {"address": previous, "verified": verified, "basis": "previous_reference"}}
        key = self.brain._contact_key(name)
        candidates = [
            {"address": e.get("address"), "names": e.get("names", []), "verified": True, "basis": "confirmed_send"}
            for e in entities if e.get("verified") and (not key or key in {self.brain._contact_key(n) for n in e.get("names", [])})
        ]
        tokens = [t for t in re.findall(r"[a-z0-9]{3,}", reference.lower()) if t not in {"gmail", "mail", "com"}]
        if tokens:
            narrowed = [c for c in candidates if any(t in str(c["address"]).split("@", 1)[0] for t in tokens)]
            if narrowed:
                candidates = narrowed
        if len(candidates) == 1:
            return {"ok": True, "status": "single", "candidate": candidates[0]}
        if candidates:
            return {"ok": True, "status": "ambiguous", "candidates": candidates[:10]}
        tentative = [
            {"address": m.get("address"), "verified": False, "basis": "prior_mention"}
            for m in mentions if not m.get("rejected") and (not key or self.brain._contact_key(m.get("recipient_name", "")) == key)
        ]
        return {"ok": False, "status": "not_found", "tentative_candidates": tentative[-5:]}

    def lookup_sent_email(self, name: str = "", reference: str = "") -> dict[str, Any]:
        name = self.brain._clean_contact_name(name)
        history = self.brain.all_sent_email_history()
        if name:
            key = self.brain._contact_key(name)
            history = [h for h in history if self.brain._contact_key(h.get("recipient_name", "")) == key]
        tokens = [t for t in re.findall(r"[a-z0-9]{3,}", str(reference).lower()) if t not in {"gmail", "mail", "com"}]
        if tokens:
            history = [h for h in history if any(t in str(h.get("to", "")).split("@", 1)[0] for t in tokens)]
        if not history:
            return {"ok": False, "status": "not_found"}
        item = history[-1]
        return {"ok": True, "status": "confirmed_sent", "email": {
            "to": item.get("to", ""), "recipient_name": item.get("recipient_name", ""),
            "subject": item.get("subject", ""), "body": item.get("body", ""),
            "sent_at": item.get("sent_at", 0),
        }}

    def recall_memory(self, query: str = "") -> dict[str, Any]:
        state = self._snapshot()
        legacy = [{"fact": str(m), "source": "legacy"} for m in state.get("memories", [])[-50:]]
        realtime = [
            {"fact": str(m.get("fact", "")), "source": "realtime", "index": i}
            for i, m in enumerate(state.get("realtime_memories", [])) if isinstance(m, dict)
        ]
        memories = legacy + realtime
        tokens = [t for t in re.findall(r"[\w\u0590-\u05ff]{3,}", str(query).lower())]
        matching = [m for m in memories if any(t in m["fact"].lower() for t in tokens)] if tokens else memories
        # Return all recent memories when lexical matching misses Hebrew inflection.
        return {"ok": True, "status": "memory", "matches": matching[-20:] or memories[-20:], "address_verified": False}

    def remember(self, fact: str, source_quote: str, replace_index: int = -1) -> dict[str, Any]:
        if not self._turn_id or not self._grounded_quote(source_quote):
            return {"ok": False, "status": "fact_not_grounded"}
        fact = " ".join(str(fact or "").strip().split())[:400]
        if not fact:
            return {"ok": False, "status": "empty_fact"}
        if re.search(r"(?:api[_ -]?key|password|סיסמ[אה]|מפתח api|smtp[_ -]?password)", fact, re.IGNORECASE):
            return {"ok": False, "status": "sensitive_fact"}
        if type(replace_index) is not int:
            return {"ok": False, "status": "invalid_index"}
        with self.brain.state_lock:
            items = self.brain.miko.setdefault("realtime_memories", [])
            if not isinstance(items, list):
                return {"ok": False, "status": "invalid_memory_state"}
            if replace_index >= 0 and (replace_index >= len(items) or not isinstance(items[replace_index], dict)):
                return {"ok": False, "status": "invalid_index"}
            if replace_index == -1 and any(str(m.get("fact", "")).casefold() == fact.casefold() for m in items if isinstance(m, dict)):
                return {"ok": True, "status": "already_remembered"}
            now = time.time()
            item = {
                "id": items[replace_index].get("id") if replace_index >= 0 else str(uuid.uuid4()),
                "fact": fact,
                "source_quote": str(source_quote)[:400],
                "created_at": items[replace_index].get("created_at", now) if replace_index >= 0 else now,
                "updated_at": now,
            }
            if replace_index >= 0:
                items[replace_index] = item
            else:
                items.append(item)
            # Keep stored personal facts. Context/lookup may select a smaller
            # subset, but a device upgrade or 101st fact must not erase memory.
            self.brain.save_state()
            return {"ok": True, "status": "replaced" if replace_index >= 0 else "remembered", "id": item["id"]}

    def set_expression(self, emotion: str, action: str) -> dict[str, Any]:
        allowed_emotions = {"happy", "sad", "angry", "sleepy", "excited", "curious", "shy"}
        allowed_actions = {"idle", "look", "bounce", "jump", "dance", "hide", "sleep"}
        if emotion not in allowed_emotions or action not in allowed_actions:
            return {"ok": False, "status": "invalid_expression"}
        with self.brain.state_lock:
            self.brain.set_current_emotion(emotion, 60)
            self.brain.save_state()
        return {"ok": True, "status": "expression_set", "emotion": emotion, "action": action}

    def perform_action(self, action: str, times: int = 1) -> dict[str, Any]:
        if action not in BODY_ACTIONS:
            return {"ok": False, "status": "invalid_action"}
        try:
            times = max(1, min(5, int(times)))
        except (TypeError, ValueError):
            times = 1
        if action == "sleep":
            with self.brain.state_lock:
                self.brain.set_current_emotion("sleepy", 60)
        # The host forwards this result to Godot and the paired device, which
        # animate it. The result is a fact for the model, not a phrase.
        return {"ok": True, "status": "performing", "action": action, "times": times}

    def get_vision(self) -> dict[str, Any]:
        provider = type(self).vision_provider
        if provider is None:
            return {"ok": True, "status": "camera_off", "seen": False}
        try:
            return {"ok": True, **provider()}
        except Exception:
            return {"ok": False, "status": "vision_unavailable"}

    def get_status(self) -> dict[str, Any]:
        state = self._snapshot()
        email = self.brain.email_public_status()
        return {
            "ok": True, "status": "state",
            "vitals": {k: state.get(k) for k in ("mood", "energy", "curiosity", "hunger", "boredom", "affection", "bond", "sleeping")},
            "email": {k: email.get(k) for k in ("enabled", "configured", "connection")},
            "has_draft": isinstance(state.get("pending_external_action"), dict),
            "has_compose": isinstance(state.get("email_compose"), dict),
        }

    def _grounded_quote(self, quote: str) -> bool:
        quote = str(quote or "").strip()
        if not quote:
            return False
        source = re.sub(r"\s+", " ", self._user_text.casefold()).strip()
        quoted = re.sub(r"\s+", " ", quote.casefold()).strip()
        if quoted and quoted in source:
            return True
        # For live audio, the Realtime model heard user audio directly. The
        # auxiliary STT transcript is useful context but not the sole arbiter.
        return bool(self._input_kind == "audio" and self._has_user_audio and len(quote) >= 2)

    def _address_basis(self, address: str, name: str, current: str, quote: str, reference: str) -> str:
        if address in self.brain._agent_explicit_addresses(self._user_text):
            return "explicit_current_text"
        named = self.brain._agent_named_candidates(name) if name else []
        if any(e.get("address") == address for e in named):
            return "verified_contact"
        if reference and any(w in reference for w in ("קודם", "קודמ", "previous", "earlier")):
            previous = self.brain._agent_previous_address(name, current, reference)
            if previous == address:
                return "previous_record"
        if current == address and self.brain.valid_email_address(current):
            return "existing_draft"
        if self._grounded_quote(quote):
            if self._has_user_audio:
                return "tentative_from_audio"
            if current and self.brain.apply_email_address_edit_locally(self._user_text, current) == address:
                return "grounded_text_correction"
            if current and self._bounded_text_address_correction(current, address, quote):
                return "grounded_text_correction"
        return ""

    @staticmethod
    def _bounded_text_address_correction(current: str, candidate: str, quote: str) -> bool:
        """Accept a small exact edit whose resulting fragment the user said.

        The brain's local edit parser handles many explicit commands. This
        covers natural corrections such as "חסרה E, זה peer" without treating
        an arbitrary model-proposed address as evidence. Domain edits need the
        entire new domain in the user's quote.
        """
        quoted = str(quote or "").casefold()
        correction_markers = (
            "חסר", "תוסיף", "תשנה", "תתקן", "תקן", "במקום", "האות", "הכתובת", "כתובת",
            "לא ", "זה ", "missing", "add", "change", "replace", "correction", "instead",
        )
        if not any(marker in quoted for marker in correction_markers):
            return False
        if "@" not in current or "@" not in candidate:
            return False
        old_local, old_domain = current.casefold().split("@", 1)
        new_local, new_domain = candidate.casefold().split("@", 1)
        if old_domain != new_domain and new_domain not in quoted:
            return False
        if old_local == new_local:
            return old_domain != new_domain
        edits = [op for op in SequenceMatcher(None, old_local, new_local, autojunk=False).get_opcodes() if op[0] != "equal"]
        if len(edits) != 1:
            return False
        _, old_start, old_end, new_start, new_end = edits[0]
        if max(old_end - old_start, new_end - new_start) > 3:
            return False
        # A literal resulting substring of length 3+ must occur in the quote
        # and span the changed position. A bare letter like "E" is too weak.
        for start in range(max(0, new_start - 5), min(new_start, len(new_local) - 3) + 1):
            for end in range(max(new_end, start + 3), min(len(new_local), start + 8) + 1):
                fragment = new_local[start:end]
                if re.search(r"[a-z]{3,}", fragment) and fragment in quoted:
                    return True
        return False

    def prepare_email(
        self, mode: str, recipient_name: str = "", to_address: str = "", subject: str = "",
        body: str = "", source_quote: str = "", reference: str = "",
    ) -> dict[str, Any]:
        if not self._turn_id:
            return {"ok": False, "status": "no_user_turn"}
        if mode not in {"new", "edit"}:
            return {"ok": False, "status": "invalid_mode"}
        existing = self._compose() if mode == "edit" else None
        pending = self._pending()
        if mode == "edit" and existing is None and pending:
            args = pending.get("args", {})
            existing = args if isinstance(args, dict) else None
        if mode == "edit" and existing is None:
            return {"ok": False, "status": "no_draft"}
        existing = existing or {}
        name = self.brain._clean_contact_name(recipient_name or existing.get("recipient_name", ""))
        current = str(existing.get("to", "") or "").strip().lower()
        previous_name = self.brain._clean_contact_name(existing.get("recipient_name", ""))
        if mode == "edit" and recipient_name and previous_name and self.brain._contact_key(name) != self.brain._contact_key(previous_name) and not to_address:
            # A new person must not silently inherit the previous person's address.
            current = ""
        address = str(to_address or current or "").strip().lower().removeprefix("mailto:").lstrip("-")
        subject = self.brain.clean_email_subject(subject or existing.get("subject", ""))
        body = self.brain._agent_clean_body(body, self._user_text) if body else self.brain.clean_email_body(existing.get("body", ""))
        if pending and pending.get("status") in {"executing", "delivery_uncertain"}:
            return {"ok": False, "status": str(pending.get("status"))}

        if not address and name:
            lookup = self.lookup_contact(name, reference)
            if lookup.get("status") == "single":
                address = str(lookup["candidate"]["address"])
            elif lookup.get("status") == "ambiguous":
                return {"ok": False, "status": "ambiguous_recipient", "candidates": lookup["candidates"]}

        if address and not self.brain.valid_email_address(address):
            normalized = self.brain._spoken_email_normalize(source_quote) if source_quote else ""
            address = normalized if self.brain.valid_email_address(normalized) else ""
        if address:
            basis = self._address_basis(address, name, current, source_quote, reference)
            if not basis:
                return {"ok": False, "status": "address_unverified", "candidate": address, "reason": "No current speech, verified contact, or recorded reference grounds this candidate."}
            if basis == "verified_contact" and name:
                matched = any(e.get("address") == address for e in self.brain._agent_named_candidates(name))
                if not matched:
                    return {"ok": False, "status": "recipient_mismatch", "candidate": address}
        else:
            basis = ""

        # A new task supersedes an older draft only after its fields have been
        # validated. An edit preserves omitted fields, including pending-only
        # 14.4 drafts.
        if pending:
            self.brain.supersede_pending_external_action("realtime_new" if mode == "new" else "realtime_edit")
        if not address:
            self.brain.set_email_compose("awaiting_recipient", recipient_name=name, subject=subject, body=body)
            return {"ok": True, "status": "needs_recipient", "recipient_name": name, "body_saved": bool(body)}

        with self.brain.state_lock:
            self.brain._agent_record_address(address, name, self._user_text or source_quote)
            self.brain.save_state()
        if not body:
            self.brain.set_email_compose("awaiting_body", recipient_name=name, to_address=address, subject=subject)
            return {"ok": True, "status": "awaiting_body", "recipient": {"address": address, "name": name, "basis": basis}}

        queued = self.brain.queue_email_action(address, subject, body, self._user_text, name)
        if not queued.get("ok"):
            return {"ok": False, "status": str(queued.get("status", "draft_failed"))}
        self.brain.set_email_compose("awaiting_confirmation", recipient_name=name, to_address=address, subject=subject, body=body)
        with self.brain.state_lock:
            active = self.brain.miko.get("pending_external_action")
            if isinstance(active, dict) and active.get("id") == queued.get("action", {}).get("id"):
                active["confirmation_active"] = False
                active["confirmation_prompted_at"] = 0
                active["realtime_address_basis"] = basis
                active["realtime_prepared_turn_id"] = self._turn_id
                active.pop("realtime_presented_at", None)
                active.pop("realtime_presented_turn_id", None)
                self.brain.save_state()
                queued = copy.deepcopy(active)
        return {"ok": True, "status": "draft_ready", "draft": self._draft_facts(queued), "requires_readback": True}

    def mark_draft_presented(self, draft_id: str) -> dict[str, Any]:
        """Trusted host callback after the assistant actually read back the draft."""
        with self.brain.state_lock:
            pending = self.brain.miko.get("pending_external_action")
            if not isinstance(pending, dict) or pending.get("id") != draft_id:
                return {"ok": False, "status": "draft_changed"}
            if pending.get("status") in {"executing", "delivery_uncertain"}:
                return {"ok": False, "status": str(pending.get("status"))}
            pending["confirmation_active"] = True
            pending["confirmation_prompted_at"] = time.time()
            pending["realtime_presented_at"] = time.time()
            pending["realtime_presented_turn_id"] = self._turn_id
            pending["realtime_presented_sequence"] = self._turn_sequence
            self.brain.save_state()
            return {"ok": True, "status": "presented", "draft_id": draft_id}

    def pause_email(self) -> dict[str, Any]:
        with self.brain.state_lock:
            compose = self.brain.miko.get("email_compose")
            pending = self.brain.miko.get("pending_external_action")
            if not isinstance(compose, dict) and not isinstance(pending, dict):
                return {"ok": False, "status": "no_draft"}
            if isinstance(compose, dict):
                compose["paused"] = True
                compose["updated_at"] = time.time()
            if isinstance(pending, dict):
                pending["confirmation_active"] = False
            self.brain.save_state()
        return {"ok": True, "status": "paused"}

    def resume_email(self) -> dict[str, Any]:
        pending = self._pending()
        if pending:
            if pending.get("status") == "delivery_uncertain":
                return {"ok": False, "status": "delivery_uncertain", "draft": self._draft_facts(pending)}
            with self.brain.state_lock:
                current = self.brain.miko.get("pending_external_action")
                if isinstance(current, dict) and current.get("id") == pending.get("id"):
                    current["confirmation_active"] = False
                    compose = self.brain.miko.get("email_compose")
                    if isinstance(compose, dict):
                        compose["paused"] = False
                    self.brain.save_state()
            return {"ok": True, "status": "draft_ready", "draft": self._draft_facts(pending), "requires_readback": True}
        compose = self._compose()
        if not compose:
            return {"ok": False, "status": "no_draft"}
        with self.brain.state_lock:
            current = self.brain.miko.get("email_compose")
            if isinstance(current, dict):
                current["paused"] = False
                current["updated_at"] = time.time()
                self.brain.save_state()
        return {"ok": True, "status": str(compose.get("stage", "in_progress")), "compose": {
            k: compose.get(k) for k in ("recipient_name", "to", "subject", "body")
        }}

    def cancel_email(self) -> dict[str, Any]:
        pending = self._pending()
        compose = self._compose()
        if pending and pending.get("status") == "executing":
            return {"ok": False, "status": "already_executing"}
        if pending:
            self.brain.cancel_pending_external_action()
        if compose:
            self.brain.clear_email_compose()
        return {"ok": True, "status": "cancelled" if pending or compose else "no_draft"}

    @staticmethod
    def _discourse_no_send(text: str) -> bool:
        """Recognize a narrow 'no, just send' correction of a readback request."""
        text = re.sub(r"\s+", " ", str(text or "").casefold()).strip()
        return bool(re.fullmatch(
            r"לא\s*[,،]?\s*(?:פשוט|רק)\s+(?:תשלח|שלח|שלחי|תשגר|שגר)"
            r"(?:\s+(?:בבקשה|עכשיו|כבר))?[.!?]?",
            text,
        ))

    @staticmethod
    def _negative_signal(text: str) -> bool:
        if MikoRealtimeTools._discourse_no_send(text):
            return False
        text = re.sub(r"[.,!?;:،]+", " ", str(text or "").casefold())
        text = re.sub(r"\s+", " ", text).strip()
        negatives = ("לא ", "אל ", "אבל", "רגע", "חכה", "תשנה", "לשנות", "תתקן", "במקום", "בטל", "עזוב")
        return any(text == n.strip() or f" {n}" in f" {text} " for n in negatives)

    @staticmethod
    def _contains_confirmation_cue(text: str) -> bool:
        text = re.sub(r"[.,!?;:]+", " ", str(text or "").casefold())
        cues = (
            "כן", "בטח", "נכון", "בדיוק", "מאשר", "מאשרת", "מאושר", "מאושרת", "סבבה", "מעולה", "קדימה", "יאללה",
            "שלח", "תשלח", "שלחי", "לשלוח", "שגר", "תשגר", "אפשר לשלוח", "זה טוב", "זה בסדר",
            "yes", "send", "go ahead", "looks good", "approved",
        )
        return any(re.search(r"(?<!\w)" + re.escape(cue) + r"(?!\w)", text) for cue in cues)

    @staticmethod
    def _confirmation_signal(text: str) -> bool:
        if MikoRealtimeTools._negative_signal(text):
            return False
        text = re.sub(r"[.,!?;:،]+", " ", str(text or "").casefold())
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return False
        send_words = ("שלח", "תשלח", "שלחי", "לשלוח", "שגר", "תשגר", "send", "approved")
        if any(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) for word in send_words):
            return True
        approval_words = {
            "כן", "בטח", "נכון", "בדיוק", "מאשר", "מאשרת", "מאושר", "מאושרת", "סבבה", "מעולה", "קדימה", "יאללה",
            "זה", "טוב", "בסדר", "בבקשה", "yes", "please", "go", "ahead", "looks", "good", "okay", "ok",
        }
        words = text.split()
        return bool(words and len(words) <= 6 and all(word in approval_words for word in words)
                    and MikoRealtimeTools._contains_confirmation_cue(text))

    def _recipient_conflict(self, utterance: str, pending: dict[str, Any]) -> bool:
        args = pending.get("args", {}) if isinstance(pending.get("args"), dict) else {}
        to = str(args.get("to", "")).lower()
        for address in self.brain._agent_explicit_addresses(utterance):
            if address != to:
                return True
        match = re.search(r"(?:שלח|תשלח|שגר|תשגר).*?\s+ל([א-ת]{2,})\b", utterance)
        if match:
            named = self.brain._contact_key(match.group(1))
            expected = self.brain._contact_key(args.get("recipient_name", ""))
            if not expected or named != expected:
                return True
        return False

    def send_email(self, draft_id: str, confirmation_quote: str = "") -> dict[str, Any]:
        pending = self._pending()
        if not pending:
            return {"ok": False, "status": "no_draft"}
        if pending.get("id") != draft_id:
            return {"ok": False, "status": "draft_changed"}
        if pending.get("status") in {"executing", "delivery_uncertain"}:
            return {"ok": False, "status": str(pending.get("status"))}
        if not self._turn_id or pending.get("realtime_presented_turn_id") == self._turn_id:
            return {"ok": False, "status": "fresh_user_confirmation_required"}
        if pending.get("realtime_presented_sequence") != self._turn_sequence - 1:
            return {"ok": False, "status": "readback_required", "draft": self._draft_facts(pending)}
        age = time.time() - float(pending.get("realtime_presented_at", 0) or 0)
        if not pending.get("confirmation_active") or age > self.CONFIRMATION_MAX_AGE_SECONDS:
            return {"ok": False, "status": "readback_required", "draft": self._draft_facts(pending)}
        quote = str(confirmation_quote or "").strip()
        if not self._grounded_quote(quote):
            return {"ok": False, "status": "confirmation_not_grounded"}
        if self._negative_signal(self._user_text):
            return {"ok": False, "status": "confirmation_ambiguous", "draft": self._draft_facts(pending)}
        # An available owner transcript is the complete confirmation evidence.
        # A model-selected "yes" cannot replace an unrelated actual question.
        # Audio may still use its quote when auxiliary STT is unavailable.
        evidence = self._user_text.strip() or quote
        if not self._confirmation_signal(evidence) or self._recipient_conflict(evidence, pending):
            return {"ok": False, "status": "confirmation_ambiguous", "draft": self._draft_facts(pending)}
        if self._user_text and self._recipient_conflict(self._user_text, pending):
            return {"ok": False, "status": "recipient_conflict", "draft": self._draft_facts(pending)}
        result = self.brain.execute_pending_external_action()
        if result.get("ok") and result.get("status") == "sent":
            return {"ok": True, "status": "sent", "smtp_confirmed": True, "draft_id": draft_id}
        status = str(result.get("status", "send_failed"))
        reason = "gmail_not_connected" if status == "email_not_configured" else str(result.get("message", ""))[:240]
        reason = re.sub(r"(?i)(password|token|secret|api[_-]?key)\s*[:=]\s*\S+", r"\1=[redacted]", reason)
        return {
            "ok": False, "status": status,
            "reason": reason,
            "smtp_confirmed": False, "draft_id": draft_id,
            "delivery_uncertain": status == "delivery_uncertain",
        }

