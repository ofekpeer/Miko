"""Run the real Miko Brain + Realtime host against the fake Realtime server.

Started by run_e2e.py inside a throwaway sandbox copy. Optionally injects
perception events (waves, covering, shakes...) at random moments, the way a
live camera does, so Miko's spontaneous remarks race with owner turns.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import runpy
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--fake-port", type=int, default=5099)
    parser.add_argument("--chaos", action="store_true", help="inject perception events")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--stats", required=True)
    args = parser.parse_args()

    sys.path.insert(0, args.sandbox)
    sys.path.insert(0, HERE)
    os.chdir(args.sandbox)
    import fake_realtime
    from websockets.asyncio.client import connect as ws_connect

    stats = fake_realtime.new_stats()
    ready = threading.Event()

    def fake_thread():
        async def run():
            await fake_realtime.serve_fake(args.fake_port, stats, args.seed)
            ready.set()
            await asyncio.Future()
        asyncio.run(run())

    threading.Thread(target=fake_thread, daemon=True).start()
    ready.wait(10)

    import miko_realtime

    async def fake_connect(url, **kwargs):
        return await ws_connect(f"ws://127.0.0.1:{args.fake_port}/v1/realtime",
                                max_size=kwargs.get("max_size", 8 * 1024 * 1024),
                                open_timeout=kwargs.get("open_timeout", 10))

    miko_realtime.connect = fake_connect
    holder = {}
    original_register = miko_realtime.register_realtime

    def register(brain):
        hub = original_register(brain)
        holder["hub"] = hub
        return hub

    miko_realtime.register_realtime = register

    def chaos():
        rng = random.Random(args.seed * 7 + 3)
        while "hub" not in holder or holder["hub"].loop is None:
            time.sleep(0.2)
        hub = holder["hub"]
        hub.perception_policy.stress_mode = True       # every event asks to speak: stress collisions
        events = ["wave", "covered", "uncovered", "shake_started", "shake_ended", "device_moved", "arrived", "motion", "scene_changed", "looked_at_miko",
                  "smiled", "laughing", "yawned", "winked", "frowned", "nodded", "someone_joined", "gesture"]
        while True:
            time.sleep(rng.uniform(0.4, 3.0))
            event = {"type": "vision_event", "event": rng.choice(events), "x": round(rng.uniform(-1, 1), 2),
                     "gesture": rng.choice(["thumbs_up", "peace", "love", "fist", "open_palm"])}
            hub.broadcast(event)
            hub.broadcast({"type": "vision", "seen": True, "x": rng.uniform(-0.5, 0.5), "y": 0.0, "size": 0.2})
            hub.vision_spoken_at = 0.0
            hub.vision_reaction(event)
            stats["vision_events"] = stats.get("vision_events", 0) + 1

    if args.chaos:
        threading.Thread(target=chaos, daemon=True).start()

    def dump():
        while True:
            time.sleep(1.0)
            hub = holder.get("hub")
            snapshot = dict(stats)
            if hub:
                snapshot["sessions"] = [{
                    "api": bool(n.api), "responding": n.responding, "active": n.active_response_id,
                    "pending": len(n.pending_responses), "deferred": len(n.deferred_requests),
                    "ptt": n.ptt_active, "input": n.input_bytes, "generation": n.generation,
                } for n in list(hub.native)]
            with open(args.stats, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False)

    threading.Thread(target=dump, daemon=True).start()
    runpy.run_path(os.path.join(args.sandbox, "miko_brain.py"), run_name="__main__")


if __name__ == "__main__":
    main()
