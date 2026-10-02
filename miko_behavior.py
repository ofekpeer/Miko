"""How should Miko respond to something it perceived?

Every perception event gets one response level:

    NO_REACTION < MICRO < FACIAL < ANIMATION_ONLY < SHORT_VOCAL < FULL_SPOKEN

decided from the event (kind, confidence, intensity), the conversation
context (who is talking), recency (same thing just happened), novelty and a
social budget, so Miko reacts like a person: most things get a glance or a
small body reaction, few get words, and a conversation is never hijacked by
a minor sensor event.

Context priority (highest first):
    USER_BARGE_IN > USER_SPEECH > CRITICAL > HIGH_CONFIDENCE_GESTURE >
    STRONG_PHYSICAL > CONVERSATION_GESTURE > AUTONOMOUS > IDLE

The level is advisory for the body (Godot's BehaviorArbiter applies its own
cooldowns and may go lower, never higher) and decisive for speech: the host
speaks only at SHORT_VOCAL or above. The model is told what happened as a
neutral fact and chooses the words; it never decides whether something real
happened or succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import random
import time
from typing import Any

from miko_log import log

LEVELS = ["NO_REACTION", "MICRO", "FACIAL", "ANIMATION_ONLY", "SHORT_VOCAL", "FULL_SPOKEN"]
NO_REACTION, MICRO, FACIAL, ANIMATION_ONLY, SHORT_VOCAL, FULL_SPOKEN = range(6)

PRIORITY = {"USER_BARGE_IN": 7, "USER_SPEECH": 6, "CRITICAL": 5, "HIGH_CONFIDENCE_GESTURE": 4,
            "STRONG_PHYSICAL": 3, "CONVERSATION_GESTURE": 2, "AUTONOMOUS": 1, "IDLE": 0}


@dataclass(frozen=True)
class Profile:
    level: int                    # best case, idle and novel
    priority: str
    vocal_cooldown: float = 60.0  # seconds between spoken reactions to this kind
    vocal_chance: float = 0.5     # words are optional even when allowed
    min_confidence: float = 0.0
    family: str = ""


# Event kinds as the host sees them ("gesture:<name>" for hand signs).
PROFILES: dict[str, Profile] = {
    "wave": Profile(SHORT_VOCAL, "HIGH_CONFIDENCE_GESTURE", 60, 0.6, 0.8, "greeting"),
    "arrived": Profile(SHORT_VOCAL, "CONVERSATION_GESTURE", 600, 0.6, 0.0, "greeting"),
    "left": Profile(MICRO, "AUTONOMOUS", family="presence"),
    "approached": Profile(FACIAL, "AUTONOMOUS", family="presence"),
    "covered": Profile(SHORT_VOCAL, "STRONG_PHYSICAL", 45, 0.55, 0.0, "covered"),
    "uncovered": Profile(SHORT_VOCAL, "STRONG_PHYSICAL", 90, 0.3, 0.0, "covered"),
    "shake_started": Profile(ANIMATION_ONLY, "STRONG_PHYSICAL", family="physical"),
    "shake_active": Profile(MICRO, "STRONG_PHYSICAL", family="physical"),
    "shake_ended": Profile(SHORT_VOCAL, "STRONG_PHYSICAL", 60, 0.75, 0.0, "physical"),
    "shaken": Profile(SHORT_VOCAL, "STRONG_PHYSICAL", 60, 0.7, 0.0, "physical"),     # legacy name
    "orientation_changed": Profile(SHORT_VOCAL, "STRONG_PHYSICAL", 90, 0.5, 0.0, "physical"),
    "device_moved": Profile(MICRO, "AUTONOMOUS", family="physical_minor"),
    "device_nudged": Profile(MICRO, "IDLE", family="physical_minor"),
    "laughing": Profile(ANIMATION_ONLY, "CONVERSATION_GESTURE", family="emotion"),
    "smiled": Profile(FACIAL, "CONVERSATION_GESTURE", family="emotion"),
    "surprised": Profile(FACIAL, "CONVERSATION_GESTURE", family="emotion"),
    "frowned": Profile(FACIAL, "CONVERSATION_GESTURE", family="emotion"),
    "yawned": Profile(ANIMATION_ONLY, "AUTONOMOUS", family="emotion"),
    "winked": Profile(FACIAL, "CONVERSATION_GESTURE", family="emotion"),
    "eyes_closed": Profile(FACIAL, "AUTONOMOUS", family="attention"),
    "eyes_opened": Profile(MICRO, "AUTONOMOUS", family="attention"),
    "looked_at_miko": Profile(FACIAL, "CONVERSATION_GESTURE", family="attention"),
    "looked_away": Profile(MICRO, "AUTONOMOUS", family="attention"),
    "looked_somewhere": Profile(MICRO, "AUTONOMOUS", family="attention"),
    "nodded": Profile(FACIAL, "CONVERSATION_GESTURE", family="head"),
    "shook_head": Profile(FACIAL, "CONVERSATION_GESTURE", family="head"),
    "tilted_head": Profile(MICRO, "AUTONOMOUS", family="head"),
    "someone_joined": Profile(SHORT_VOCAL, "CONVERSATION_GESTURE", 180, 0.7, 0.0, "people"),
    "someone_left": Profile(MICRO, "AUTONOMOUS", family="people"),
    "light_changed": Profile(MICRO, "AUTONOMOUS", family="room"),
    "scene_changed": Profile(MICRO, "AUTONOMOUS", family="room"),
    "motion": Profile(MICRO, "AUTONOMOUS", family="room"),
    "gesture:thumbs_up": Profile(SHORT_VOCAL, "HIGH_CONFIDENCE_GESTURE", 180, 0.35, 0.0, "hand_sign"),
    "gesture:thumbs_down": Profile(ANIMATION_ONLY, "HIGH_CONFIDENCE_GESTURE", family="hand_sign"),
    "gesture:peace": Profile(ANIMATION_ONLY, "HIGH_CONFIDENCE_GESTURE", family="hand_sign"),
    "gesture:love": Profile(SHORT_VOCAL, "HIGH_CONFIDENCE_GESTURE", 120, 0.7, 0.0, "hand_sign"),
    "gesture:fist": Profile(ANIMATION_ONLY, "HIGH_CONFIDENCE_GESTURE", family="hand_sign"),
    "gesture:open_palm": Profile(ANIMATION_ONLY, "HIGH_CONFIDENCE_GESTURE", family="hand_sign"),
    "gesture:pointing": Profile(MICRO, "CONVERSATION_GESTURE", family="hand_sign"),
}
DEFAULT = Profile(NO_REACTION, "IDLE")

SOCIAL_BUDGET_S = 60.0         # at most one spoken remark this often, any kind
CONVERSATION_WINDOW_S = 20.0   # a turn this recent means "we are talking"
NOVELTY_WINDOW_S = 600.0


def event_kind(event: dict[str, Any]) -> str:
    kind = str(event.get("event", ""))
    return kind + ":" + str(event.get("gesture", "")) if kind == "gesture" else kind


@dataclass
class Context:
    now: float = field(default_factory=time.time)
    user_speaking: bool = False       # push-to-talk held / owner audio in flight
    miko_speaking: bool = False       # a response is being generated or played
    last_turn_age: float = 1e9        # seconds since the last conversation line
    session_open: bool = True


@dataclass
class Decision:
    kind: str
    level: int
    reason: str
    priority: str

    @property
    def name(self) -> str:
        return LEVELS[self.level]

    @property
    def vocal(self) -> bool:
        return self.level >= SHORT_VOCAL


class ResponsePolicy:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.history: dict[str, list[float]] = {}     # kind -> event times
        self.last_vocal: dict[str, float] = {}
        self.last_any_vocal = -1e9
        # Test/stress harnesses only: every speakable event asks to speak, so
        # the session layer's collision handling is exercised.
        self.stress_mode = False

    def decide(self, event: dict[str, Any], ctx: Context) -> Decision:
        kind = event_kind(event)
        p = PROFILES.get(kind, DEFAULT)
        now = ctx.now
        seen = [t for t in self.history.get(kind, []) if now - t <= NOVELTY_WINDOW_S]
        self.history[kind] = (seen + [now])[-20:]
        level, reasons = p.level, []
        if self.stress_mode:
            return Decision(kind, level, "stress_mode", p.priority)

        def cap(limit: int, why: str) -> None:
            nonlocal level
            if level > limit:
                level = limit
                reasons.append(why)

        confidence = float(event.get("confidence", 1.0) or 0.0)
        if confidence < p.min_confidence:
            cap(MICRO, f"low_confidence({confidence:.2f})")
        if kind == "arrived" and float(event.get("away", 1e6) or 0.0) < 600.0:
            cap(ANIMATION_ONLY, "short_absence")       # back from a few minutes away: a wave, no words
        if kind == "shake_ended":
            duration = float(event.get("duration", 0.0) or 0.0)
            if duration < 1.2:
                cap(ANIMATION_ONLY, "short_shake")
        # Conversation wins over sensor events.
        rank = PRIORITY[p.priority]
        if ctx.user_speaking:
            cap(MICRO if rank < PRIORITY["CRITICAL"] else ANIMATION_ONLY, "owner_speaking")
        elif ctx.miko_speaking:
            cap(FACIAL if rank < PRIORITY["STRONG_PHYSICAL"] else ANIMATION_ONLY, "miko_speaking")
        elif ctx.last_turn_age < CONVERSATION_WINDOW_S:
            cap(FACIAL if rank < PRIORITY["STRONG_PHYSICAL"] else ANIMATION_ONLY, "mid_conversation")
        # Repetition: the same thing again soon is less remarkable - words
        # only the first time in the novelty window, then less and less body.
        if len(seen) == 1:
            cap(ANIMATION_ONLY, "repeated_x2")
        elif len(seen) >= 2:
            cap(max(MICRO, p.level - len(seen)), f"repeated_x{len(seen) + 1}")
        if level >= SHORT_VOCAL:
            if now - self.last_vocal.get(kind, -1e9) < p.vocal_cooldown:
                cap(ANIMATION_ONLY, "vocal_cooldown")
            elif now - self.last_any_vocal < SOCIAL_BUDGET_S:
                cap(ANIMATION_ONLY, "social_budget")
            elif self.rng.random() > p.vocal_chance:
                cap(ANIMATION_ONLY, "body_only_this_time")
        if level >= SHORT_VOCAL:
            self.last_vocal[kind] = now
            self.last_any_vocal = now
        reason = ",".join(reasons) if reasons else ("novel" if not seen else "allowed")
        decision = Decision(kind, level, reason, p.priority)
        log("BEHAVIOR", "perception response", event=kind, level=decision.name, reason=reason,
            confidence=round(confidence, 2), priority=p.priority)
        return decision

    def spoke_anyway(self, kind: str, now: float | None = None) -> None:
        """Record speech that happened outside decide() (keeps the budget honest)."""
        now = time.time() if now is None else now
        self.last_vocal[kind] = now
        self.last_any_vocal = now

    def refund(self, decision: Decision) -> None:
        """A vocal reaction could not be delivered (session busy): do not let
        it consume the cooldown."""
        if decision.vocal:
            self.last_vocal.pop(decision.kind, None)
            self.last_any_vocal = -1e9
