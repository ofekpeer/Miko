"""Desktop client for an authenticated physical-device voice session.

Uses a WAV file or an optional desktop microphone as the device microphone.
An optional real speaker is required before it reports playback complete.
The server, not this client, owns Miko's brain, memory and credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import ssl
import sys
import wave
from pathlib import Path

from websockets.asyncio.client import connect

from .protocol import (
    MAX_JPEG_BYTES,
    PATH,
    SAMPLE_RATE,
    ProtocolError,
    decode_downlink_pcm,
    decode_pairing_secret,
    encode_camera_jpeg,
    encode_motion_imu,
    encode_vision_jpeg,
    encode_uplink_pcm,
    new_nonce,
    pairing_proof,
)


def _load_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, SAMPLE_RATE):
            raise ValueError("input WAV must be mono 16-bit PCM at 24 kHz")
        return source.readframes(source.getnframes())


def _record_microphone(seconds: float) -> bytes:
    try:
        import sounddevice as sd
    except ImportError as error:
        raise RuntimeError("--mic-seconds requires the optional sounddevice package") from error
    recording = sd.rec(
        int(SAMPLE_RATE * seconds), samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocking=True
    )
    return recording.tobytes()


class _Speaker:
    def __init__(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as error:
            raise RuntimeError("--speaker requires the optional sounddevice package") from error
        self.stream = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        self.stream.start()
        self.items: dict[str, int] = {}

    async def play(self, item_id: str, pcm: bytes) -> None:
        self.items[item_id] = self.items.get(item_id, 0) + len(pcm)
        await asyncio.to_thread(self.stream.write, pcm)

    async def finish(self, item_id: str) -> bool:
        if not self.items.pop(item_id, 0):
            return False
        # PortAudio's blocking stop waits for pending output buffers to play;
        # elapsed-time estimates cannot establish that an utterance was heard.
        await asyncio.to_thread(self.stream.stop)
        await asyncio.to_thread(self.stream.start)
        return True

    async def interrupt(self) -> None:
        self.items.clear()
        await asyncio.to_thread(self.stream.abort)
        await asyncio.to_thread(self.stream.start)

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()


async def _send_pcm(ws, pcm: bytes) -> None:
    # No hardware playback cursor is available in file/microphone simulation.
    # Treat any prior speaker output as unplayed before starting the next turn.
    await ws.send(json.dumps({
        "type": "interrupt", "item_id": "", "content_index": 0,
        "audio_end_ms": 0, "played_ms": 0, "unplayed_item_ids": [],
    }))
    await ws.send(json.dumps({"type": "start"}))
    for sequence, offset in enumerate(range(0, len(pcm), 960)):
        chunk = pcm[offset : offset + 960]
        if len(chunk) % 2:
            chunk = chunk[:-1]
        if chunk:
            await ws.send(encode_uplink_pcm(sequence, chunk))
            await asyncio.sleep(len(chunk) / (SAMPLE_RATE * 2))
    await ws.send(json.dumps({"type": "stop"}))


async def _send_shake(ws, seconds: float = 1.5) -> None:
    """A synthetic IMU episode: rest, a brisk 4 Hz shake, rest (100 Hz)."""
    import math
    sequence, device_ms = 0, 0
    phases = [(1.0, 0.0), (seconds, 400.0), (2.0, 0.0)]
    for duration, amplitude in phases:
        for _ in range(int(duration * 10)):              # 10 batches per second
            batch = []
            for i in range(10):
                t = (device_ms + i * 10) / 1000.0
                gx = amplitude * math.sin(2 * math.pi * 4 * t)
                batch.append((i * 10, 0, 0, 1000, int(gx * 10), 0, int(amplitude * 2)))
            await ws.send(encode_motion_imu(sequence, device_ms, batch))
            sequence, device_ms = sequence + 1, device_ms + 100
            await asyncio.sleep(0.1)


async def run(args: argparse.Namespace) -> None:
    secret = decode_pairing_secret(Path(args.secret_file).read_text(encoding="ascii").strip())
    context = ssl.create_default_context(cafile=args.ca)
    speaker = _Speaker() if args.speaker else None
    wav_out = wave.open(args.wav_out, "wb") if args.wav_out else None
    if wav_out:
        wav_out.setnchannels(1)
        wav_out.setsampwidth(2)
        wav_out.setframerate(SAMPLE_RATE)
    try:
        async with connect(args.url, ssl=context, max_size=MAX_JPEG_BYTES + 17, ping_interval=20) as ws:
            challenge = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if challenge.get("type") != "challenge" or challenge.get("version") != 1:
                raise ProtocolError("unexpected device challenge")
            client_nonce = new_nonce()
            capabilities = ["audio", "display", "gesture"]
            if args.camera_file:
                capabilities.append("camera")
            vision_file = getattr(args, "vision_file", None)
            if vision_file:
                capabilities.append("vision")
            if getattr(args, "shake_demo", False):
                capabilities.append("motion")
            await ws.send(json.dumps({
                "type": "authenticate", "device_id": args.device_id,
                "client_nonce": client_nonce,
                "proof": pairing_proof(secret, args.device_id, challenge["server_nonce"], client_nonce),
                "capabilities": capabilities,
            }))
            accepted = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if accepted.get("type") != "accepted":
                raise ProtocolError("device authentication rejected")
            print("paired device connected; logical session resumed:", bool(accepted.get("resumed")))
            ready = asyncio.Event()
            done = asyncio.Event()
            expected_downlink = 0
            vision_task: asyncio.Task | None = None

            async def stream_vision(fps: float) -> None:
                # Stands in for the device camera: the same small JPEG, looped.
                jpeg = Path(vision_file).read_bytes()
                sequence = 0
                while True:
                    await ws.send(encode_vision_jpeg(sequence, jpeg))
                    sequence = (sequence + 1) & 0xFFFFFFFF
                    await asyncio.sleep(1.0 / max(1.0, fps))

            async def reader() -> None:
                nonlocal expected_downlink, vision_task
                async for payload in ws:
                    if isinstance(payload, bytes):
                        sequence, item_id, _content_index, pcm = decode_downlink_pcm(payload)
                        if sequence != expected_downlink:
                            raise ProtocolError("out-of-order speaker PCM")
                        expected_downlink = (sequence + 1) & 0xFFFFFFFF
                        if wav_out:
                            wav_out.writeframes(pcm)
                        if speaker:
                            await speaker.play(item_id, pcm)
                        continue
                    event = json.loads(payload)
                    kind = event.get("type")
                    if kind == "ready":
                        ready.set()
                    elif kind == "audio_done" and speaker:
                        item_id = str(event.get("item_id", ""))
                        if await speaker.finish(item_id):
                            await ws.send(json.dumps({"type": "playback", "item_id": item_id, "finished": True}))
                    elif kind == "interrupted":
                        if speaker:
                            await speaker.interrupt()
                        print("playback interrupted")
                    elif kind == "camera_request":
                        if not args.camera_file:
                            print("camera request declined: no camera file")
                            continue
                        answer = await asyncio.to_thread(input, "Explicit camera request: send selected JPEG? [y/N] ")
                        if answer.strip().lower() == "y":
                            jpeg = Path(args.camera_file).read_bytes()
                            await ws.send(encode_camera_jpeg(event["request_id"], jpeg))
                    elif kind == "vision_stream":
                        if vision_task:
                            vision_task.cancel()
                            vision_task = None
                        if event.get("active") and vision_file:
                            print("vision stream requested at", event.get("fps"), "fps")
                            vision_task = asyncio.create_task(stream_vision(float(event.get("fps", 4))))
                    elif kind == "response_done" and not event.get("has_tool_calls"):
                        done.set()
                    elif kind in {"status", "transcript", "gesture", "error", "device_session"}:
                        print(json.dumps(event, ensure_ascii=False))

            task = asyncio.create_task(reader())
            try:
                await ws.send(json.dumps({"type": "configure", "mode": "ptt"}))
                await asyncio.wait_for(ready.wait(), timeout=30)
                if getattr(args, "shake_demo", False):
                    print("sending a synthetic shake from the device IMU")
                    await _send_shake(ws)
                if args.text:
                    await ws.send(json.dumps({"type": "text", "text": args.text}))
                elif args.wav_in:
                    await _send_pcm(ws, _load_wav(Path(args.wav_in)))
                elif args.mic_seconds:
                    pcm = await asyncio.to_thread(_record_microphone, args.mic_seconds)
                    await _send_pcm(ws, pcm)
                else:
                    print("connected without audio input; waiting for display events")
                try:
                    await asyncio.wait_for(done.wait(), timeout=args.wait_seconds)
                except asyncio.TimeoutError:
                    pass
            finally:
                if vision_task:
                    vision_task.cancel()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    finally:
        if speaker:
            speaker.close()
        if wav_out:
            wav_out.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Miko paired-device desktop simulator")
    parser.add_argument("--url", required=True, help=f"wss://<private-LAN-IP>:5443{PATH}")
    parser.add_argument("--ca", required=True, help="server certificate PEM used for TLS verification")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--secret-file", required=True, help="private file containing the base64 pairing secret")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--wav-in")
    source.add_argument("--mic-seconds", type=float)
    source.add_argument("--text")
    parser.add_argument("--wav-out", help="record received PCM to a 24 kHz WAV; never acknowledges playback")
    parser.add_argument("--speaker", action="store_true", help="play through a real speaker and acknowledge completion")
    parser.add_argument("--camera-file", help="JPEG offered only after a server request and interactive confirmation")
    parser.add_argument("--vision-file", help="small JPEG streamed as perception frames when the server asks")
    parser.add_argument("--shake-demo", action="store_true", help="advertise the motion capability and send a synthetic IMU shake")
    parser.add_argument("--wait-seconds", type=float, default=30)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, ProtocolError, ConnectionError, TimeoutError) as error:
        print(f"device simulator: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
