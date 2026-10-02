extends SceneTree
## Renders the conversation UI with a sample Hebrew chat to a PNG.
## xvfb-run godot --path . --rendering-driver opengl3 --script res://tools/ui_preview.gd -- out.png

func _initialize() -> void:
	var out := "user://ui_preview.png"
	var args := OS.get_cmdline_user_args()
	if not args.is_empty():
		out = args[0]
	root.size = Vector2i(720, 840)
	var world: Node3D = load("res://main.tscn").instantiate()
	root.add_child(world)
	for i in 30:
		await process_frame
	var sid := "preview"
	var lines := [["turn-1", "user", "היי מיקו, מה קורה?"], ["turn-1", "assistant", "היי! הכל טוב. איך עבר היום?"],
		["turn-2", "user", "סיימתי סוף סוף את הפרויקט"], ["turn-2", "assistant", "יואו, סוף סוף! איך זה מרגיש?"],
		["turn-3", "user", "תכתוב לדנה שאני מגיע בשמונה"],
		["turn-3", "assistant", "לדנה בכתובת dana@example.com: אני מגיע בשמונה. לשלוח?"]]
	var number := 0
	for line in lines:
		if line[1] == "user":
			number += 1
			world._on_realtime_turn_started(line[0], number, sid)
		world._on_realtime_transcript(line[1], line[2], line[0], number, sid, "item-%d-%s" % [number, line[1]])
		await process_frame
	world._on_realtime_status("listening", "")
	for i in 20:
		await process_frame
	var image := root.get_texture().get_image()
	image.save_png(out)
	print("UI_PREVIEW_SAVED ", out)
	quit(0)
