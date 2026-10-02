extends Node3D
## Miko robot: a free-roaming companion with a live visor face.
##
## main.tscn instances this scene as "MikoScene" (Model = RobotMiko.glb). The
## controller and Presence keep owning voice, memory and the room; every frame
## this script reads their state, decides what Miko wants to do, walks the body
## around the desk and animates skeleton + visor face procedurally.
##
## Behaviour model (deliberately not a fixed loop):
## * Conversation pulls attention: turn to the user, come closer, greet with a
##   wave after an absence, listen attentively, gesture while speaking.
## * Alone, Miko picks its own activities by weighted chance shaped by mood,
##   cooldowns and recent history (never the same thing twice in a row), with
##   randomised timing and amplitude, and gets drowsy over long silences.
## * Brain cues (wave, laugh, dance, bounce, roll, sleep, look) map to gestures.

const FACE_SHADER := preload("res://characters/robot/robot_face.gdshader")
const BODY_SHADER := preload("res://characters/robot/robot_body.gdshader")
const CUES := {
	"idle": 4.0, "look": 2.2, "bounce": 1.3, "laugh": 1.8,
	"wave": 2.0, "dance": 3.2, "sleep": 2.0, "roll": 1.6,
}
const RIGHT := Vector3(1, 0, 0)
const UP := Vector3(0, 1, 0)
const FORWARD := Vector3(0, 0, 1)
# Desk area this body may walk on (MikoScene local units).
const STAGE_MIN := Vector2(-1.45, -0.70)
const STAGE_MAX := Vector2(1.45, 0.90)
const HOME := Vector2(0.0, 0.30)          # where it likes to chat
const DOCK := Vector2(-1.05, -0.35)       # charging pad: rest and sleep
const WALK_SPEED := 0.34
const TURN_RATE := 2.0
# Leg length in stage units (hip height 0.194 x model scale 1.3) and the peak
# thigh swing; one gait cycle (two steps) covers 4 * LEG * sin(swing), so the
# feet plant instead of sliding.
const LEG := 0.25
const THIGH_SWING := 0.42
# weight, cooldown seconds
const BEHAVIORS := {
	"idle_pause": [3.0, 0.0], "wander": [2.6, 4.0], "look_around": [1.6, 9.0],
	"look_at_user": [1.4, 12.0], "stretch": [0.6, 55.0], "inspect_hand": [0.6, 45.0],
	"little_dance": [0.3, 120.0], "turn_around": [0.4, 60.0], "wave_user": [0.22, 200.0],
	"sit_rest": [0.45, 80.0], "hum_bob": [0.6, 30.0],
}

## State overrides for previews/tests (same keys as _read_state()).
var manual_state: Dictionary = {}
## Animated by the cue clips; only used to give them a track.
var cue_phase := 0.0

var _model: Node3D
var _skeleton: Skeleton3D
var _face: ShaderMaterial
var _bones: Dictionary = {}
var _cue_player: AnimationPlayer
var _controller: Node
var _presence: Node
var _rng := RandomNumberGenerator.new()
var _clock := 0.0
var _materials_ready := false

var _pos := HOME
var _yaw := 0.0
var _target := HOME
var _walking := false
var _speed := 0.0
var _phase := 0.0
var _step_amp := 0.0
var _yaw_vel := 0.0
var _lean := 0.0
var _face_yaw_goal := 0.0
var _spin_left := 0.0
var _turn_hold := 0.0

var _behavior := "idle_pause"
var _behavior_left := 2.0
var _history: Array[String] = []
var _cooldowns: Dictionary = {}
var _look_point := Vector3(0.0, 0.9, 5.0)
var _look_user := true
var _look_wait := 0.0
var _last_engaged := 0.0           # startup counts as a fresh encounter
var _was_engaged := false
var _thought_this_turn := false
var _linger := 0.0
var _sit := 0.0
var _sit_goal := 0.0
var _asleep := false
var _sleep_walk := false
var _sleep_left := 0.0

var _gesture := ""
var _gesture_t := 0.0
var _gesture_len := 1.0
var _gesture_side := 1.0
var _gesture_amp := 1.0
var _beat_wait := 1.0
var _last_cue := ""

var _head := Vector2.ZERO
var _head_vel := Vector2.ZERO
var _saccade := Vector2.ZERO
var _saccade_wait := 1.0
var _own_blink := 0.0
var _own_blink_wait := 3.0
var _speak_energy := 0.0
var _face_now: Dictionary = {}


func _ready() -> void:
	_rng.randomize()
	_model = get_node_or_null("Model") as Node3D
	var skeletons := find_children("*", "Skeleton3D", true, false)
	if skeletons.is_empty():
		push_error("MIKO ROBOT: Skeleton3D not found in RobotMiko.glb")
		return
	_skeleton = skeletons[0] as Skeleton3D
	for index in _skeleton.get_bone_count():
		_bones[_skeleton.get_bone_name(index)] = index
	var screen := find_child("RobotVisorScreen", true, false) as MeshInstance3D
	if screen != null:
		_face = ShaderMaterial.new()
		_face.shader = FACE_SHADER
		screen.material_override = _face
	_create_cue_player()
	_controller = get_parent()
	if _controller != null:
		_presence = _controller.get_node_or_null("Presence")
	_behavior_left = _rng.randf_range(1.0, 3.0)
	print("MIKO ROBOT: character ready (", _bones.size(), " bones, live face ", _face != null, ")")


# Presence hooks: walk freely on an open desk, follow with the camera.
func wants_open_stage() -> bool:
	return true


func focus_point() -> Vector3:
	if _model == null:
		return global_position
	return _model.global_transform * Vector3(0.0, 0.55, 0.0)


func _create_cue_player() -> void:
	_cue_player = get_node_or_null("AnimationPlayer") as AnimationPlayer
	if _cue_player != null:
		return
	var library := AnimationLibrary.new()
	for cue_name in CUES:
		var clip := Animation.new()
		clip.length = CUES[cue_name]
		var track := clip.add_track(Animation.TYPE_VALUE)
		clip.track_set_path(track, NodePath(".:cue_phase"))
		clip.track_insert_key(track, 0.0, 0.0)
		clip.track_insert_key(track, clip.length, 1.0)
		library.add_animation(cue_name, clip)
	_cue_player = AnimationPlayer.new()
	_cue_player.name = "AnimationPlayer"
	add_child(_cue_player)
	_cue_player.add_animation_library("", library)


# ------------------------------------------------------------------ state

func _num(value: Variant) -> float:
	return float(value) if (value is float or value is int) else 0.0


func _read_state() -> Dictionary:
	var state := {
		"speaking": false, "listening": false, "thinking": false, "sleeping": false,
		"emotion": "happy", "blink": 0.0, "open": 0.0, "round": 0.0, "wide": 0.0,
		"cue": "idle", "cue_t": 0.0,
	}
	var c := _controller
	if c != null and c.has_method("_miko_is_currently_speaking"):
		state["speaking"] = bool(c.call("_miko_is_currently_speaking"))
		state["blink"] = _num(c.call("_blink_amount"))
		state["open"] = _num(c.get("lip_open_amount"))
		state["round"] = _num(c.get("lip_round_amount"))
		state["wide"] = _num(c.get("lip_wide_amount"))
		state["sleeping"] = c.get("sleeping_pose") == true
		state["thinking"] = str(c.get("realtime_display_status")) == "thinking"
		var emotion: Variant = c.get("face_emotion")
		if emotion is String and not emotion.is_empty():
			state["emotion"] = emotion
	if _presence != null:
		state["listening"] = _presence.get("listening") == true
	if _cue_player != null:
		var cue_name := String(_cue_player.current_animation)
		if cue_name.is_empty():
			cue_name = String(_cue_player.assigned_animation)
		if not cue_name.is_empty() and _cue_player.has_animation(cue_name):
			var length := maxf(_cue_player.get_animation(cue_name).length, 0.001)
			var position := _cue_player.current_animation_position
			if _cue_player.is_playing() or position < length - 0.001:
				state["cue"] = cue_name
				state["cue_t"] = clampf(position / length, 0.0, 1.0)
	state.merge(manual_state, true)
	state["engaged"] = state["listening"] or state["speaking"] or state["thinking"]
	return state


# ------------------------------------------------------------------ frame

func _process(delta: float) -> void:
	if _skeleton == null:
		return
	if not _materials_ready:
		_apply_materials()
		_materials_ready = true
	_clock += delta
	var state := _read_state()
	_update_engagement(delta, state)
	_update_cues(state)
	_update_behavior(delta, state)
	_update_locomotion(delta)
	if _gesture != "":
		_gesture_t += delta / _gesture_len
		if _gesture_t >= 1.0:
			_gesture = ""
	_compose_pose(delta, state)
	_update_face(delta, state)


# ------------------------------------------------------------------ attention / conversation

func _update_engagement(delta: float, state: Dictionary) -> void:
	var engaged: bool = state["engaged"]
	if engaged and not _was_engaged:
		_on_conversation_start()
	if engaged:
		_last_engaged = _clock
		_linger = _rng.randf_range(4.0, 8.0)
		if state["listening"]:
			_thought_this_turn = false
	elif _was_engaged:
		_behavior = "linger"
		_behavior_left = _linger
	_was_engaged = engaged
	if state["sleeping"] and not _asleep and not engaged:
		_go_to_sleep()


func _on_conversation_start() -> void:
	var away := _clock - _last_engaged
	_asleep = false
	_sleep_walk = false
	_sit_goal = 0.0
	_behavior = "converse"
	# Come over if it wandered off; otherwise just turn around to face you.
	if _pos.distance_to(HOME) > 0.7:
		_walk_to(HOME + Vector2(_rng.randf_range(-0.35, 0.35), _rng.randf_range(-0.1, 0.15)))
	else:
		_walking = false
	if away > 90.0:
		_queue_gesture("wave", 2.3, 0.0, true)


func _camera_local() -> Vector3:
	var camera := get_viewport().get_camera_3d()
	if camera == null:
		return Vector3(0.0, 1.0, 5.0)
	return to_local(camera.global_position)


func _yaw_toward(point: Vector3) -> float:
	return atan2(point.x - _pos.x, point.z - _pos.y)


func _update_cues(state: Dictionary) -> void:
	var cue: String = state["cue"]
	if cue == _last_cue:
		return
	_last_cue = cue
	match cue:
		"wave":
			_queue_gesture("wave", 2.3, 0.0, true)
		"laugh":
			_queue_gesture("laugh", 1.9, 0.0, true)
		"bounce":
			_queue_gesture("hop", 1.25, 0.0, true)
		"dance":
			_queue_gesture("dance", 3.6, 0.0, true)
		"roll":
			_spin_left = TAU * (1.0 if _rng.randf() < 0.5 else -1.0)
		"sleep":
			_go_to_sleep()
		"look":
			if not state["engaged"]:
				_queue_gesture("glance", 2.0)


# ------------------------------------------------------------------ behaviour

func _update_behavior(delta: float, state: Dictionary) -> void:
	_look_wait -= delta
	if state["engaged"]:
		_converse(delta, state)
		return
	if _asleep:
		_look_user = false
		_sleep_left -= delta
		# A nap ends by itself after a while, unless the Brain asked for sleep.
		if _sleep_left <= 0.0 and not state["sleeping"]:
			_asleep = false
			_sit_goal = 0.0
			_cooldowns["nap"] = _clock + _rng.randf_range(300.0, 600.0)
			_queue_gesture("stretch", 3.4)
			_behavior = "idle_pause"
			_behavior_left = 4.0
		return
	if _sleep_walk:
		if not _walking:
			_sleep_walk = false
			_asleep = true
			_sleep_left = _rng.randf_range(150.0, 360.0)
			_sit_goal = 1.0
			_face_yaw_goal = _yaw_toward(_camera_local()) + _rng.randf_range(-0.4, 0.4)
		return
	if _behavior == "linger":
		_look_user = true
		_face_yaw_goal = _yaw_toward(_camera_local())
	_behavior_left -= delta
	if _walking and _behavior == "wander":
		return
	if _behavior_left > 0.0 or _gesture != "" or _spin_left != 0.0:
		_behavior_tick(delta, state)
		return
	_choose_behavior(state)


func _converse(delta: float, state: Dictionary) -> void:
	_look_user = true
	_sit_goal = 0.0
	if not _walking:
		_face_yaw_goal = _yaw_toward(_camera_local())
	if state["speaking"]:
		_beat_wait -= delta
		if _beat_wait <= 0.0 and _gesture == "" and _speak_energy > 0.22 and not _walking:
			var beats := ["beat", "beat", "beat_both", "nod", "open_hands", "tilt"]
			if state["emotion"] in ["happy", "excited"]:
				beats.append("shrug")
			_queue_gesture(beats[_rng.randi_range(0, beats.size() - 1)], _rng.randf_range(0.9, 1.5))
			_beat_wait = _rng.randf_range(1.2, 3.0)
	elif state["thinking"] and not _thought_this_turn and _gesture == "":
		_thought_this_turn = true
		if _rng.randf() < 0.6:
			_queue_gesture("think", _rng.randf_range(2.2, 3.4))


func _choose_behavior(state: Dictionary) -> void:
	var silence := _clock - _last_engaged
	var emotion: String = state["emotion"]
	if silence > 480.0 and _cooldowns.get("nap", -999.0) <= _clock and _rng.randf() < 0.5:
		_go_to_sleep()
		return
	var total := 0.0
	var weights: Dictionary = {}
	for name in BEHAVIORS:
		var weight: float = BEHAVIORS[name][0]
		if _cooldowns.get(name, -999.0) > _clock:
			continue
		if not _history.is_empty() and _history[-1] == name:
			continue
		if _history.count(name) >= 2:
			weight *= 0.35
		match name:
			"little_dance":
				weight *= 3.0 if emotion in ["happy", "excited"] else 0.25
			"wander":
				weight *= 0.4 if emotion in ["sleepy", "sad"] else (1.5 if emotion == "excited" else 1.0)
			"sit_rest":
				weight *= 1.0 + clampf(silence / 120.0, 0.0, 3.0) + (2.0 if emotion == "sleepy" else 0.0)
			"look_at_user", "wave_user":
				weight *= 1.0 + clampf(silence / 90.0, 0.0, 2.0)
		weights[name] = weight
		total += weight
	var pick := "idle_pause"
	var roll := _rng.randf() * total
	for name in weights:
		roll -= weights[name]
		if roll <= 0.0:
			pick = name
			break
	_start_behavior(pick)


func _start_behavior(name: String) -> void:
	_behavior = name
	_history.append(name)
	if _history.size() > 5:
		_history.pop_front()
	_cooldowns[name] = _clock + float(BEHAVIORS.get(name, [1.0, 0.0])[1])
	_look_user = false
	if _sit > 0.5 and name != "sit_rest":
		_sit_goal = 0.0
	match name:
		"idle_pause":
			_behavior_left = _rng.randf_range(2.0, 6.0)
			_pick_look_point()
		"wander":
			var away := Vector2(_rng.randf_range(-1.0, 1.0), _rng.randf_range(-0.8, 0.8)).normalized() * _rng.randf_range(0.45, 1.2)
			var goal := _pos + away
			goal = goal.lerp(HOME, 0.25)        # drift back toward the middle over time
			_walk_to(goal)
			_behavior_left = 0.5
			_look_point = Vector3.INF            # look where it walks
		"look_around":
			_behavior_left = _rng.randf_range(4.0, 7.0)
			_pick_look_point()
		"look_at_user":
			_behavior_left = _rng.randf_range(3.0, 5.0)
			_look_user = true
			_face_yaw_goal = _yaw_toward(_camera_local())
			if _rng.randf() < 0.4:
				_queue_gesture("tilt", 1.4)
		"stretch":
			_behavior_left = 3.6
			_queue_gesture("stretch", 3.4)
		"inspect_hand":
			_behavior_left = 4.0
			_queue_gesture("inspect_hand", 3.8)
		"little_dance":
			_behavior_left = 3.8
			_queue_gesture("dance", 3.6)
		"turn_around":
			_behavior_left = 3.0
			_spin_left = TAU * (1.0 if _rng.randf() < 0.5 else -1.0)
		"wave_user":
			_behavior_left = 3.0
			_look_user = true
			_face_yaw_goal = _yaw_toward(_camera_local())
			_queue_gesture("wave", 2.3)
		"sit_rest":
			_behavior_left = _rng.randf_range(7.0, 15.0)
			_sit_goal = 1.0
			_pick_look_point()
		"hum_bob":
			_behavior_left = _rng.randf_range(3.5, 5.5)
			_queue_gesture("hum", _behavior_left)


func _behavior_tick(_delta: float, _state: Dictionary) -> void:
	if _behavior in ["idle_pause", "look_around", "sit_rest"] and _look_wait <= 0.0:
		_pick_look_point()
	if _behavior == "look_around" and _look_wait <= 0.0 and _rng.randf() < 0.3 and _sit < 0.2:
		_face_yaw_goal = _yaw + _rng.randf_range(-0.9, 0.9)


func _pick_look_point() -> void:
	_look_wait = _rng.randf_range(1.4, 3.6)
	if _rng.randf() < 0.3:
		_look_user = true
		return
	_look_user = false
	var ahead := Vector3(sin(_yaw), 0.0, cos(_yaw))
	var side := Vector3(ahead.z, 0.0, -ahead.x)
	_look_point = Vector3(_pos.x, 0.0, _pos.y) + ahead * _rng.randf_range(1.5, 3.0) \
		+ side * _rng.randf_range(-2.0, 2.0) + Vector3(0.0, _rng.randf_range(0.0, 1.4), 0.0)


func _go_to_sleep() -> void:
	_sleep_walk = true
	_walk_to(DOCK)


# ------------------------------------------------------------------ locomotion

func _walk_to(goal: Vector2) -> void:
	_target = Vector2(clampf(goal.x, STAGE_MIN.x, STAGE_MAX.x), clampf(goal.y, STAGE_MIN.y, STAGE_MAX.y))
	_walking = _target.distance_to(_pos) > 0.06
	_sit_goal = 0.0


func _steer(goal_rate: float, delta: float) -> float:
	# Angular velocity eases in and out, so turns start and settle like a body
	# with weight instead of snapping to a fixed rate.
	_yaw_vel = move_toward(_yaw_vel, goal_rate, delta * 7.0)
	_yaw += _yaw_vel * delta
	return clampf(absf(_yaw_vel) / TURN_RATE, 0.0, 1.0)


func _update_locomotion(delta: float) -> void:
	var turning := 0.0
	var can_move := _sit < 0.15 and _gesture not in ["hop", "stretch"]
	var previous_speed := _speed
	if _walking and can_move:
		var to := _target - _pos
		var distance := to.length()
		if distance < 0.04:
			_walking = false
		else:
			var dyaw := wrapf(atan2(to.x, to.y) - _yaw, -PI, PI)
			turning = _steer(clampf(dyaw * 3.0, -TURN_RATE, TURN_RATE), delta)
			var align := clampf(1.0 - absf(dyaw) / 1.2, 0.0, 1.0)
			# Slow down smoothly on approach (no abrupt stop at the target).
			var arrive := clampf(distance / 0.35, 0.0, 1.0)
			var goal_speed := WALK_SPEED * align * lerpf(0.25, 1.0, arrive)
			_speed = move_toward(_speed, goal_speed, delta * 0.9)
	if not (_walking and can_move):
		_speed = move_toward(_speed, 0.0, delta * 1.1)
		if _spin_left != 0.0 and can_move:
			var before := _yaw
			turning = _steer(clampf(_spin_left * 2.5, -TURN_RATE * 0.85, TURN_RATE * 0.85), delta)
			_spin_left -= _yaw - before
			if absf(_spin_left) < 0.01:
				_spin_left = 0.0
		elif can_move:
			var dyaw2 := wrapf(_face_yaw_goal - _yaw, -PI, PI)
			# A person doesn't shuffle for tiny corrections; turn only when it matters.
			if absf(dyaw2) > 0.32 or (absf(dyaw2) > 0.05 and _turn_hold > 0.0):
				_turn_hold = 0.4
			_turn_hold = maxf(0.0, _turn_hold - delta)
			var rate := clampf(dyaw2 * 2.5, -TURN_RATE * 0.7, TURN_RATE * 0.7) if _turn_hold > 0.0 else 0.0
			turning = _steer(rate, delta)
		else:
			_steer(0.0, delta)
	_pos += Vector2(sin(_yaw), cos(_yaw)) * _speed * delta
	_pos = Vector2(clampf(_pos.x, STAGE_MIN.x, STAGE_MAX.x), clampf(_pos.y, STAGE_MIN.y, STAGE_MAX.y))
	var gait := clampf(_speed / WALK_SPEED, 0.0, 1.0)
	_step_amp = lerpf(_step_amp, maxf(gait, turning * 0.5), 1.0 - exp(-delta * 5.0))
	# Phase follows distance covered: shorter, slower steps at low speed.
	var stride := 4.0 * LEG * sin(THIGH_SWING * maxf(_step_amp, 0.35))
	var phase_rate := TAU * _speed / maxf(stride, 0.05)
	phase_rate = maxf(phase_rate, turning * 6.5)          # stepping in place while turning
	_phase += delta * phase_rate
	# Lean into acceleration and a little into speed.
	var accel := (_speed - previous_speed) / maxf(delta, 0.0001)
	_lean = lerpf(_lean, 0.10 * gait + clampf(accel * 0.12, -0.06, 0.08), 1.0 - exp(-delta * 4.0))
	_sit = move_toward(_sit, _sit_goal, delta * 0.9)
	if _model != null:
		_model.position = Vector3(_pos.x, 0.0, _pos.y)
		_model.rotation = Vector3(0.0, _yaw, 0.0)


# ------------------------------------------------------------------ gestures

func _queue_gesture(name: String, length: float, side: float = 0.0, important: bool = false) -> void:
	if _gesture != "" and not important:
		return
	_gesture = name
	_gesture_t = 0.0
	_gesture_len = maxf(0.3, length * _rng.randf_range(0.9, 1.15))
	_gesture_side = side if side != 0.0 else (-1.0 if _rng.randf() < 0.5 else 1.0)
	_gesture_amp = _rng.randf_range(0.85, 1.1)
	if name in ["wave", "hop", "dance", "stretch", "think", "inspect_hand"]:
		_sit_goal = 0.0


func _ease(t: float, a: float, b: float) -> float:
	return smoothstep(a, b, t)


func _gesture_pose(q: Dictionary, root: Array) -> Dictionary:
	# Adds gesture rotations to q; returns per-bone override weights (0..1)
	# that fade the base layer for those bones.
	var override: Dictionary = {}
	if _gesture == "":
		return override
	var t := _gesture_t
	var e := _ease(t, 0.0, 0.16) * (1.0 - _ease(t, 0.84, 1.0))
	var s := _gesture_side
	var arm := "R" if s > 0.0 else "L"
	var amp := _gesture_amp
	match _gesture:
		"wave":
			override["upperarm." + arm] = e
			override["forearm." + arm] = e
			# Arm up beside the big helmet; the hand sways from the elbow and
			# the wrist follows a beat later, like a relaxed human wave.
			var sway := sin(t * TAU * 3.2)
			_add(q, "upperarm." + arm, Quaternion(FORWARD, s * (1.30 + 0.04 * sway) * e) * Quaternion(RIGHT, -0.75 * e))
			_add(q, "forearm." + arm, Quaternion(FORWARD, s * (0.80 + 0.42 * sway) * e))
			override["hand." + arm] = e
			_add(q, "hand." + arm, Quaternion(FORWARD, s * 0.30 * sin(t * TAU * 3.2 - 0.9) * e))
			_add(q, "head", Quaternion(FORWARD, -s * 0.10 * e))
			_add(q, "spine", Quaternion(FORWARD, -s * 0.05 * e))
		"think":
			override["upperarm." + arm] = e
			override["forearm." + arm] = e
			_add(q, "upperarm." + arm, Quaternion(RIGHT, -1.05 * e) * Quaternion(FORWARD, s * 0.20 * e))
			_add(q, "forearm." + arm, Quaternion(RIGHT, -1.25 * e) * Quaternion(FORWARD, -s * 0.55 * e))
			_add(q, "head", Quaternion(RIGHT, -0.06 * e) * Quaternion(FORWARD, s * 0.12 * e))
		"stretch":
			var up := _ease(t, 0.05, 0.45) * (1.0 - _ease(t, 0.7, 0.98))
			for side_name in ["L", "R"]:
				var k := -1.0 if side_name == "L" else 1.0
				override["upperarm." + side_name] = up
				override["forearm." + side_name] = up
				# The helmet is huge: arms stretch out sideways and back, never overhead.
				_add(q, "upperarm." + side_name, Quaternion(FORWARD, k * 1.20 * up) * Quaternion(RIGHT, 0.35 * up))
				_add(q, "forearm." + side_name, Quaternion(FORWARD, k * 0.30 * up))
			_add(q, "spine", Quaternion(RIGHT, -0.12 * up))
			_add(q, "head", Quaternion(RIGHT, -0.18 * up) * Quaternion(FORWARD, 0.06 * sin(t * TAU) * up))
		"inspect_hand":
			override["upperarm." + arm] = e
			override["forearm." + arm] = e
			override["hand." + arm] = e
			_add(q, "upperarm." + arm, Quaternion(RIGHT, -0.85 * e) * Quaternion(FORWARD, s * 0.25 * e))
			_add(q, "forearm." + arm, Quaternion(RIGHT, -0.95 * e))
			_add(q, "hand." + arm, Quaternion(UP, s * 0.7 * sin(t * TAU * 1.5) * e))
			_add(q, "head", Quaternion(UP, s * 0.38 * e) * Quaternion(RIGHT, 0.22 * e))
		"hop":
			var crouch := _ease(t, 0.0, 0.28) * (1.0 - _ease(t, 0.3, 0.38)) + _ease(t, 0.66, 0.74) * (1.0 - _ease(t, 0.8, 1.0))
			var air := sin(clampf((t - 0.32) / 0.36, 0.0, 1.0) * PI)
			root[0] += Vector3(0.0, 0.11 * air * amp - 0.035 * crouch, 0.0)
			for side_name in ["L", "R"]:
				var k := -1.0 if side_name == "L" else 1.0
				_add(q, "thigh." + side_name, Quaternion(RIGHT, -0.55 * crouch - 0.25 * air))
				_add(q, "shin." + side_name, Quaternion(RIGHT, 0.95 * crouch + 0.45 * air))
				_add(q, "foot." + side_name, Quaternion(RIGHT, -0.4 * crouch))
				override["upperarm." + side_name] = maxf(air, crouch)
				_add(q, "upperarm." + side_name, Quaternion(FORWARD, k * 1.1 * air) * Quaternion(RIGHT, 0.3 * crouch))
		"laugh":
			var shake := sin(t * TAU * 7.0) * e
			_add(q, "spine", Quaternion(RIGHT, -0.05 * e + 0.035 * shake))
			_add(q, "head", Quaternion(RIGHT, -0.18 * e + 0.05 * shake))
			for side_name in ["L", "R"]:
				var k := -1.0 if side_name == "L" else 1.0
				override["forearm." + side_name] = e
				_add(q, "upperarm." + side_name, Quaternion(RIGHT, -0.35 * e))
				_add(q, "forearm." + side_name, Quaternion(RIGHT, -0.85 * e) * Quaternion(FORWARD, -k * 0.45 * e))
		"dance":
			var beat := t * TAU * 3.0
			root[0] += Vector3(0.0, 0.022 * absf(sin(beat)) * e, 0.0)
			_add(q, "hips", Quaternion(FORWARD, 0.13 * sin(beat * 0.5) * e))
			_add(q, "spine", Quaternion(FORWARD, -0.10 * sin(beat * 0.5) * e))
			_add(q, "head", Quaternion(FORWARD, 0.10 * sin(beat * 0.5 + 0.6) * e) * Quaternion(RIGHT, 0.06 * sin(beat) * e))
			for side_name in ["L", "R"]:
				var k := -1.0 if side_name == "L" else 1.0
				var lift := (0.5 + 0.5 * sin(beat * 0.5 + (0.0 if side_name == "L" else PI))) * e
				override["upperarm." + side_name] = e
				override["forearm." + side_name] = e
				_add(q, "upperarm." + side_name, Quaternion(FORWARD, k * (0.45 + 0.85 * lift)))
				_add(q, "forearm." + side_name, Quaternion(FORWARD, k * 0.6 * lift))
		"glance":
			_add(q, "head", Quaternion(UP, s * 0.55 * e))
		"nod":
			_add(q, "head", Quaternion(RIGHT, 0.16 * sin(t * TAU * 2.0) * e))
		"tilt":
			_add(q, "head", Quaternion(FORWARD, s * 0.17 * e))
		"hum":
			_add(q, "head", Quaternion(FORWARD, 0.07 * sin(t * TAU * 4.0) * e) * Quaternion(RIGHT, 0.04 * sin(t * TAU * 8.0) * e))
			_add(q, "spine", Quaternion(FORWARD, -0.03 * sin(t * TAU * 4.0) * e))
		"shrug":
			for side_name in ["L", "R"]:
				var k := -1.0 if side_name == "L" else 1.0
				override["forearm." + side_name] = e
				_add(q, "upperarm." + side_name, Quaternion(FORWARD, k * 0.30 * e))
				_add(q, "forearm." + side_name, Quaternion(RIGHT, -0.75 * e) * Quaternion(FORWARD, k * 0.55 * e))
			_add(q, "head", Quaternion(FORWARD, s * 0.12 * e))
		"beat":
			override["forearm." + arm] = e
			_add(q, "upperarm." + arm, Quaternion(RIGHT, -0.45 * e * amp) * Quaternion(FORWARD, s * 0.12 * e))
			_add(q, "forearm." + arm, Quaternion(RIGHT, -0.55 * e * amp) * Quaternion(FORWARD, s * 0.25 * e))
		"beat_both", "open_hands":
			var open := 0.35 if _gesture == "open_hands" else 0.12
			for side_name in ["L", "R"]:
				var k := -1.0 if side_name == "L" else 1.0
				override["forearm." + side_name] = e
				_add(q, "upperarm." + side_name, Quaternion(RIGHT, -0.30 * e * amp))
				_add(q, "forearm." + side_name, Quaternion(RIGHT, -0.60 * e * amp) * Quaternion(FORWARD, k * open * e))
	return override


# ------------------------------------------------------------------ pose

func _add(q: Dictionary, bone: String, rotation: Quaternion) -> void:
	q[bone] = (q.get(bone, Quaternion.IDENTITY) as Quaternion) * rotation


func _compose_pose(delta: float, state: Dictionary) -> void:
	var base: Dictionary = {}
	var root := [Vector3.ZERO]
	var a := _step_amp
	var ph := _phase
	var breath := sin(_clock * (0.75 if _asleep else 1.25))
	var drift := sin(_clock * 0.47) * 0.6 + sin(_clock * 0.83 + 1.3) * 0.4
	var gait_l := sin(ph)
	var gait_r := sin(ph + PI)

	# Torso: breathing, weight shift, walking counter-rotation. The body dips
	# at each foot contact and rises over the stance leg (two bobs per cycle),
	# sways toward the supporting foot and leans into its speed.
	root[0] += Vector3(0.012 * a * sin(ph), 0.010 * a * (0.5 - 0.5 * cos(2.0 * ph)) - 0.008 * a, 0.0)
	root[0] += Vector3(0.006 * (1.0 - a) * drift, 0.0, 0.0)       # idle weight shift
	_add(base, "hips", Quaternion(FORWARD, 0.045 * a * gait_l + 0.025 * (1.0 - a) * drift) * Quaternion(UP, -0.06 * a * gait_l))
	_add(base, "spine", Quaternion(UP, 0.09 * a * gait_l) * Quaternion(RIGHT, 0.012 * breath + _lean) \
		* Quaternion(FORWARD, -0.035 * a * gait_l - 0.015 * (1.0 - a) * drift))
	for side_name in ["L", "R"]:
		var k := -1.0 if side_name == "L" else 1.0
		var g := gait_l if side_name == "L" else gait_r
		var c := cos(ph if side_name == "L" else ph + PI)
		# Knee lifts smoothly in swing (leg travelling forward), soft in stance.
		var swing := pow(maxf(0.0, c), 1.6)
		var thigh := -THIGH_SWING * a * g - 0.10 * a * swing
		var knee := 0.08 * a + 0.70 * a * swing
		_add(base, "thigh." + side_name, Quaternion(RIGHT, thigh))
		_add(base, "shin." + side_name, Quaternion(RIGHT, knee))
		# Foot stays level on the ground; toes push off as the leg trails.
		var toe_off := 0.25 * a * smoothstep(0.3, 1.0, -g) * (1.0 - swing)
		_add(base, "foot." + side_name, Quaternion(RIGHT, -(thigh + knee) + toe_off))
		# Arms hang relaxed and swing against the legs while walking.
		_add(base, "upperarm." + side_name, Quaternion(FORWARD, k * (0.07 + 0.012 * breath + 0.03 * a)) * Quaternion(RIGHT, 0.32 * a * g))
		_add(base, "forearm." + side_name, Quaternion(RIGHT, -0.12 - 0.16 * a - 0.10 * a * maxf(0.0, -g)))

	# Sitting (rest / sleep): fold the legs forward and lower the body.
	if _sit > 0.001:
		var sit := smoothstep(0.0, 1.0, _sit)
		root[0] += Vector3(0.0, -0.125 * sit, 0.0)
		for side_name in ["L", "R"]:
			var k := -1.0 if side_name == "L" else 1.0
			_add(base, "thigh." + side_name, Quaternion(RIGHT, -1.35 * sit) * Quaternion(FORWARD, k * 0.12 * sit))
			_add(base, "shin." + side_name, Quaternion(RIGHT, 0.20 * sit))
			_add(base, "foot." + side_name, Quaternion(RIGHT, -0.15 * sit))
			_add(base, "upperarm." + side_name, Quaternion(RIGHT, -0.30 * sit))
			_add(base, "forearm." + side_name, Quaternion(RIGHT, -0.45 * sit))
		_add(base, "spine", Quaternion(RIGHT, 0.08 * sit))

	# Head: look at the user or at something interesting, eyes lead.
	var target_local: Vector3
	if _look_user or state["engaged"]:
		target_local = _camera_local()
	elif _look_point == Vector3.INF:
		target_local = Vector3(_pos.x, 0.7, _pos.y) + Vector3(sin(_yaw), 0.0, cos(_yaw)) * 2.0
	else:
		target_local = _look_point
	var head_world := to_global(Vector3(_pos.x, 0.75, _pos.y))
	var to_target := _model.global_transform.basis.inverse() * (to_global(target_local) - head_world) if _model != null else Vector3.FORWARD
	var want_yaw := clampf(atan2(to_target.x, to_target.z), -1.15, 1.15)
	var want_pitch := clampf(atan2(to_target.y, Vector2(to_target.x, to_target.z).length()), -0.45, 0.40)
	if state["thinking"] and _gesture != "think":
		want_pitch = 0.25
		want_yaw += 0.25 * _gesture_side
	if _asleep:
		want_yaw = 0.0
		want_pitch = -0.30
	# Critically damped spring: the head eases out of rest and into the new
	# target (no velocity jump when attention switches).
	var w := 7.0 if state["engaged"] else 4.5
	var dt := minf(delta, 0.05)
	_head_vel += ((Vector2(want_yaw, want_pitch) - _head) * w * w - _head_vel * 2.0 * w) * dt
	_head += _head_vel * dt
	# Turn the whole body when the head would have to twist too far for long.
	if absf(want_yaw) > 0.85 and not _walking and _gesture == "" and _sit < 0.2:
		_face_yaw_goal = _yaw + want_yaw
	_add(base, "spine", Quaternion(UP, _head.x * 0.22))
	_add(base, "head", Quaternion(UP, _head.x * 0.70) * Quaternion(RIGHT, -_head.y * 0.75))
	if state["listening"]:
		_add(base, "head", Quaternion(FORWARD, 0.07 * sin(_clock * 0.6) + 0.06))
		_add(base, "spine", Quaternion(RIGHT, 0.04))
	if state["speaking"]:
		_add(base, "head", Quaternion(RIGHT, 0.035 * _speak_energy * sin(_clock * 6.5)) * Quaternion(UP, 0.03 * _speak_energy * sin(_clock * 2.9)))
	if _asleep:
		_add(base, "head", Quaternion(FORWARD, 0.12))

	# Gestures on top; they fade the base motion of the bones they use.
	var gesture: Dictionary = {}
	var override := _gesture_pose(gesture, root)
	for bone in _bones:
		var rotation: Quaternion = base.get(bone, Quaternion.IDENTITY)
		var weight: float = override.get(bone, 0.0)
		if weight > 0.0:
			rotation = rotation.slerp(Quaternion.IDENTITY, weight * 0.85)
		if gesture.has(bone):
			rotation = rotation * (gesture[bone] as Quaternion)
		_set_model_rotation(bone, rotation)
	_set_offset("root", root[0])


# ------------------------------------------------------------------ face

func _update_face(delta: float, state: Dictionary) -> void:
	if _face == null:
		return
	var emotion: String = state["emotion"]
	var goal := {
		"happy": 0.0, "sad": 0.0, "angry": 0.0, "surprise": 0.0, "smile": 0.35, "size": 1.0,
		"open_base": 1.0, "power": 1.0,
	}
	match emotion:
		"happy":
			goal["smile"] = 0.6
		"excited":
			goal["smile"] = 0.85
			goal["surprise"] = 0.25
			goal["size"] = 1.06
		"sad":
			goal["sad"] = 0.85
			goal["smile"] = -0.5
		"angry":
			goal["angry"] = 0.85
			goal["smile"] = -0.25
		"sleepy":
			goal["open_base"] = 0.42
			goal["smile"] = 0.1
		"curious":
			goal["surprise"] = 0.22
			goal["size"] = 1.08
			goal["smile"] = 0.2
		"shy":
			goal["happy"] = 0.35
			goal["smile"] = 0.3
	if _gesture in ["laugh", "dance", "hop"] or (_gesture == "stretch" and _gesture_t > 0.3 and _gesture_t < 0.75):
		goal["happy"] = 1.0
	if _gesture == "wave":
		goal["smile"] = 0.8
	if state["listening"]:
		goal["size"] = maxf(goal["size"], 1.05)
	if _asleep:
		goal["open_base"] = 0.0
		goal["power"] = 0.38
		goal["happy"] = 0.0
	# Blinks: the controller's timer plus our own when it is quiet.
	_own_blink_wait -= delta
	if _own_blink_wait <= 0.0:
		_own_blink = 1.0
		_own_blink_wait = _rng.randf_range(2.2, 5.5)
	_own_blink = maxf(0.0, _own_blink - delta * 7.0)
	var blink := maxf(_num(state["blink"]), sin(_own_blink * PI))
	# Gaze: eyes lead the head, with small saccades.
	_saccade_wait -= delta
	if _saccade_wait <= 0.0:
		var spread := 0.12 if state["engaged"] else 0.35
		_saccade = Vector2(_rng.randf_range(-spread, spread), _rng.randf_range(-spread, spread) * 0.6)
		_saccade_wait = _rng.randf_range(0.4, 1.5) if state["engaged"] else _rng.randf_range(1.0, 3.0)
	var gaze := Vector2(clampf(_head.x * 0.9, -1.0, 1.0), clampf(_head.y * 1.4, -1.0, 1.0)) + _saccade
	if _gesture == "think" or state["thinking"]:
		gaze = Vector2(0.55 * _gesture_side, 0.75)
	# Mouth: only speech opens it (one drawn shape, nothing painted beneath).
	var mouth_open := 0.0
	var mouth_round := 0.0
	var mouth_wide := 0.0
	if state["speaking"]:
		mouth_open = _num(state["open"])
		mouth_round = _num(state["round"])
		mouth_wide = _num(state["wide"])
	elif _gesture == "laugh":
		mouth_open = 0.55 + 0.25 * absf(sin(_gesture_t * TAU * 5.0))
	_speak_energy = lerpf(_speak_energy, mouth_open, 1.0 - exp(-delta * 6.0))

	var values := {
		"happy": goal["happy"], "sad": goal["sad"], "angry": goal["angry"], "surprise": goal["surprise"],
		"mouth_smile": goal["smile"], "eye_size": goal["size"], "power": goal["power"],
		"eye_open_l": float(goal["open_base"]) * (1.0 - blink), "eye_open_r": float(goal["open_base"]) * (1.0 - blink),
		"mouth_open": mouth_open, "mouth_round": mouth_round, "mouth_wide": mouth_wide,
	}
	for key in values:
		var speed := 30.0 if key in ["eye_open_l", "eye_open_r", "mouth_open", "mouth_round", "mouth_wide"] else 5.0
		var now: float = _face_now.get(key, values[key])
		now = lerpf(now, values[key], 1.0 - exp(-delta * speed))
		_face_now[key] = now
		_face.set_shader_parameter(key, now)
	var current_gaze: Vector2 = _face_now.get("gaze", gaze)
	current_gaze = current_gaze.lerp(gaze, 1.0 - exp(-delta * 18.0))
	_face_now["gaze"] = current_gaze
	_face.set_shader_parameter("gaze", current_gaze)


# ------------------------------------------------------------------ bone helpers

func _set_model_rotation(bone_name: String, model_rotation: Quaternion) -> void:
	var index: int = _bones.get(bone_name, -1)
	if index < 0:
		return
	var rest := _skeleton.get_bone_rest(index)
	var global_basis := _skeleton.get_bone_global_rest(index).basis.orthonormalized()
	var local_delta := Quaternion(global_basis.inverse() * Basis(model_rotation) * global_basis)
	_skeleton.set_bone_pose_rotation(index, rest.basis.get_rotation_quaternion() * local_delta)


func _set_offset(bone_name: String, model_offset: Vector3) -> void:
	var index: int = _bones.get(bone_name, -1)
	if index < 0:
		return
	var parent := _skeleton.get_bone_parent(index)
	var parent_basis := _skeleton.get_bone_global_rest(parent).basis.orthonormalized() if parent >= 0 else Basis()
	_skeleton.set_bone_pose_position(index, _skeleton.get_bone_rest(index).origin + parent_basis.inverse() * model_offset)


# ------------------------------------------------------------------ materials

func _apply_materials() -> void:
	for node in find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := node as MeshInstance3D
		if mesh_instance.mesh == null or mesh_instance.material_override != null:
			continue
		mesh_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
		# Two-tone plastic from the baked whiteness field (no texture atlas).
		var material := ShaderMaterial.new()
		material.shader = BODY_SHADER
		for surface in mesh_instance.mesh.get_surface_count():
			mesh_instance.set_surface_override_material(surface, material)
