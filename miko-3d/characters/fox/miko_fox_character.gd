extends Node3D
## Miko's fox creature: procedural life on top of the rigged MikoFox.glb.
##
## main.tscn instances this scene as "MikoScene". The controller and Presence
## keep owning voice, emotion and staging; every frame this script reads their
## state and poses the skeleton, eyelids and talking mouth.
##
## The controller requires MikoScene/AnimationPlayer and plays gesture cues on
## it (idle, look, bounce, laugh, wave, dance, sleep, roll). They are provided
## here as timing-only clips; this script turns the active cue into motion.

const META_PATH := "res://characters/fox/MikoFox.meta.json"
const CUES := {
	"idle": 4.0, "look": 2.2, "bounce": 1.3, "laugh": 1.8,
	"wave": 2.0, "dance": 3.2, "sleep": 2.0, "roll": 1.6,
}
# Model space of the imported character: faces +Z, up +Y, screen right +X.
const RIGHT := Vector3(1, 0, 0)
const UP := Vector3(0, 1, 0)
const FORWARD := Vector3(0, 0, 1)

## State overrides for previews/tests (same keys as _read_state()).
var manual_state: Dictionary = {}
## Animated by the cue clips; only used to give them a track.
var cue_phase := 0.0

var _controller: Node
var _presence: Node
var _skeleton: Skeleton3D
var _mouth: MeshInstance3D
var _bones: Dictionary = {}
var _eyes: Dictionary = {}
var _lid_close_up := deg_to_rad(96.0)
var _lid_close_low := deg_to_rad(62.0)
var _cue_player: AnimationPlayer
var _rng := RandomNumberGenerator.new()
var _clock := 0.0
var _materials_ready := false
var _mouth_keys: Dictionary = {}
var _mouth_weights: Dictionary = {}

var _head := Vector3.ZERO          # pitch (+ nods down), yaw (+ toward +X), roll
var _eye_gaze: Dictionary = {"L": Vector2.ZERO, "R": Vector2.ZERO}   # yaw, pitch(+ up)
var _saccade := Vector2.ZERO
var _saccade_wait := 1.0
var _lid_up := 0.0
var _lid_low := 0.0
var _ears := Vector2.ZERO          # x = perk forward (+) / back (-), y = droop
var _twitch_side := "L"
var _twitch_time := 0.0
var _twitch_wait := 3.0
var _tail_phase := 0.0
var _tail_speed := 1.4
var _tail_amp := 0.12
var _speak_energy := 0.0
var _look_sign := 1.0
var _last_cue := ""


func _ready() -> void:
	_rng.randomize()
	_load_meta()
	var skeletons := find_children("*", "Skeleton3D", true, false)
	if skeletons.is_empty():
		push_error("MIKO FOX: Skeleton3D not found in MikoFox.glb")
		return
	_skeleton = skeletons[0] as Skeleton3D
	for index in _skeleton.get_bone_count():
		_bones[_skeleton.get_bone_name(index)] = index
	_mouth = find_child("FoxMouth", true, false) as MeshInstance3D
	if _mouth != null:
		for key in ["open", "round", "wide", "smile", "sad", "surprised"]:
			var shape := _mouth.find_blend_shape_by_name(key)
			if shape >= 0:
				_mouth_keys[key] = shape
				_mouth_weights[key] = 0.0
	_create_cue_player()
	_controller = get_parent()
	if _controller != null:
		_presence = _controller.get_node_or_null("Presence")
	print("MIKO FOX: character ready (", _bones.size(), " bones, ", _mouth_keys.size(), " mouth shapes)")


func _load_meta() -> void:
	var text := FileAccess.get_file_as_string(META_PATH)
	var parsed: Variant = JSON.parse_string(text)
	if not (parsed is Dictionary):
		push_warning("MIKO FOX: metadata missing; eyes use default axes")
		return
	var meta: Dictionary = parsed
	for side in ["L", "R"]:
		var eye: Dictionary = meta.get("eyes", {}).get(side, {})
		_eyes[side] = {
			"center": _vec(eye.get("center", [0, 0.65, 0.3])),
			"forward": _vec(eye.get("forward", [0, 0, 1])).normalized(),
			"right": _vec(eye.get("right", [1, 0, 0])).normalized(),
			"up": _vec(eye.get("up", [0, 1, 0])).normalized(),
		}
	var lids: Dictionary = meta.get("lid_close_deg", {})
	_lid_close_up = deg_to_rad(float(lids.get("up", 96.0)))
	_lid_close_low = deg_to_rad(float(lids.get("low", 62.0)))


func _vec(values: Variant) -> Vector3:
	if values is Array and values.size() == 3:
		return Vector3(float(values[0]), float(values[1]), float(values[2]))
	return Vector3.ZERO


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
		# A cue counts while playing or paused part-way (the controller pauses
		# "sleep" to hold it); a finished clip no longer drives any motion.
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
	return state


# ------------------------------------------------------------------ frame

func _process(delta: float) -> void:
	if _skeleton == null:
		return
	if not _materials_ready:
		# Presence duplicates and retunes every imported material during the
		# controller's _ready; apply the fox materials after that has run.
		_apply_materials()
		_materials_ready = true
	_clock += delta
	var state := _read_state()
	if state["cue"] != _last_cue:
		_last_cue = state["cue"]
		_look_sign = -1.0 if _rng.randf() < 0.5 else 1.0
	_update_mouth(delta, state)
	_update_gaze_and_head(delta, state)
	_update_lids(delta, state)
	_update_body(delta, state)


func _envelope(t: float) -> float:
	return sin(clampf(t, 0.0, 1.0) * PI)


func _rate(delta: float, speed: float) -> float:
	return 1.0 - exp(-delta * speed)


# ------------------------------------------------------------------ mouth

func _update_mouth(delta: float, state: Dictionary) -> void:
	var goal := {"open": 0.0, "round": 0.0, "wide": 0.0, "smile": 0.0, "sad": 0.0, "surprised": 0.0}
	var emotion: String = state["emotion"]
	if state["speaking"]:
		var level: float = state["open"]
		var round_part: float = state["round"]
		var wide_part: float = state["wide"]
		var vowel := clampf(round_part + wide_part, 0.0, 1.0)
		goal["open"] = level * (1.0 - 0.55 * vowel)
		goal["round"] = level * round_part * 0.85
		goal["wide"] = level * wide_part * 0.75
		if emotion in ["happy", "excited"]:
			goal["smile"] = level * 0.35
		elif emotion == "sad":
			goal["sad"] = level * 0.45
	elif state["cue"] == "laugh":
		goal["smile"] = 0.5 + 0.35 * absf(sin(float(state["cue_t"]) * TAU * 3.0))
	elif emotion == "excited":
		goal["smile"] = 0.18
	var energy := 0.0
	for key in goal:
		if not _mouth_weights.has(key):
			continue
		_mouth_weights[key] = lerpf(_mouth_weights[key], goal[key], _rate(delta, 24.0))
		_mouth.set_blend_shape_value(_mouth_keys[key], _mouth_weights[key])
		energy += _mouth_weights[key]
	_speak_energy = lerpf(_speak_energy, clampf(energy, 0.0, 1.0), _rate(delta, 8.0))


# ------------------------------------------------------------------ eyes / head

func _target_local() -> Vector3:
	var camera := get_viewport().get_camera_3d()
	var world_target := camera.global_position if camera != null else global_position + Vector3(0.0, 1.0, 5.0)
	return _skeleton.global_transform.affine_inverse() * world_target


func _update_gaze_and_head(delta: float, state: Dictionary) -> void:
	var engaged: bool = state["speaking"] or state["listening"]
	var target := _target_local()
	_saccade_wait -= delta
	if _saccade_wait <= 0.0:
		var spread := 0.05 if engaged else 0.20
		_saccade = Vector2(_rng.randf_range(-spread, spread), _rng.randf_range(-spread, spread) * 0.6)
		_saccade_wait = _rng.randf_range(0.5, 1.6) if engaged else _rng.randf_range(1.2, 3.4)

	var glance := Vector2.ZERO
	if state["thinking"]:
		glance = Vector2(-0.28 * _look_sign, 0.22)
	elif state["cue"] == "look" and not engaged:
		glance = Vector2(0.35 * _look_sign * _envelope(state["cue_t"]), 0.0)
	if state["sleeping"]:
		glance = Vector2(0.0, -0.25)

	var face_goal := Vector2.ZERO
	for side in ["L", "R"]:
		if not _eyes.has(side):
			continue
		var eye: Dictionary = _eyes[side]
		var to_target: Vector3 = target - eye["center"]
		var forward: Vector3 = eye["forward"]
		var yaw := atan2(to_target.x, to_target.z) - atan2(forward.x, forward.z)
		var pitch := atan2(to_target.y, Vector2(to_target.x, to_target.z).length()) - asin(clampf(forward.y, -1.0, 1.0))
		var goal := Vector2(yaw, pitch) + _saccade + glance
		goal.x = clampf(goal.x, -0.42, 0.42)
		goal.y = clampf(goal.y, -0.30, 0.30)
		_eye_gaze[side] = (_eye_gaze[side] as Vector2).lerp(goal, _rate(delta, 20.0))
		face_goal += goal * 0.5
		var gaze: Vector2 = _eye_gaze[side]
		_set_model_rotation("eye." + side,
			Quaternion(eye["up"], gaze.x) * Quaternion(eye["right"], -gaze.y))

	# The head follows the eyes partway, with posture from the conversation.
	var head_goal := Vector3(-face_goal.y * 0.35, face_goal.x * 0.45, 0.0)
	var emotion: String = state["emotion"]
	if state["listening"]:
		head_goal += Vector3(0.05, 0.0, 0.07)
	if state["thinking"]:
		head_goal += Vector3(-0.08, 0.0, -0.10)
	if state["speaking"]:
		head_goal.x += 0.045 * _speak_energy * sin(_clock * 7.0)
		head_goal.y += 0.03 * _speak_energy * sin(_clock * 3.1)
	match emotion:
		"sad":
			head_goal += Vector3(0.12, 0.0, 0.0)
		"shy":
			head_goal += Vector3(0.15, -0.18, 0.10)
		"curious":
			head_goal.z += 0.06
		"excited":
			head_goal.x -= 0.05
	if state["cue"] == "laugh":
		head_goal.x -= 0.16 * _envelope(state["cue_t"])
	if state["sleeping"]:
		head_goal = Vector3(0.32, 0.0, 0.14)
	# Slow idle drift so it never freezes.
	head_goal += Vector3(sin(_clock * 0.37) * 0.015, sin(_clock * 0.23) * 0.04, sin(_clock * 0.31) * 0.02)
	_head = _head.lerp(head_goal, _rate(delta, 3.5))
	_set_model_rotation("head",
		Quaternion(UP, _head.y) * Quaternion(RIGHT, _head.x) * Quaternion(FORWARD, _head.z))


func _update_lids(delta: float, state: Dictionary) -> void:
	var up_goal := 0.0
	var low_goal := 0.0
	match state["emotion"]:
		"sleepy":
			up_goal = 0.55
		"sad":
			up_goal = 0.26
			low_goal = 0.10
		"angry":
			up_goal = 0.30
		"shy":
			up_goal = 0.22
			low_goal = 0.15
		"happy":
			low_goal = 0.20
		"excited":
			low_goal = 0.28
		"curious":
			up_goal = -0.06
	if state["cue"] in ["laugh", "bounce", "dance"]:
		low_goal = maxf(low_goal, 0.62 * _envelope(state["cue_t"]))
	if state["listening"]:
		up_goal = minf(up_goal, 0.0) - 0.04
	if state["sleeping"]:
		up_goal = 1.0
		low_goal = 0.6
	var blink: float = state["blink"]
	_lid_up = lerpf(_lid_up, maxf(up_goal, blink), _rate(delta, 28.0))
	_lid_low = lerpf(_lid_low, maxf(low_goal, blink * 0.55), _rate(delta, 28.0))
	for side in ["L", "R"]:
		if not _eyes.has(side):
			continue
		var axis: Vector3 = _eyes[side]["right"]
		_set_model_rotation("lid_up." + side, Quaternion(axis, _lid_close_up * _lid_up))
		_set_model_rotation("lid_low." + side, Quaternion(axis, -_lid_close_low * _lid_low))


# ------------------------------------------------------------------ body

func _update_body(delta: float, state: Dictionary) -> void:
	var cue: String = state["cue"]
	var t: float = state["cue_t"]
	var env := _envelope(t)
	var emotion: String = state["emotion"]
	var sleeping: bool = state["sleeping"]

	# Breathing and posture.
	var breath := sin(_clock * (0.8 if sleeping else 1.35))
	var amp := 0.022 if sleeping else 0.012
	var lean := 0.0
	if state["listening"]:
		lean += 0.05
	if sleeping:
		lean += 0.07
	var sway := sin(_clock * 0.6) * 0.015
	var hop := 0.0
	var spin := 0.0
	match cue:
		"bounce":
			hop = 0.07 * absf(sin(t * PI * 2.0))
		"dance":
			hop = 0.035 * absf(sin(t * PI * 6.0))
			sway += sin(t * TAU * 2.0) * 0.12 * env
		"laugh":
			hop = 0.018 * absf(sin(t * PI * 8.0))
		"roll":
			spin = smoothstep(0.0, 1.0, t) * TAU
	var squash := 1.0 - 0.05 * clampf(hop / 0.07, 0.0, 1.0)
	_set_model_rotation("body", Quaternion(RIGHT, lean) * Quaternion(FORWARD, sway))
	_set_scale("body", Vector3(1.0 - amp * 0.5 * breath, (1.0 + amp * breath) / squash, 1.0 - amp * 0.5 * breath))
	_set_model_rotation("root", Quaternion(UP, spin + (sin(t * TAU * 2.0) * 0.3 * env if cue == "dance" else 0.0)))
	_set_offset("root", Vector3(0.0, hop, 0.0))

	# Ears: perk toward the listener, droop when sad or sleepy, twitch now and then.
	var ear_goal := Vector2.ZERO
	if state["listening"]:
		ear_goal.x = 0.22
	match emotion:
		"curious":
			ear_goal.x = maxf(ear_goal.x, 0.15)
		"excited":
			ear_goal.x = maxf(ear_goal.x, 0.10)
		"angry":
			ear_goal.x = -0.30
		"sad":
			ear_goal.y = 0.45
		"sleepy":
			ear_goal.y = 0.45
		"shy":
			ear_goal.y = 0.25
	if sleeping:
		ear_goal = Vector2(-0.05, 0.5)
	_ears = _ears.lerp(ear_goal, _rate(delta, 4.0))
	_twitch_wait -= delta
	if _twitch_wait <= 0.0 and not sleeping:
		_twitch_side = "L" if _rng.randf() < 0.5 else "R"
		_twitch_time = 0.28
		_twitch_wait = _rng.randf_range(3.0, 9.0)
	_twitch_time = maxf(0.0, _twitch_time - delta)
	var twitch := sin((1.0 - _twitch_time / 0.28) * PI) * 0.32 if _twitch_time > 0.0 else 0.0
	var wiggle := sin(_clock * 11.0) * 0.08 * env if cue in ["bounce", "dance", "laugh"] else 0.0
	for side in ["L", "R"]:
		var outward := 1.0 if side == "L" else -1.0     # +Z rotation swings an up-vector toward -X
		var perk := _ears.x + wiggle + (twitch if side == _twitch_side else 0.0)
		var droop := _ears.y
		_set_model_rotation("ear.%s.1" % side, Quaternion(RIGHT, perk * 0.6) * Quaternion(FORWARD, outward * droop * 0.6))
		_set_model_rotation("ear.%s.2" % side, Quaternion(RIGHT, perk * 0.4) * Quaternion(FORWARD, outward * droop * 0.5))

	# Tail wag tempo and size follow the mood.
	var wag := Vector2(1.4, 0.12)
	match emotion:
		"happy":
			wag = Vector2(3.2, 0.30)
		"excited":
			wag = Vector2(6.0, 0.45)
		"curious":
			wag = Vector2(2.0, 0.18)
		"sad":
			wag = Vector2(0.8, 0.06)
		"sleepy":
			wag = Vector2(0.6, 0.04)
		"angry":
			wag = Vector2(1.0, 0.08)
	if state["listening"]:
		wag = Vector2(2.0, 0.16)
	if cue in ["bounce", "dance", "laugh"]:
		wag = Vector2(6.5, 0.5)
	if sleeping:
		wag = Vector2(0.4, 0.03)
	_tail_speed = lerpf(_tail_speed, wag.x, _rate(delta, 2.0))
	_tail_amp = lerpf(_tail_amp, wag.y, _rate(delta, 2.0))
	_tail_phase += delta * _tail_speed
	_set_model_rotation("tail.1", Quaternion(UP, sin(_tail_phase) * _tail_amp))
	_set_model_rotation("tail.2", Quaternion(UP, sin(_tail_phase - 0.7) * _tail_amp * 0.8))

	# Paws: tiny breathing motion, talking gestures, waving and cheering.
	var raise_l := 0.03 * breath
	var raise_r := 0.03 * breath
	var swing_r := 0.0
	if state["speaking"]:
		raise_l -= 0.10 * _speak_energy * maxf(0.0, sin(_clock * 2.7))
		raise_r -= 0.10 * _speak_energy * maxf(0.0, sin(_clock * 2.7 + 1.9))
	match cue:
		"wave":
			raise_r = -1.05 * env
			swing_r = sin(t * TAU * 3.0) * 0.35 * env
		"bounce", "dance", "laugh":
			raise_l = -0.5 * env
			raise_r = -0.5 * env
	_set_model_rotation("arm.L", Quaternion(RIGHT, raise_l))
	_set_model_rotation("arm.R", Quaternion(RIGHT, raise_r) * Quaternion(FORWARD, swing_r))


# ------------------------------------------------------------------ bone helpers

func _set_model_rotation(bone_name: String, model_rotation: Quaternion) -> void:
	# Rotate a bone about axes given in model space, relative to its rest pose.
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


func _set_scale(bone_name: String, value: Vector3) -> void:
	var index: int = _bones.get(bone_name, -1)
	if index >= 0:
		_skeleton.set_bone_pose_scale(index, value)


# ------------------------------------------------------------------ materials

func _apply_materials() -> void:
	for node in find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := node as MeshInstance3D
		if mesh_instance.mesh == null:
			continue
		var lower := str(mesh_instance.name).to_lower()
		mesh_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
		for surface in mesh_instance.mesh.get_surface_count():
			var source := mesh_instance.mesh.surface_get_material(surface) as StandardMaterial3D
			var material: StandardMaterial3D = source.duplicate() if source != null else StandardMaterial3D.new()
			material.metallic = 0.0
			material.metallic_texture = null
			material.roughness_texture = null
			if lower.begins_with("foxbody"):
				# Short velvety fur: matte, low specular, soft rim on the silhouette.
				material.vertex_color_use_as_albedo = false
				material.roughness = 0.9
				material.metallic_specular = 0.22
				material.rim_enabled = true
				material.rim = 0.55
				material.rim_tint = 0.85
			elif lower.begins_with("foxeye"):
				material.roughness = 0.04
				material.metallic_specular = 0.8
				material.clearcoat_enabled = true
				material.clearcoat = 1.0
				material.clearcoat_roughness = 0.02
			elif lower.begins_with("foxlid"):
				material.vertex_color_use_as_albedo = true
				material.albedo_color = Color.WHITE
				material.roughness = 0.88
				material.rim_enabled = true
				material.rim = 0.4
				material.rim_tint = 0.85
			elif lower.begins_with("foxmouth"):
				material.vertex_color_use_as_albedo = true
				material.albedo_color = Color.WHITE
				material.roughness = 0.5
			mesh_instance.set_surface_override_material(surface, material)
