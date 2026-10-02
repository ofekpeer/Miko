extends SceneTree
## The guardian gives every stuck state a deterministic exit, with a log line.


func _fail(message: String) -> void:
	print("GUARDIAN_FAIL ", message)
	quit(1)


func _wait(seconds: float) -> void:
	var until := Time.get_ticks_msec() + int(seconds * 1000)
	while Time.get_ticks_msec() < until:
		await process_frame


func _initialize() -> void:
	var world: Node3D = load("res://main.tscn").instantiate()
	get_root().add_child.call_deferred(world)
	await process_frame
	await _wait(0.5)
	var guardian: Node = world.get_node("MikoGuardian")
	var robot: Node3D = world.get_node("MikoScene")
	# 1) A failing update step: the heartbeat stops -> recover() -> idle.
	robot._gesture = "dance"
	robot._command = "dance"
	robot.debug_halt = true
	await _wait(2.6)
	robot.debug_halt = false
	if robot._gesture != "" or robot._command != "":
		_fail("stalled robot was not recovered"); return
	var serial: int = robot.frame_serial
	await _wait(0.3)
	if robot.frame_serial == serial:
		_fail("heartbeat did not resume"); return
	# 2) "thinking" that never ends is released.
	guardian.state_limits = {"thinking": 1.0, "connecting": 1.0, "finishing": 1.0}
	world.realtime_display_status = "thinking"
	await _wait(2.2)
	if world.realtime_display_status == "thinking" or world.user_turn_active:
		_fail("stuck thinking state was not released"); return
	# 3) Slow frames lower render quality instead of looking frozen.
	guardian._frame_ms_avg = 120.0
	guardian._slow_since = guardian._now() - 4.0
	guardian._check_performance()
	if guardian._quality_level != 1:
		_fail("performance guard did not step down"); return
	print("GUARDIAN_OK")
	quit(0)
