extends SceneTree
## Headless behaviour simulation of the robot in the real main scene (preview
## mode: no Brain, audio or credentials). Logs every activity change, where it
## walked, and a mock conversation in the middle.
## Godot console exe: --headless --fixed-fps 30 --path <miko-3d> --script res://tools/robot_sim.gd -- --miko-preview

const SECONDS := 600.0


func _initialize() -> void:
	var world := load("res://main.tscn").instantiate() as Node3D
	get_root().add_child.call_deferred(world)
	await process_frame
	await process_frame
	var robot := world.get_node("MikoScene")
	var t := 0.0
	var last_behavior := ""
	var last_gesture := ""
	var counts: Dictionary = {}
	var travelled := 0.0
	var previous: Vector2 = robot._pos
	var errors := 0
	while t < SECONDS:
		# Mock conversation at 4:00: listen 3 s, think 1.5 s, speak 6 s.
		var state := {}
		if t > 240.0 and t < 243.0:
			state = {"listening": true, "emotion": "curious"}
		elif t >= 243.0 and t < 244.5:
			state = {"thinking": true}
		elif t >= 244.5 and t < 250.5:
			var o := 0.5 + 0.45 * sin(t * 13.0)
			state = {"speaking": true, "open": o, "round": 0.3, "wide": 0.3, "emotion": "happy"}
		robot.manual_state = state
		await process_frame
		t += 1.0 / 30.0
		var b: String = robot._behavior
		if robot._asleep:
			b = "ASLEEP"
		if b != last_behavior:
			print("%6.1fs  %-13s pos=(%.2f, %.2f)" % [t, b, robot._pos.x, robot._pos.y])
			last_behavior = b
			counts[b] = counts.get(b, 0) + 1
		if robot._gesture != last_gesture:
			if robot._gesture != "":
				print("%6.1fs     gesture: %s" % [t, robot._gesture])
			last_gesture = robot._gesture
		travelled += robot._pos.distance_to(previous)
		previous = robot._pos
		if not (is_finite(robot._pos.x) and is_finite(robot._yaw)):
			errors += 1
	print("SUMMARY activities=", counts, " travelled=%.1f m-units nan_frames=%d" % [travelled, errors])
	quit(0)
