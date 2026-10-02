"""Local TLS/WSS integration checks; no OpenAI account or hardware required."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from device.bridge import BridgeSettings, DeviceBridge, DeviceConfigError, DeviceTransport
from device.protocol import (
    decode_downlink_pcm,
    encode_camera_jpeg,
    encode_uplink_pcm,
    new_nonce,
    pairing_proof,
)
from device.simulator import run as run_simulator


def _openssl() -> str | None:
    return shutil.which("openssl") or next(
        (str(path) for path in (
            Path("C:/Program Files/Git/mingw64/bin/openssl.exe"),
            Path("C:/Program Files/Git/usr/bin/openssl.exe"),
        ) if path.is_file()),
        None,
    )


class _FakeHub:
    def __init__(self) -> None:
        self.native: set = set()
        self.browser_id = None


class _FakeNative:
    instances: list["_FakeNative"] = []
    pause_suspend: asyncio.Event | None = None
    suspend_entered: asyncio.Event | None = None

    def __init__(self, hub, ws) -> None:
        self.hub = hub
        self.ws = ws
        self.api = True
        self.state = object()
        self.events: list[dict] = []
        self.suspends = 0
        self.__class__.instances.append(self)

    async def send(self, event: dict) -> None:
        await self.ws.send(json.dumps(event))

    async def client_event(self, event: dict) -> None:
        self.events.append(event)
        if event.get("type") == "configure":
            await self.send({"type": "ready", "stage": "model"})
        if event.get("type") == "text":
            pcm = b"\x00\x00" * 480
            await self.send({
                "type": "audio", "item_id": "item_test", "content_index": 0,
                "audio": base64.b64encode(pcm).decode("ascii"),
            })
            await self.send({"type": "audio_done", "item_id": "item_test"})
            await self.send({"type": "response_done", "status": "completed", "has_tool_calls": False})

    async def suspend(self) -> None:
        self.suspends += 1
        if self.__class__.pause_suspend is not None:
            if self.__class__.suspend_entered is not None:
                self.__class__.suspend_entered.set()
            await self.__class__.pause_suspend.wait()
        self.api = None


class DeviceBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.openssl = _openssl()
        if not cls.openssl:
            raise unittest.SkipTest("OpenSSL is needed only to generate a temporary test certificate")

    def test_real_tls_pairing_resume_privacy_camera_and_simulator(self) -> None:
        asyncio.run(self._exercise())

    def test_interrupted_queued_audio_keeps_output_sequence_contiguous(self) -> None:
        async def exercise() -> None:
            class Socket:
                def __init__(self) -> None:
                    self.sent: list[str | bytes] = []

                async def send(self, payload: str | bytes) -> None:
                    self.sent.append(payload)

                async def close(self, **kwargs) -> None:
                    pass

            socket = Socket()
            transport = DeviceTransport(socket)
            audio = base64.b64encode(b"\0\0" * 480).decode("ascii")
            await transport.send(json.dumps({"type": "audio", "item_id": "discarded", "audio": audio}))
            await transport.send(json.dumps({"type": "interrupted"}))
            await transport.send(json.dumps({"type": "audio", "item_id": "played", "audio": audio}))
            transport.start()
            for _ in range(20):
                if len(socket.sent) >= 2:
                    break
                await asyncio.sleep(0.01)
            pcm_frames = [frame for frame in socket.sent if isinstance(frame, bytes)]
            self.assertEqual(len(pcm_frames), 1)
            self.assertEqual(decode_downlink_pcm(pcm_frames[0])[0], 0)
            self.assertEqual(decode_downlink_pcm(pcm_frames[0])[1], "played")
            await transport.close()

        asyncio.run(exercise())

    def test_listener_is_opt_in_and_rejects_public_bind(self) -> None:
        with tempfile.TemporaryDirectory(prefix="miko_device_config_") as directory:
            path = Path(directory) / "settings.json"
            self.assertFalse(BridgeSettings.load(path).enabled)
            path.write_text(json.dumps({
                "version": 1, "enabled": True, "bind_host": "0.0.0.0",
                "port": 5443, "paired_devices": [],
            }), encoding="utf-8")
            with self.assertRaises(DeviceConfigError):
                BridgeSettings.load(path)

    async def _exercise(self) -> None:
        _FakeNative.instances.clear()
        _FakeNative.pause_suspend = None
        _FakeNative.suspend_entered = None
        with tempfile.TemporaryDirectory(prefix="miko_device_test_") as directory:
            root = Path(directory)
            cert = root / "cert.pem"
            key = root / "key.pem"
            subprocess.run([
                self.openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-keyout", str(key), "-out", str(cert), "-subj", "/CN=127.0.0.1",
                "-addext", "subjectAltName=IP:127.0.0.1",
            ], check=True, capture_output=True)
            with socket.socket() as finder:
                finder.bind(("127.0.0.1", 0))
                port = finder.getsockname()[1]
            secret = bytes(range(32))
            secret_file = root / "pairing.txt"
            secret_file.write_text(base64.b64encode(secret).decode("ascii"), encoding="ascii")
            settings = root / "device_settings.json"
            settings.write_text(json.dumps({
                "version": 1, "enabled": True, "bind_host": "127.0.0.1", "port": port,
                "tls_cert": str(cert), "tls_key": str(key),
                "paired_devices": [{
                    "device_id": "miko_s3", "pairing_secret_b64": base64.b64encode(secret).decode("ascii"),
                    "capabilities": ["audio", "display", "gesture", "camera"],
                }],
            }), encoding="utf-8")
            hub = _FakeHub()
            bridge = DeviceBridge(hub, settings, native_factory=_FakeNative)
            self.assertTrue(await bridge.start())
            url = f"wss://127.0.0.1:{port}/device/v1"
            tls = ssl.create_default_context(cafile=str(cert))
            try:
                # An invalid proof cannot access the owner's NativeSession.
                async with connect(url, ssl=tls) as attacker:
                    challenge = json.loads(await attacker.recv())
                    await attacker.send(json.dumps({
                        "type": "authenticate", "device_id": "miko_s3",
                        "client_nonce": new_nonce(), "proof": "0" * 64,
                        "capabilities": ["audio"],
                    }))
                    with self.assertRaises(ConnectionClosed):
                        await attacker.recv()
                self.assertFalse(_FakeNative.instances)

                async with connect(url, ssl=tls) as device:
                    challenge = json.loads(await device.recv())
                    client_nonce = new_nonce()
                    await device.send(json.dumps({
                        "type": "authenticate", "device_id": "miko_s3",
                        "client_nonce": client_nonce,
                        "proof": pairing_proof(secret, "miko_s3", challenge["server_nonce"], client_nonce),
                        "capabilities": ["audio", "display", "gesture", "camera"],
                    }))
                    accepted = json.loads(await device.recv())
                    self.assertFalse(accepted["resumed"])
                    self.assertEqual(_FakeNative.instances[0].device_id, "miko_s3")
                    self.assertEqual(_FakeNative.instances[0].ws.device_id, "miko_s3")
                    await device.recv()  # own idle status
                    await bridge.publish_ui_event({"type": "tool_result", "result": {"private": "secret"}})
                    with self.assertRaises(asyncio.TimeoutError):
                        await asyncio.wait_for(device.recv(), 0.1)
                    await device.send(json.dumps({"type": "configure", "mode": "ptt"}))
                    self.assertEqual(json.loads(await device.recv())["type"], "ready")
                    await device.send(encode_uplink_pcm(0, b"\x00\x00" * 480))
                    for _ in range(20):
                        if _FakeNative.instances[0].events[-1]["type"] == "audio":
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(_FakeNative.instances[0].events[-1]["type"], "audio")
                    with self.assertRaises(PermissionError):
                        await bridge.request_snapshot("miko_s3", requested_by_user=False)
                    snapshot = asyncio.create_task(bridge.request_snapshot("miko_s3", requested_by_user=True))
                    requested = json.loads(await device.recv())
                    self.assertEqual(requested["type"], "camera_request")
                    self.assertTrue(requested["requires_local_confirmation"])
                    jpeg = b"\xff\xd8temporary-test-image\xff\xd9"
                    await device.send(encode_camera_jpeg(requested["request_id"], jpeg))
                    self.assertEqual(await snapshot, jpeg)

                # The same authenticated device resumes one logical session.
                async with connect(url, ssl=tls) as resumed_device:
                    challenge = json.loads(await resumed_device.recv())
                    nonce = new_nonce()
                    await resumed_device.send(json.dumps({
                        "type": "authenticate", "device_id": "miko_s3", "client_nonce": nonce,
                        "proof": pairing_proof(secret, "miko_s3", challenge["server_nonce"], nonce),
                        "capabilities": ["audio"],
                    }))
                    accepted = json.loads(await resumed_device.recv())
                    self.assertTrue(accepted["resumed"])
                    self.assertEqual(len(_FakeNative.instances), 1)
                    previous_transport = _FakeNative.instances[0].ws
                    _FakeNative.pause_suspend = asyncio.Event()
                    _FakeNative.suspend_entered = asyncio.Event()

                    async def takeover() -> dict:
                        async with connect(url, ssl=tls) as replacement:
                            challenge = json.loads(await replacement.recv())
                            nonce = new_nonce()
                            await replacement.send(json.dumps({
                                "type": "authenticate", "device_id": "miko_s3", "client_nonce": nonce,
                                "proof": pairing_proof(secret, "miko_s3", challenge["server_nonce"], nonce),
                                "capabilities": ["audio"],
                            }))
                            response = json.loads(await replacement.recv())
                            self.assertIsNot(_FakeNative.instances[0].ws, previous_transport)
                            return response

                    replacement_task = asyncio.create_task(takeover())
                    await asyncio.wait_for(_FakeNative.suspend_entered.wait(), 2)
                    self.assertFalse(replacement_task.done())
                    self.assertIs(_FakeNative.instances[0].ws, previous_transport)
                    _FakeNative.pause_suspend.set()
                    self.assertTrue((await asyncio.wait_for(replacement_task, 2))["resumed"])
                    _FakeNative.pause_suspend = None
                    _FakeNative.suspend_entered = None

                for _ in range(50):
                    if not bridge.active:
                        break
                    await asyncio.sleep(0.01)
                self.assertFalse(bridge.active)

                # This time the old socket has already disconnected. Its
                # handler is paused inside suspend when a new auth arrives.
                _FakeNative.pause_suspend = asyncio.Event()
                _FakeNative.suspend_entered = asyncio.Event()
                async with connect(url, ssl=tls) as disconnecting_device:
                    challenge = json.loads(await disconnecting_device.recv())
                    nonce = new_nonce()
                    await disconnecting_device.send(json.dumps({
                        "type": "authenticate", "device_id": "miko_s3", "client_nonce": nonce,
                        "proof": pairing_proof(secret, "miko_s3", challenge["server_nonce"], nonce),
                        "capabilities": ["audio"],
                    }))
                    self.assertTrue(json.loads(await disconnecting_device.recv())["resumed"])
                    previous_transport = _FakeNative.instances[0].ws
                await asyncio.wait_for(_FakeNative.suspend_entered.wait(), 2)
                old = bridge.active.get("miko_s3")
                self.assertIsNotNone(old)
                self.assertFalse(old.finalized.is_set())
                auth_sent = asyncio.Event()

                async def reconnect_after_disconnect() -> dict:
                    async with connect(url, ssl=tls) as replacement:
                        challenge = json.loads(await replacement.recv())
                        nonce = new_nonce()
                        await replacement.send(json.dumps({
                            "type": "authenticate", "device_id": "miko_s3", "client_nonce": nonce,
                            "proof": pairing_proof(secret, "miko_s3", challenge["server_nonce"], nonce),
                            "capabilities": ["audio"],
                        }))
                        auth_sent.set()
                        return json.loads(await replacement.recv())

                replacement_task = asyncio.create_task(reconnect_after_disconnect())
                await asyncio.wait_for(auth_sent.wait(), 2)
                await asyncio.sleep(0.05)
                self.assertFalse(replacement_task.done())
                self.assertIs(_FakeNative.instances[0].ws, previous_transport)
                _FakeNative.pause_suspend.set()
                self.assertTrue((await asyncio.wait_for(replacement_task, 2))["resumed"])
                _FakeNative.pause_suspend = None
                _FakeNative.suspend_entered = None

                # The shipped desktop simulator speaks the same WSS protocol.
                output = root / "speaker.wav"
                args = argparse.Namespace(
                    url=url, ca=str(cert), device_id="miko_s3", secret_file=str(secret_file),
                    speaker=False, wav_out=str(output), camera_file=None,
                    text="test", wav_in=None, mic_seconds=None, wait_seconds=2,
                )
                await run_simulator(args)
                with wave.open(str(output), "rb") as recording:
                    self.assertEqual(recording.getframerate(), 24_000)
                    self.assertEqual(recording.getnframes(), 480)
                self.assertEqual(len(_FakeNative.instances), 1)
            finally:
                await bridge.stop()


if __name__ == "__main__":
    unittest.main()
