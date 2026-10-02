# Miko 17.6 verification — believable reactions, no frozen states

## Root causes found and fixed

- **Shake false positives (video 11):** any two image-shift reversals within
  1.4 s (or five jolts) became "shaken", repeatedly, so moving the laptop or
  adjusting the lid made Miko react again and again. Replaced by
  `miko_physical.py`: episodes with dead zone, smoothing, hysteresis, minimum
  durations, sustained (non-decaying) swings, a briskness window, refractory
  period and confidence. One action now gives `shake_started` →
  `shake_active` (≤ 1/s) → `shake_ended`; small movement is `device_nudged` /
  `device_moved`.
- **Camera motion measured on the person:** a head moving in front of a plain
  wall registered as camera motion (and could become a shake). Global motion is
  now measured on the background only (face/body masked, raw and smoothed boxes)
  and only when the background has texture.
- **Hallucinated waves:** a wave fired from 2 flow reversals or 2 wrist swings
  of 6 % frame width, with no openness, height, camera-stability or head-motion
  checks, and the HandAnalyzer also reported a moving open palm as a held
  "open_palm" sign (double reaction). `miko_gestures.py` adds an evidence model
  (POSSIBLE logged only, CONFIRMED ≥ 0.8 with all gates); a moving hand is no
  longer a held sign; while the camera/device moves, waves, room changes and
  head-pose events are suspended.
- **Host waiting states without exits (freeze class):**
  - a refused audio commit left `pending_commits` non-empty for the rest of the
    session, which disabled Miko's own reactions;
  - a spontaneous request that never started was never released;
  - a response that failed without audio left the window on "thinking" until
    the 45 s client backstop;
  - a response that started and then went silent had no exit at all.
  Each now has a deterministic exit and a `RECOVERY` log line.
- **Robot body freeze while audio continues (video 10):** not reproduced
  offline. The plausible mechanism (a robot update step failing every frame, or
  a non-finite pose) is now detected: frame heartbeat with the failing stage
  named in the log plus a reset to idle, and a non-finite-state guard. The real
  cause on the owner's PC will be visible in the log as `RECOVERY: robot update
  stalled stage=...`.
- **Unnatural Hebrew ("איזה נופף חמוד"):** the model echoed Hebrew perception
  notes ("מנופף"). Notes and background facts are now neutral English facts;
  style rules and the concrete counter-example are in the instructions;
  `miko_language.py` flags invented words, canned phrases, sensor narration
  and repeated openers, and recent openers are excluded from the next
  spontaneous reply.
- **"נשלח" without a send:** only prompt-enforced before. A spoken success claim
  without a successful executor result in the last 15 minutes is now logged
  (`EMAIL`) and corrected immediately by a host fact.

## Verified checks (this environment, Linux, Godot 4.7.2 headless)

`python diagnostics/run_all_tests.py --e2e` → **ALL PASSED**:

- Python: realtime tools, realtime host (26), confirm flow, long session,
  device host, vision (24), perception (12), **events (23, acceptance A–H)**,
  **language (10, acceptance O, P, Q)**, device bridge incl. IMU frames.
- Godot: robot commands, presence, soak, **behaviour arbiter (I, C, D, P)**,
  **robot fuzz (9000 frames, heartbeat never stalls)**, **guardian (stall
  recovery, thinking timeout, performance step-down)**, plus the existing
  voice/caption/SPACE gates.
- End-to-end, real host + real Godot window + fake Realtime:
  - 25 turns with random perception events, ~25 % barge-in (J):
    25 answered, 0 stuck;
  - 25 turns with rapid SPACE bursts (N) and injected STT failures (K), lost
    model requests (L) and audio-generation failures (M): 12 answered,
    13 released cleanly, 0 stuck. In the faults-only run every injected TTS
    failure released the window, lost requests were re-asked and answered,
    and STT failures were still answered.
- One host-suite run out of ~14 repetitions failed once and could not be
  reproduced afterwards; treat it as a possible pre-existing timing flake.

## Not verified

- Nothing was run on the owner's Windows PC, webcam, GPU or microphone.
  Thresholds (shake px/frame, wave amplitude, texture) were tuned on synthetic
  video; real-room tuning may be needed — the new logs show the measured
  values and the reason for every decision.
- The real OpenAI model's Hebrew was not re-sampled in this release; the
  language checks are heuristics run on sample lines and on live transcripts.
- Firmware IMU batching was not compiled (no ESP-IDF here) or run on a board.

# Miko 17.2 verification

## Confirmation loop

The native UI previously waited for the complete spoken readback to finish.
Pressing SPACE could cancel its playback association before that callback,
although the complete draft was already visible. A subsequent send was then
rejected with `readback_required`.

- Native UI now acknowledges the final caption after actual Label layout.
  Both ends must fit within the visible transcript viewport. Hidden, partial,
  scrolled offscreen, compact, unfocused, browser-owned and wrong-session captions
  cannot authorize presentation. Duplicate final events produce one acknowledgment.
- The host validates the exact final assistant item, originating session and
  matching current draft. A bounded five-second interrupted-caption record lets
  a still-visible acknowledgment arrive just after the next PTT start.
  A stale audio-finished event alone cannot authorize an interrupted readback.
- Sending still requires a fresh owner confirmation after the complete address
  and body were presented. Changing the draft invalidates its previous readback.
  The actual available owner transcript takes precedence over a model-selected
  quote. An unrelated owner question cannot be replaced with an invented yes.
- No SMTP action is inferred from conversational text. The send tool must return
  a confirmed success; duplicate/concurrent calls remain covered.

## Verified checks

- **67 Python tests passed:** 26 Realtime lifecycle, 19 deterministic tools,
  16 device-host, 3 confirmation flow, 3 local WSS integration tests.
- Native full-chain test drives start/audio/stop, model function output, visible
  readback and next PTT confirmation through the actual host event handlers.
  It sends once through fake SMTP. Readback/confirmation tests fail on the
  previous implementation and pass with this change.
- Godot 4.7.2 actual Label/ScrollContainer test:
  `NATIVE_VISIBLE_CAPTION_ACK_OK`. Includes 720x840 and 560x660 Hebrew readback,
  partial/final/duplicate events, next-turn timing, offscreen history, compact
  240x280, hidden panel, focus, session and external-voice guards.
- Existing native gates passed:
  `VOICE_PTT_TAIL_AND_MUTE_OK`,
  `VOICE_BACKPRESSURE_PRESERVES_START_AUDIO_STOP_OK`,
  `VOICE_TRANSCRIPT_DRAINS_WITHOUT_NEXT_PRESS_OK`,
  `SPACE_BUTTON_FOCUS_REGRESSION_OK`,
  `PTT_REUSES_NATIVE_SESSION_OK`, `NATIVE_TRANSCRIPT_ORIGIN_ORDER_OK`.
- Browser gates: `BROWSER_LATE_STT_ORDER_OK`, `BROWSER_STREAMED_CAPTIONS_OK`.
  The browser source is unchanged in this release.
- Live OpenAI Realtime check used isolated synthetic state and a fake SMTP
  function. The real model prepared a draft, read back its address and body,
  then called `miko_send_email` successfully after one "תשלח". One fake SMTP call,
  zero real emails, one API session, no audio-finished acknowledgment.

## Background and posture

- More centered camera; sustained listening/curiosity roll removed. Small
  gestures return to upright. Actual scene tests passed:
  `PRESENCE_UPRIGHT_PASS`, `PRESENCE_GROUND_PASS`, `PRESENCE_SMOKE_PASS`.
  Maximum tested gesture roll was 0.369 degrees; idle/listening remained upright.
- The room now contains plaster, wood ribs, shelf, plant, books, wall light and
  relief art. Removing the rear refractive glass sheet fixed a large white flare;
  side glass and frame remain.
- Desktop 720x840 and compact 240x280 previews were rendered with Forward+ D3D12
  and visually inspected. Eight device display states were re-exported from the
  actual scene as RGB PNGs at 240x280.

## Scope and limits

The configured desktop voice model remains `gpt-realtime-2.1`, voice `cedar`.
The prior microphone tail, bounded WebSocket queue, streamed captions, memory
preservation, address correction and topic-switch behavior remain in place.
Physical microphone quality still requires owner use; no owner microphone was
recorded by these tests. The robot is the supplied stylized GLB with improved
rendering and motion, not a newly sculpted photorealistic model.

No ESP board was available. ESP-IDF was unavailable, so the firmware core was
not compiled/flashed. `miko_board_stub.c` is inert; actual board drivers, external
microphone/speaker/camera, AEC, PNG decoding, power and pin budget require physical
integration and measurement. No cloud deployment or WhatsApp connection was made.

No real email was sent during testing. Personal state, credentials, videos and
screenshots are excluded from this release. Installation makes a recoverable
backup and checks protected file integrity before replacing code.

## Installed runtime verification

Installed and launched Brain `AUTONOMY-17.2-CONFIRMATION-HOME`. Health is OK,
Gmail remains configured, and the native scene/voice relay started without
script errors. All 24 pre-install memories,
228 conversation records,
112 voice history records and
4 email history records remain intact.
Credential/integration/pairing hashes were preserved and installed core,
controller, voice and presence sources match the release. A full recoverable
backup was created before replacing code.
