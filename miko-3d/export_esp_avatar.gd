extends SceneTree
## Offline export for a 240x280 device display. Run with Godot's console
## executable (normal Forward+ renderer), --path <miko-3d> --script
## res://export_esp_avatar.gd. This explicitly removes the main controller
## script before entering the tree: it never starts audio, HTTP or credentials.

const IMAGE_SIZE := Vector2i(240, 280)
const OUTPUT_RELATIVE := "res://../device/display_assets"


func _initialize() -> void:
	var target_dir := ProjectSettings.globalize_path(OUTPUT_RELATIVE)
	var make_result := DirAccess.make_dir_recursive_absolute(target_dir)
	if make_result != OK:
		push_error("Could not create display asset directory: " + str(make_result))
		quit(1)
		return

	var view := SubViewport.new()
	view.size = IMAGE_SIZE
	view.own_world_3d = true
	view.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	view.msaa_3d = Viewport.MSAA_4X
	get_root().add_child.call_deferred(view)
	await process_frame

	var world := load("res://main.tscn").instantiate() as Node3D
	if world == null:
		push_error("Could not instantiate main.tscn")
		quit(1)
		return
	world.set_script(null)
	view.add_child(world)
	await process_frame
	var presence := world.get_node("Presence") as Node3D
	var actor := world.get_node("MikoScene") as Node3D
	var animation := actor.get_node("AnimationPlayer") as AnimationPlayer
	var eyes_pivot := actor.find_child("MikoEyes", true, false) as Node3D
	var eyes_mesh := actor.find_child("MikoEyesMeshNode", true, false) as MeshInstance3D
	var source_mouth := actor.find_child("MikoMouthMeshNode", true, false) as MeshInstance3D
	var mouth_pivot := actor.find_child("MikoMouth", true, false) as Node3D
	if presence == null or animation == null or eyes_pivot == null or eyes_mesh == null or mouth_pivot == null:
		push_error("Imported Miko hierarchy does not match the display exporter.")
		quit(1)
		return
	presence.setup_stage()
	if source_mouth != null:
		source_mouth.visible = false
	var mouth := _add_display_mouth(mouth_pivot)
	var frames := [
		{"name": "idle", "animation": "idle", "at": 0.25, "emotion": "calm", "mouth": 0.010, "eyes": 1.0, "listening": false, "speaking": false},
		{"name": "listening", "animation": "look", "at": 0.22, "emotion": "curious", "mouth": 0.010, "eyes": 1.05, "listening": true, "speaking": false},
		{"name": "speaking", "animation": "look", "at": 0.35, "emotion": "happy", "mouth": 0.065, "eyes": 1.0, "listening": false, "speaking": true},
		{"name": "happy", "animation": "idle", "at": 0.25, "emotion": "excited", "mouth": 0.035, "eyes": 1.12, "listening": false, "speaking": false},
		{"name": "sleep", "animation": "sleep", "at": 0.35, "emotion": "sleepy", "mouth": 0.009, "eyes": 0.14, "listening": false, "speaking": false},
	]
	var exported: Array[String] = []
	for config in frames:
		var name_value := str(config["name"])
		var animation_name := str(config["animation"])
		if not animation.has_animation(animation_name):
			push_error("Missing imported animation: " + animation_name)
			quit(1)
			return
		animation.stop()
		animation.play(animation_name)
		animation.seek(float(config["at"]), true)
		animation.pause()
		# The look animation can leave the eye pivot compressed when a later
		# animation does not key eyes. Set each exported pose explicitly.
		eyes_pivot.scale = Vector3.ONE
		presence.set_conversation_active(false)
		presence.set_listening(bool(config["listening"]))
		presence.set_speaking(bool(config["speaking"]))
		presence.set_emotion(str(config["emotion"]))
		if name_value == "happy":
			presence.perform_action("happy")
		eyes_mesh.scale = Vector3(1.0, float(config["eyes"]), 1.0)
		mouth.scale = Vector3(0.145, float(config["mouth"]), 0.020)
		for index in range(14):
			await process_frame
		await RenderingServer.frame_post_draw
		if not _save_image(view, target_dir.path_join(name_value + ".png")):
			quit(1)
			return
		exported.append(name_value + ".png")
		if name_value in ["idle", "listening", "speaking"]:
			eyes_mesh.scale.y = 0.08
			for index in range(3):
				await process_frame
			await RenderingServer.frame_post_draw
			if not _save_image(view, target_dir.path_join(name_value + "_blink.png")):
				quit(1)
				return
			exported.append(name_value + "_blink.png")
			eyes_mesh.scale.y = float(config["eyes"])

	var manifest := {
		"width": IMAGE_SIZE.x,
		"height": IMAGE_SIZE.y,
		"format": "PNG RGB8",
		"states": ["idle", "listening", "speaking", "happy", "sleep"],
		"blink_variants": ["idle_blink", "listening_blink", "speaking_blink"],
		"suggested_status_map": {"idle": "idle.png", "ready": "idle.png", "listening": "listening.png", "thinking": "listening.png", "speaking": "speaking.png", "sleep": "sleep.png"},
		"suggested_caption_safe_rect": [8, 242, 224, 30],
		"files": exported,
		"display_note": "ESP display shows these pre-rendered frames; 3D rendering remains on the desktop host.",
	}
	var manifest_file := FileAccess.open(target_dir.path_join("manifest.json"), FileAccess.WRITE)
	if manifest_file == null:
		push_error("Could not write display manifest.")
		quit(1)
		return
	manifest_file.store_string(JSON.stringify(manifest, "  "))
	manifest_file.close()
	print("ESP_AVATAR_EXPORT_OK ", target_dir, " files=", exported.size())
	quit()


func _add_display_mouth(pivot: Node3D) -> MeshInstance3D:
	var mouth := MeshInstance3D.new()
	mouth.name = "ExportMouth"
	var mesh := SphereMesh.new()
	mesh.radius = 0.5
	mesh.height = 1.0
	mesh.radial_segments = 24
	mesh.rings = 12
	mouth.mesh = mesh
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.10, 0.94, 1.0)
	material.emission_enabled = true
	material.emission = Color(0.07, 0.78, 1.0)
	material.emission_energy_multiplier = 1.3
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mouth.material_override = material
	pivot.add_child(mouth)
	return mouth


func _save_image(view: SubViewport, path: String) -> bool:
	var image := view.get_texture().get_image()
	if image == null:
		push_error("Renderer produced no image for " + path)
		return false
	var result := image.save_png(path)
	if result != OK:
		push_error("Could not save " + path + ": " + str(result))
		return false
	return true
