"""Pack Miko's latest logs into one ZIP on the Desktop, ready to send.

Only log files are included (newest brain and window logs). Memory, history,
credentials and settings are never added.
"""
from pathlib import Path
import time
import zipfile

HERE = Path(__file__).resolve().parent


def main() -> int:
    logs = HERE / "miko_logs"
    if not logs.is_dir():
        print("No miko_logs folder next to Miko yet. Start Miko once, then try again.")
        return 1
    picked = []
    for prefix in ("brain_", "godot_"):
        files = sorted(logs.glob(prefix + "*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        picked += files[:3]
    if not picked:
        print("No log files found in", logs)
        return 1
    target = HERE / ("Miko_logs_" + time.strftime("%Y%m%d_%H%M%S") + ".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in picked:
            archive.write(file, file.name)
    print("Created:", target)
    print("Send this one file. It contains only logs (no memory, history or passwords).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
