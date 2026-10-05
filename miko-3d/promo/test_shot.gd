extends SceneTree
func _initialize() -> void:
	root.size = Vector2i(1280, 720)
	var world := Node3D.new()
	root.add_child(world)
	var env := WorldEnvironment.new()
	var e := Environment.new()
	e.background_mode = Environment.BG_COLOR
	e.background_color = Color(0.89, 0.85, 0.80)
	e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	e.ambient_light_color = Color(0.95, 0.92, 0.88)
	e.ambient_light_energy = 0.6
	e.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	e.glow_enabled = true
	e.ssao_enabled = true
	env.environment = e
	world.add_child(env)
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, 35, 0)
	sun.light_energy = 1.2
	sun.shadow_enabled = true
	world.add_child(sun)
	var floor := MeshInstance3D.new()
	var pm := PlaneMesh.new(); pm.size = Vector2(30, 30)
	floor.mesh = pm
	var fm := StandardMaterial3D.new(); fm.albedo_color = Color(0.88, 0.84, 0.79); fm.roughness = 0.9
	floor.material_override = fm
	world.add_child(floor)
	var robot = load("res://characters/robot/miko_robot.tscn").instantiate()
	world.add_child(robot)
	var cap := MeshInstance3D.new()
	var cm := CapsuleMesh.new(); cm.radius = 0.75; cm.height = 2.6
	cap.mesh = cm
	var gm := ShaderMaterial.new(); gm.shader = load("res://promo/glass.gdshader")
	cap.material_override = gm
	cap.position = Vector3(0, 1.3, 0)
	world.add_child(cap)
	var cam := Camera3D.new()
	cam.position = Vector3(0, 1.4, 4.2)
	cam.fov = 35
	world.add_child(cam)
	cam.look_at(Vector3(0, 1.0, 0))
	for i in 40:
		await process_frame
	var aabb := AABB()
	for m in robot.find_children("*", "MeshInstance3D", true, false):
		aabb = aabb.merge((m as MeshInstance3D).get_aabb() * (m as MeshInstance3D).global_transform) if aabb.size != Vector3.ZERO else (m as MeshInstance3D).global_transform * (m as MeshInstance3D).get_aabb()
	print("ROBOT_AABB ", aabb, " pos ", robot._pos)
	var t := Time.get_ticks_msec()
	await process_frame
	await process_frame
	print("FRAME_MS ", Time.get_ticks_msec() - t)
	root.get_texture().get_image().save_png("/tmp/claude-0/-home-user/5a1a92b1-1c21-517c-bbb3-1b645f82224a/scratchpad/shot_test.png")
	quit(0)
