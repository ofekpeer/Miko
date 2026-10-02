extends SceneTree
## Renders the real main scene with the fox character in several states.
## Godot console exe: --path <miko-3d> --script res://tools/fox_preview.gd -- --miko-preview [--size=WxH]
## Preview mode never connects to the Brain, audio or credentials.

const OUTPUT := "res://../character_pipeline/renders/godot"

const STATES := [
	{"name": "idle", "state": {}},
	{"name": "speaking", "state": {"speaking": true, "open": 0.85, "round": 0.25, "wide": 0.15, "emotion": "happy"}},
	{"name": "speaking_o", "state": {"speaking": true, "open": 0.8, "round": 0.9, "wide": 0.0, "emotion": "curious"}},
	{"name": "listening", "state": {"listening": true, "emotion": "curious"}},
	{"name": "blink", "state": {"blink": 1.0}},
	{"name": "sad", "state": {"emotion": "sad"}},
	{"name": "sleeping", "state": {"sleeping": true, "emotion": "sleepy"}},
	{"name": "laugh", "cue": "laugh", "at": 0.45, "state": {"emotion": "excited"}},
	{"name": "wave", "cue": "wave", "at": 0.5, "state": {"emotion": "happy"}},
]


func _initialize() -> void:
	var size := Vector2i(720, 840)
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--size="):
			var parts := arg.trim_prefix("--size=").split("x")
			size = Vector2i(int(parts[0]), int(parts[1]))
	var target_dir := ProjectSettings.globalize_path(OUTPUT)
	DirAccess.make_dir_recursive_absolute(target_dir)

	var view := SubViewport.new()
	view.size = size
	view.own_world_3d = true
	view.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	view.msaa_3d = Viewport.MSAA_4X
	get_root().add_child.call_deferred(view)
	await process_frame

	var world := load("res://main.tscn").instantiate() as Node3D
	view.add_child(world)
	for i in 30:
		await process_frame
	var fox := world.get_node("MikoScene")
	var cues := fox.get_node("AnimationPlayer") as AnimationPlayer
	for entry in STATES:
		fox.manual_state = entry["state"]
		if entry.has("cue"):
			cues.play(entry["cue"])
			cues.seek(cues.current_animation_length * float(entry["at"]), true)
			cues.pause()
		else:
			cues.play("idle")
		for i in 45:
			await process_frame
		var image := view.get_texture().get_image()
		var path := target_dir.path_join("%s_%dx%d.png" % [entry["name"], size.x, size.y])
		image.save_png(path)
		print("FOX PREVIEW ", path)
	quit(0)
