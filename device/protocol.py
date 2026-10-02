"""Miko Device Protocol v1: authenticated control plus bounded PCM/JPEG frames.

This module contains no OpenAI or application credentials. A device has only its
own pairing secret, Wi-Fi settings and a pinned certificate for the local server.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import struct
import uuid

PROTOCOL_VERSION = 1
PATH = "/device/v1"
SAMPLE_RATE = 24_000
CHANNELS = 1
PCM_BITS = 16
PCM_BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * PCM_BITS // 8
MAX_PCM_CHUNK = 4_096
MAX_JPEG_BYTES = 160_000
MAX_CONTROL_BYTES = 32_768
MAX_UPLINK_BYTES_PER_SECOND = 96_000
MAX_OUTBOUND_QUEUE_BYTES = 262_144
MAX_OUTBOUND_QUEUE_MESSAGES = 64

UPLINK_PCM = 0x01
DOWNLINK_PCM = 0x02
CAMERA_JPEG = 0x03
# Continuous low-resolution frames for local perception (presence, waves).
# Only while the server sent vision_stream active=true; processed in memory
# on the server, never stored or forwarded to a model.
VISION_JPEG = 0x04
MAX_VISION_JPEG_BYTES = 24_000
VISION_MAX_FPS = 6
VISION_STREAM = {"fps": 4, "width": 240, "height": 180, "max_bytes": MAX_VISION_JPEG_BYTES}
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
HEX_16_RE = re.compile(r"^[0-9a-f]{32}$")
HEX_32_RE = re.compile(r"^[0-9a-f]{64}$")


class ProtocolError(ValueError):
    """A malformed or unauthorized device frame."""


def new_nonce() -> str:
    return secrets.token_hex(16)


def pairing_proof(secret: bytes, device_id: str, server_nonce: str, client_nonce: str) -> str:
    if len(secret) != 32 or not DEVICE_ID_RE.fullmatch(device_id):
        raise ProtocolError("invalid device identity")
    if not HEX_16_RE.fullmatch(server_nonce) or not HEX_16_RE.fullmatch(client_nonce):
        raise ProtocolError("invalid challenge nonce")
    message = f"MIKO-DEVICE-V1\n{device_id}\n{server_nonce}\n{client_nonce}".encode("ascii")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_pairing_proof(
    secret: bytes, device_id: str, server_nonce: str, client_nonce: str, supplied: str
) -> bool:
    if not isinstance(supplied, str) or not HEX_32_RE.fullmatch(supplied):
        return False
    try:
        expected = pairing_proof(secret, device_id, server_nonce, client_nonce)
    except ProtocolError:
        return False
    return hmac.compare_digest(expected, supplied)


def decode_pairing_secret(encoded: str) -> bytes:
    try:
        secret = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ProtocolError("invalid pairing secret encoding") from error
    if len(secret) != 32:
        raise ProtocolError("pairing secret must contain 32 bytes")
    return secret


def encode_uplink_pcm(sequence: int, pcm: bytes) -> bytes:
    if not 0 <= sequence <= 0xFFFFFFFF or not pcm or len(pcm) > MAX_PCM_CHUNK or len(pcm) % 2:
        raise ProtocolError("invalid PCM chunk")
    return bytes([UPLINK_PCM]) + struct.pack(">I", sequence) + pcm


def decode_uplink_pcm(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < 7 or frame[0] != UPLINK_PCM:
        raise ProtocolError("invalid uplink PCM frame")
    sequence = struct.unpack_from(">I", frame, 1)[0]
    pcm = frame[5:]
    if not pcm or len(pcm) > MAX_PCM_CHUNK or len(pcm) % 2:
        raise ProtocolError("invalid uplink PCM payload")
    return sequence, pcm


def encode_downlink_pcm(sequence: int, item_id: str, content_index: int, pcm: bytes) -> bytes:
    item = item_id.encode("ascii")
    if not 1 <= len(item) <= 64 or not 0 <= sequence <= 0xFFFFFFFF:
        raise ProtocolError("invalid output audio identity")
    if not 0 <= content_index <= 255 or not pcm or len(pcm) > 64_000 or len(pcm) % 2:
        raise ProtocolError("invalid output PCM chunk")
    return bytes([DOWNLINK_PCM, len(item), content_index]) + struct.pack(">I", sequence) + item + pcm


def decode_downlink_pcm(frame: bytes) -> tuple[int, str, int, bytes]:
    if len(frame) < 10 or frame[0] != DOWNLINK_PCM:
        raise ProtocolError("invalid downlink PCM frame")
    item_length = frame[1]
    if not 1 <= item_length <= 64 or len(frame) < 7 + item_length + 2:
        raise ProtocolError("invalid downlink audio identity")
    content_index = frame[2]
    sequence = struct.unpack_from(">I", frame, 3)[0]
    try:
        item_id = frame[7 : 7 + item_length].decode("ascii")
    except UnicodeDecodeError as error:
        raise ProtocolError("invalid downlink item id") from error
    pcm = frame[7 + item_length :]
    if len(pcm) % 2:
        raise ProtocolError("unaligned downlink PCM")
    return sequence, item_id, content_index, pcm


def encode_camera_jpeg(request_id: str, jpeg: bytes) -> bytes:
    try:
        request_bytes = uuid.UUID(request_id).bytes
    except (ValueError, AttributeError) as error:
        raise ProtocolError("invalid camera request id") from error
    validate_jpeg(jpeg)
    return bytes([CAMERA_JPEG]) + request_bytes + jpeg


def decode_camera_jpeg(frame: bytes) -> tuple[str, bytes]:
    if len(frame) < 21 or frame[0] != CAMERA_JPEG:
        raise ProtocolError("invalid camera frame")
    request_id = str(uuid.UUID(bytes=frame[1:17]))
    jpeg = frame[17:]
    validate_jpeg(jpeg)
    return request_id, jpeg


def validate_jpeg(jpeg: bytes) -> None:
    if not isinstance(jpeg, bytes) or len(jpeg) < 4 or len(jpeg) > MAX_JPEG_BYTES:
        raise ProtocolError("JPEG size outside device limit")
    if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        raise ProtocolError("invalid JPEG boundary markers")


def encode_vision_jpeg(sequence: int, jpeg: bytes) -> bytes:
    if not 0 <= sequence <= 0xFFFFFFFF or len(jpeg) > MAX_VISION_JPEG_BYTES:
        raise ProtocolError("invalid vision frame")
    validate_jpeg(jpeg)
    return bytes([VISION_JPEG]) + struct.pack(">I", sequence) + jpeg


def decode_vision_jpeg(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < 9 or frame[0] != VISION_JPEG:
        raise ProtocolError("invalid vision frame")
    jpeg = frame[5:]
    if len(jpeg) > MAX_VISION_JPEG_BYTES:
        raise ProtocolError("vision frame too large")
    validate_jpeg(jpeg)
    return struct.unpack_from(">I", frame, 1)[0], jpeg
