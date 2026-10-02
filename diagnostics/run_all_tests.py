"""Run every Miko check in one go.

  python diagnostics/run_all_tests.py [--godot godot] [--e2e]

1. Every GDScript must load (a parse error would otherwise hang a test).
2. Python suites (brain tools, Realtime host, long session, vision,
   perception, physical/gesture events, language, device bridge).
3. Godot scene tests (voice client, robot commands, behaviour arbiter,
   guardian/recovery, fuzz, soak, transcript).
4. With --e2e: the end-to-end stuck test with random camera events, rapid
   SPACE bursts and injected STT / TTS / lost-request failures.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAG = ROOT / "diagnostics"
GODOT_PROJECT = ROOT / "miko-3d"

PY_SUITES = [
    ["test_miko_realtime_tools.py", "--brain", "../miko_brain.py"],
    ["test_miko_realtime_host.py", "--brain", "../miko_brain.py"],
    ["test_miko_confirm_flow.py", "--brain", "../miko_brain.py"],
    ["test_miko_long_session.py", "--brain", "../miko_brain.py"],
    ["test_miko_device_host.py"],
    ["test_miko_vision.py"],
    ["test_miko_perception.py"],
    ["test_miko_events.py"],
    ["test_miko_language.py"],
]
GODOT_TESTS = {
    "tests/test_robot_commands.gd": "ROBOT_COMMANDS_OK",
    "tests/test_robot_presence.gd": "ROBOT_PRESENCE_OK",
    "tests/test_robot_soak.gd": "ROBOT_SOAK_OK",
    "tests/test_behavior_arbiter.gd": "BEHAVIOR_ARBITER_OK",
    "tests/test_robot_fuzz.gd": "ROBOT_FUZZ_OK",
    "tests/test_guardian.gd": "GUARDIAN_OK",
    "tests/test_spontaneous_transcript.gd": "SPONTANEOUS_TRANSCRIPT_AND_STALL_OK",
    "test_native_transcript_order.gd": "NATIVE_TRANSCRIPT_ORIGIN_ORDER_OK",
    "tests/test_native_caption_ack.gd": "NATIVE_VISIBLE_CAPTION_ACK_OK",
    "tests/test_voice_tail.gd": "VOICE_PTT_TAIL_AND_MUTE_OK",
    "tests/test_voice_transcript_poll.gd": "VOICE_TRANSCRIPT_DRAINS_WITHOUT_NEXT_PRESS_OK",
    "tests/test_voice_backpressure.gd": "VOICE_BACKPRESSURE_PRESERVES_START_AUDIO_STOP_OK",
    "test_voice_session.gd": "PTT_REUSES_NATIVE_SESSION_OK",
    "test_space_focus.gd": "SPACE_BUTTON_FOCUS_REGRESSION_OK",
}


def run(cmd, cwd, timeout):
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        return done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as error:
        return 124, (error.stdout or "") if isinstance(error.stdout, str) else "TIMEOUT"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default=os.environ.get("GODOT", "godot"))
    parser.add_argument("--e2e", action="store_true")
    args = parser.parse_args()
    failures = []

    print("== GDScript load check")
    for script in sorted(GODOT_PROJECT.rglob("*.gd")):
        if ".godot" in script.parts:
            continue
        res = "res://" + script.relative_to(GODOT_PROJECT).as_posix()
        code, out = run([args.godot, "--headless", "--path", str(GODOT_PROJECT), "--check-only", "--script", res], ROOT, 120)
        if "Parse Error" in out or "Failed to load script" in out:
            failures.append(res)
            print("  FAIL", res, out.strip().splitlines()[-1] if out.strip() else "")
    print("  scripts checked")

    print("== Python suites")
    for suite in PY_SUITES:
        code, out = run([sys.executable, *suite], DIAG, 900)
        ok = code == 0
        print(f"  {'ok  ' if ok else 'FAIL'} {suite[0]}")
        if not ok:
            failures.append(suite[0])
            print(out[-2500:])
    code, out = run([sys.executable, "-m", "unittest", "device.test_bridge"], ROOT, 300)
    print(f"  {'ok  ' if code == 0 else 'FAIL'} device.test_bridge")
    if code:
        failures.append("device.test_bridge")
        print(out[-2500:])

    print("== Godot tests")
    for script, marker in GODOT_TESTS.items():
        code, out = run([args.godot, "--headless", "--fixed-fps", "30", "--path", str(GODOT_PROJECT),
                         "--script", "res://" + script], ROOT, 900)
        ok = marker in out
        print(f"  {'ok  ' if ok else 'FAIL'} {script}")
        if not ok:
            failures.append(script)
            print("\n".join(line for line in out.splitlines() if "FAIL" in line or "ERROR" in line)[-2000:])

    if args.e2e:
        for label, extra in (("random camera events", ["--chaos"]),
                             ("rapid SPACE + injected STT/TTS/brain failures", ["--chaos", "--rapid", "--faults", "stt,tts,brain"])):
            print("== End-to-end (" + label + ")")
            code, out = run([sys.executable, str(DIAG / "e2e" / "run_e2e.py"), "--turns", "25", "--seed", "9", *extra], ROOT, 1800)
            summary = [line for line in out.splitlines() if line.startswith(("RESULT", "E2E_STUCK"))]
            print("  " + "\n  ".join(summary))
            if code:
                failures.append("e2e " + label)

    print("\nALL PASSED" if not failures else f"\nFAILED: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
