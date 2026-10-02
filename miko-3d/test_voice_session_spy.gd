extends "res://miko_realtime_voice.gd"

var sent_messages: Array[Dictionary] = []


func _connect_local_relay() -> void:
	pass


func _drain_microphone() -> void:
	pass


func _send(payload: Dictionary) -> bool:
	sent_messages.append(payload)
	return true
