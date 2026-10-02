extends SceneTree


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var controller = load("res://miko_controller.gd").new()
	controller.realtime_transcript_label = Label.new()
	controller._on_realtime_turn_started("turn_1", 1, "session_A")
	controller._on_realtime_transcript("assistant", "Answer one", "turn_1", 1, "session_A", "answer_1")
	assert(controller.realtime_transcript_label.text == "אתה: מתמלל…\nמיקו: Answer one")
	controller._on_realtime_turn_started("turn_2", 2, "session_A")
	controller._on_realtime_transcript("user", "Question two", "turn_2", 2, "session_A", "user_2")
	controller._on_realtime_transcript("user", "Question one", "turn_1", 1, "session_A", "user_1")
	controller._on_realtime_transcript("assistant", "Answer one corrected", "turn_1", 1, "session_A", "answer_1")
	assert(controller.realtime_transcript_turns.size() == 2)
	assert(controller.realtime_transcript_turns[0]["turn_id"] == "turn_1")
	assert(controller.realtime_transcript_turns[0]["assistant"].size() == 1)
	assert(controller.realtime_transcript_label.text.begins_with("אתה: Question one\nמיקו: Answer one corrected\nאתה: Question two"))
	print("NATIVE_TRANSCRIPT_ORIGIN_ORDER_OK")
	controller.realtime_transcript_label.free()
	controller.free()
	quit(0)
