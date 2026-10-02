extends Node
## End-to-end driver: the real Miko window (main.tscn, real controller and
## voice client) talking to the real host. Simulates push-to-talk turns with
## microphone audio, sometimes barging in while Miko speaks, and reports any
## turn that does not get an answer ("stuck").
## Run via diagnostics/e2e/run_e2e.py.

var turns := 20
var rng := RandomNumberGenerator.new()
var world: Node
var voice: Node
var last_turn_id := ""
var stuck := 0
var answered := 0
var recovered := 0
var faults := false         # failures are injected: a released window counts as recovered
var rapid := false          # bursts of rapid SPACE taps before some turns


func _ready() -> void:
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--turns="):
			turns = int(arg.trim_prefix("--turns="))
		elif arg.begins_with("--seed="):
			rng.seed = int(arg.trim_prefix("--seed="))
		elif arg == "--faults":
			faults = true
		elif arg == "--rapid":
			rapid = true
	world = (load("res://main.tscn") as PackedScene).instantiate()
	add_child(world)
	_run.call_deferred()


func _wait(seconds: float) -> void:
	var until := Time.get_ticks_msec() + int(seconds * 1000.0)
	while Time.get_ticks_msec() < until:
		await get_tree().process_frame


func _state() -> String:
	return JSON.stringify({
		"turn_pending": voice._turn_pending, "capture": voice._capture_active, "finalizing": voice._ptt_finalizing,
		"remote_ready": voice._remote_ready, "start_sent": voice._remote_start_sent,
		"native_requested": voice._native_requested, "speaking": voice._speaker_active,
		"item": voice._current_item_id, "queued": voice._queued_audio_items.size(),
		"pending_frames": voice._pending_frames.size(), "socket": voice._socket_open,
		"status": world.realtime_status_label.text if world.realtime_status_label else "",
	})


func _answer_count(turn_id: String) -> int:
	for slot in world.realtime_transcript_turns:
		if str(slot.get("turn_id", "")) == turn_id:
			return (slot.get("assistant", []) as Array).size()
	return 0


func _run() -> void:
	voice = world.realtime_voice
	voice.turn_started.connect(func(turn_id: String, _n: int, _s: String) -> void: last_turn_id = turn_id)
	# Wait for the local relay.
	var started := Time.get_ticks_msec()
	while not voice._socket_open and Time.get_ticks_msec() - started < 20000:
		await get_tree().process_frame
	print("E2E relay connected: ", voice._socket_open)
	for turn in turns:
		# Sometimes speak again while Miko is still answering (barge-in).
		var barge := rng.randf() < 0.25
		if not barge:
			var idle_until := Time.get_ticks_msec() + 15000
			while voice.is_speaking() and Time.get_ticks_msec() < idle_until:
				await get_tree().process_frame
			await _wait(rng.randf_range(0.2, 2.5))
		if rapid and rng.randf() < 0.5:
			# Rapid SPACE: taps too short to be speech, some with a sliver of audio.
			for tap in rng.randi_range(3, 7):
				voice._begin_ptt()
				await _wait(rng.randf_range(0.03, 0.15))
				if rng.randf() < 0.5:
					var sliver := PackedByteArray()
					sliver.resize(960)
					voice._accept_captured_pcm(sliver)
				voice._end_ptt()
				await _wait(rng.randf_range(0.02, 0.2))
		last_turn_id = ""
		voice._begin_ptt()
		var hold := rng.randf_range(0.6, 2.0)
		var hold_until := Time.get_ticks_msec() + int(hold * 1000.0)
		var next_chunk := Time.get_ticks_msec()
		while Time.get_ticks_msec() < hold_until:
			if Time.get_ticks_msec() >= next_chunk:
				var chunk := PackedByteArray()
				chunk.resize(960)                 # 20 ms of 24 kHz PCM16
				for i in range(0, 960, 2):
					chunk[i] = rng.randi_range(0, 255)
				voice._accept_captured_pcm(chunk)
				next_chunk += 20
			await get_tree().process_frame
		voice._end_ptt()
		var begin := Time.get_ticks_msec()
		var ok := false
		var released := false
		while Time.get_ticks_msec() - begin < 30000:
			await get_tree().process_frame
			if not last_turn_id.is_empty() and _answer_count(last_turn_id) > 0 and not voice.is_speaking() \
					and not voice._turn_pending:
				ok = true
				break
			# Injected failure: the turn has no answer, but the window must
			# come back to a usable state (idle/ready) instead of hanging.
			if faults and Time.get_ticks_msec() - begin > 1500 and not voice.is_speaking() and not voice._turn_pending \
					and str(world.realtime_display_status) in ["idle", "ready"]:
				released = true
				break
		if ok:
			answered += 1
			print("E2E turn %d answered in %d ms%s" % [turn, Time.get_ticks_msec() - begin, " (barge-in)" if barge else ""])
		elif released:
			recovered += 1
			print("E2E turn %d recovered without an answer in %d ms (status=%s)" % [turn, Time.get_ticks_msec() - begin, world.realtime_display_status])
		else:
			stuck += 1
			print("E2E_STUCK turn %d turn_id=%s state=%s" % [turn, last_turn_id, _state()])
			print("E2E transcript:\n", world.realtime_transcript_label.text.right(600))
	print("E2E_SUMMARY answered=%d recovered=%d stuck=%d" % [answered, recovered, stuck])
	get_tree().quit(0 if stuck == 0 else 1)
