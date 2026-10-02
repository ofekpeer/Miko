extends SceneTree


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var listener := TCPServer.new()
	assert(listener.listen(0, "127.0.0.1") == OK)
	var voice = load("res://tests/voice_relay_no_audio.gd").new()
	voice.relay_url = "ws://127.0.0.1:%d/voice" % listener.get_local_port()
	var received: Array[String] = []
	voice.transcript.connect(func(role: String, text: String, _turn_id: String, _turn_number: int, _session_id: String, _item_id: String):
		if role == "user":
			received.append(text)
	)
	root.add_child(voice)
	var server_peer: WebSocketPeer
	for tick in range(300):
		if server_peer == null and listener.is_connection_available():
			server_peer = WebSocketPeer.new()
			assert(server_peer.accept_stream(listener.take_connection()) == OK)
		if server_peer != null:
			server_peer.poll()
			if server_peer.get_ready_state() == WebSocketPeer.STATE_OPEN:
				break
		await process_frame
	assert(server_peer != null)
	assert(server_peer.get_ready_state() == WebSocketPeer.STATE_OPEN)
	assert(server_peer.send_text(JSON.stringify({
		"type": "transcript", "role": "user", "text": "סוף המשפט הגיע",
		"turn_id": "turn_1", "turn_number": 1, "session_id": "session_1", "item_id": "item_1",
	})) == OK)
	# No push-to-talk key, new turn, or extra client request occurs here.
	for tick in range(300):
		server_peer.poll()
		if not received.is_empty():
			break
		await process_frame
	assert(received == ["סוף המשפט הגיע"])
	assert(not voice._turn_pending)
	print("VOICE_TRANSCRIPT_DRAINS_WITHOUT_NEXT_PRESS_OK")
	voice.queue_free()
	server_peer.close()
	listener.stop()
	await process_frame
	quit(0)
