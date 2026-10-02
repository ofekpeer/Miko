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
