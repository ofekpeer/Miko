extends SceneTree

var clicks := 0


func _initialize() -> void:
	call_deferred("_run")


func _send_space() -> void:
	var down := InputEventKey.new()
	down.keycode = KEY_SPACE
	down.pressed = true
	root.push_input(down)
	var up := InputEventKey.new()
	up.keycode = KEY_SPACE
	up.pressed = false
	root.push_input(up)


func _run() -> void:
	var button := Button.new()
	root.add_child(button)
	button.pressed.connect(func(): clicks += 1)
	button.focus_mode = Control.FOCUS_ALL
	button.grab_focus()
	await process_frame
	assert(button.has_focus())
	_send_space()
	await process_frame
	assert(clicks == 1)
	button.focus_mode = Control.FOCUS_NONE
	button.release_focus()
	await process_frame
	assert(not button.has_focus())
	_send_space()
	await process_frame
	assert(clicks == 1)
	print("SPACE_BUTTON_FOCUS_REGRESSION_OK")
	quit(0)
