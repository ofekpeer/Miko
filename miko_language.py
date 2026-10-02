"""Spoken-Hebrew quality checks for what Miko says.

Used three ways: the host logs a LANGUAGE warning when a spoken line breaks a
rule (so bad phrasing is visible in the logs, not only by ear), the
spontaneous-speech directive gets the recent openers to avoid, and the test
suite checks sample lines. These are heuristics for known failure modes, not
a grammar checker:

* invented or broken forms seen in practice ("איזה נופף חמוד": a verb root
  turned into a noun by echoing the perception note);
* canned assistant phrases ("איך אוכל לעזור", "אני כאן בשבילך");
* narrating sensors or internals ("זיהיתי ש...", "המצלמה", "הערת מערכת");
* repetition: the same line, or the same opening words, again soon.
"""

from __future__ import annotations

from collections import deque
import re
import unicodedata

from miko_log import log

# Forms that are not natural Hebrew in this context. Keep entries specific.
INVENTED = [
    (r"(?:איזה|איזו|איזשהו|ה|של)\s*נופף\b", "'נופף' is a verb form, not a noun ('נפנוף', 'נפנפת')"),
    (r"\bנופף\s+(?:חמוד|יפה|מתוק)", "'נופף' used as a noun"),
    (r"\bנפנופון\b|\bנופפון\b", "invented diminutive"),
    (r"\bטלטלון\b|\bרעדון\b", "invented diminutive"),
    (r"\bמכוסה עיניים\b", "awkward literal phrasing"),
]
CANNED = [
    "איך אוכל לעזור", "במה אוכל לעזור", "אני כאן בשבילך", "אני כאן ומוכן", "אני זמין",
    "רגוע וזמין", "תודה ששאלת", "שמח לדבר איתך", "אשמח לעזור", "יש עוד משהו שאוכל",
    "כמובן! אשמח", "כמודל שפה", "אני רק בינה מלאכותית",
]
NARRATION = [
    (r"(?:זיהיתי|אני מזהה|המערכת זיהתה|המצלמה זיהתה|קלטתי במצלמה)", "narrates detection"),
    (r"(?:ראיתי|אני רואה)\s+ש(?:אתה|את)?\s*(?:מנופף|נופפת|מנפנף|מנפנפת|זז|זזה)", "describes the event back"),
    (r"(?:הערת מערכת|הודעת מערכת|\bחיישן\b|\bחיישנים\b|\[ראייה\]|perception)", "mentions internals"),
]
_HEBREW = re.compile(r"[֐-׿]")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"[֑-ׇ]", "", text)          # niqqud / cantillation
    text = re.sub(r"[^\w\s֐-׿]", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def opener(text: str, words: int = 2) -> str:
    return " ".join(normalize(text).split()[:words])


def check(text: str, recent: list[str] | None = None) -> list[str]:
    """Problems with one spoken line (empty list = fine)."""
    problems: list[str] = []
    raw = str(text or "")
    plain = normalize(raw)
    if not plain:
        return problems
    for pattern, why in INVENTED:
        if re.search(pattern, raw):
            problems.append("invented_word: " + why)
    for phrase in CANNED:
        if normalize(phrase) in plain:
            problems.append("canned_phrase: " + phrase)
    for pattern, why in NARRATION:
        if re.search(pattern, raw, re.IGNORECASE):
            problems.append("narration: " + why)
    if recent:
        previous = [normalize(r) for r in recent if normalize(r)]
        if plain in previous:
            problems.append("repeated_line")
        elif _HEBREW.search(raw) and len(plain.split()) >= 3 and opener(raw) in {opener(r) for r in previous[-4:]}:
            problems.append("repeated_opener: " + opener(raw))
    return problems


class SpeechMonitor:
    """Watches Miko's spoken lines: logs problems, remembers openers."""

    def __init__(self, size: int = 12) -> None:
        self.recent: deque = deque(maxlen=size)

    def observe(self, text: str, origin: str = "") -> list[str]:
        problems = check(text, list(self.recent))
        if problems:
            log("LANGUAGE", "spoken line flagged", origin=origin or "turn", problems="|".join(problems),
                words=len(normalize(text).split()))
        if normalize(text):
            self.recent.append(str(text))
        return problems

    def avoid_openers(self, count: int = 5) -> list[str]:
        seen: list[str] = []
        for line in reversed(self.recent):
            head = opener(line)
            if head and head not in seen:
                seen.append(head)
            if len(seen) >= count:
                break
        return seen
