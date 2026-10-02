extends SceneTree
## Renders the real main scene with the robot in chosen states (preview mode:
## no Brain, audio or credentials).
## Godot console exe: --path <miko-3d> --script res://tools/robot_preview.gd -- --miko-preview [--only=a,b]

const OUTPUT := "res://../character_pipeline/renders/robot_godot"

const SHOTS := [
	{"name": "idle", "state": {}},
	{"name": "listening", "state": {"listening": true, "emotion": "curious"}},
	{"name": "speaking", "state": {"speaking": true, "open": 0.85, "round": 0.2, "wide": 0.25, "emotion": "happy"}},
	{"name": "speaking_o", "state": {"speaking": true, "open": 0.75, "round": 0.95, "wide": 0.0, "emotion": "curious"}},
	{"name": "laugh", "state": {"emotion": "excited"}, "gesture": "laugh", "at": 0.5},
	{"name": "wave", "state": {"emotion": "happy"}, "gesture": "wave", "at": 0.45, "side": 1.0},
	{"name": "think", "state": {"thinking": true}, "gesture": "think", "at": 0.5, "side": -1.0},
	{"name": "sad", "state": {"emotion": "sad"}},
	{"name": "stretch", "state": {}, "gesture": "stretch", "at": 0.55},
	{"name": "walking", "state": {}, "walk": Vector2(1.2, -0.3)},
	{"name": "sitting", "state": {"emotion": "happy"}, "sit": true},
	{"name": "asleep", "state": {}, "sleep": true},
	{"name": "dance", "state": {"emotion": "excited"}, "gesture": "dance", "at": 0.42},
	{"name": "face_closeup", "state": {"emotion": "happy"}, "closeup": true},
]


func _initialize() -> void:
	var only: Array = []
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--only="):
			only = arg.trim_prefix("--only=").split(",")
	var target_dir := ProjectSettings.globalize_path(OUTPUT)
	DirAccess.make_dir_recursive_absolute(target_dir)
	var view := SubViewport.new()
	view.size = Vector2i(720, 840)
	view.own_world_3d = true
	view.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	view.msaa_3d = Viewport.MSAA_4X
	get_root().add_child.call_deferred(view)
	await process_frame
	var world := load("res://main.tscn").instantiate() as Node3D
	view.add_child(world)
	for i in 20:
		await process_frame
	var robot := world.get_node("MikoScene")
	for shot in SHOTS:
		if not only.is_empty() and not only.has(shot["name"]):
			continue
		_reset(robot)
		robot.manual_state = shot["state"]
		if shot.has("walk"):
			robot._walk_to(shot["walk"])
		if shot.has("sit"):
			robot._sit_goal = 1.0
			robot._sit = 1.0
		if shot.has("sleep"):
			robot._asleep = true
			robot._sit_goal = 1.0
			robot._sit = 1.0
		var frames := 70 if shot.has("walk") else 45
		for i in frames:
			if shot.has("gesture"):
				if robot._gesture == "":
					robot._queue_gesture(shot["gesture"], 100.0, float(shot.get("side", 1.0)), true)
				robot._gesture_t = float(shot["at"])
			await process_frame
		if shot.has("closeup"):
			var cam := view.get_camera_3d()
			cam.global_position = robot.focus_point() + Vector3(0.0, 0.05, 1.25)
			cam.look_at(robot.focus_point() + Vector3(0.0, 0.0, 0.0))
			await process_frame
			await process_frame
		var path := target_dir.path_join(str(shot["name"]) + ".png")
		view.get_texture().get_image().save_png(path)
		print("ROBOT PREVIEW ", path)
	quit(0)


func _reset(robot: Node) -> void:
	robot._gesture = ""
	robot._walking = false
	robot._speed = 0.0
	robot._sit = 0.0
	robot._sit_goal = 0.0
	robot._asleep = false
	robot._sleep_walk = false
	robot._pos = Vector2(0.0, 0.3)
	robot._yaw = 0.0
	robot._face_yaw_goal = 0.0
	robot._behavior = "preview"
	robot._behavior_left = 1.0e9
	robot._look_user = true
	robot._spin_left = 0.0
