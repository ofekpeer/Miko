extends "res://miko_realtime_voice.gd"

var sent_messages: Array[Dictionary] = []


func _ready() -> void:
	pass # No microphone, speaker, or relay connection in this scene test.


func _process(_delta: float) -> void:
	pass


func _send(payload: Dictionary) -> bool:
	sent_messages.append(payload.duplicate(true))
	return true
