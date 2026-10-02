extends SceneTree
## Behaviour arbitration (acceptance I, C, D, P): conversation stays primary,
## a long shake is one wobble not twenty, Miko settles back to idle, and
## repeated events do not repeat the same animation.
## --headless --fixed-fps 30 --path <miko-3d> --script res://tests/test_behavior_arbiter.gd

var robot: Node3D


func _fail(message: String) -> void:
	print("BEHAVIOR_ARBITER_FAIL ", message)
	quit(1)


func _frames(count: int) -> void:
	for i in count:
		await process_frame


func _event(kind: String, extra: Dictionary = {}) -> void:
	var event := {"type": "vision_event", "event": kind}
	event.merge(extra, true)
	robot.on_vision(event)


func _initialize() -> void:
	var world: Node3D = load("res://main.tscn").instantiate()
	world.set_script(null)
	get_root().add_child.call_deferred(world)
	await process_frame
	world.get_node("Presence").setup_stage()
	robot = world.get_node("MikoScene")
	robot.reaction_delay_enabled = false
	await _frames(10)

	# I: while the owner is talking, small sensor events never start a gesture.
	robot.manual_state = {"listening": true}
	await _frames(2)
	robot._gesture = ""
	robot._gesture_queue.clear()
	for kind in ["device_nudged", "device_moved", "motion", "scene_changed", "smiled", "looked_away", "nodded"]:
		_event(kind, {"x": 0.4})
		if robot._gesture != "" or not robot._gesture_queue.is_empty():
			_fail("gesture started by %s while the owner was talking" % kind); return
	# A host-level NO_REACTION is respected even for a wave.
	_event("wave", {"level": "NO_REACTION"})
	if robot._gesture != "":
		_fail("NO_REACTION from the host still produced a gesture"); return
	robot.manual_state = {}
	await _frames(2)

	# C: a 3-second shake (start, active x3, end) is one wobble and one recovery.
	robot._gesture = ""
	robot._gesture_queue.clear()
	var wobbles := 0
	var starts := 0
	var last := ""
	_event("shake_started", {"confidence": 0.9, "level": "ANIMATION_ONLY"})
	for i in 90:
		if i % 30 == 15:
			_event("shake_active", {"level": "MICRO"})
		await process_frame
		if robot._gesture != last and robot._gesture != "":
			starts += 1
			if robot._gesture == "wobble":
				wobbles += 1
		last = robot._gesture
	_event("shake_ended", {"duration": 3.0, "level": "ANIMATION_ONLY"})
	if wobbles != 1:
		_fail("long shake produced %d wobbles" % wobbles); return
	if starts > 2:
		_fail("long shake started %d gestures" % starts); return

	# D: after the shake Miko settles back to idle (its own idle life may go on).
	await _frames(300)
	var residue := ["wobble", "shake_head", "laugh", "tilt"]
	if robot._gesture in residue or robot._dizzy_left > 0.0 or robot._plop_left > 0.0 or robot._surprise_left > 0.0:
		_fail("did not settle: gesture=%s dizzy=%.2f plop=%.2f" % [robot._gesture, robot._dizzy_left, robot._plop_left]); return
	for item in robot._gesture_queue:
		if item[0] in residue:
			_fail("shake reaction still queued: " + str(robot._gesture_queue)); return

	# P: ten smiles in a row do not give ten body reactions.
	var reactions := 0
	for i in 10:
		robot._gesture = ""
		robot._gesture_queue.clear()
		_event("smiled")
		if robot._gesture != "":
			reactions += 1
		await _frames(45)
	if reactions > 3:
		_fail("ten smiles gave %d body reactions" % reactions); return
	# Repeated laughs vary their routine.
	var routines := {}
	for i in 4:
		robot._gesture = ""
		robot._gesture_queue.clear()
		robot.arbiter._family_until.clear()
		robot.arbiter._history.clear()
		_event("laughing")
		routines[str(robot._gesture) + "/" + str(robot._gesture_queue)] = true
		await _frames(80)
	if routines.size() < 2:
		_fail("repeated laughs always got the identical routine"); return

	# An idle wave right after greeting for real is suppressed.
	robot.arbiter._family_until.clear()
	_event("wave", {"confidence": 0.95})
	if robot.arbiter.family_ready("greeting", robot._clock):
		_fail("greeting family not marked after a real wave"); return
	print("BEHAVIOR_ARBITER_OK decisions=", robot.arbiter.decisions)
	quit(0)
