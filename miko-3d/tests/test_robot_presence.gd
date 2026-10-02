extends SceneTree
## Open-stage robot: no glass case, stands on the desk, stays in bounds,
## brain cues are accepted, and the visor mouth follows speech only.
## --headless --fixed-fps 30 --path <miko-3d> --script res://tests/test_robot_presence.gd

func _fail(message: String) -> void:
	push_error("ROBOT_PRESENCE_FAIL " + message)
	print("ROBOT_PRESENCE_FAIL ", message)
	quit(1)


func _initialize() -> void:
	var world: Node3D = load("res://main.tscn").instantiate()
	world.set_script(null)            # no Brain, audio or HTTP
	get_root().add_child.call_deferred(world)
	await process_frame
	var presence: Node3D = world.get_node("Presence")
	presence.setup_stage()
	var robot: Node3D = world.get_node("MikoScene")
	var cues: AnimationPlayer = robot.get_node("AnimationPlayer")
	for cue in ["idle", "look", "bounce", "laugh", "wave", "dance", "sleep", "roll"]:
		if not cues.has_animation(cue):
			_fail("missing cue " + cue); return
	if not presence.open_stage:
		_fail("robot should use the open stage"); return
	if presence.find_child("LeftGlass", true, false) != null or presence.find_child("DockCeramic", true, false) != null:
		_fail("glass case or tall dock still present"); return
	if absf(robot.position.y - presence.DESK_TOP_Y) > 0.001:
		_fail("robot is not standing on the desk"); return
	var face: ShaderMaterial = robot.find_child("RobotVisorScreen", true, false).material_override
	if face == null:
		_fail("visor face shader missing"); return

	# Quiet: wander for a simulated minute, always inside the desk area.
	for i in 1800:
		await process_frame
		var p: Vector2 = robot._pos
		if not (is_finite(p.x) and is_finite(p.y)) or p.x < -1.46 or p.x > 1.46 or p.y < -0.71 or p.y > 0.91:
			_fail("left the desk area at " + str(p)); return
	var quiet_mouth: float = face.get_shader_parameter("mouth_open")
	if quiet_mouth > 0.02:
		_fail("mouth open while silent: " + str(quiet_mouth)); return

	# Speaking: the one drawn mouth opens with the voice level.
	robot.manual_state = {"speaking": true, "open": 0.9, "round": 0.2, "wide": 0.3}
	for i in 30:
		await process_frame
	var speaking_mouth: float = face.get_shader_parameter("mouth_open")
	if speaking_mouth < 0.6:
		_fail("mouth did not open while speaking: " + str(speaking_mouth)); return
	robot.manual_state = {}
	cues.play("wave")
	for i in 20:
		await process_frame
	if robot._gesture != "wave":
		_fail("wave cue was not performed"); return
	print("ROBOT_PRESENCE_OK pos=", robot._pos, " quiet_mouth=", quiet_mouth, " speaking_mouth=", speaking_mouth)
	quit(0)
