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
	var voice = load("res://test_voice_session_spy.gd").new()
	root.add_child(voice)
	await process_frame
	voice.set_process(false)
	voice._socket_open = true
	voice._begin_ptt()
	assert(_count(voice.sent_messages, "configure") == 1)
	voice._handle_event({"type": "ready", "stage": "model"})
	voice._end_ptt()
	voice._finish_ptt_if_due(voice._ptt_release_due_msec)
	voice._begin_ptt()
	voice._end_ptt()
	voice._finish_ptt_if_due(voice._ptt_release_due_msec)
	assert(_count(voice.sent_messages, "configure") == 1)
	assert(_count(voice.sent_messages, "start") == 2)
	assert(_count(voice.sent_messages, "stop") == 2)
	assert(voice._native_requested)
	assert(voice._remote_ready)
	print("PTT_REUSES_NATIVE_SESSION_OK")
	voice.queue_free()
	await process_frame
	quit(0)
