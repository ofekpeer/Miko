extends SceneTree
## Diagnose visor artifacts: the same tilted-head pose rendered with
## variations of the screen. --path <miko-3d> --script res://tools/visor_debug.gd -- --miko-preview

const OUTPUT := "res://../character_pipeline/renders/robot_godot"


func _initialize() -> void:
	var target_dir := ProjectSettings.globalize_path(OUTPUT)
	var view := SubViewport.new()
	view.size = Vector2i(720, 840)
	view.own_world_3d = true
	view.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	get_root().add_child.call_deferred(view)
	await process_frame
	var world := load("res://main.tscn").instantiate() as Node3D
	view.add_child(world)
	for i in 20:
		await process_frame
	var robot := world.get_node("MikoScene")
	robot._behavior = "preview"
	robot._behavior_left = 1.0e9
	robot._pos = Vector2(0.0, 0.3)
	robot._yaw = 0.0
	var screen := robot.find_child("RobotVisorScreen", true, false) as MeshInstance3D
	var original: Material = screen.material_override
	var plain := StandardMaterial3D.new()
	plain.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	plain.albedo_color = Color(0.05, 0.3, 0.9)
	for variant in ["normal", "screen_unshaded", "screen_hidden"]:
		screen.visible = variant != "screen_hidden"
		screen.material_override = plain if variant == "screen_unshaded" else original
		for i in 40:
			robot._queue_gesture("stretch", 100.0, 1.0, true)
			robot._gesture_t = 0.55
			await process_frame
		view.get_texture().get_image().save_png(target_dir.path_join("visor_" + variant + ".png"))
		print("VISOR DEBUG ", variant)
	quit(0)
