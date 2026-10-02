class_name MikoRealtimeVoice
extends Node
const MikoLog = preload("res://miko_log.gd")

signal status_changed(status: String, detail: String)
signal transcript(role: String, text: String, turn_id: String, turn_number: int, session_id: String, item_id: String)
signal turn_started(turn_id: String, turn_number: int, session_id: String)
signal final_transcript(item_id: String, session_id: String)
signal speaking_changed(active: bool)
signal tool_result(name: String, result: Variant)
signal external_voice_changed(active: bool)
## Local camera perception from the host: "vision" (where the owner is),
## "vision_event" (wave/arrived/left/approached) and "vision_status".
signal vision_update(event: Dictionary)

const RELAY_URL := "ws://127.0.0.1:5001/voice"
const MIC_BUS := "MikoRealtimeMic"
const VOICE_BUS := "MikoVoice"
const PCM_RATE := 24000
const MAX_PENDING_INPUT_BYTES := 1440000 # Thirty seconds at 24 kHz, mono PCM16.
const RECONNECT_DELAY_MS := 1500
const PLAYBACK_REPORT_INTERVAL_MS := 200
const PTT_RELEASE_GRACE_MS := 250
const SOCKET_INBOUND_BYTES := 2097152
const SOCKET_OUTBOUND_BYTES := 2097152
const SOCKET_OUTBOUND_HIGH_WATER_BYTES := 1572864
const MAX_OUTBOUND_QUEUE_BYTES := 2500000
const OUTBOUND_STALL_MS := 5000
const MAX_OUTBOUND_SENDS_PER_TICK := 32

@export var hands_free_toggle_key: Key = KEY_F8
@export var mute_toggle_key: Key = KEY_F9
@export var hotkeys_enabled := true
@export var relay_url := RELAY_URL

var mode := "ptt"
var microphone_muted := false
var external_voice_active := false
var voice_level := 0.0

var _socket: WebSocketPeer
var _socket_open := false
var _remote_ready := false
var _native_requested := false
var _next_connect_msec := 0
var _previous_space := false
var _pointer_ptt_down := false
var _previous_mode_key := false
var _previous_mute_key := false

var _mic_player: AudioStreamPlayer
var _capture: AudioEffectCapture
var _source_samples := PackedFloat32Array()
var _source_position := 0.0
var _last_source_sample := 0.0
var _has_source_sample := false
var _pending_input := PackedByteArray()
var _capture_active := false
var _turn_pending := false
var _remote_start_sent := false
var _pending_say := ""
var _ptt_finalizing := false
var _ptt_release_due_msec := 0
var _outbound_queue: Array[String] = []
var _outbound_queue_bytes := 0
var _outbound_stalled_since_msec := 0

var _voice_player: AudioStreamPlayer
var _generator: AudioStreamGenerator
var _playback: AudioStreamGeneratorPlayback
var _pending_frames := PackedVector2Array()
var _queued_audio_items: Array[Dictionary] = []
var _speaker_active := false
## Safety net: speech whose completion marker never arrives (a cancelled or
## failed response) must not leave Miko "speaking" forever.
const STALLED_SPEECH_MS := 4000
var _last_audio_msec := 0
var _remote_audio_done := false
var _current_item_id := ""
var _current_content_index := 0
var _frames_submitted := 0
var _frames_played := 0
var _generator_capacity := 0
var _last_playback_report_msec := 0


func _ready() -> void:
	_setup_audio()
	status_changed.emit("idle", "Hold SPACE to talk")
	_connect_local_relay()


func _exit_tree() -> void:
	if _socket != null and _socket.get_ready_state() == WebSocketPeer.STATE_OPEN:
		if _remote_start_sent:
			_send({"type": "stop"})
		_socket.close()


func _process(_delta: float) -> void:
	_poll_relay()
	_handle_hotkeys()
	_drain_microphone()
	_finish_ptt_if_due(Time.get_ticks_msec())
	_feed_playback()
	_report_playback(false)
	_maybe_finish_playback()
	_release_stalled_speech(Time.get_ticks_msec())


func configure(new_mode: String) -> void:
	if new_mode != "ptt" and new_mode != "hands_free":
		return
	if mode == new_mode:
		return
	if mode == "ptt" and _turn_pending:
		_finalize_ptt()
	if _remote_start_sent:
		_send({"type": "stop"})
	_remote_start_sent = false
	_capture_active = false
	_turn_pending = false
	_ptt_finalizing = false
	_pending_input.clear()
	_clear_capture()
	mode = new_mode
	_remote_ready = false
	if _native_requested and _socket_open and not external_voice_active:
		_send({"type": "configure", "mode": mode})
	if mode == "hands_free" and not microphone_muted and not external_voice_active:
		_request_native_session()
		_capture_active = true
		_turn_pending = true
		_try_start_remote_turn()
	status_changed.emit("mode", mode)


func set_microphone_muted(value: bool) -> void:
	if microphone_muted == value:
		return
	if value and mode == "ptt" and _turn_pending:
		_end_ptt(true)
	microphone_muted = value
	if microphone_muted:
		_capture_active = false
		_ptt_finalizing = false
		_pending_input.clear()
		_clear_capture()
		if mode == "hands_free" and _remote_start_sent:
			_send({"type": "stop"})
			_remote_start_sent = false
	else:
		if mode == "hands_free" and not external_voice_active:
			_request_native_session()
			_capture_active = true
			_turn_pending = true
			_try_start_remote_turn()
	status_changed.emit("muted" if microphone_muted else "listening", "Microphone muted" if microphone_muted else "Microphone live")


func is_speaking() -> bool:
	return _speaker_active


func is_listening() -> bool:
	return _capture_active or _turn_pending


func set_pointer_ptt(pressed: bool) -> void:
	_pointer_ptt_down = pressed


func send_text(message: String) -> void:
	if message.strip_edges().is_empty() or external_voice_active:
		return
	_request_native_session()
	if _remote_ready:
		_send({"type": "text", "text": message})
	else:
		status_changed.emit("connecting", "Voice session is starting; try text again when ready")


func report_text_presented(item_id: String, session_id: String) -> void:
	if item_id.is_empty() or session_id.is_empty() or external_voice_active:
		return
	_send({"type": "output_text_presented", "item_id": item_id, "session_id": session_id})


func send_spontaneous(message: String) -> void:
	var clean := message.strip_edges()
	if clean.is_empty() or external_voice_active or _capture_active or _turn_pending or _speaker_active:
		return
	_request_native_session()
	if _remote_ready:
		_send({"type": "say", "text": clean})
	else:
		_pending_say = clean


func interrupt() -> void:
	if external_voice_active:
		return
	var played_ms := _played_milliseconds()
	var unplayed_item_ids: Array[String] = []
	for queued in _queued_audio_items:
		var queued_id := str(queued.get("item_id", ""))
		if not queued_id.is_empty() and not unplayed_item_ids.has(queued_id):
			unplayed_item_ids.append(queued_id)
	if not _current_item_id.is_empty() or not unplayed_item_ids.is_empty() or _speaker_active:
		_send({
			"type": "interrupt",
			"item_id": _current_item_id,
			"content_index": _current_content_index,
			"audio_end_ms": played_ms,
			"unplayed_item_ids": unplayed_item_ids,
		})
	_clear_playback()
	status_changed.emit("interrupted", "Listening")


func _setup_audio() -> void:
	var mic_bus_index := AudioServer.get_bus_index(MIC_BUS)
	if mic_bus_index < 0:
		AudioServer.add_bus()
		mic_bus_index = AudioServer.bus_count - 1
		AudioServer.set_bus_name(mic_bus_index, MIC_BUS)
	_capture = AudioEffectCapture.new()
	_capture.buffer_length = 1.0
	AudioServer.add_bus_effect(mic_bus_index, _capture)
	# A muted bus may stop processing capture entirely. Capture first, then
	# reduce the live monitor signal to an inaudible level.
	var monitor_silencer := AudioEffectAmplify.new()
	monitor_silencer.volume_db = -80.0
	AudioServer.add_bus_effect(mic_bus_index, monitor_silencer)
	AudioServer.set_bus_mute(mic_bus_index, false)
	_mic_player = AudioStreamPlayer.new()
	_mic_player.name = "MikoRealtimeMicrophone"
	_mic_player.stream = AudioStreamMicrophone.new()
	_mic_player.bus = MIC_BUS
	add_child(_mic_player)
	_mic_player.play()

	if AudioServer.get_bus_index(VOICE_BUS) < 0:
		AudioServer.add_bus()
		AudioServer.set_bus_name(AudioServer.bus_count - 1, VOICE_BUS)
	_voice_player = AudioStreamPlayer.new()
	_voice_player.name = "MikoRealtimeSpeaker"
	_voice_player.bus = VOICE_BUS
	add_child(_voice_player)
	_reset_generator()


func _reset_generator() -> void:
	if _voice_player != null and _voice_player.playing:
		_voice_player.stop()
	_playback = null
	_generator = AudioStreamGenerator.new()
	_generator.mix_rate = PCM_RATE
	_generator.buffer_length = 0.25
	_voice_player.stream = _generator


func _connect_local_relay() -> void:
	_socket = WebSocketPeer.new()
	_socket.inbound_buffer_size = SOCKET_INBOUND_BYTES
	_socket.outbound_buffer_size = SOCKET_OUTBOUND_BYTES
	_socket_open = false
	_remote_ready = false
	_next_connect_msec = Time.get_ticks_msec() + RECONNECT_DELAY_MS
	var error := _socket.connect_to_url(relay_url)
	if error != OK:
		status_changed.emit("disconnected", "Local voice relay is unavailable")
	else:
		status_changed.emit("connecting", "Connecting to local voice relay")


func _poll_relay() -> void:
	if _socket == null:
		return
	_socket.poll()
	var state := _socket.get_ready_state()
	if state == WebSocketPeer.STATE_OPEN:
		if not _socket_open:
			_socket_open = true
			status_changed.emit("idle", "Voice relay connected")
			if _native_requested and not external_voice_active:
				_send({"type": "configure", "mode": mode})
		while _socket.get_available_packet_count() > 0:
			var packet := _socket.get_packet()
			if _socket.was_string_packet():
				var event = JSON.parse_string(packet.get_string_from_utf8())
				if event is Dictionary:
					_handle_event(event)
		_flush_outbound_queue()
	elif state == WebSocketPeer.STATE_CLOSED:
		if _socket_open:
			_socket_open = false
			_remote_ready = false
			_remote_start_sent = false
			_outbound_queue.clear()
			_outbound_queue_bytes = 0
			_outbound_stalled_since_msec = 0
			_pending_input.clear()
			_capture_active = false
			_turn_pending = false
			_ptt_finalizing = false
			_clear_capture()
			_clear_playback()
			status_changed.emit("disconnected", "Voice relay disconnected")
		if Time.get_ticks_msec() >= _next_connect_msec:
			_next_connect_msec = Time.get_ticks_msec() + RECONNECT_DELAY_MS
			_connect_local_relay()


func _handle_event(event: Dictionary) -> void:
	match str(event.get("type", "")):
		"ready":
			if str(event.get("stage", "model")) != "model":
				return
			_remote_ready = true
			status_changed.emit("ready", "Voice session ready")
			_try_start_remote_turn()
			if not _pending_say.is_empty():
				if not _capture_active and not _turn_pending and not _speaker_active and not external_voice_active:
					_send({"type": "say", "text": _pending_say})
				_pending_say = ""
		"status":
			status_changed.emit(str(event.get("status", "")), str(event.get("detail", "")))
		"audio":
			if not external_voice_active:
				_receive_audio(event)
		"audio_done":
			_mark_audio_done(event)
		"response_done":
			pass # An audio item finishes only after its own audio_done and local playback.
		"transcript":
			transcript.emit(
				str(event.get("role", "")), str(event.get("text", "")),
				str(event.get("turn_id", event.get("origin_turn_id", ""))),
				int(event.get("turn_number", -1)), str(event.get("session_id", "")),
				str(event.get("item_id", ""))
			)
			if str(event.get("role", "")) == "assistant" and bool(event.get("final", true)) and not external_voice_active:
				final_transcript.emit(str(event.get("item_id", "")), str(event.get("session_id", "")))
		"turn_started":
			turn_started.emit(
				str(event.get("turn_id", "")), int(event.get("turn_number", -1)),
				str(event.get("session_id", ""))
			)
		"speaking":
			if external_voice_active:
				_set_speaking(bool(event.get("active", false)))
		"voice_level":
			if external_voice_active:
				voice_level = clampf(float(event.get("level", 0.0)), 0.0, 1.0)
		"interrupted":
			# The remote VAD can cancel generation, but only this player knows
			# how much of the audio the user actually heard.
			interrupt()
		"external_voice":
			_set_external_voice(bool(event.get("active", false)))
		"tool_result":
			tool_result.emit(str(event.get("name", "")), event.get("result"))
		"vision", "vision_event", "vision_status":
			vision_update.emit(event)
		"error":
			status_changed.emit("error", str(event.get("message", "Voice relay error")))
			if bool(event.get("retryable", false)):
				_remote_ready = false
				_native_requested = false
				_remote_start_sent = false
				_capture_active = false
				_turn_pending = false
				_ptt_finalizing = false
				_pending_input.clear()
				_pending_say = ""
				_outbound_queue.clear()
				_outbound_queue_bytes = 0
				_outbound_stalled_since_msec = 0
				_clear_capture()


func _set_external_voice(active: bool) -> void:
	if external_voice_active == active:
		return
	external_voice_active = active
	if active:
		if _remote_start_sent:
			_send({"type": "stop"})
		_remote_start_sent = false
		_capture_active = false
		_turn_pending = false
		_ptt_finalizing = false
		_pending_input.clear()
		_pending_say = ""
		_outbound_queue.clear()
		_outbound_queue_bytes = 0
		_outbound_stalled_since_msec = 0
		_clear_capture()
		_clear_playback()
		_remote_ready = false
		_native_requested = false
		status_changed.emit("browser", "Browser voice is active")
	else:
		_set_speaking(false)
		voice_level = 0.0
		status_changed.emit("idle", "Press SPACE for native voice")
	external_voice_changed.emit(active)


func _handle_hotkeys() -> void:
	if not hotkeys_enabled:
		return
	# Key-up can be delivered to another app after an Alt-Tab or browser
	# switch. Close the current push-to-talk turn instead of leaving the mic on.
	if not get_window().has_focus():
		if mode == "ptt" and _capture_active and _turn_pending:
			_end_ptt(true)
		_pointer_ptt_down = false
		_previous_space = false
		_previous_mode_key = false
		_previous_mute_key = false
		return
	var space_down := Input.is_key_pressed(KEY_SPACE) or _pointer_ptt_down
	var mode_down := Input.is_key_pressed(hands_free_toggle_key)
	var mute_down := Input.is_key_pressed(mute_toggle_key)
	if mode_down and not _previous_mode_key and not external_voice_active:
		configure("ptt" if mode == "hands_free" else "hands_free")
	if mute_down and not _previous_mute_key and mode == "hands_free":
		set_microphone_muted(not microphone_muted)
	if space_down and not _previous_space and not external_voice_active:
		_begin_ptt()
	elif not space_down and _previous_space and _turn_pending and mode == "ptt":
		_end_ptt()
	_previous_space = space_down
	_previous_mode_key = mode_down
	_previous_mute_key = mute_down


func _begin_ptt() -> void:
	if mode == "hands_free":
		if _speaker_active:
			interrupt()
		return
	if _ptt_finalizing:
		# A quick re-press during the mixer tail belongs to the same utterance.
		_ptt_finalizing = false
		status_changed.emit("listening", "Recording while SPACE is held")
		return
	if _speaker_active:
		interrupt()
	_pending_input.clear()
	_pending_say = ""
	_clear_capture()
	_capture_active = true
	_turn_pending = true
	_ptt_finalizing = false
	_remote_start_sent = false
	_request_native_session()
	_try_start_remote_turn()
	status_changed.emit("listening", "Recording while SPACE is held")


func _end_ptt(immediate := false) -> void:
	if mode != "ptt" or not _turn_pending:
		return
	if not immediate:
		# AudioServer mixes on another thread. Keep the capture ring alive long
		# enough for the microphone samples already in flight to arrive.
		_ptt_finalizing = true
		_ptt_release_due_msec = Time.get_ticks_msec() + PTT_RELEASE_GRACE_MS
		status_changed.emit("finishing", "Finishing the last microphone samples")
		return
	_finalize_ptt()


func _finish_ptt_if_due(now_msec: int) -> void:
	if _ptt_finalizing and now_msec >= _ptt_release_due_msec:
		_finalize_ptt()


func _finalize_ptt() -> void:
	if mode != "ptt" or not _turn_pending:
		_ptt_finalizing = false
		return
	_drain_microphone()
	_flush_resampler_tail()
	_capture_active = false
	_ptt_finalizing = false
	if _remote_ready:
		_try_start_remote_turn()
	else:
		status_changed.emit("connecting", "Finishing voice connection")


func _request_native_session() -> void:
	if external_voice_active:
		return
	if not _native_requested:
		_native_requested = true
		if _socket_open:
			_send({"type": "configure", "mode": mode})
		status_changed.emit("connecting", "Starting voice session")


func _try_start_remote_turn() -> void:
	if not _remote_ready or external_voice_active or not _turn_pending:
		return
	if not _remote_start_sent:
		if not _send({"type": "start"}):
			return
		_remote_start_sent = true
	_flush_pending_input()
	if not _pending_input.is_empty():
		return
	if mode == "ptt" and not _capture_active:
		if not _send({"type": "stop"}):
			return
		_remote_start_sent = false
		_turn_pending = false
		status_changed.emit("thinking", "Miko is responding")


func _drain_microphone() -> void:
	if _capture == null:
		return
	if not _capture_active or external_voice_active or microphone_muted:
		_capture.clear_buffer()
		return
	# The native hands-free mode gates speaker output. Browser WebRTC handles
	# acoustic echo cancellation for real full-duplex conversations.
	if mode == "hands_free" and _speaker_active:
		_capture.clear_buffer()
		return
	var available := _capture.get_frames_available()
	while available > 0:
		var frames := _capture.get_buffer(mini(available, 4096))
		if frames.is_empty():
			break
		for frame in frames:
			_source_samples.append((frame.x + frame.y) * 0.5)
		var bytes := _resample_to_pcm16()
		_accept_captured_pcm(bytes)
		if not _capture_active:
			return
		available = _capture.get_frames_available()


func _accept_captured_pcm(bytes: PackedByteArray) -> void:
	if bytes.is_empty():
		return
	if _remote_ready and _remote_start_sent:
		_send_pcm(bytes)
	else:
		_pending_input.append_array(bytes)
		if _pending_input.size() > MAX_PENDING_INPUT_BYTES:
			_capture_active = false
			_turn_pending = false
			_ptt_finalizing = false
			_pending_input.clear()
			status_changed.emit("error", "Voice connection took too long; please try again")


func _flush_resampler_tail() -> void:
	if not _has_source_sample:
		return
	# The streaming interpolator keeps its last source frame for continuity.
	# Its last input frame can also be skipped by the downsampling step. Repeat
	# that value just enough to include it in the last output interval.
	var step := float(AudioServer.get_mix_rate()) / float(PCM_RATE)
	for index in range(maxi(2, ceili(step) + 1)):
		_source_samples.append(_last_source_sample)
	_accept_captured_pcm(_resample_to_pcm16())
	_source_samples.clear()
	_source_position = 0.0
	_has_source_sample = false


func _resample_to_pcm16() -> PackedByteArray:
	var pcm := PackedByteArray()
	if not _source_samples.is_empty():
		_last_source_sample = _source_samples[_source_samples.size() - 1]
		_has_source_sample = true
	var step := float(AudioServer.get_mix_rate()) / float(PCM_RATE)
	if step <= 0.0:
		return pcm
	while _source_position + 1.0 < float(_source_samples.size()):
		var first := int(_source_position)
		var fraction := _source_position - float(first)
		var sample := lerpf(_source_samples[first], _source_samples[first + 1], fraction)
		var signed_sample := int(roundf(clampf(sample, -1.0, 1.0) * 32767.0))
		pcm.append(signed_sample & 0xFF)
		pcm.append((signed_sample >> 8) & 0xFF)
		_source_position += step
	var consumed := int(floorf(_source_position))
	if consumed > 0:
		_source_samples = _source_samples.slice(consumed)
		_source_position -= float(consumed)
	return pcm


func _send_pcm(bytes: PackedByteArray) -> bool:
	return _send({"type": "audio", "audio": Marshalls.raw_to_base64(bytes)})


func _flush_pending_input() -> void:
	while _pending_input.size() > 0:
		var count := mini(_pending_input.size(), 4800)
		if not _send_pcm(_pending_input.slice(0, count)):
			return
		_pending_input = _pending_input.slice(count)


func _clear_capture() -> void:
	if _capture != null:
		_capture.clear_buffer()
	_source_samples.clear()
	_source_position = 0.0
	_has_source_sample = false


func _release_stalled_speech(now: int) -> void:
	if not _speaker_active or external_voice_active or now - _last_audio_msec < STALLED_SPEECH_MS:
		return
	if not _pending_frames.is_empty():
		return
	if _playback != null and _playback.get_frames_available() < _generator_capacity:
		return                          # still draining what was received
	if not _current_item_id.is_empty():
		_remote_audio_done = true
		_maybe_finish_playback()
	if _speaker_active:
		_clear_playback()
		status_changed.emit("ready", "Listening")


func _receive_audio(event: Dictionary) -> void:
	_last_audio_msec = Time.get_ticks_msec()
	var item_id := str(event.get("item_id", ""))
	var content_index := int(event.get("content_index", 0))
	var bytes := Marshalls.base64_to_raw(str(event.get("audio", "")))
	if bytes.size() % 2 != 0:
		status_changed.emit("error", "Invalid PCM audio chunk")
		return
	var decoded_frames := PackedVector2Array()
	for offset in range(0, bytes.size(), 2):
		var value := int(bytes[offset]) | (int(bytes[offset + 1]) << 8)
		if value >= 32768:
			value -= 65536
		var sample := float(value) / 32768.0
		decoded_frames.append(Vector2(sample, sample))
	if _current_item_id.is_empty():
		_current_item_id = item_id
		_current_content_index = content_index
		_remote_audio_done = false
		_pending_frames.append_array(decoded_frames)
	elif item_id == _current_item_id and content_index == _current_content_index:
		_pending_frames.append_array(decoded_frames)
	else:
		var found := false
		for index in range(_queued_audio_items.size()):
			var queued: Dictionary = _queued_audio_items[index]
			if str(queued.get("item_id", "")) == item_id and int(queued.get("content_index", 0)) == content_index:
				var frames: PackedVector2Array = queued.get("frames", PackedVector2Array())
				frames.append_array(decoded_frames)
				queued["frames"] = frames
				_queued_audio_items[index] = queued
				found = true
				break
		if not found:
			_queued_audio_items.append({
				"item_id": item_id,
				"content_index": content_index,
				"frames": decoded_frames,
				"done": false,
			})
	if not decoded_frames.is_empty():
		if not _speaker_active:
			_set_speaking(true)
			status_changed.emit("speaking", "Miko is speaking")


func _mark_audio_done(event: Dictionary) -> void:
	var item_id := str(event.get("item_id", ""))
	var content_index := int(event.get("content_index", 0))
	if item_id.is_empty():
		if not _queued_audio_items.is_empty():
			var last_index := _queued_audio_items.size() - 1
			var last: Dictionary = _queued_audio_items[last_index]
			last["done"] = true
			_queued_audio_items[last_index] = last
		else:
			_remote_audio_done = true
		return
	if item_id == _current_item_id and content_index == _current_content_index:
		_remote_audio_done = true
		return
	for index in range(_queued_audio_items.size()):
		var queued: Dictionary = _queued_audio_items[index]
		if str(queued.get("item_id", "")) == item_id and int(queued.get("content_index", 0)) == content_index:
			queued["done"] = true
			_queued_audio_items[index] = queued
			return
	# Keep the completion marker if it arrives before the item's audio.
	_queued_audio_items.append({
		"item_id": item_id,
		"content_index": content_index,
		"frames": PackedVector2Array(),
		"done": true,
	})


func _feed_playback() -> void:
	if _pending_frames.is_empty():
		return
	if _playback == null:
		_voice_player.play()
		_playback = _voice_player.get_stream_playback() as AudioStreamGeneratorPlayback
		if _playback == null:
			return
		_generator_capacity = _playback.get_frames_available()
	var free_frames := _playback.get_frames_available()
	if free_frames <= 0:
		return
	var count := mini(free_frames, _pending_frames.size())
	if _playback.push_buffer(_pending_frames.slice(0, count)):
		_pending_frames = _pending_frames.slice(count)
		_frames_submitted += count


func _played_milliseconds() -> int:
	if _playback != null:
		var generator_buffered := maxi(0, _generator_capacity - _playback.get_frames_available())
		_frames_played = maxi(_frames_played, _frames_submitted - generator_buffered)
	return int(float(_frames_played) * 1000.0 / float(PCM_RATE))


func _report_playback(finished: bool) -> void:
	if _current_item_id.is_empty() or not _socket_open:
		return
	var now := Time.get_ticks_msec()
	if not finished and now - _last_playback_report_msec < PLAYBACK_REPORT_INTERVAL_MS:
		return
	_last_playback_report_msec = now
	_send({
		"type": "playback",
		"item_id": _current_item_id,
		"audio_end_ms": _played_milliseconds(),
		"finished": finished,
	})


func _maybe_finish_playback() -> void:
	if _current_item_id.is_empty() or not _remote_audio_done or not _pending_frames.is_empty():
		return
	if _playback != null and _playback.get_frames_available() < _generator_capacity:
		return
	_report_playback(true)
	_reset_current_item()
	if not _queued_audio_items.is_empty():
		_activate_next_item()
	else:
		_set_speaking(false)
		status_changed.emit("ready", "Listening")


func _activate_next_item() -> void:
	var next_item: Dictionary = _queued_audio_items.pop_front()
	_current_item_id = str(next_item.get("item_id", ""))
	_current_content_index = int(next_item.get("content_index", 0))
	_pending_frames = next_item.get("frames", PackedVector2Array())
	_remote_audio_done = bool(next_item.get("done", false))
	_last_playback_report_msec = 0


func _reset_current_item() -> void:
	_reset_generator()
	_pending_frames.clear()
	_current_item_id = ""
	_current_content_index = 0
	_remote_audio_done = false
	_frames_submitted = 0
	_frames_played = 0
	_generator_capacity = 0


func _clear_playback() -> void:
	_reset_current_item()
	_queued_audio_items.clear()
	_set_speaking(false)


func _set_speaking(active: bool) -> void:
	if not active:
		voice_level = 0.0
	if _speaker_active == active:
		return
	_speaker_active = active
	if active and mode == "hands_free":
		_clear_capture()
	speaking_changed.emit(active)


func _send(payload: Dictionary) -> bool:
	if not _transport_is_open():
		return false
	var message := JSON.stringify(payload)
	if _outbound_queue.is_empty() and _transport_buffered_bytes() < SOCKET_OUTBOUND_HIGH_WATER_BYTES:
		if _transport_send_text(message) == OK:
			return true
	return _queue_outbound(message)


func _queue_outbound(message: String) -> bool:
	var length := message.to_utf8_buffer().size()
	if _outbound_queue_bytes + length > MAX_OUTBOUND_QUEUE_BYTES:
		_fail_transport("Voice relay is too slow; please repeat that turn")
		return false
	_outbound_queue.append(message)
	_outbound_queue_bytes += length
	if _outbound_stalled_since_msec == 0:
		_outbound_stalled_since_msec = Time.get_ticks_msec()
	return true


func _flush_outbound_queue() -> void:
	if not _transport_is_open():
		return
	var sent := 0
	while not _outbound_queue.is_empty() and sent < MAX_OUTBOUND_SENDS_PER_TICK:
		if _transport_buffered_bytes() >= SOCKET_OUTBOUND_HIGH_WATER_BYTES:
			break
		var message: String = _outbound_queue[0]
		if _transport_send_text(message) != OK:
			break
		_outbound_queue.pop_front()
		_outbound_queue_bytes -= message.to_utf8_buffer().size()
		_outbound_stalled_since_msec = Time.get_ticks_msec() if not _outbound_queue.is_empty() else 0
		sent += 1
	if not _outbound_queue.is_empty() and Time.get_ticks_msec() - _outbound_stalled_since_msec > OUTBOUND_STALL_MS:
		_fail_transport("Voice relay stalled; please repeat that turn")


func _transport_is_open() -> bool:
	return _socket != null and _socket.get_ready_state() == WebSocketPeer.STATE_OPEN


func _transport_buffered_bytes() -> int:
	return _socket.get_current_outbound_buffered_amount()


func _transport_send_text(message: String) -> Error:
	return _socket.send_text(message)


func _fail_transport(message: String) -> void:
	_outbound_queue.clear()
	_outbound_queue_bytes = 0
	_outbound_stalled_since_msec = 0
	_pending_input.clear()
	_capture_active = false
	_turn_pending = false
	_ptt_finalizing = false
	_remote_start_sent = false
	_remote_ready = false
	_native_requested = false
	_clear_capture()
	status_changed.emit("error", message)
	if _socket != null and _socket.get_ready_state() == WebSocketPeer.STATE_OPEN:
		_socket.close(1011, "Voice transport stalled")


## Deterministic exit from any turn state (watchdog/backstop): stop the
## microphone, drop pending input and playback, and become ready again.
func recover(reason: String) -> void:
	MikoLog.info("RECOVERY", "voice client reset", {"reason": reason, "turn_pending": _turn_pending,
		"capture": _capture_active, "speaking": _speaker_active, "remote_ready": _remote_ready})
	if _remote_start_sent:
		_send({"type": "stop"})
	_remote_start_sent = false
	_capture_active = false
	_turn_pending = false
	_ptt_finalizing = false
	_pending_input.clear()
	_clear_capture()
	_clear_playback()
	status_changed.emit("idle", "Ready")


func set_vision_enabled(enabled: bool) -> void:
	_send({"type": "vision_toggle", "enabled": enabled})
