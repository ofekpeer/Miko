extends SceneTree


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var voice = load("res://tests/voice_backpressure_spy.gd").new()
	root.add_child(voice)
	await process_frame
	voice.set_process(false)
	voice._remote_ready = true
	voice._native_requested = true
	voice._turn_pending = true
	voice._capture_active = false
	var input_pcm := PackedByteArray()
	input_pcm.resize(12000)
	for index in input_pcm.size():
		input_pcm[index] = index % 251
	input_pcm[input_pcm.size() - 2] = 0x64
	input_pcm[input_pcm.size() - 1] = 0x55
	voice._pending_input = input_pcm.duplicate()
	voice._try_start_remote_turn()
	assert(voice.delivered.is_empty())
	assert(voice._outbound_queue.size() == 5) # start, three PCM chunks, stop
	assert(voice._pending_input.is_empty())
	voice._flush_outbound_queue()
	assert(voice._outbound_queue.is_empty())
	assert(voice.delivered.size() == 5)
	assert(voice.delivered[0].get("type") == "start")
	assert(voice.delivered[4].get("type") == "stop")
	var reconstructed := PackedByteArray()
	for index in range(1, 4):
		assert(voice.delivered[index].get("type") == "audio")
		reconstructed.append_array(Marshalls.base64_to_raw(str(voice.delivered[index].get("audio", ""))))
	assert(reconstructed == input_pcm)
	print("VOICE_BACKPRESSURE_PRESERVES_START_AUDIO_STOP_OK")
	voice.queue_free()
	await process_frame
	quit(0)
