"""Local, explicit pairing and LAN-listener settings for one owner device.

No device server is started by this command. Pairing and LAN enablement are
separate actions; the settings default to disabled.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from .bridge import BridgeSettings, DeviceConfigError, create_pairing_secret
from .protocol import DEVICE_ID_RE


def _read(path: Path) -> dict:
    if not path.exists():
        return {
            "version": 1,
            "enabled": False,
            "bind_host": "127.0.0.1",
            "port": 5443,
            "tls_cert": "",
            "tls_key": "",
            "paired_devices": [],
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise DeviceConfigError("unsupported device settings")
    return data


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".miko_device_", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pair one local Miko device")
    parser.add_argument("--settings", required=True, type=Path, help="private server settings JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    pair = commands.add_parser("pair", help="create a 256-bit device pairing secret; listener stays disabled")
    pair.add_argument("--device-id", required=True)
    pair.add_argument("--secret-file", required=True, type=Path, help="private file to provision into device NVS")
    pair.add_argument("--replace", action="store_true", help="revoke an existing pairing for this owner")
    pair.add_argument("--camera", action="store_true", help="allow explicit, locally confirmed camera snapshots")

    enable = commands.add_parser("enable", help="enable TLS on one private LAN IP")
    enable.add_argument("--bind-host", required=True, help="numeric private LAN IP, never 0.0.0.0")
    enable.add_argument("--port", type=int, default=5443)
    enable.add_argument("--tls-cert", required=True, type=Path)
    enable.add_argument("--tls-key", required=True, type=Path)
    commands.add_parser("disable", help="disable the device listener")

    args = parser.parse_args()
    settings = args.settings.expanduser().resolve()
    data = _read(settings)
    if args.command == "pair":
        if not DEVICE_ID_RE.fullmatch(args.device_id):
            parser.error("device id must contain 1-32 letters, digits, '_' or '-'")
        if data.get("paired_devices") and not args.replace:
            parser.error("one owner device is already paired; use --replace to revoke it")
        secret_file = args.secret_file.expanduser().resolve()
        if secret_file == settings:
            parser.error("secret-file and settings must be different paths")
        if secret_file.exists():
            parser.error("secret-file already exists; choose a new private path")
        secret = create_pairing_secret()
        capabilities = ["audio", "display", "gesture"] + (["camera"] if args.camera else [])
        data["paired_devices"] = [{
            "device_id": args.device_id,
            "pairing_secret_b64": secret,
            "capabilities": capabilities,
        }]
        data["enabled"] = False
        _atomic_write(secret_file, secret + "\n")
        _atomic_write(settings, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print("Paired identity saved. The LAN listener remains disabled.")
        print("Provision the private secret file into device NVS; never put it in firmware source.")
    elif args.command == "enable":
        data.update({
            "enabled": True,
            "bind_host": args.bind_host,
            "port": args.port,
            "tls_cert": str(args.tls_cert.expanduser().resolve()),
            "tls_key": str(args.tls_key.expanduser().resolve()),
        })
        settings.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=settings.parent, prefix=".miko_validate_", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            _atomic_write(temporary, json.dumps(data))
            BridgeSettings.load(temporary)
        finally:
            if temporary.exists():
                temporary.unlink()
        _atomic_write(settings, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print("TLS device listener enabled on the selected private interface after server restart.")
    else:
        data["enabled"] = False
        _atomic_write(settings, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print("Device listener disabled after server restart.")


if __name__ == "__main__":
    main()
