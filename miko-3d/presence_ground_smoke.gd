extends SceneTree
## Offline regression for the actual tread vertices and physical dock contact.

const DOCK_TOP_Y := 0.152


func _initialize() -> void:
	var world := load("res://main.tscn").instantiate() as Node3D
	world.set_script(null)
	get_root().add_child.call_deferred(world)
	await process_frame
	var presence := world.get_node("Presence") as Node3D
	presence.setup_stage()
	var actor := world.get_node("MikoScene") as Node3D
	var player := actor.get_node("AnimationPlayer") as AnimationPlayer
	var wheels: Array[MeshInstance3D] = []
	for name_value in ["MikoWheelLTreadMeshNode", "MikoWheelRTreadMeshNode"]:
		wheels.append(actor.find_child(name_value, true, false) as MeshInstance3D)

	for animation_name in ["idle", "look"]:
		var clip := player.get_animation(animation_name)
		for fraction in [0.0, 0.25, 0.5, 0.75, 0.99]:
			player.play(animation_name)
			player.seek(clip.length * fraction, true)
			player.pause()
			for frame in range(3):
				await process_frame
			var lowest := _lowest_vertex_y(wheels)
			if absf(lowest - DOCK_TOP_Y) > 0.001:
				push_error("Ground contact failed for " + animation_name + " at " + str(fraction) + ": " + str(lowest))
				quit(1)
				return

	player.play("bounce")
	player.seek(player.get_animation("bounce").length * 0.5, true)
	player.pause()
	for frame in range(3):
		await process_frame
	if _lowest_vertex_y(wheels) < 0.40:
		push_error("Bounce lift was canceled by ground contact.")
		quit(1)
		return

	player.play("sleep")
	player.seek(player.get_animation("sleep").length * 0.99, true)
	player.pause()
	for frame in range(3):
		await process_frame
	if absf(_lowest_vertex_y(wheels) - DOCK_TOP_Y) > 0.001:
		push_error("Sleep pose penetrated or hovered above dock.")
		quit(1)
		return

	print("PRESENCE_GROUND_PASS idle/look contact, bounce lift, sleep contact")
	quit()


func _lowest_vertex_y(wheels: Array[MeshInstance3D]) -> float:
	var lowest := INF
	for wheel in wheels:
		for surface in range(wheel.mesh.get_surface_count()):
			var vertices: PackedVector3Array = wheel.mesh.surface_get_arrays(surface)[Mesh.ARRAY_VERTEX]
			for point in vertices:
				lowest = minf(lowest, (wheel.global_transform * point).y)
	return lowest
