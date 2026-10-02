extends SceneTree
## Two simulated minutes of random perception events, commands and speech:
## Miko must never get stuck (gesture queue drains, commands finish, stays on
## the desk, pose stays finite) and must keep reacting afterwards.
## --headless --fixed-fps 30 --path <miko-3d> --script res://tests/test_robot_soak.gd


func _fail(message: String) -> void:
	print("ROBOT_SOAK_FAIL ", message)
	quit(1)


func _initialize() -> void:
	var world: Node3D = load("res://main.tscn").instantiate()
	world.set_script(null)
	get_root().add_child.call_deferred(world)
	await process_frame
	world.get_node("Presence").setup_stage()
	var robot: Node3D = world.get_node("MikoScene")
	var rng := RandomNumberGenerator.new()
	rng.seed = 1234
	var events := ["wave", "arrived", "left", "approached", "covered", "uncovered", "shaken",
		"light_changed", "motion", "scene_changed", "looked_at_miko", "looked_away"]
	var commands := ["walk_left", "walk_right", "come_here", "jump", "wave", "sit", "stand_up", "spin", "stop", "dance"]
	for frame in 3600:
		if frame % 45 == 0:
			robot.on_vision({"type": "vision", "seen": rng.randf() < 0.8, "x": rng.randf_range(-1, 1),
				"y": rng.randf_range(-0.5, 0.5), "size": rng.randf_range(0.08, 0.4)})
		if rng.randf() < 0.02:
			robot.on_vision({"type": "vision_event", "event": events[rng.randi_range(0, events.size() - 1)],
				"x": rng.randf_range(-1, 1)})
		if rng.randf() < 0.004:
			robot.perform_command(commands[rng.randi_range(0, commands.size() - 1)], rng.randi_range(1, 3))
		if frame % 300 == 0:
			robot.manual_state = {"speaking": rng.randf() < 0.5, "open": rng.randf(), "emotion": "happy"}
		await process_frame
		var p: Vector2 = robot._pos
		if not (is_finite(p.x) and is_finite(p.y)) or absf(p.x) > 1.5 or p.y < -0.75 or p.y > 0.95:
			_fail("left the desk or NaN at frame %d: %s" % [frame, p]); return
		if robot._gesture_queue.size() > 8:
			_fail("gesture queue grows without bound"); return
	# Quiet now: everything must settle within a few seconds.
	robot.manual_state = {}
	robot.on_vision({"type": "vision_event", "event": "uncovered"})
	robot._sit_hold = false
	for frame in 600:
		await process_frame
	if robot._command != "" or not robot._gesture_queue.is_empty() or robot._blind:
		_fail("did not settle: command=%s queue=%d blind=%s" % [robot._command, robot._gesture_queue.size(), robot._blind]); return
	robot.on_vision({"type": "vision_event", "event": "wave"})
	await process_frame
	var waving: bool = robot._gesture == "wave" or robot._gesture_queue.any(func(g): return g[0] == "wave")
	if not waving:
		_fail("stopped reacting after the soak"); return
	print("ROBOT_SOAK_OK")
	quit(0)
