extends SceneTree

const SESSION := "synthetic-session"
const FULL_CAPTION := "לדנה בכתובת dana@example.com: אני מגיע. לשלוח?"


func _initialize() -> void:
	call_deferred("_run")


func _frames(count: int = 4) -> void:
	for index in range(count):
		await process_frame


func _spawn(size := Vector2i(720, 840)) -> Node3D:
	root.content_scale_size = size
	root.size = size
	var controller: Node3D = load("res://tests/caption_controller_spy.gd").new()
	var animation := AnimationPlayer.new()
	animation.name = "AnimationPlayer"
	controller.add_child(animation)
	root.add_child(controller)
	await _frames()
	return controller


func _remove(controller: Node3D) -> void:
	controller.queue_free()
	await _frames(2)


func _start(voice: Node, number: int, session_id := SESSION) -> void:
	voice._handle_event({
		"type": "turn_started", "turn_id": "turn-%d" % number,
		"turn_number": number, "session_id": session_id,
	})


func _assistant(voice: Node, text: String, item_id: String, number: int,
		final := true, session_id := SESSION) -> void:
	voice._handle_event({
		"type": "transcript", "role": "assistant", "text": text,
		"turn_id": "turn-%d" % number, "turn_number": number,
		"session_id": session_id, "item_id": item_id, "final": final,
	})


func _acks(voice: Node) -> Array[Dictionary]:
	var messages: Array[Dictionary] = []
	for message in voice.sent_messages:
		if message.get("type") == "output_text_presented":
			messages.append(message)
	return messages


func _run() -> void:
	var failures: Array[String] = []
	# The same native signal path as production must wait for final text and
	# actual Label layout. A partial caption is visible but cannot acknowledge.
	var controller := await _spawn()
	var voice: Node = controller.realtime_voice
	_start(voice, 1)
	_assistant(voice, "לדנה בכתובת dana@example.com: אני מג", "answer-1", 1, false)
	await _frames(5)
	assert(_acks(voice).is_empty(), "Partial caption acknowledged a draft")
	_assistant(voice, FULL_CAPTION, "answer-1", 1)
	await _frames(6)
	assert(_acks(voice).size() == 1, "Visible final caption was not acknowledged once")
	assert(_acks(voice)[0].get("session_id") == SESSION)
	assert(_acks(voice)[0].get("item_id") == "answer-1")
	# Duplicate delivery of the same final event cannot renew confirmation.
	_assistant(voice, FULL_CAPTION, "answer-1", 1)
	await _frames(6)
	if _acks(voice).size() != 1:
		failures.append("Duplicate final caption acknowledged twice")
	await _remove(controller)

	# A shorter desktop window still has to show the complete address and body
	# in its actual Label bounds before sending the presentation acknowledgment.
	controller = await _spawn(Vector2i(560, 660))
	voice = controller.realtime_voice
	_start(voice, 1)
	_assistant(voice, FULL_CAPTION, "answer-medium", 1)
	await _frames(6)
	if _acks(voice).size() != 1:
		failures.append("560x660 complete readback remained unacknowledged")
	await _remove(controller)

	# User speech may start while the spoken readback tail drains. The earlier
	# full caption still fits inside the native panel and can be reviewed.
	controller = await _spawn()
	voice = controller.realtime_voice
	_start(voice, 1)
	_assistant(voice, FULL_CAPTION, "old-visible", 1, false)
	await _frames(2)
	_assistant(voice, FULL_CAPTION, "old-visible", 1)
	_start(voice, 2)
	await _frames(6)
	assert(_acks(voice).size() == 1, "Next PTT suppressed a still-visible older caption")
	assert(_acks(voice)[0].get("item_id") == "old-visible")
	await _remove(controller)

	# Scrolling history off the viewport must withhold review acknowledgment.
	controller = await _spawn()
	voice = controller.realtime_voice
	_start(voice, 1)
	_assistant(voice, FULL_CAPTION, "old-offscreen", 1, false)
	for number in range(2, 11):
		_start(voice, number)
		_assistant(voice, "Synthetic answer number %d with enough words to wrap." % number,
			"filler-%d" % number, number, false)
	await _frames(5)
	controller.realtime_transcript_scroll.scroll_vertical = 100000
	await _frames(3)
	assert(controller.realtime_transcript_scroll.scroll_vertical > 0)
	_assistant(voice, FULL_CAPTION, "old-offscreen", 1)
	await _frames(6)
	assert(_acks(voice).is_empty(), "Offscreen old caption acknowledged")
	await _remove(controller)

	# Hidden, compact, or unfocused UI cannot assert that the user reviewed text.
	controller = await _spawn()
	voice = controller.realtime_voice
	controller.realtime_voice_panel.visible = false
	_start(voice, 1)
	_assistant(voice, FULL_CAPTION, "hidden", 1)
	await _frames(6)
	assert(_acks(voice).is_empty(), "Hidden panel acknowledged a caption")
	await _remove(controller)

	controller = await _spawn(Vector2i(240, 280))
	voice = controller.realtime_voice
	assert(controller.realtime_compact_ui)
	_start(voice, 1)
	_assistant(voice, FULL_CAPTION, "compact", 1)
	await _frames(6)
	assert(_acks(voice).is_empty(), "Compact UI acknowledged hidden history")
	await _remove(controller)

	controller = await _spawn()
	voice = controller.realtime_voice
	controller.test_focused = false
	_start(voice, 1)
	_assistant(voice, FULL_CAPTION, "unfocused", 1)
	await _frames(6)
	assert(_acks(voice).is_empty(), "Unfocused UI acknowledged a caption")
	await _remove(controller)

	controller = await _spawn()
	voice = controller.realtime_voice
	_start(voice, 1)
	_assistant(voice, FULL_CAPTION, "session-guard", 1, false)
	voice.final_transcript.emit("session-guard", "wrong-session")
	await _frames(6)
	assert(_acks(voice).is_empty(), "Wrong session acknowledged a caption")
	voice.external_voice_active = true
	voice.final_transcript.emit("session-guard", SESSION)
	await _frames(6)
	assert(_acks(voice).is_empty(), "External browser voice acknowledged native caption")
	await _remove(controller)

	if failures.is_empty():
		print("NATIVE_VISIBLE_CAPTION_ACK_OK")
		quit(0)
	else:
		for failure in failures:
			push_error(failure)
		quit(1)
