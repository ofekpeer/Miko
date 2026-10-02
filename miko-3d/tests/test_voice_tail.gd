extends SceneTree


func _initialize() -> void:
	call_deferred("_run")


func _count(messages: Array[Dictionary], kind: String) -> int:
	var total := 0
	for message in messages:
		if message.get("type") == kind:
			total += 1
	return total


func _run() -> void:
	var voice = load("res://tests/voice_tail_spy.gd").new()
	root.add_child(voice)
	await process_frame
	voice.set_process(false)
	voice._native_requested = true
	voice._remote_ready = true
	voice._remote_start_sent = true
	voice._socket_open = true
	voice._capture_active = true
	voice._turn_pending = true
	voice._end_ptt()
	assert(voice._ptt_finalizing)
	assert(voice._capture_active)
	assert(_count(voice.sent_messages, "stop") == 0)
	# These source samples arrive from the mixer after the key is released.
	voice.late_source_samples = PackedFloat32Array([0.1, 0.2, 0.75])
	voice._finish_ptt_if_due(voice._ptt_release_due_msec - 1)
	assert(_count(voice.sent_messages, "stop") == 0)
	voice._finish_ptt_if_due(voice._ptt_release_due_msec)
	assert(not voice._ptt_finalizing)
	assert(not voice._capture_active)
	print("tail message types: ", voice.sent_messages.map(func(message: Dictionary): return message.get("type")))
	assert(voice.sent_messages.size() == 3)
	assert(voice.sent_messages[0].get("type") == "audio")
	assert(voice.sent_messages[1].get("type") == "audio")
	assert(voice.sent_messages[2].get("type") == "stop")
	var final_bytes := Marshalls.base64_to_raw(str(voice.sent_messages[1].get("audio", "")))
	assert(final_bytes.size() >= 2)
	var sample := int(final_bytes[0]) | (int(final_bytes[1]) << 8)
	assert(sample > 20000)

	# A quick re-press during the grace period stays in the same turn.
	voice.sent_messages.clear()
	voice._capture_active = true
	voice._turn_pending = true
	voice._remote_start_sent = true
	voice._end_ptt()
	voice._begin_ptt()
	assert(not voice._ptt_finalizing)
	assert(voice._capture_active)
	assert(_count(voice.sent_messages, "start") == 0)
	assert(_count(voice.sent_messages, "stop") == 0)

	# Physical mute closes immediately, without waiting for the grace timer.
	voice.set_microphone_muted(true)
	assert(not voice._capture_active)
	assert(not voice._ptt_finalizing)
	assert(_count(voice.sent_messages, "stop") == 1)
	print("VOICE_PTT_TAIL_AND_MUTE_OK")
	voice.queue_free()
	await process_frame
	quit(0)
