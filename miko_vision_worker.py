"""Miko's sight in its own process.

Camera capture, OpenCV and MediaPipe are CPU heavy. In the voice host's
process they compete with the real-time audio relay for the Python
interpreter, which can make the conversation stutter or stall. This worker
runs miko_vision.VisionService on its own and talks to the host over stdin /
stdout with one JSON object per line (logs go to stderr):

  host -> worker   {"c":"enable","v":true} | {"c":"jpeg","b":"<base64>"}
                   {"c":"imu","unstable":0.3,"active":2.0} | {"c":"stop"}
  worker -> host   {"t":"emit","m":{...}}     Godot-facing state/status
                   {"t":"event","m":{...}}    perception event (host decides the level)
                   {"t":"summary","m":{...}}  facts for miko_get_vision, ~1/s
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time

PROTOCOL = sys.stdout
sys.stdout = sys.stderr                       # stray prints never corrupt the protocol
os.environ.setdefault("MIKO_LOG_STDERR", "1")

import miko_vision  # noqa: E402

_lock = threading.Lock()


def send(kind: str, message) -> None:
    line = json.dumps({"t": kind, "m": message}, ensure_ascii=False)
    with _lock:
        try:
            PROTOCOL.write(line + "\n")
            PROTOCOL.flush()
        except (OSError, ValueError):
            os._exit(0)                       # the host went away


def main() -> None:
    service = miko_vision.VisionService(
        emit=lambda m: None if m.get("type") == "vision_event" else send("emit", m),
        react=lambda m: send("event", m))
    service.start()
    stop = threading.Event()

    def summaries() -> None:
        while not stop.wait(1.0):
            try:
                send("summary", service.summary())
            except Exception:
                pass
    threading.Thread(target=summaries, daemon=True).start()

    for raw in sys.stdin:
        try:
            command = json.loads(raw)
        except ValueError:
            continue
        kind = command.get("c")
        try:
            if kind == "enable":
                service.set_enabled(bool(command.get("v")))
            elif kind == "jpeg":
                service.feed_jpeg(base64.b64decode(command.get("b", "")))
            elif kind == "imu":
                engine = service.engine
                if engine is not None:
                    now = time.monotonic()
                    engine.imu_active_until = now + float(command.get("active", 2.0))
                    if command.get("unstable"):
                        engine.imu_unstable_until = now + float(command.get("unstable"))
            elif kind == "stop":
                break
        except Exception as error:            # one bad command never ends sight
            print("MIKO VISION WORKER: command failed:", kind, type(error).__name__, file=sys.stderr)
    stop.set()
    service.stop()


if __name__ == "__main__":
    main()
