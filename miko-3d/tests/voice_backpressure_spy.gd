extends "res://miko_realtime_voice.gd"

var rejected_sends := 1
var delivered: Array[Dictionary] = []

func _setup_audio() -> void:
	pass

func _connect_local_relay() -> void:
	pass

func _transport_is_open() -> bool:
	return true

func _transport_buffered_bytes() -> int:
	return 0

func _transport_send_text(message: String) -> Error:
	if rejected_sends > 0:
		rejected_sends -= 1
		return ERR_OUT_OF_MEMORY
	var parsed = JSON.parse_string(message)
	assert(parsed is Dictionary)
	delivered.append(parsed)
	return OK
