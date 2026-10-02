extends SceneTree
## Robustness fuzz: 9000 frames of random, malformed and out-of-order
## perception events, commands, cues and conversation states. The body must
## keep updating (heartbeat), stay finite and never stop in a failing step.
## --headless --fixed-fps 30 --path <miko-3d> --script res://tests/test_robot_fuzz.gd


func _initialize() -> void:
	var world: Node3D = load("res://main.tscn").instantiate()
	get_root().add_child.call_deferred(world)
	await process_frame
	var robot: Node3D = world.get_node("MikoScene")
	var rng := RandomNumberGenerator.new()
	rng.seed = 99
	var kinds := ["wave", "arrived", "left", "approached", "covered", "uncovered", "shaken", "light_changed", "motion",
		"scene_changed", "looked_at_miko", "looked_away", "smiled", "laughing", "yawned", "surprised", "frowned",
		"eyes_closed", "eyes_opened", "winked", "nodded", "shook_head", "tilted_head", "looked_somewhere", "gesture",
		"someone_joined", "someone_left", "shake_started", "shake_active", "shake_ended", "device_moved",
		"device_nudged", "orientation_changed", "bogus"]
	var levels := ["NO_REACTION", "MICRO", "FACIAL", "ANIMATION_ONLY", "SHORT_VOCAL", "FULL_SPOKEN", "", "junk"]
	var last_serial := -1
	var frozen := 0
	var gestures := ["thumbs_up", "thumbs_down", "peace", "open_palm", "pointing", "fist", "love", "", "zzz"]
	var cmds := ["walk_forward", "walk_back", "walk_left", "walk_right", "come_here", "go_away", "turn_around", "spin",
		"jump", "wave", "dance", "nod", "shake_head", "sit", "stand_up", "stretch", "think", "laugh", "look_around",
		"sleep", "wake_up", "stop", "bogus"]
	var cues := ["idle", "look", "bounce", "laugh", "wave", "dance", "sleep", "roll"]
	for frame in 9000:
		var r := rng.randf()
		if r < 0.03:
			var e := {"type": "vision_event", "event": kinds[rng.randi_range(0, kinds.size() - 1)]}
			if rng.randf() < 0.7: e["gesture"] = gestures[rng.randi_range(0, gestures.size() - 1)]
			if rng.randf() < 0.7: e["x"] = rng.randf_range(-3, 3)
			if rng.randf() < 0.5: e["direction"] = ["left", "right", "up", "down", "", "x"][rng.randi_range(0, 5)]
			if rng.randf() < 0.3: e["side"] = "left"
			if rng.randf() < 0.6: e["level"] = levels[rng.randi_range(0, levels.size() - 1)]
			if rng.randf() < 0.3: e["duration"] = rng.randf_range(-1.0, 6.0)
			robot.on_vision(e)
		elif r < 0.06:
			var v := {"type": "vision", "seen": rng.randf() < 0.7}
			if rng.randf() < 0.8: v["x"] = rng.randf_range(-1.5, 1.5)
			if rng.randf() < 0.8: v["y"] = rng.randf_range(-1.5, 1.5)
			if rng.randf() < 0.8: v["size"] = rng.randf_range(0.0, 0.9)
			if rng.randf() < 0.5: v["roll"] = rng.randf_range(-60, 60)
			if rng.randf() < 0.5: v["expression"] = ["smiling", "laughing", "sad", "neutral", "sleepy", "surprised"][rng.randi_range(0, 5)]
			if rng.randf() < 0.5: v["looking"] = rng.randf() < 0.5
			robot.on_vision(v)
		elif r < 0.065:
			robot.perform_command(cmds[rng.randi_range(0, cmds.size() - 1)], rng.randi_range(-2, 9))
		elif r < 0.07:
			world.animation_player.play(cues[rng.randi_range(0, cues.size() - 1)])
		elif r < 0.075:
			robot.manual_state = {"speaking": rng.randf() < 0.5, "listening": rng.randf() < 0.3, "thinking": rng.randf() < 0.2,
				"open": rng.randf(), "round": rng.randf(), "wide": rng.randf(),
				"emotion": ["happy", "sad", "angry", "sleepy", "excited", "curious", "shy", "weird"][rng.randi_range(0, 7)]}
		await process_frame
		if robot.frame_serial == last_serial:
			frozen += 1
		last_serial = robot.frame_serial
		if not (is_finite(robot._pos.x) and is_finite(robot._pos.y) and is_finite(robot._yaw)):
			print("ROBOT_FUZZ_FAIL non-finite body state at frame ", frame)
			quit(1)
			return
	if frozen > 0:
		print("ROBOT_FUZZ_FAIL heartbeat stalled on ", frozen, " frames, last stage ", robot.stage)
		quit(1)
		return
	print("ROBOT_FUZZ_OK clock=", robot._clock, " decisions=", robot.arbiter.decisions)
	quit(0)
