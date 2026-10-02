extends "res://miko_controller.gd"

var test_focused := true


func _ready() -> void:
	# Use the production controls, caption callbacks, and Label layout only.
	# Production health polling, audio, and HTTP are deliberately not started.
	realtime_voice = load("res://tests/voice_caption_spy.gd").new()
	realtime_voice.name = "VoiceCaptionSpy"
	realtime_voice.transcript.connect(_on_realtime_transcript)
	realtime_voice.final_transcript.connect(_on_realtime_final_transcript)
	realtime_voice.turn_started.connect(_on_realtime_turn_started)
	_setup_voice_controls()
	add_child(realtime_voice)


func _process(_delta: float) -> void:
	pass


func _input(_event: InputEvent) -> void:
	pass


func _realtime_caption_ui_focused() -> bool:
	return test_focused
