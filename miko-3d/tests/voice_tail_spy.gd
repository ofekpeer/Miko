extends "res://miko_realtime_voice.gd"

var sent_messages: Array[Dictionary] = []
var late_source_samples := PackedFloat32Array()

func _setup_audio() -> void:
	pass

func _connect_local_relay() -> void:
	pass

func _drain_microphone() -> void:
	if late_source_samples.is_empty():
		return
	_source_samples.append_array(late_source_samples)
	late_source_samples.clear()
	_accept_captured_pcm(_resample_to_pcm16())

func _send(payload: Dictionary) -> bool:
	sent_messages.append(payload)
	return true
