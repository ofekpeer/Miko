"""TLS and paired-identity bridge from a physical device to NativeSession.

The bridge never runs unless its private settings explicitly enable it. It
does not hold OpenAI, Gmail, SMTP or brain credentials on the device. The
server-side NativeSession and VoiceState remain the single owner of model
context, memory and actions across reconnects.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import json
import os
import secrets
import ssl
import struct
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .protocol import (
    MAX_CONTROL_BYTES,
    MAX_JPEG_BYTES,
    MAX_OUTBOUND_QUEUE_BYTES,
    MAX_OUTBOUND_QUEUE_MESSAGES,
    MAX_UPLINK_BYTES_PER_SECOND,
    PATH,
    PROTOCOL_VERSION,
    DEVICE_ID_RE,
    HEX_16_RE,
    ProtocolError,
    decode_camera_jpeg,
    decode_vision_jpeg,
    VISION_MAX_FPS,
    VISION_STREAM,
    decode_pairing_secret,
    decode_uplink_pcm,
    encode_downlink_pcm,
    new_nonce,
    verify_pairing_proof,
)


class DeviceConfigError(ValueError):
    """A missing or unsafe opt-in device configuration."""


def create_pairing_secret() -> str:
    """Return one base64-encoded, 256-bit secret for secure out-of-band pairing."""
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


@dataclass(frozen=True)
class PairedDevice:
    device_id: str
    secret: bytes
    capabilities: frozenset[str]


@dataclass(frozen=True)
class BridgeSettings:
    enabled: bool
    bind_host: str
    port: int
    cert_path: Path | None
    key_path: Path | None
    devices: dict[str, PairedDevice]

    @classmethod
    def load(cls, path: Path) -> "BridgeSettings":
        if not path.exists():
            return cls(False, "127.0.0.1", 5443, None, None, {})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DeviceConfigError("device settings are unreadable") from error
        if not isinstance(data, dict) or data.get("version") != PROTOCOL_VERSION:
            raise DeviceConfigError("unsupported device settings version")
        enabled = data.get("enabled") is True
        bind_host = data.get("bind_host", "127.0.0.1")
        try:
            address = ipaddress.ip_address(bind_host)
        except ValueError as error:
            raise DeviceConfigError("bind_host must be a numeric local IP address") from error
        if address.is_unspecified or address.is_multicast or address.is_global:
            raise DeviceConfigError("bind_host must be one private or loopback interface")
        port = data.get("port", 5443)
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise DeviceConfigError("invalid device bridge port")
        cert_value = data.get("tls_cert")
        key_value = data.get("tls_key")
        cert_path = Path(cert_value).expanduser().resolve() if isinstance(cert_value, str) and cert_value else None
        key_path = Path(key_value).expanduser().resolve() if isinstance(key_value, str) and key_value else None
        rows = data.get("paired_devices", [])
        if not isinstance(rows, list) or len(rows) > 1:
            raise DeviceConfigError("exactly one owner device may be paired")
        devices: dict[str, PairedDevice] = {}
        for row in rows:
            if not isinstance(row, dict) or not DEVICE_ID_RE.fullmatch(str(row.get("device_id", ""))):
                raise DeviceConfigError("invalid paired device identity")
            device_id = row["device_id"]
            if device_id in devices:
                raise DeviceConfigError("duplicate paired device")
            try:
                secret = decode_pairing_secret(row.get("pairing_secret_b64", ""))
            except ProtocolError as error:
                raise DeviceConfigError("invalid paired device secret") from error
            raw_caps = row.get("capabilities", [])
            if not isinstance(raw_caps, list) or not all(isinstance(x, str) for x in raw_caps):
                raise DeviceConfigError("invalid device capabilities")
            capabilities = frozenset(raw_caps)
            if not capabilities or not capabilities <= {"audio", "display", "gesture", "camera", "vision"}:
                raise DeviceConfigError("unsupported device capability")
            devices[device_id] = PairedDevice(device_id, secret, capabilities)
        if enabled and (not devices or not cert_path or not key_path or not cert_path.is_file() or not key_path.is_file()):
            raise DeviceConfigError("enabled bridge requires one paired device and TLS certificate/key files")
        return cls(enabled, str(address), port, cert_path, key_path, devices)


class _RateLimit:
    def __init__(self) -> None:
        self.tokens = float(MAX_UPLINK_BYTES_PER_SECOND)
        self.last = time.monotonic()

    def consume(self, size: int) -> bool:
        now = time.monotonic()
        self.tokens = min(
            float(MAX_UPLINK_BYTES_PER_SECOND),
            self.tokens + (now - self.last) * 48_000,
        )
        self.last = now
        if size > self.tokens:
            return False
        self.tokens -= size
        return True


class DeviceTransport:
    """Adapter matching NativeSession's ws.send(JSON) surface.

    Model PCM is converted to bounded binary frames. It is never silently
    dropped: a slow device is disconnected before stale audio can be marked
    played. No private hub-wide broadcast is subscribed to this transport.
    """

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[tuple[str | bytes, str]] = asyncio.Queue(MAX_OUTBOUND_QUEUE_MESSAGES)
        self.queued_bytes = 0
        self.sequence = 0
        self.audio_done_items: set[str] = set()
        self.audio_payload_items: set[str] = set()
        self.writer_task: asyncio.Task[None] | None = None
        self.closed = False

    def start(self) -> None:
        self.writer_task = asyncio.create_task(self._writer(), name="MikoDeviceWriter")

    async def send(self, raw: str) -> None:
        if self.closed:
            return
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CONTROL_BYTES * 4:
            await self.fail("oversized device output")
            return
        try:
            event = json.loads(raw)
            if event.get("type") == "interrupted":
                self._drop_pending()
                self.audio_done_items.clear()
                self.audio_payload_items.clear()
            if event.get("type") == "audio":
                pcm = base64.b64decode(event.get("audio", ""), validate=True)
                item_id = str(event.get("item_id", ""))
                content_index = int(event.get("content_index", 0))
                # Realtime deltas may exceed one device frame. Preserve order.
                for offset in range(0, len(pcm), 4_096):
                    chunk = pcm[offset : offset + 4_096]
                    if chunk:
                        # Assign the sequence only when the writer actually
                        # sends this frame. Interrupted queued frames can be
                        # discarded without leaving gaps at the device.
                        payload = encode_downlink_pcm(0, item_id, content_index, chunk)
                        self._enqueue(payload, "")
                        self.audio_payload_items.add(item_id)
                return
            audio_done_id = str(event.get("item_id", "")) if event.get("type") == "audio_done" else ""
            if len(raw.encode("utf-8")) > MAX_CONTROL_BYTES:
                raise ProtocolError("oversized device control output")
            self._enqueue(raw, audio_done_id)
        except (ValueError, TypeError, ProtocolError, binascii.Error):
            await self.fail("invalid device output")

    def _drop_pending(self) -> None:
        while not self.queue.empty():
            payload, _ = self.queue.get_nowait()
            self.queued_bytes -= len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)

    def _enqueue(self, payload: str | bytes, audio_done_id: str) -> None:
        size = len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)
        if self.queued_bytes + size > MAX_OUTBOUND_QUEUE_BYTES or self.queue.full():
            asyncio.create_task(self.fail("slow device"))
            raise ProtocolError("slow device")
        self.queued_bytes += size
        self.queue.put_nowait((payload, audio_done_id))

    async def _writer(self) -> None:
        try:
            while not self.closed:
                payload, audio_done_id = await self.queue.get()
                if isinstance(payload, bytes):
                    frame = payload[:3] + struct.pack(">I", self.sequence) + payload[7:]
                    await self.ws.send(frame)
                    self.sequence = (self.sequence + 1) & 0xFFFFFFFF
                else:
                    await self.ws.send(payload)
                self.queued_bytes -= len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)
                if audio_done_id and audio_done_id in self.audio_payload_items:
                    self.audio_done_items.add(audio_done_id)
                    self.audio_payload_items.discard(audio_done_id)
        except asyncio.CancelledError:
            pass
        except Exception:
            await self.fail("device transport failed")

    async def fail(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            await self.ws.close(code=1013, reason=reason[:80])
        except Exception:
            pass

    async def close(self) -> None:
        self.closed = True
        if self.writer_task:
            self.writer_task.cancel()
            try:
                await self.writer_task
            except asyncio.CancelledError:
                pass
        self.audio_done_items.clear()
        self.audio_payload_items.clear()


class DeviceBridge:
    """Opt-in WSS listener, one paired device, persistent logical NativeSession."""

    def __init__(
        self,
        hub: Any,
        settings_path: str | os.PathLike[str],
        native_factory: Callable[[Any, DeviceTransport], Any] | None = None,
    ) -> None:
        self.hub = hub
        self.settings_path = Path(settings_path)
        self.native_factory = native_factory
        self.settings: BridgeSettings | None = None
        self.server: Any = None
        self.active: dict[str, _DeviceConnection] = {}
        self.logical_sessions: dict[str, Any] = {}
        self._lifecycle_locks: dict[str, asyncio.Lock] = {}
        self._connection_count = 0

    async def start(self) -> bool:
        self.settings = BridgeSettings.load(self.settings_path)
        if not self.settings.enabled:
            return False
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(str(self.settings.cert_path), str(self.settings.key_path))
        self.server = await serve(
            self._handler,
            self.settings.bind_host,
            self.settings.port,
            ssl=tls,
            max_size=MAX_JPEG_BYTES + 17,
            max_queue=8,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=10,
        )
        return True

    async def stop(self) -> None:
        closing = list(self.active.values())
        for connection in closing:
            await connection.ws.close(code=1001, reason="bridge stopping")
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        for connection in closing:
            await connection.finalized.wait()

    async def _handler(self, ws: Any) -> None:
        if self._connection_count >= 4:
            await ws.close(code=1013, reason="device bridge busy")
            return
        self._connection_count += 1
        connection: _DeviceConnection | None = None
        try:
            if ws.request.path != PATH or ws.request.headers.get("Origin"):
                await ws.close(code=1008, reason="invalid device endpoint")
                return
            settings = self.settings
            if not settings or not settings.enabled:
                await ws.close(code=1008, reason="device bridge disabled")
                return
            server_nonce = new_nonce()
            await ws.send(json.dumps({"type": "challenge", "version": 1, "server_nonce": server_nonce}))
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4_096:
                raise ProtocolError("invalid authentication frame")
            hello = json.loads(raw)
            if not isinstance(hello, dict) or hello.get("type") != "authenticate":
                raise ProtocolError("authentication required")
            device_id = hello.get("device_id")
            client_nonce = hello.get("client_nonce")
            if not isinstance(device_id, str) or not isinstance(client_nonce, str) or not HEX_16_RE.fullmatch(client_nonce):
                raise ProtocolError("invalid authentication identity")
            paired = settings.devices.get(device_id)
            if not paired or not verify_pairing_proof(
                paired.secret, device_id, server_nonce, client_nonce, hello.get("proof", "")
            ):
                raise ProtocolError("unpaired device")
            raw_caps = hello.get("capabilities", [])
            if not isinstance(raw_caps, list) or not all(isinstance(x, str) for x in raw_caps):
                raise ProtocolError("invalid capabilities")
            capabilities = paired.capabilities.intersection(raw_caps)
            if "audio" not in capabilities:
                raise ProtocolError("audio capability required")

            # One logical NativeSession may outlive several sockets. Finish
            # closing its old API/transport before rebinding it, including
            # when two reconnections arrive at nearly the same instant.
            async with self._lifecycle_locks.setdefault(device_id, asyncio.Lock()):
                old = self.active.get(device_id)
                if old:
                    await old.ws.close(code=4001, reason="reconnected elsewhere")
                    await old.finalized.wait()
                transport = DeviceTransport(ws)
                if device_id in self.logical_sessions:
                    native = self.logical_sessions[device_id]
                    native.ws = transport
                    resumed = True
                else:
                    factory = self.native_factory
                    if factory is None:
                        from miko_realtime import NativeSession
                        factory = NativeSession
                    native = factory(self.hub, transport)
                    self.logical_sessions[device_id] = native
                    resumed = False
                # The host uses this authenticated identity when deciding whether
                # an explicit camera request may reach this physical device.
                native.device_id = device_id
                transport.device_id = device_id
                connection = _DeviceConnection(ws, transport, native, device_id, capabilities)
                self.active[device_id] = connection
                self.hub.native.add(native)
                await ws.send(json.dumps({
                    "type": "accepted", "version": 1, "device_id": device_id,
                    "connection_id": uuid.uuid4().hex, "resumed": resumed,
                    "sample_rate": 24_000, "channels": 1, "pcm_bits": 16,
                    "capabilities": sorted(capabilities),
                }))
                transport.start()
                await native.send({"type": "status", "status": "idle", "detail": "Device connected"})
                await self._sync_vision(connection)
            async for payload in ws:
                if isinstance(payload, bytes):
                    await self._binary(connection, payload)
                else:
                    await self._control(connection, payload)
        except (asyncio.TimeoutError, json.JSONDecodeError, ProtocolError):
            await ws.close(code=1008, reason="device authentication or protocol failed")
        except ConnectionClosed:
            pass
        finally:
            self._connection_count -= 1
            if connection:
                try:
                    try:
                        await connection.transport.close()
                    finally:
                        if self.active.get(connection.device_id) is connection:
                            # Keep this entry visible while suspend awaits. A
                            # reconnect must wait before rebinding NativeSession.
                            await connection.native.suspend()
                finally:
                    if self.active.get(connection.device_id) is connection:
                        self.active.pop(connection.device_id, None)
                        self.hub.native.discard(connection.native)
                    for future in connection.pending_snapshots.values():
                        if not future.done():
                            future.set_exception(ConnectionError("device disconnected"))
                    connection.finalized.set()

    async def _control(self, connection: "_DeviceConnection", raw: str) -> None:
        if len(raw.encode("utf-8")) > MAX_CONTROL_BYTES:
            raise ProtocolError("oversized device control frame")
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ProtocolError("invalid control event")
        kind = event.get("type")
        if kind not in {"configure", "start", "stop", "interrupt", "playback", "text"}:
            raise ProtocolError("unsupported device event")
        if kind == "playback":
            item_id = event.get("item_id")
            if event.get("finished") is not True or item_id not in connection.transport.audio_done_items:
                raise ProtocolError("unearned playback acknowledgement")
            connection.transport.audio_done_items.remove(item_id)
        await connection.native.client_event(event)

    async def _binary(self, connection: "_DeviceConnection", frame: bytes) -> None:
        if not frame:
            raise ProtocolError("empty device frame")
        if frame[0] == 0x01:
            sequence, pcm = decode_uplink_pcm(frame)
            if sequence != connection.expected_sequence:
                raise ProtocolError("out-of-order PCM")
            connection.expected_sequence = (sequence + 1) & 0xFFFFFFFF
            if not connection.rate_limit.consume(len(pcm)):
                raise ProtocolError("PCM rate limit exceeded")
            if not connection.native.api:
                return
            await connection.native.client_event({"type": "audio", "audio": base64.b64encode(pcm).decode("ascii")})
        elif frame[0] == 0x04:
            await self._vision_frame(connection, frame)
        elif frame[0] == 0x03:
            request_id, jpeg = decode_camera_jpeg(frame)
            future = connection.pending_snapshots.pop(request_id, None)
            if not future or future.done():
                raise ProtocolError("unsolicited camera image")
            future.set_result(jpeg)
        else:
            raise ProtocolError("unknown binary frame")

    def _vision_wanted(self, connection: "_DeviceConnection") -> bool:
        vision = getattr(self.hub, "vision", None)
        return "vision" in connection.capabilities and vision is not None and bool(vision.enabled)

    async def _sync_vision(self, connection: "_DeviceConnection") -> None:
        """Tell the device whether to stream perception frames (owner toggle)."""
        active = self._vision_wanted(connection)
        if "vision" not in connection.capabilities or active == connection.vision_active:
            return
        connection.vision_active = active
        message = {"type": "vision_stream", "active": active}
        if active:
            message.update(VISION_STREAM)
        await connection.transport.send(json.dumps(message))

    async def set_vision_stream(self) -> None:
        for connection in list(self.active.values()):
            if not connection.transport.closed:
                await self._sync_vision(connection)

    async def _vision_frame(self, connection: "_DeviceConnection", frame: bytes) -> None:
        if not connection.vision_active or "vision" not in connection.capabilities:
            raise ProtocolError("unsolicited vision frame")
        _, jpeg = decode_vision_jpeg(frame)
        now = time.monotonic()
        # Bounded: drop (not disconnect) frames above the agreed rate or while
        # the previous frame is still being analysed.
        if now - connection.last_vision_at < 1.0 / VISION_MAX_FPS or connection.vision_busy:
            return
        connection.last_vision_at = now
        vision = getattr(self.hub, "vision", None)
        if vision is None:
            return
        connection.vision_busy = True
        try:
            await asyncio.to_thread(vision.feed_jpeg, jpeg)
        finally:
            connection.vision_busy = False

    async def request_snapshot(self, device_id: str, *, requested_by_user: bool) -> bytes:
        """Request one in-memory JPEG only for an explicit user request.

        The device firmware must also require a physical confirmation before
        capture. The bridge does not persist or automatically upload the image.
        """
        if requested_by_user is not True:
            raise PermissionError("camera capture requires a user request")
        connection = self.active.get(device_id)
        if not connection or connection.transport.closed or "camera" not in connection.capabilities:
            raise ConnectionError("paired camera device unavailable")
        if connection.pending_snapshots:
            raise RuntimeError("camera request already in progress")
        request_id = str(uuid.uuid4())
        future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        connection.pending_snapshots[request_id] = future
        try:
            await connection.transport.send(json.dumps({
                "type": "camera_request", "request_id": request_id,
                "expires_ms": 10_000, "max_bytes": MAX_JPEG_BYTES,
                "requires_local_confirmation": True,
            }))
            return await asyncio.wait_for(future, timeout=10)
        finally:
            connection.pending_snapshots.pop(request_id, None)

    async def publish_ui_event(self, event: dict[str, Any]) -> None:
        """Send non-private UI cues to the currently paired owner device only."""
        if not self.active or self.hub.browser_id:
            return
        connection = next(iter(self.active.values()))
        kind = event.get("type")
        if kind == "expression" and "gesture" in connection.capabilities:
            cue = {
                "type": "gesture",
                "emotion": str(event.get("emotion", "neutral"))[:32],
                "action": str(event.get("action", "idle"))[:32],
            }
        elif kind == "action" and "gesture" in connection.capabilities:
            try:
                times = max(1, min(5, int(event.get("times", 1))))
            except (TypeError, ValueError):
                times = 1
            cue = {"type": "gesture", "emotion": "", "action": str(event.get("action", "idle"))[:32], "times": times}
        elif kind == "status" and "display" in connection.capabilities:
            cue = {
                "type": "status",
                "status": str(event.get("status", "idle"))[:32],
                "detail": str(event.get("detail", ""))[:160],
            }
        elif kind == "voice_level" and "display" in connection.capabilities:
            try:
                level = max(0.0, min(1.0, float(event.get("level", 0))))
            except (TypeError, ValueError):
                level = 0.0
            cue = {"type": "voice_level", "level": level}
        else:
            return
        await connection.transport.send(json.dumps(cue, ensure_ascii=False))


@dataclass
class _DeviceConnection:
    ws: Any
    transport: DeviceTransport
    native: Any
    device_id: str
    capabilities: frozenset[str]
    expected_sequence: int = 0
    rate_limit: _RateLimit | None = None
    pending_snapshots: dict[str, asyncio.Future[bytes]] | None = None
    vision_active: bool = False
    vision_busy: bool = False
    last_vision_at: float = 0.0
    finalized: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self.rate_limit = _RateLimit()
        self.pending_snapshots = {}
