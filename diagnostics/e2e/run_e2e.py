"""End-to-end stuck test: real Brain + Realtime host + real Godot window.

  python diagnostics/e2e/run_e2e.py --turns 20 [--chaos] [--seed 3] [--godot godot]

A throwaway sandbox copy of the Brain runs against a fake Realtime server
(no network, no API key, no personal data). The real Godot project runs
headless and holds push-to-talk turns with synthetic microphone audio,
sometimes barging in. With --chaos, camera events (waves, covering, shakes)
arrive at random moments, so Miko's spontaneous remarks race with turns.
Every owner turn must get an answer and the window must come back to idle.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def wait_health(seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:5000/health", timeout=1) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.3)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--chaos", action="store_true")
    parser.add_argument("--godot", default=os.environ.get("GODOT", "godot"))
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    sandbox = Path(tempfile.mkdtemp(prefix="miko_e2e_"))
    for name in ROOT.glob("*.py"):
        shutil.copy2(name, sandbox / name.name)
    for name in ("miko_voice.html",):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, sandbox / name)
    shutil.copytree(ROOT / "device", sandbox / "device", ignore=shutil.ignore_patterns("__pycache__"))
    (sandbox / "miko_brain_state.json").write_text(json.dumps({"last_seen": time.time()}), encoding="utf-8")
    stats_path = sandbox / "e2e_stats.json"
    env = dict(os.environ, HOME=str(sandbox), USERPROFILE=str(sandbox), OPENAI_API_KEY="offline-e2e",
               OPENAI_BASE_URL="http://127.0.0.1:9/v1", MIKO_VISION="0", PYTHONUNBUFFERED="1", PYTHONUTF8="1")
    host_log = open(sandbox / "host.log", "w", encoding="utf-8")
    host = subprocess.Popen([sys.executable, "-X", "utf8", str(HERE / "e2e_host.py"), "--sandbox", str(sandbox),
                             "--stats", str(stats_path), "--seed", str(args.seed)] + (["--chaos"] if args.chaos else []),
                            cwd=sandbox, env=env, stdout=host_log, stderr=subprocess.STDOUT)
    code = 1
    try:
        if not wait_health():
            print("host did not start; log:", (sandbox / "host.log").read_text(encoding="utf-8")[-3000:])
            return 1
        godot = subprocess.Popen([args.godot, "--headless", "--path", str(ROOT / "miko-3d"), "res://tests/e2e_driver.tscn",
                                  "--", f"--turns={args.turns}", f"--seed={args.seed}"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        summary = ""
        for line in godot.stdout:
            line = line.rstrip()
            if line.startswith("E2E") or "SCRIPT ERROR" in line or line.startswith("ERROR"):
                print(line, flush=True)
            if line.startswith("E2E_SUMMARY"):
                summary = line
        godot.wait(timeout=60)
        code = godot.returncode
        try:
            print("host stats:", stats_path.read_text(encoding="utf-8"))
        except OSError:
            pass
        if code != 0:
            print("--- host log tail ---")
            print((sandbox / "host.log").read_text(encoding="utf-8", errors="replace")[-4000:])
        print("RESULT", "PASS" if code == 0 and summary else "FAIL", summary)
        return 0 if code == 0 and summary else 1
    finally:
        host.terminate()
        try:
            host.wait(timeout=10)
        except subprocess.TimeoutExpired:
            host.kill()
        host_log.close()
        if not args.keep:
            shutil.rmtree(sandbox, ignore_errors=True)
        else:
            print("sandbox kept:", sandbox)


if __name__ == "__main__":
    raise SystemExit(main())
