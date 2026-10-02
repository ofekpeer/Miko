extends Node
## Scripted walk/turn/gesture sequence for checking motion quality with
## Godot's movie writer (fixed time step, every frame rendered):
## godot --path miko-3d --write-movie out.png --fixed-fps 30 res://tools/robot_walk_clip.tscn -- --miko-preview [--side]

var _robot: Node
var _t := 0.0
var _step := 0
var _side := false
const PLAN := [
	[0.5, "walk", Vector2(1.1, 0.2)],
	[4.5, "walk", Vector2(-0.9, 0.5)],
	[9.5, "face_user", null],
	[10.5, "wave", null],
	[13.5, "walk", Vector2(0.0, -0.4)],
	[17.0, "face_user", null],
	[17.5, "spin", null],
	[21.0, "end", null],
]


func _ready() -> void:
	_side = OS.get_cmdline_user_args().has("--side")
	var world := (load("res://main.tscn") as PackedScene).instantiate()
	add_child(world)
	_robot = world.get_node("MikoScene")
	_robot.manual_state = {"emotion": "happy"}
	# Keep the scripted plan in charge (no autonomous choices mid-clip).
	_robot._behavior_left = 9999.0
	if _side:
		world.get_node("Presence").set_process(false)
		var cam := world.get_node("Camera3D") as Camera3D
		cam.global_position = Vector3(3.2, 1.0, 1.4)
		cam.look_at(Vector3(0.5, 0.45, 1.4))


func _process(delta: float) -> void:
	_t += delta
	_robot._behavior_left = 9999.0
	while _step < PLAN.size() and _t >= PLAN[_step][0]:
		var item: Array = PLAN[_step]
		match item[1]:
			"walk":
				_robot._walk_to(item[2])
				_robot._behavior = "wander"
			"face_user":
				_robot._face_yaw_goal = _robot._yaw_toward(_robot._camera_local())
				_robot._look_user = true
			"wave":
				_robot._queue_gesture("wave", 2.3, 1.0, true)
			"spin":
				_robot._spin_left = TAU
			"end":
				get_tree().quit()
		_step += 1
