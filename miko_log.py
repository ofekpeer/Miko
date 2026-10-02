"""Structured, greppable log lines for Miko's host.

    log("GESTURE", "wave", confidence=0.91, duration=1.2, decision="CONFIRMED_WAVE")
    -> 12:03:41.207 GESTURE: wave confidence=0.91 duration=1.2 decision=CONFIRMED_WAVE

Categories: PERCEPTION, GESTURE, PHYSICAL, BEHAVIOR, BRAIN, ACTION, EMAIL,
STT, TTS, RECOVERY, LANGUAGE. Never pass credentials, message bodies of
emails or raw images here. Per-frame detail is only printed when
MIKO_VISION_DEBUG=1, so normal logs stay readable.
"""

from __future__ import annotations

import os
import sys
import threading
import time

DEBUG = os.environ.get("MIKO_VISION_DEBUG", "").strip() == "1"
# The vision worker process uses stdout for its protocol; its logs go to stderr.
_STREAM = sys.stderr if os.environ.get("MIKO_LOG_STDERR", "").strip() == "1" else None
_lock = threading.Lock()
_last: dict[str, float] = {}


def _format(value) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    text = str(value)
    return text if " " not in text else repr(text)


def log(category: str, message: str, **fields) -> None:
    stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
    extra = " ".join(f"{k}={_format(v)}" for k, v in fields.items())
    line = f"{stamp} {category}: {message}" + (f" {extra}" if extra else "")
    with _lock:
        try:
            print(line, flush=True, file=_STREAM or sys.stdout)
        except Exception:
            try:
                stream = _STREAM or sys.stdout
                stream.buffer.write((line + "\n").encode("utf-8", "replace"))
                stream.flush()
            except Exception:
                pass


def debug(category: str, message: str, **fields) -> None:
    if DEBUG:
        log(category, message, **fields)


def throttled(key: str, seconds: float, category: str, message: str, **fields) -> None:
    """At most one line per `key` every `seconds` (for repetitive suppressions)."""
    now = time.monotonic()
    if now - _last.get(key, -1e9) >= seconds:
        _last[key] = now
        log(category, message, **fields)
