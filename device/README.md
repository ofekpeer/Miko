# Miko physical device bridge (protocol v1)

Miko's Python host owns the brain, memory, Realtime connection, credentials and tools. The ESP32-S3 is a paired Wi-Fi audio/display client. The existing browser voice UI and the physical device share the same server policy; the device does not receive an OpenAI key or mail password. One paired owner identity is supported. A reconnect resumes its logical `NativeSession` and `VoiceState`; the upstream Realtime socket is reopened after a disconnect. The host must remain running for voice or actions.

The device listener is **off by default**. It is a separate WSS server on one explicitly selected private or loopback IP; the Flask browser server remains loopback-only. It refuses wildcard/public binds, unpaired identities, unverified TLS and capabilities absent from the pairing record. The server does not forward private browser events to device spectators. The device's own conversation comes from its authenticated `NativeSession`.

## Hardware boundary

One board matching the stated 1.69-inch, 240×280 ESP32-S3 screen is the [Waveshare ESP32-S3-Touch-LCD-1.69](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.69); the exact physical board has not been confirmed. Its published resources include an ST7789V2 SPI LCD, CST816T touch, QMI8658 IMU and a buzzer. The buzzer is not a speech speaker; the published board specification does not provide an onboard microphone, speech DAC/amplifier or camera. Those features require compatible external hardware or a custom board, a pin and power budget, and a board-specific adapter. Do not promise camera, full-duplex speech or echo cancellation on the bare display board. The firmware's `miko_board_stub.c` intentionally leaves networking disabled until that adapter and provisioning are implemented.

For the exact board revision, the [Waveshare documentation](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.69) lists LCD DC 4, CS 5, CLK 6, DIN 7, RST 8, BL 15; touch I²C SCL 10, SDA 11, RST 13, INT 14; buzzer 42; and extension GPIO 2, 3, 17, 18 plus UART 43, 44. Confirm those against the physical board and all peripherals before assigning external I²S or camera pins. ESP-IDF offers standard/PDM I²S modes and 24 kHz capture/output when the attached codecs support them; see [Espressif's ESP32-S3 I²S guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-reference/peripherals/i2s.html). If an AFE produces 16 kHz PCM, resample it deterministically to 24 kHz in the board adapter before sending. A hands-free build needs acoustic echo cancellation using the actual speaker playback reference, a visible active microphone state and a physical mute switch. Push-to-talk is the simpler initial board mode.

## Pairing and listener

Run these from the installed Python host directory (the Desktop on Ofek's computer) (replace the private paths and LAN IP with your own):

```powershell
python -m device.pair_device --settings .\miko_device_settings.json pair --device-id miko-01 --secret-file .\private\miko-01.secret
```

`pair` generates a random 256-bit secret, writes it to a separate private provisioning file and saves its copy in host settings. The listener stays disabled. Provision only the device identity, that secret, Wi-Fi credentials, the server WSS URL and a pinned server certificate into protected device storage. Never compile secrets into the firmware. `--camera` at pairing grants the camera capability only if real camera hardware and a local consent control exist; it is absent by default. `--replace` revokes the old pairing on the next host restart.

Create a server certificate whose Subject Alternative Name includes the chosen private IP, and retain the private key on the host. A self-signed certificate can be pinned in the device/simulator; keep the exact certificate as a trusted CA on that client. Then enable only the intended interface:

```powershell
python -m device.pair_device --settings .\miko_device_settings.json enable --bind-host 192.168.1.20 --port 5443 --tls-cert .\private\device-server.crt --tls-key .\private\device-server.key
```

Restart Miko to apply pairing or listener changes. The host loads `miko_device_settings.json` from its `BASE_DIR`. The CLI validates the settings before replacing the file. Disable with `python -m device.pair_device --settings .\miko_device_settings.json disable` and restart. Keep firewall access limited to the trusted LAN; do not forward this port through a router. On Windows, verify the settings, secret and key files have suitable NTFS ACLs; `chmod(0600)` is only a best-effort extra step.

## Desktop device simulator

The simulator exercises real TLS, challenge authentication, PCM framing, optional live microphone/speaker, and the display/control messages without ESP hardware. It verifies the certificate against `--ca`. The secret is read from a file, not passed as a shell argument.

```powershell
python -m device.simulator --url wss://192.168.1.20:5443/device/v1 --ca .\private\device-server.crt --device-id miko-01 --secret-file .\private\miko-01.secret --mic-seconds 5 --speaker
```

Alternatives include `--wav-in speech-24k-mono.wav`, `--text "שלום מיקו"`, and `--wav-out reply.wav`. WAV output is for inspection and intentionally **does not** acknowledge playback as heard. `--speaker` uses a local sound device and acknowledges only after all samples have played. `--vision-file face.jpg` adds the `vision` capability and loops that JPEG as perception frames whenever the host asks. Camera is disabled unless the simulator is started with `--camera-file image.jpg`, the pairing record includes `camera`, the host sends a one-time request, and the operator types `y` at the local confirmation prompt. It never uploads a file automatically.

Run protocol and local WSS integration tests with `python -m unittest device.test_bridge -v`. The test makes a temporary certificate and uses a fake server-side NativeSession, so it does not call OpenAI or SMTP. The ESP firmware is a hardware integration skeleton and has not been built or flashed for the physical board.

For a confirmed board, replace `firmware/main/miko_board_stub.c` with a board adapter that provisions Wi-Fi, the pairing secret and pinned certificate in protected storage and implements the declared display, microphone, speaker, physical mute and optional camera hooks. Then build with the ESP-IDF toolchain: `cd device/firmware`, `idf.py set-target esp32s3`, `idf.py build`. Flash only after checking the actual revision's pins, voltage levels and peripherals. The stub returns `ESP_ERR_NOT_SUPPORTED`, so its firmware intentionally does not connect to Wi-Fi.

## Wire protocol

Endpoint: `wss://<private-host>:<port>/device/v1`. TLS certificate verification is mandatory on the device. Text frames are JSON UTF-8 controls (maximum 32,768 bytes); binary frames carry audio or one camera JPEG. The host allows one paired device and one current connection for that identity. Reconnect replaces an older connection and resets binary sequence numbers. Never treat a network reconnect as a new person or a new permission for an action.

1. Host sends `{"type":"challenge","version":1,"server_nonce":"<32 lower-case hex>"}`.
2. Client sends `{"type":"authenticate","device_id":"miko-01","client_nonce":"<32 lower-case hex>","proof":"<64 lower-case hex>","capabilities":["audio","display","gesture"]}` within five seconds. `proof` is hex HMAC-SHA256 of ASCII bytes `MIKO-DEVICE-V1\n<device_id>\n<server_nonce>\n<client_nonce>`, keyed by the 32 raw pairing-secret bytes. Both nonces are fresh random 16-byte values. The client adds `camera` only when provisioned and physically available. The server intersects the advertised and paired capabilities; `audio` is required.
3. Host sends `accepted` with `connection_id`, `resumed`, `sample_rate:24000`, `channels:1`, `pcm_bits:16` and enabled capabilities. Client sends `{"type":"configure","mode":"ptt"}` or `"hands_free"`. After `ready`, it may send mic frames. `hands_free` uses semantic VAD on the server; the device must prevent acoustic feedback itself.

All PCM is 24,000 Hz mono signed 16-bit **little-endian**, at most 4,096 payload bytes per uplink frame. Binary integers are **big-endian**:

| Direction | Frame layout | Meaning |
| --- | --- | --- |
| Device → host | `01 | uint32 sequence | PCM` | Mic audio; sequence starts at 0 per connection. In `ptt`, send `start`, chunks, then `stop`. |
| Host → device | `02 | uint8 item_id_len | uint8 content_index | uint32 sequence | ASCII item_id | PCM` | Speaker audio; output sequence starts at 0 per connection. Play frames in order. |
| Device → host | `03 | 16-byte UUID | JPEG` | One solicited camera frame, only after physical confirmation. JPEG is at most 160,000 bytes. |
| Device → host | `05 | uint32 sequence | uint32 base_ms | uint8 count | samples` | IMU motion batch (motion capability), see Motion below. |
| Device → host | `04 | uint32 sequence | JPEG` | Perception frame (vision capability), only while the host's last `vision_stream` said `active:true`. At most 24,000 bytes; frames above 6 fps or arriving while the previous one is analysed are dropped. |

The host sends `audio_done` after the last model audio chunk for an item. The device sends `{"type":"playback","item_id":"...","finished":true}` **only after the speaker has actually drained that complete item**. On a push-to-talk press, the firmware samples the actual DAC playback cursor, immediately flushes speaker DMA, sends `interrupt` with the active `item_id`, `content_index`, `audio_end_ms` and other `unplayed_item_ids`, then sends `start`. If the board cannot measure the cursor, it reports `audio_end_ms:0` conservatively. It never acknowledges flushed audio. On host `interrupted`, it likewise clears local playback. Downlink audio and controls use a bounded queue; a slow device is disconnected rather than silently dropping speech. The mic rate limit is 96,000 burst bytes with 48,000 bytes/second refill. The client should send about 20 ms of audio (960 bytes) per chunk.

The host also sends `status`, `transcript`, `gesture`, `turn_started`, `response_done`, `error` and `interrupted` controls. A client can display captions and animate expression from these; they are presentation data, not action commands. The host accepts only `configure`, `start`, `stop`, `interrupt`, `playback` and `text` controls from a device. It rejects arbitrary tool results. A model tool runs on the host, with the same grounding and confirmation rules used by browser voice.

The host sends `camera_request` containing a fresh UUID, a 10-second expiry, maximum JPEG size and `requires_local_confirmation:true` only after a grounded current-turn camera request. The firmware must display a one-time consent prompt and require a physical button press before capture. It may ignore or decline the request. Apart from the perception stream below, it must never stream video. The host accepts the JPEG only while that request is outstanding, passes it transiently to the current Realtime conversation and does not save it. Camera capability is omitted by default.

### Perception stream ("Miko sees you")

With the separate `vision` capability (pair with `--vision`; firmware `vision_enabled`), the host sends `{"type":"vision_stream","active":true,"fps":4,"width":240,"height":180,"max_bytes":24000}` while the owner's camera toggle is on, and `{"type":"vision_stream","active":false}` when it is switched off (F7 / camera button). The device then sends small `04` frames and must show a visible camera indicator for as long as it streams. The host analyses frames **in memory only** (`miko_vision.py`: face presence and position, waves, arrivals); frames are never stored, logged or sent to OpenAI. Only derived facts reach Godot, the device (`gesture` cues such as a wave back) and the model (`miko_get_vision`). An unsolicited `04` frame closes the connection. The one-shot camera snapshot above keeps its own request and physical confirmation.

### Motion (IMU)

With the `motion` capability (firmware `motion_enabled`, pairing record includes `motion`), the device sends batches of IMU samples: `05 | uint32 sequence | uint32 base_ms | uint8 count | count × (uint16 offset_ms, int16 ax, ay, az [milli-g], int16 gx, gy, gz [0.1 °/s])`, at most 32 samples per batch and 200 samples per second (excess batches are dropped). The host's `miko_physical.py` turns the stream into one semantic event per physical action — `shake_started` / `shake_active` (at most 1 per second) / `shake_ended`, `device_moved`, `device_nudged`, `orientation_changed` — with a dead zone, smoothing, hysteresis, minimum durations and a refractory period. While the device moves, camera-based waves and room changes are suspended. The Waveshare board's QMI8658 is a suitable source; `miko_board_imu_read` is the board hook. `python -m device.simulator ... --shake-demo` sends a synthetic shake. Motion works with the camera switched off: it is the device feeling itself moved, not sight.

The Waveshare 1.69-inch board has no camera interface, so perception needs an external camera module (for example an ESP32-S3 camera board streaming over the same protocol) or a board revision with a camera connector. Choose and test that hardware before enabling `vision_enabled`.

The host also sends `gesture` cues for owner-requested body actions (`miko_perform_action`), for example `{"type":"gesture","emotion":"","action":"jump","times":3}`. A screen-only device animates what it can (a hop, a wave, a spin).

## Extending actions

New integrations, including WhatsApp if independently configured, belong to the server's authenticated action/tool registry. An adapter should declare its capability and scopes, validate recipient and content, provide a reviewable draft, require a fresh explicit confirmation, and return an idempotent outcome. It must not appear in the model tool list until it is configured and tested. The device receives only presentation cues and PCM; it does not hold third-party account tokens or decide whether a send is authorized.

## Sources

- [Waveshare ESP32-S3 Touch LCD 1.69 board](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.69)
- [Espressif ESP32-S3 I²S driver](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-reference/peripherals/i2s.html)
- [Espressif WebSocket client with WSS certificate verification](https://docs.espressif.com/projects/esp-protocols/esp_websocket_client/docs/latest/index.html)
- [OpenAI Realtime audio input/output](https://developers.openai.com/api/docs/guides/realtime-conversations)

