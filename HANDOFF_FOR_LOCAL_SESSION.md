# Miko — handoff to a local session (on the owner's Windows PC)

Paste this whole file into a new Claude Code session that runs **locally**, opened
in the Miko folder on the Desktop (the folder with `miko_brain.py`).

## Project
- Miko: a desktop AI companion robot. Godot 4.7 window (`miko-3d/`, robot model,
  room, chat UI) + Python host (`miko_brain.py`, `miko_realtime.py`): OpenAI
  Realtime voice (model gpt-realtime-2.1, voice cedar), push-to-talk on SPACE,
  memory, contacts, Gmail tools with real confirmation.
- Code: GitHub `ofekpeer/miko`, branch `claude/miko-polish-17-3-1`, latest
  version **17.9** (window title "Miko 17.9"). Install from the repo folder with
  `Install Miko Update.cmd`; run with `Start Miko.cmd` on the Desktop.
- Logs: `miko_logs\brain_*.log` and `miko_logs\godot_*.log` next to the
  installed Miko. `Collect Miko Logs.cmd` zips the latest ones.

## Never do
- Never delete/reset `miko_brain_state.json`, `miko_credentials.dat`,
  `miko_integrations.json` (memory, bond, history, encrypted Gmail).
- Never put credentials in prompts, Godot, source, state JSON or logs.
- The model must never claim "נשלח" unless the email executor succeeded.
- Back up before destructive changes.

## Owner's open complaints (most important first)
1. **Camera perception doesn't work on the real PC**: only covering/uncovering
   the lens is noticed. Waves are not noticed; shaking the laptop/screen is not
   noticed. (All detectors pass synthetic + real-photo tests in the cloud, so
   the problem is something about the real webcam/PC.)
2. **Still gets stuck** sometimes during conversation (cause not yet seen in
   real logs).
3. Talks on its own without a reason (17.9 reduced spontaneous speech).
4. Wants the conversation more human; wants the chat UI Apple-like (done in 17.8).

## What to do first on the PC
1. Confirm window title "Miko 17.9".
2. Start Miko, press **F6** ("מה הוא רואה" live view). Top bar shows
   `fast N fps / slow N fps`, camera steady/moving; bottom shows the shake meter
   and wave evidence; green box = face, orange = hands.
3. Read the newest `miko_logs\brain_*.log`: look for `PERCEPTION: sight heartbeat`
   (fps, face_seen, errors), `GESTURE:` (wave decisions + rejection reason),
   `PHYSICAL:` (shake episodes), `RECOVERY:` (anything that failed/was reset).
4. Ask the owner to wave and shake while watching F6 + logs, then tune with real
   data. Useful: `MIKO_CAMERA_FILE=<video>` plays a recorded video as the webcam;
   record the owner's real webcam to a file and replay it while tuning.
5. For "stuck": reproduce, then read the brain + godot logs around that time.

## Architecture of perception (17.9)
- `miko_vision_worker.py`: camera + OpenCV + MediaPipe in a **separate process**
  (JSON lines over stdin/stdout), auto-restarts; `VisionProcess` in
  `miko_vision.py` is the host-side proxy. `MIKO_VISION_INPROCESS=1` = old mode.
- `miko_vision.py` `VisionEngine`: **two lanes** — `process_fast` (cover/light,
  background motion → shake, optical-flow wave, room change) on every frame
  ~30 fps; `analyze_slow`/`apply_slow` (YuNet + MediaPipe faces, expressions,
  hands, hand-landmark waves) in its own thread on the newest frame.
- `miko_physical.py`: shake episodes (shake_started/active/ended, device_moved,
  device_nudged), thresholds in px per 1/15 s; also IMU interpreter for a future
  device (protocol frame 05).
- Camera motion = background only (face/body/hands masked), at least 3 regions
  on both sides of the room must agree; blur fallback for fast shakes.
- `miko_gestures.py`: WaveDetector (hand landmarks via fingertips, or flow blob);
  CONFIRMED at confidence ≥ 0.8 (flow without a seen hand ≥ 0.86).
- `miko_behavior.py`: response level per event (NO_REACTION … FULL_SPOKEN);
  words only for things addressed to Miko; ≤ 1 remark/minute.
- Godot: `characters/robot/behavior_arbiter.gd` (body reaction levels, cooldowns),
  `miko_guardian.gd` (robot heartbeat, state timeouts), chat bubbles in
  `miko_controller.gd`.

## Fixes already made (for context)
- Conversation lines were saved to disk inside the voice loop (could stall or
  drop the voice connection on OneDrive/antivirus Desktops) → background saver.
- One bad API event no longer ends the voice session; waiting states all have
  timeouts (lost request re-asked at 7 s, silent response cancelled at 30 s,
  failed TTS releases the window).
- A big hand near the camera used to look like camera motion (false shake,
  wave suppressed) → hands masked, both room sides must move.
- A single frame error used to kill the camera thread silently → loop survives.
- `Check Miko Camera.cmd` crashed (undefined CHECK_VERSION) → fixed.

## Tests
`python diagnostics/run_all_tests.py --e2e` (Python suites, Godot tests, end-to-end
with injected failures). All passed in the cloud for 17.9.
