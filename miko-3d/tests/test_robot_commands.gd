extends SceneTree
## Owner voice commands drive the robot body (Realtime tool miko_perform_action
## -> controller -> perform_command). Directions are from the owner's view.
## --headless --fixed-fps 30 --path <miko-3d> --script res://tests/test_robot_commands.gd

var robot: Node3D


func _fail(message: String) -> void:
	print("ROBOT_COMMANDS_FAIL ", message)
	quit(1)


func _frames(count: int) -> void:
	for i in count:
		await process_frame


func _initialize() -> void:
	var world: Node3D = load("res://main.tscn").instantiate()
	world.set_script(null)
	get_root().add_child.call_deferred(world)
	await process_frame
	world.get_node("Presence").setup_stage()
	robot = world.get_node("MikoScene")
	await _frames(10)

	# Walk left / right as the owner sees it (screen left = -X).
	robot._pos = Vector2(0.0, 0.3)
	robot.perform_command("walk_left")
	await _frames(240)
	if robot._pos.x > -0.45:
		_fail("walk_left did not move left: " + str(robot._pos)); return
	var x_left: float = robot._pos.x
	robot.perform_command("walk_right")
	await _frames(300)
	if robot._pos.x < x_left + 0.5:
		_fail("walk_right did not move right: " + str(robot._pos)); return

	# Come here: front of the desk, then face the owner.
	robot._pos = Vector2(0.0, -0.4)
	robot.perform_command("come_here")
	await _frames(330)
	if robot._pos.y < 0.6:
		_fail("come_here did not approach: " + str(robot._pos)); return

	# Step back while still facing the owner.
	var yaw_before: float = robot._yaw
	var y_before: float = robot._pos.y
	robot.perform_command("walk_back")
	await _frames(150)
	if robot._pos.y > y_before - 0.3:
		_fail("walk_back did not step back: " + str(robot._pos)); return
	if absf(wrapf(robot._yaw - yaw_before, -PI, PI)) > 0.6:
		_fail("walk_back turned away instead of backing up"); return

	# Jump three times: three hops, one after another.
	robot.perform_command("jump", 3)
	var hops := 0
	var last := ""
	for i in 200:
		await process_frame
		if robot._gesture == "hop" and last != "hop":
			hops += 1
		last = robot._gesture
	if hops < 1 or robot._gesture_queue.size() > 0:
		_fail("jump x3 did not run all hops (started " + str(hops) + ")"); return

	# Wave hello.
	robot.perform_command("wave")
	await _frames(3)
	if robot._gesture != "wave":
		_fail("wave did not start"); return
	await _frames(90)

	# Sit stays seated even when a conversation starts.
	robot.perform_command("sit")
	robot.manual_state = {"speaking": true, "open": 0.5}
	await _frames(90)
	if robot._sit < 0.9:
		_fail("sit did not hold while speaking: " + str(robot._sit)); return
	robot.manual_state = {}
	robot.perform_command("stand_up")
	await _frames(60)
	if robot._sit > 0.1:
		_fail("stand_up did not stand"); return

	# Stop cancels a walk.
	robot._pos = Vector2(-1.0, 0.3)
	robot.perform_command("walk_right")
	await _frames(20)
	robot.perform_command("stop")
	await _frames(60)
	if robot._walking or robot._pos.x > -0.6:
		_fail("stop did not stop walking: " + str(robot._pos)); return
	# Vision: looks where the owner actually is (viewer's right = +X).
	var plain: Vector3 = robot._camera_local()
	robot.on_vision({"type": "vision", "seen": true, "x": 0.8, "y": 0.0, "size": 0.2})
	if robot._camera_local().x < plain.x + 0.3:
		_fail("vision did not move the attention point to the owner's side"); return
	# The owner waves -> Miko waves back.
	robot._gesture = ""
	robot.on_vision({"type": "vision_event", "event": "wave", "hand": "right"})
	await _frames(2)
	if robot._gesture != "wave":
		_fail("did not wave back"); return
	await _frames(90)
	# Coming back wakes a sleeping Miko.
	robot._asleep = true
	robot.on_vision({"type": "vision_event", "event": "arrived"})
	if robot._asleep:
		_fail("arrival did not wake Miko"); return
	# Without fresh vision data it falls back to the screen camera.
	robot.on_vision({"type": "vision", "seen": false})
	var screen: Vector3 = robot.to_local(robot.get_viewport().get_camera_3d().global_position)
	if robot._camera_local().distance_to(screen) > 0.01:
		_fail("unseen owner should fall back to the screen"); return
	print("ROBOT_COMMANDS_OK pos=", robot._pos)
	quit(0)
