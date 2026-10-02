extends SceneTree
## Bugs seen in the owner's recording: replies without an owner turn showed an
## empty "אתה: מתמלל…" row per caption update (duplicated Miko lines), and a
## reply whose completion never arrived left Miko "speaking" for good.


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var controller = load("res://miko_controller.gd").new()
	controller.realtime_transcript_label = Label.new()
	controller._on_realtime_turn_started("turn_1", 1, "S")
	controller._on_realtime_transcript("user", "שלום", "turn_1", 1, "S", "u1")
	controller._on_realtime_transcript("assistant", "היי!", "turn_1", 1, "S", "a1")
	# Miko speaks up on its own (vision): its own auto turn, no owner line.
	controller._on_realtime_transcript("assistant", "אוי, חושך", "auto_x", 0, "S", "a2")
	controller._on_realtime_transcript("assistant", "אוי, חושך פתאום!", "auto_x", 0, "S", "a2")
	# Legacy reply without any turn id: partial and final stay in one row.
	controller._on_realtime_transcript("assistant", "הנה", "", 0, "S", "a3")
	controller._on_realtime_transcript("assistant", "הנה אתה!", "", 0, "S", "a3")
	var text: String = controller.realtime_transcript_label.text
	assert(text == "אתה: שלום\nמיקו: היי!\nמיקו: אוי, חושך פתאום!\nמיקו: הנה אתה!", text)
	assert(not text.contains("מתמלל"))
	controller.realtime_transcript_label.free()
	controller.free()

	# Stalled speech: audio stopped arriving and audio_done never came.
	var voice = load("res://miko_realtime_voice.gd").new()
	root.add_child(voice)
	await process_frame
	voice.set_process(false)
	voice._set_speaking(true)
	voice._current_item_id = "item_lost"
	voice._last_audio_msec = 1000
	voice._release_stalled_speech(2000)
	assert(voice.is_speaking())                 # too early to give up
	voice._release_stalled_speech(1000 + voice.STALLED_SPEECH_MS + 1)
	assert(not voice.is_speaking())
	assert(voice._current_item_id.is_empty())
	print("SPONTANEOUS_TRANSCRIPT_AND_STALL_OK")
	quit(0)
