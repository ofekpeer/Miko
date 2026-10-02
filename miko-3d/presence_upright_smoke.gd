extends SceneTree
## Offline pose check for the physical stage. No controller, audio or network.

const DOCK_TOP_Y := 0.152
const STEP := 1.0 / 60.0


func _initialize() -> void:
	var world := load("res://main.tscn").instantiate() as Node3D
	world.set_script(null)
	get_root().add_child.call_deferred(world)
	await process_frame
	var presence := world.get_node("Presence") as Node3D
	presence.setup_stage()
	presence.set_process(false)
	var actor := world.get_node("MikoScene") as Node3D
	var animation := actor.get_node("AnimationPlayer") as AnimationPlayer
	var wheels: Array[MeshInstance3D] = []
	for name_value in ["MikoWheelLTreadMeshNode", "MikoWheelRTreadMeshNode"]:
		wheels.append(actor.find_child(name_value, true, false) as MeshInstance3D)
	animation.play("idle")
	animation.seek(animation.get_animation("idle").length * 0.25, true)
	animation.pause()
	_tick(presence, 120)
	if not _upright(actor) or not _grounded(wheels):
		_fail("Idle pose banks or loses wheel contact.")
		return

	presence.set_listening(true)
	presence.set_emotion("curious")
	_tick(presence, 120)
	if not _upright(actor) or not _grounded(wheels) or absf(rad_to_deg(actor.rotation.x)) > 1.0:
		_fail("Listening forces a body bank or excessive pitch.")
		return

	presence.perform_action("wave")
	var maximum_roll := 0.0
	for frame in range(65):
		presence.call("_process", STEP)
		maximum_roll = maxf(maximum_roll, absf(rad_to_deg(actor.rotation.z)))
	if maximum_roll > 0.9 or not _grounded(wheels):
		_fail("Gesture bank was too large or wheels left the dock.")
		return

	presence.set_listening(false)
	presence.set_emotion("calm")
	presence.set_conversation_active(false)
	_tick(presence, 150)
	if not _upright(actor) or not _grounded(wheels):
		_fail("Gesture did not settle back to an upright resting pose.")
		return
	print("PRESENCE_UPRIGHT_PASS idle/listening/recovery contact max_roll=", maximum_roll)
	quit(0)


func _tick(presence: Node3D, count: int) -> void:
	for frame in range(count):
		presence.call("_process", STEP)


func _upright(actor: Node3D) -> bool:
	return absf(rad_to_deg(actor.rotation.z)) < 0.15


func _grounded(wheels: Array[MeshInstance3D]) -> bool:
	var lowest := INF
	for wheel in wheels:
		for surface in range(wheel.mesh.get_surface_count()):
			var points: PackedVector3Array = wheel.mesh.surface_get_arrays(surface)[Mesh.ARRAY_VERTEX]
			for point in points:
				lowest = minf(lowest, (wheel.global_transform * point).y)
	return absf(lowest - DOCK_TOP_Y) < 0.002


func _fail(reason: String) -> void:
	push_error(reason)
	quit(1)
