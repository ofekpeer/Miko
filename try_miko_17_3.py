"""Try Miko 17.3 (the new character) with the existing, installed Brain.

Nothing is installed or overwritten. The Brain, memory, Gmail connection and
history stay exactly where they are (the runtime folder from miko_launch.json).
Only the window changes: the 17.2 robot window is closed and the 17.3 window
from this folder is opened in its place. To go back, close this window and run
"Start Miko.cmd" on the Desktop as usual.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import start_miko  # reuses the installed launcher's health checks and Godot lookup

HERE = Path(__file__).resolve().parent
NEW_PROJECT = HERE / "miko-3d"


def ensure_brain() -> bool:
    current = start_miko.health()
    if current and start_miko.is_miko_17(current):
        print("Brain is running:", current.get("version"))
        return True
    if current:
        print("Port 5000 belongs to a different service. Stop it and try again.")
        return False
    runtime = start_miko.root
    log_dir = runtime / "miko_logs"
    log_dir.mkdir(exist_ok=True)
    log = (log_dir / ("brain_" + time.strftime("%Y%m%d_%H%M%S") + ".log")).open("a", encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    subprocess.Popen([sys.executable, "-X", "utf8", str(runtime / "miko_brain.py")], cwd=runtime, env=env,
                     stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    for _ in range(60):
        time.sleep(0.25)
        current = start_miko.health()
        if current:
            break
    if not current or not start_miko.is_miko_17(current):
        print("The Brain did not start. See the newest log in:", log_dir)
        return False
    print("Brain started:", current.get("version"))
    return True


def main() -> int:
    print("Miko 17.3 preview - new character, same Brain and memory")
    if not ensure_brain():
        input("Press Enter to close...")
        return 1
    old_pid = start_miko.running_game_pid(start_miko.root / "miko-3d")
    if old_pid:
        print("Closing the 17.2 robot window (PID", old_pid, ") so only one window listens.")
        subprocess.run(["taskkill", "/PID", str(old_pid)], capture_output=True)
        time.sleep(1.0)
    current_pid = start_miko.running_game_pid(NEW_PROJECT)
    if current_pid:
        # Reopen so the newest character files are loaded.
        print("Reopening the 17.3 window (PID", current_pid, ").")
        subprocess.run(["taskkill", "/PID", str(current_pid)], capture_output=True)
        time.sleep(1.0)
    exe = start_miko.godot_executable()
    if not exe:
        print("Godot was not found. Open miko-3d/project.godot in Godot and press F5.")
        input("Press Enter to close...")
        return 1
    log_dir = start_miko.root / "miko_logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / ("godot_17_3_" + time.strftime("%Y%m%d_%H%M%S") + ".log")
    subprocess.Popen([exe, "--path", str(NEW_PROJECT), "--log-file", str(log_file)], cwd=NEW_PROJECT,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Ready. In the Miko window: hold SPACE to talk, release for a reply.")
    return 0


if __name__ == "__main__":
    with start_miko.launch_lock():
        raise SystemExit(main())
