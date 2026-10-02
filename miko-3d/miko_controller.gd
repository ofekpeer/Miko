extends Node3D

# ============================================================
# MIKO GODOT CONTROLLER - COMPLETE CONVERSATION BUILD
# Godot 4.x
#
# Flow:
#   SPACE -> native streaming Realtime speech-to-speech
#   F8 -> echo-cancelled, hands-free browser WebRTC
#   deterministic local tools own email and persistent memory
#   voice + animation start together
#   autonomous /event messages also speak + animate
# ============================================================

const BRAIN_URL := "http://127.0.0.1:5000"
const EVENT_POLL_SECONDS := 1.2
const HEALTH_RETRY_SECONDS := 5.0

# ============================================================
# MIKO SCI-FI ROOM
# The uploaded background.glb is a MODULAR TOOLKIT, not a finished room.
# We use its textures/screens to construct a clean playable room.
# ============================================================
const ROOM_WIDTH := 12.0
const ROOM_DEPTH := 9.0
const ROOM_HEIGHT := 5.0
const ROOM_ROBOT_SCALE := 1.48

const BG_PANEL_TEXTURE := "res://miko_bg/img_00.jpg"
const BG_SCREEN_MAIN := "res://miko_bg/img_14.jpg"
const BG_SCREEN_LEFT := "res://miko_bg/img_12.jpg"
const BG_SCREEN_RIGHT := "res://miko_bg/img_08.jpg"
const BG_SCREEN_SMALL := "res://miko_bg/img_05.jpg"

const MIC_BUS_NAME := "MikoMicRecord"
const MIC_WAV_PATH := "user://miko_mic_input.wav"
const MIN_RECORDING_SECONDS := 0.20

const VOICE_BUS_NAME := "MikoVoice"
const LIPSYNC_MIN_DB := -55.0
const LIPSYNC_MAX_DB := -16.0
const LIPSYNC_OPEN_SCALE := 1.0
const LIPSYNC_CLOSED_SCALE := 0.0

# Living movement / roaming
const ROAM_MIN_DELAY := 1.8
const ROAM_MAX_DELAY := 4.8
const ROAM_RADIUS_X := 1.18
const ROAM_RADIUS_Z := 0.78
const ROAM_MAX_DISTANCE := 1.34
const DRIVE_MIN_DURATION := 0.28
const DRIVE_MAX_DURATION := 2.40
const TREAD_SCROLL_SPEED := 6.5
const MOVE_LEAN_DEGREES := 3.5

@onready var animation_player: AnimationPlayer = _find_animation_player()

const RealtimeVoiceScript = preload("res://miko_realtime_voice.gd")
@onready var presence: Node3D = get_node_or_null("Presence")
var realtime_voice: Node
var realtime_status_label: Label
var realtime_transcript_label: Label
var realtime_transcript_scroll: ScrollContainer
var realtime_voice_panel: PanelContainer
var realtime_talk_button: Button
var realtime_browser_button: Button
var realtime_compact_ui := false
var realtime_medium_ui := false
var realtime_narrow_ui := false
var realtime_display_status := "idle"
var realtime_display_detail := ""
var realtime_last_browser_open_msec := -2000
var miko_preview_mode := false
var realtime_transcript_turns: Array[Dictionary] = []
var realtime_caption_offsets: Dictionary = {}
var realtime_presented_captions: Dictionary = {}
var realtime_session_order: Dictionary = {}
var realtime_next_session_order := 0
var realtime_legacy_transcript_sequence := 0

var health_request: HTTPRequest
var event_request: HTTPRequest
var transcribe_request: HTTPRequest
var think_request: HTTPRequest
var speak_request: HTTPRequest

var event_timer: Timer
var health_retry_timer: Timer

var voice_player: AudioStreamPlayer
var voice_bus_index := -1
var voice_spectrum: AudioEffectSpectrumAnalyzerInstance

var mouth_node: Node3D
var eyes_node: Node3D
var wheel_left_node: Node3D
var wheel_right_node: Node3D

# IMPORTANT:
# The wheel housings and tread meshes NEVER rotate as Node3D objects.
# Only a procedural material animates across the black tread surface.
var wheel_left_tread_mesh: MeshInstance3D
var wheel_right_tread_mesh: MeshInstance3D
var wheel_left_tread_material: ShaderMaterial
var wheel_right_tread_material: ShaderMaterial
var tread_left_phase := 0.0
var tread_right_phase := 0.0

var mouth_base_scale := Vector3.ONE

# Guaranteed visible runtime mouth used for real lip-sync.
# The source model's original mouth is only a thin static light strip.
var lipsync_mouth_visual: MeshInstance3D
var lipsync_mouth_closed_scale := Vector3(0.145, 0.010, 0.020)
var lipsync_mouth_open_scale := Vector3(0.115, 0.085, 0.026)

# Clean runtime wheel guards. The broken orange source wheel fragments
# are hidden and replaced by simple rigid housings that never deform.
var wheel_guard_material: StandardMaterial3D

# Face / personality system
var eyes_mesh: MeshInstance3D
var eyes_material: StandardMaterial3D
var eyes_base_position := Vector3.ZERO
var eyes_base_scale := Vector3.ONE

var antenna_mesh: MeshInstance3D
var antenna_material: StandardMaterial3D

var face_emotion := "happy"
var face_emotion_seconds := 0.0

var blink_wait := 2.5
var blink_progress := 0.0
var blink_active := false

var gaze_wait := 1.0
var gaze_target := Vector2.ZERO
var gaze_current := Vector2.ZERO

var face_rng := RandomNumberGenerator.new()
var face_clock := 0.0

# Social body-language layer.
# This uses Robot_008, which is NOT directly animated by the imported
# AnimationPlayer, so it can add subtle motion on top of idle/look/dance.
var social_body: Node3D
var social_body_base_position := Vector3.ZERO
var social_body_base_rotation := Vector3.ZERO
var social_body_ready := false

var movement_root: Node3D
var movement_home_position := Vector3.ZERO
var movement_home_rotation := Vector3.ZERO
var roam_timer: Timer
var roam_rng := RandomNumberGenerator.new()

var drive_active := false
var drive_kind := ""
var drive_elapsed := 0.0
var drive_duration := 1.0
var drive_start_position := Vector3.ZERO
var drive_target_position := Vector3.ZERO
var drive_start_yaw := 0.0
var drive_target_yaw := 0.0
var drive_spin_radians := 0.0
var drive_lean_radians := 0.0
var drive_left_wheel_speed := 0.0
var drive_right_wheel_speed := 0.0
var drive_queue: Array = []
var active_choreo := ""
var lip_open_amount := 0.0
var lip_round_amount := 0.0
var lip_wide_amount := 0.0
var lip_speech_clock := 0.0
var mic_player: AudioStreamPlayer
var mic_record_effect: AudioEffectRecord
var mic_bus_index := -1

# Runtime room / layout
var miko_room: Node3D
var world_camera: Camera3D
var room_floor_material: StandardMaterial3D
var room_wall_material: StandardMaterial3D
var room_panel_material: StandardMaterial3D
var room_dark_material: StandardMaterial3D
var room_trim_material: StandardMaterial3D
var room_warm_trim_material: StandardMaterial3D
var dynamic_roam_radius_x := 3.7
var dynamic_roam_radius_z := 2.45
var dynamic_roam_max_distance := 4.1

var brain_connected := false

var mic_recording := false
var mic_started_msec := 0
var user_turn_active := false

var tts_request_active := false
var voice_active := false

# Natural conversation: the owner can interrupt Miko while he is speaking.
var barge_in_cooldown := 0.0
const BARGE_IN_COOLDOWN_SECONDS := 0.18

var action_playing := false
var vision_enabled := true
var vision_available := false
var realtime_camera_button: Button
var sleeping_pose := false

# These belong to the ONE speech request currently in flight.
var speech_action := "look"
var speech_emotion := "happy"
var speech_message := ""


func _ready() -> void:
	miko_preview_mode = OS.get_cmdline_user_args().has("--miko-preview") or OS.get_cmdline_args().has("--miko-preview")
	if not miko_preview_mode:
		get_window().min_size = Vector2i(360, 420)
	if animation_player == null:
		push_error("Miko AnimationPlayer was not found at MikoScene/AnimationPlayer.")
		return

	animation_player.animation_finished.connect(
		_on_animation_finished
	)

	_setup_robot_features()
	_setup_expression_system()
	if presence != null:
		presence.setup_stage()
		presence.set_emotion(face_emotion)
	_play_idle()
	if miko_preview_mode:
		_setup_voice_controls()
		print("MIKO PREVIEW: presentation and native controls only")
		return

	_setup_health()
	_setup_event_polling()
	_setup_voice()
	_setup_realtime_voice()

	print("MIKO CONTROLLER READY")
	print("SPACE: streaming Realtime | F8: open hands-free voice | F10: native hands-free")



# ============================================================
# MIKO SCI-FI ROOM / BACKGROUND
# ============================================================

func _setup_miko_room() -> void:
	_hide_legacy_background_nodes()
	_setup_world_environment()

	miko_room = Node3D.new()
	miko_room.name = "MikoBackgroundRoom"
	add_child(miko_room)
	move_child(miko_room, 0)

	_build_room_materials()
	_build_room_geometry()
	_build_room_screens()
	_build_room_lighting()
	_place_miko_in_room()
	_frame_room_camera()

	dynamic_roam_radius_x = 3.15
	dynamic_roam_radius_z = 1.95
	dynamic_roam_max_distance = 3.55

	print(
        "MIKO ROOM READY: modular background rebuilt as a clean sci-fi room"
	)


func _hide_legacy_background_nodes() -> void:
	# The raw uploaded GLB is a kit of separate pieces displayed apart.
	# If it was previously added to main.tscn, hide it so those black
	# floating fragments never appear again.
	for child in get_children():
		if not (child is Node3D):
			continue

		var node_3d: Node3D = child as Node3D
		var lower_name: String = node_3d.name.to_lower()

		if (
			lower_name == "mikobackground"
			or
			lower_name == "background"
			or
			lower_name == "backgroundglb"
			or
			lower_name.begins_with("background_")
		):
			node_3d.visible = false


func _setup_world_environment() -> void:
	var world_env: WorldEnvironment = null
	var existing: Node = get_node_or_null("WorldEnvironment")

	if existing is WorldEnvironment:
		world_env = existing as WorldEnvironment
	else:
		world_env = WorldEnvironment.new()
		world_env.name = "WorldEnvironment"
		add_child(world_env)
		move_child(world_env, 0)

	var environment: Environment = world_env.environment

	if environment == null:
		environment = Environment.new()

	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color(
		0.06,
		0.08,
		0.11,
		1.0
	)
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color(
		0.82,
		0.89,
		0.97,
		1.0
	)
	environment.ambient_light_energy = 1.35
	environment.ambient_light_sky_contribution = 0.18
	environment.reflected_light_source = Environment.REFLECTION_SOURCE_BG
	environment.tonemap_mode = Environment.TONE_MAPPER_ACES
	environment.glow_enabled = true
	environment.glow_intensity = 0.03
	environment.glow_strength = 0.22
	environment.glow_bloom = 0.03

	world_env.environment = environment


func _build_room_materials() -> void:
	room_floor_material = StandardMaterial3D.new()
	room_floor_material.albedo_color = Color(
		0.23,
		0.27,
		0.31,
		1.0
	)
	room_floor_material.metallic = 0.58
	room_floor_material.roughness = 0.42

	var panel_texture: Texture2D = (
		load(BG_PANEL_TEXTURE) as Texture2D
	)

	if panel_texture != null:
		room_floor_material.albedo_texture = (
			panel_texture
		)

	room_wall_material = StandardMaterial3D.new()
	room_wall_material.albedo_color = Color(
		0.15,
		0.18,
		0.22,
		1.0
	)
	room_wall_material.metallic = 0.38
	room_wall_material.roughness = 0.50

	room_panel_material = StandardMaterial3D.new()
	room_panel_material.albedo_color = Color(
		0.09,
		0.115,
		0.145,
		1.0
	)
	room_panel_material.metallic = 0.46
	room_panel_material.roughness = 0.34

	room_dark_material = StandardMaterial3D.new()
	room_dark_material.albedo_color = Color(
		0.025,
		0.035,
		0.050,
		1.0
	)
	room_dark_material.metallic = 0.24
	room_dark_material.roughness = 0.48

	room_trim_material = StandardMaterial3D.new()
	room_trim_material.albedo_color = Color(
		0.08,
		0.64,
		0.86,
		1.0
	)
	room_trim_material.emission_enabled = true
	room_trim_material.emission = Color(
		0.035,
		0.42,
		0.66,
		1.0
	)
	room_trim_material.metallic = 0.12
	room_trim_material.roughness = 0.28

	room_warm_trim_material = StandardMaterial3D.new()
	room_warm_trim_material.albedo_color = Color(
		0.78,
		0.23,
		0.055,
		1.0
	)
	room_warm_trim_material.emission_enabled = true
	room_warm_trim_material.emission = Color(
		0.48,
		0.105,
		0.02,
		1.0
	)
	room_warm_trim_material.metallic = 0.18
	room_warm_trim_material.roughness = 0.30


func _build_room_geometry() -> void:
	# --------------------------------------------------------
	# Main shell
	# --------------------------------------------------------
	_room_add_box(
		"Floor",
		Vector3(
			ROOM_WIDTH,
			0.18,
			ROOM_DEPTH
		),
		Vector3(
			0.0,
			-0.09,
			0.0
		),
		room_floor_material
	)

	_room_add_box(
		"BackWall",
		Vector3(
			ROOM_WIDTH,
			ROOM_HEIGHT,
			0.22
		),
		Vector3(
			0.0,
			ROOM_HEIGHT * 0.5,
			-ROOM_DEPTH * 0.5
		),
		room_wall_material
	)

	_room_add_box(
		"Ceiling",
		Vector3(
			ROOM_WIDTH,
			0.18,
			ROOM_DEPTH
		),
		Vector3(
			0.0,
			ROOM_HEIGHT + 0.02,
			0.0
		),
		room_dark_material
	)

	_room_add_box(
		"LeftWall",
		Vector3(
			0.22,
			ROOM_HEIGHT,
			ROOM_DEPTH
		),
		Vector3(
			-ROOM_WIDTH * 0.5,
			ROOM_HEIGHT * 0.5,
			0.0
		),
		room_wall_material
	)

	_room_add_box(
		"RightWall",
		Vector3(
			0.22,
			ROOM_HEIGHT,
			ROOM_DEPTH
		),
		Vector3(
			ROOM_WIDTH * 0.5,
			ROOM_HEIGHT * 0.5,
			0.0
		),
		room_wall_material
	)

	# --------------------------------------------------------
	# Back-wall architecture: screen wall feels built-in,
	# not like floating rectangles on a black plane.
	# --------------------------------------------------------
	_room_add_box(
		"BackCenterPanel",
		Vector3(
			5.50,
			3.55,
			0.18
		),
		Vector3(
			0.0,
			2.62,
			-4.34
		),
		room_panel_material
	)

	_room_add_box(
		"BackLeftPanel",
		Vector3(
			2.35,
			2.35,
			0.16
		),
		Vector3(
			-4.15,
			2.72,
			-4.33
		),
		room_panel_material
	)

	_room_add_box(
		"BackRightPanel",
		Vector3(
			2.35,
			2.35,
			0.16
		),
		Vector3(
			4.15,
			2.72,
			-4.33
		),
		room_panel_material
	)

	_room_add_box(
		"BackLowerConsole",
		Vector3(
			9.65,
			0.68,
			0.55
		),
		Vector3(
			0.0,
			0.48,
			-4.07
		),
		room_dark_material
	)

	# Strong architectural pillars.
	_room_add_box(
		"PillarL",
		Vector3(
			0.32,
			4.15,
			0.42
		),
		Vector3(
			-5.15,
			2.18,
			-4.12
		),
		room_dark_material
	)

	_room_add_box(
		"PillarR",
		Vector3(
			0.32,
			4.15,
			0.42
		),
		Vector3(
			5.15,
			2.18,
			-4.12
		),
		room_dark_material
	)

	# --------------------------------------------------------
	# Floor center guide only - no raised stage.
	# --------------------------------------------------------
	_room_add_box(
		"CenterGuide",
		Vector3(
			1.85,
			0.02,
			1.55
		),
		Vector3(
			0.0,
			0.01,
			0.78
		),
		room_dark_material
	)

	# --------------------------------------------------------
	# Side consoles, integrated with walls.
	# --------------------------------------------------------
	_room_add_box(
		"TerminalBaseL",
		Vector3(
			1.45,
			1.35,
			0.90
		),
		Vector3(
			-4.42,
			0.675,
			-2.78
		),
		room_panel_material
	)

	_room_add_box(
		"TerminalTopL",
		Vector3(
			1.62,
			0.12,
			1.02
		),
		Vector3(
			-4.42,
			1.37,
			-2.78
		),
		room_dark_material
	)

	_room_add_box(
		"TerminalBaseR",
		Vector3(
			1.45,
			1.35,
			0.90
		),
		Vector3(
			4.42,
			0.675,
			-2.78
		),
		room_panel_material
	)

	_room_add_box(
		"TerminalTopR",
		Vector3(
			1.62,
			0.12,
			1.02
		),
		Vector3(
			4.42,
			1.37,
			-2.78
		),
		room_dark_material
	)

	# --------------------------------------------------------
	# Floor language: short accents instead of two giant laser lines.
	# --------------------------------------------------------
	_room_add_box(
		"FloorDashL1",
		Vector3(
			0.055,
			0.018,
			0.82
		),
		Vector3(
			-2.30,
			0.018,
			3.25
		),
		room_trim_material
	)

	_room_add_box(
		"FloorDashL2",
		Vector3(
			0.055,
			0.018,
			0.82
		),
		Vector3(
			-2.30,
			0.018,
			2.05
		),
		room_trim_material
	)

	_room_add_box(
		"FloorDashL3",
		Vector3(
			0.055,
			0.018,
			0.82
		),
		Vector3(
			-2.30,
			0.018,
			0.85
		),
		room_trim_material
	)

	_room_add_box(
		"FloorDashR1",
		Vector3(
			0.055,
			0.018,
			0.82
		),
		Vector3(
			2.30,
			0.018,
			3.25
		),
		room_trim_material
	)

	_room_add_box(
		"FloorDashR2",
		Vector3(
			0.055,
			0.018,
			0.82
		),
		Vector3(
			2.30,
			0.018,
			2.05
		),
		room_trim_material
	)

	_room_add_box(
		"FloorDashR3",
		Vector3(
			0.055,
			0.018,
			0.82
		),
		Vector3(
			2.30,
			0.018,
			0.85
		),
		room_trim_material
	)

	# --------------------------------------------------------
	# Ceiling light bars add depth and remove the flat gray top.
	# --------------------------------------------------------
	_room_add_box(
		"CeilingLightL",
		Vector3(
			2.65,
			0.035,
			0.11
		),
		Vector3(
			-2.65,
			4.89,
			-0.70
		),
		room_trim_material
	)

	_room_add_box(
		"CeilingLightR",
		Vector3(
			2.65,
			0.035,
			0.11
		),
		Vector3(
			2.65,
			4.89,
			-0.70
		),
		room_trim_material
	)

	_room_add_box(
		"CeilingWarmCenter",
		Vector3(
			1.20,
			0.035,
			0.11
		),
		Vector3(
			0.0,
			4.89,
			1.20
		),
		room_warm_trim_material
	)

	# Back-wall trim, subtle instead of full-width laser rails.
	_room_add_box(
		"BackTrimTop",
		Vector3(
			5.95,
			0.055,
			0.040
		),
		Vector3(
			0.0,
			4.30,
			-4.205
		),
		room_trim_material
	)

	_room_add_box(
		"WarmAccentL",
		Vector3(
			0.055,
			2.55,
			0.040
		),
		Vector3(
			-5.02,
			2.60,
			-4.205
		),
		room_warm_trim_material
	)

	_room_add_box(
		"WarmAccentR",
		Vector3(
			0.055,
			2.55,
			0.040
		),
		Vector3(
			5.02,
			2.60,
			-4.205
		),
		room_warm_trim_material
	)


func _build_room_screens() -> void:
	_room_add_screen(
		"MainScreen",
		BG_SCREEN_MAIN,
		Vector2(
			4.98,
			2.49
		),
		Vector3(
			0.0,
			2.84,
			-4.225
		)
	)

	_room_add_screen(
		"LeftScreen",
		BG_SCREEN_LEFT,
		Vector2(
			1.86,
			0.94
		),
		Vector3(
			-4.15,
			2.92,
			-4.215
		)
	)

	_room_add_screen(
		"RightScreen",
		BG_SCREEN_RIGHT,
		Vector2(
			1.86,
			0.94
		),
		Vector3(
			4.15,
			2.92,
			-4.215
		)
	)

	_room_add_screen(
		"TerminalScreenL",
		BG_SCREEN_SMALL,
		Vector2(
			0.84,
			0.72
		),
		Vector3(
			-4.42,
			1.48,
			-2.28
		)
	)

	_room_add_screen(
		"TerminalScreenR",
		BG_SCREEN_SMALL,
		Vector2(
			0.84,
			0.72
		),
		Vector3(
			4.42,
			1.48,
			-2.28
		)
	)


func _build_room_lighting() -> void:
	var key_light: OmniLight3D = OmniLight3D.new()
	key_light.name = "RoomKeyLight"
	key_light.position = Vector3(
		-2.7,
		3.55,
		2.65
	)
	key_light.light_color = Color(
		0.70,
		0.86,
		1.00,
		1.0
	)
	key_light.light_energy = 4.1
	key_light.omni_range = 9.5
	key_light.shadow_enabled = false
	miko_room.add_child(key_light)

	var warm_light: OmniLight3D = OmniLight3D.new()
	warm_light.name = "RoomWarmLight"
	warm_light.position = Vector3(
		3.15,
		2.55,
		1.65
	)
	warm_light.light_color = Color(
		1.00,
		0.46,
		0.27,
		1.0
	)
	warm_light.light_energy = 1.95
	warm_light.omni_range = 7.5
	warm_light.shadow_enabled = false
	miko_room.add_child(warm_light)

	var back_light: OmniLight3D = OmniLight3D.new()
	back_light.name = "RoomBackLight"
	back_light.position = Vector3(
		0.0,
		3.25,
		-2.85
	)
	back_light.light_color = Color(
		0.20,
		0.66,
		1.00,
		1.0
	)
	back_light.light_energy = 1.75
	back_light.omni_range = 6.2
	back_light.shadow_enabled = false
	miko_room.add_child(back_light)

	var fill_light: OmniLight3D = OmniLight3D.new()
	fill_light.name = "RoomFillLight"
	fill_light.position = Vector3(
		0.0,
		4.25,
		3.80
	)
	fill_light.light_color = Color(
		0.92,
		0.96,
		1.00,
		1.0
	)
	fill_light.light_energy = 2.25
	fill_light.omni_range = 10.5
	fill_light.shadow_enabled = false
	miko_room.add_child(fill_light)

	var front_soft_light: OmniLight3D = OmniLight3D.new()
	front_soft_light.name = "RoomFrontSoftLight"
	front_soft_light.position = Vector3(
		0.0,
		2.7,
		4.55
	)
	front_soft_light.light_color = Color(
		0.98,
		0.99,
		1.00,
		1.0
	)
	front_soft_light.light_energy = 1.15
	front_soft_light.omni_range = 8.6
	front_soft_light.shadow_enabled = false
	miko_room.add_child(front_soft_light)

	var existing_directional: Node = (
		get_node_or_null("DirectionalLight3D")
	)

	if existing_directional is DirectionalLight3D:
		var directional: DirectionalLight3D = (
			existing_directional as DirectionalLight3D
		)

		directional.light_color = Color(
			0.82,
			0.90,
			1.00,
			1.0
		)
		directional.light_energy = 1.22
		directional.rotation_degrees = Vector3(
			-52.0,
			-32.0,
			0.0
		)


func _place_miko_in_room() -> void:
	var robot_root: Node3D = (
		_find_robot_root_for_room()
	)

	if robot_root == null:
		print(
            "MIKO ROOM WARNING: MikoScene/MikoHolder not found"
		)
		return

	robot_root.scale = Vector3(
		ROOM_ROBOT_SCALE,
		ROOM_ROBOT_SCALE,
		ROOM_ROBOT_SCALE
	)

	robot_root.position = Vector3(
		0.0,
		0.03,
		0.98
	)

	robot_root.rotation = Vector3.ZERO


func _frame_room_camera() -> void:
	var camera_candidate: Node = (
		get_node_or_null("Camera3D")
	)

	if camera_candidate is Camera3D:
		world_camera = camera_candidate as Camera3D
	else:
		var found_camera: Node = (
			find_child(
				"Camera3D",
				true,
				false
			)
		)

		if found_camera is Camera3D:
			world_camera = found_camera as Camera3D

	if world_camera == null:
		return

	world_camera.position = Vector3(
		0.0,
		2.24,
		6.15
	)

	world_camera.fov = 50.0

	world_camera.look_at(
		Vector3(
			0.0,
			1.24,
			0.50
		),
		Vector3.UP
	)


func _find_robot_root_for_room() -> Node3D:
	var holder: Node = (
		get_node_or_null("MikoHolder")
	)

	if holder is Node3D:
		return holder as Node3D

	var direct_scene: Node = (
		get_node_or_null("MikoScene")
	)

	if direct_scene is Node3D:
		return direct_scene as Node3D

	var found_scene: Node = (
		find_child(
			"MikoScene",
			true,
			false
		)
	)

	if found_scene is Node3D:
		return found_scene as Node3D

	return null


func _room_add_box(
	node_name: String,
	size: Vector3,
	position_value: Vector3,
	material_value: Material
) -> void:
	if miko_room == null:
		return

	var mesh_instance: MeshInstance3D = (
		MeshInstance3D.new()
	)

	mesh_instance.name = node_name

	var box_mesh: BoxMesh = BoxMesh.new()
	box_mesh.size = size

	mesh_instance.mesh = box_mesh
	mesh_instance.position = position_value
	mesh_instance.material_override = (
		material_value
	)

	miko_room.add_child(
		mesh_instance
	)


func _room_add_cylinder(
	node_name: String,
	radius: float,
	height: float,
	position_value: Vector3,
	material_value: Material
) -> void:
	if miko_room == null:
		return

	var mesh_instance: MeshInstance3D = (
		MeshInstance3D.new()
	)

	mesh_instance.name = node_name

	var cylinder_mesh: CylinderMesh = (
		CylinderMesh.new()
	)

	cylinder_mesh.top_radius = radius
	cylinder_mesh.bottom_radius = radius
	cylinder_mesh.height = height
	cylinder_mesh.radial_segments = 48

	mesh_instance.mesh = cylinder_mesh
	mesh_instance.position = position_value
	mesh_instance.material_override = (
		material_value
	)

	miko_room.add_child(
		mesh_instance
	)


func _room_add_screen(
	node_name: String,
	texture_path: String,
	size: Vector2,
	position_value: Vector3
) -> void:
	if miko_room == null:
		return

	var texture_value: Texture2D = (
		load(texture_path) as Texture2D
	)

	if texture_value == null:
		print(
            "MIKO ROOM WARNING: missing screen texture "
			+ texture_path
		)
		return

	# Frame/backplate makes the display look embedded in the architecture.
	_room_add_box(
		node_name + "Frame",
		Vector3(
			size.x + 0.20,
			size.y + 0.20,
			0.075
		),
		Vector3(
			position_value.x,
			position_value.y,
			position_value.z - 0.045
		),
		room_dark_material
	)

	var screen: MeshInstance3D = (
		MeshInstance3D.new()
	)

	screen.name = node_name

	var quad: QuadMesh = QuadMesh.new()
	quad.size = size

	screen.mesh = quad
	screen.position = position_value

	var screen_material: StandardMaterial3D = (
		StandardMaterial3D.new()
	)

	screen_material.albedo_texture = texture_value
	screen_material.albedo_color = Color(
		0.72,
		0.80,
		0.86,
		1.0
	)
	screen_material.emission_enabled = true
	screen_material.emission = Color(
		0.075,
		0.15,
		0.20,
		1.0
	)
	screen_material.emission_texture = texture_value
	screen_material.metallic = 0.0
	screen_material.roughness = 0.31

	screen.material_override = (
		screen_material
	)

	miko_room.add_child(
		screen
	)


# ============================================================
# CONNECTION / HEALTH
# ============================================================

func _setup_health() -> void:
	health_request = HTTPRequest.new()
	health_request.name = "BrainHealthRequest"
	health_request.timeout = 8.0
	add_child(health_request)

	health_request.request_completed.connect(
		_on_health_request_completed
	)

	health_retry_timer = Timer.new()
	health_retry_timer.name = "BrainHealthRetryTimer"
	health_retry_timer.wait_time = HEALTH_RETRY_SECONDS
	health_retry_timer.one_shot = false
	health_retry_timer.autostart = true
	add_child(health_retry_timer)

	health_retry_timer.timeout.connect(
		_request_health
	)

	_request_health()


func _request_health() -> void:
	if (
		health_request.get_http_client_status()
		!= HTTPClient.STATUS_DISCONNECTED
	):
		return

	var error := health_request.request(
		BRAIN_URL + "/health"
	)

	if error != OK:
		brain_connected = false
		print(
			"MIKO HEALTH ERROR: could not start request. Error=",
			error
		)


func _on_health_request_completed(
	result: int,
	response_code: int,
	_headers: PackedStringArray,
	body: PackedByteArray
) -> void:
	if (
		result == HTTPRequest.RESULT_SUCCESS
		and
		response_code == 200
	):
		var was_connected := brain_connected
		brain_connected = true

		if not was_connected:
			print("MIKO BRAIN CONNECTED")

		var data = JSON.parse_string(
			body.get_string_from_utf8()
		)

		if data is Dictionary:
			print(
				"Brain version: ",
				data.get(
					"version",
                    "unknown"
				)
			)

		return

	if brain_connected:
		print(
			"MIKO BRAIN DISCONNECTED: HTTP=",
			response_code,
			" result=",
			result
		)

	brain_connected = false


# ============================================================
# AUTONOMOUS EVENTS
# ============================================================

func _setup_event_polling() -> void:
	event_request = HTTPRequest.new()
	event_request.name = "BrainEventRequest"
	event_request.timeout = 10.0
	add_child(event_request)

	event_request.request_completed.connect(
		_on_event_request_completed
	)

	event_timer = Timer.new()
	event_timer.name = "BrainEventTimer"
	event_timer.wait_time = EVENT_POLL_SECONDS
	event_timer.one_shot = false
	event_timer.autostart = true
	add_child(event_timer)

	event_timer.timeout.connect(
		_poll_event
	)


func _poll_event() -> void:
	if realtime_voice != null and (realtime_voice.external_voice_active or realtime_voice.is_listening()):
		return
	if not brain_connected:
		return

	# Never let an autonomous message interrupt a user conversation,
	# transcription, thinking, TTS request, or current spoken reply.
	if _interaction_busy():
		return

	if (
		event_request.get_http_client_status()
		!= HTTPClient.STATUS_DISCONNECTED
	):
		return

	var error := event_request.request(
		BRAIN_URL + "/event"
	)

	if error != OK:
		print(
			"MIKO EVENT ERROR: could not poll /event. Error=",
			error
		)


func _on_event_request_completed(
	result: int,
	response_code: int,
	_headers: PackedStringArray,
	body: PackedByteArray
) -> void:
	if result != HTTPRequest.RESULT_SUCCESS:
		return

	if response_code == 204:
		return

	if response_code != 200:
		print(
			"MIKO EVENT ERROR: HTTP=",
			response_code
		)
		return

	# If the user started talking while /event was in flight,
	# user speech wins. Do not talk over them.
	if user_turn_active or mic_recording:
		print("MIKO EVENT SKIPPED: user is talking")
		return

	var data = JSON.parse_string(
		body.get_string_from_utf8()
	)

	if not (data is Dictionary):
		return

	var message := str(
		data.get(
			"message",
            ""
		)
	).strip_edges()

	var emotion := str(
		data.get(
			"emotion",
            "happy"
		)
	).strip_edges()

	var action := str(
		data.get(
			"action",
            "look"
		)
	).strip_edges()

	if message.is_empty():
		return

	print(
		"MIKO EVENT: ",
		message,
		" | action=",
		action,
		" | emotion=",
		emotion
	)

	_set_face_emotion(
		emotion,
		7.0
	)

	_request_miko_speech(
		message,
		emotion,
		action
	)



# ============================================================
# BARGE-IN / NATURAL INTERRUPTION
# ============================================================

func _miko_is_currently_speaking() -> bool:
	if voice_active:
		return true

	if (
		voice_player != null
		and
		voice_player.playing
	):
		return true

	return false


func _barge_in_stop_miko() -> void:
	if barge_in_cooldown > 0.0:
		return

	barge_in_cooldown = (
		BARGE_IN_COOLDOWN_SECONDS
	)

	print(
        "MIKO BARGE-IN: owner interrupted speech"
	)

	# Stop sound immediately before opening the microphone,
	# so Miko does not record his own speaker output.
	if (
		voice_player != null
		and
		voice_player.playing
	):
		voice_player.stop()

	voice_active = false

	_reset_mouth()

	# Stop a big gesture/animation and return to an attentive pose.
	if animation_player != null:
		animation_player.stop()

	action_playing = false
	sleeping_pose = false

	# Stop autonomous travel immediately.
	_interrupt_roam_for_interaction()

	# Face/status reacts as "I'm listening now".
	_set_face_emotion(
		"curious",
		4.0
	)

	if antenna_material != null:
		antenna_material.albedo_color = Color(
			0.20,
			1.00,
			0.48,
			1.0
		)

		antenna_material.emission = Color(
			0.20,
			1.00,
			0.48,
			1.0
		)

	_play_idle()


# ============================================================
# MICROPHONE
# ============================================================

func _setup_microphone() -> void:
	mic_bus_index = AudioServer.get_bus_index(
		MIC_BUS_NAME
	)

	if mic_bus_index == -1:
		AudioServer.add_bus()
		mic_bus_index = AudioServer.bus_count - 1

		AudioServer.set_bus_name(
			mic_bus_index,
			MIC_BUS_NAME
		)

	mic_record_effect = null

	for effect_index in range(
		AudioServer.get_bus_effect_count(
			mic_bus_index
		)
	):
		var effect := AudioServer.get_bus_effect(
			mic_bus_index,
			effect_index
		)

		if effect is AudioEffectRecord:
			mic_record_effect = (
				effect as AudioEffectRecord
			)
			break

	if mic_record_effect == null:
		mic_record_effect = AudioEffectRecord.new()

		AudioServer.add_bus_effect(
			mic_bus_index,
			mic_record_effect
		)

	# We need the mic routed through the bus for recording,
	# but we do not want to hear live microphone feedback.
	AudioServer.set_bus_mute(
		mic_bus_index,
		true
	)

	mic_player = AudioStreamPlayer.new()
	mic_player.name = "MikoMicrophone"
	mic_player.stream = AudioStreamMicrophone.new()
	mic_player.bus = MIC_BUS_NAME
	add_child(mic_player)

	mic_player.play()

	print("MIKO MIC READY")


func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.keycode == KEY_F8 and event.pressed and not event.echo:
		_open_voice_conversation()
		get_viewport().set_input_as_handled()
	if event is InputEventKey and event.keycode == KEY_F7 and event.pressed and not event.echo:
		_toggle_vision()
		get_viewport().set_input_as_handled()


func _process(_delta: float) -> void:
	if barge_in_cooldown > 0.0:
		barge_in_cooldown = maxf(
			0.0,
			barge_in_cooldown - _delta
		)

	_update_lipsync(_delta)
	_update_expression_system(_delta)


func _try_start_mic_recording() -> void:
	if not brain_connected:
		return

	# Avoid recording Miko's own voice or starting a second turn
	# while the previous one is still being processed.
	if _interaction_busy():
		return

	if mic_record_effect == null:
		print(
            "MIKO MIC ERROR: AudioEffectRecord is missing"
		)
		return

	_interrupt_roam_for_interaction()

	mic_recording = true
	user_turn_active = true
	mic_started_msec = Time.get_ticks_msec()

	_set_face_emotion(
		"curious",
		6.0
	)

	mic_record_effect.set_recording_active(
		true
	)

	print("MIKO MIC: RECORDING...")


func _stop_mic_recording() -> void:
	if not mic_recording:
		return

	mic_recording = false

	mic_record_effect.set_recording_active(
		false
	)

	var duration_seconds := (
		float(
			Time.get_ticks_msec()
			- mic_started_msec
		)
		/ 1000.0
	)

	var recording := (
		mic_record_effect.get_recording()
	)

	if recording == null:
		print(
            "MIKO MIC ERROR: no recording returned"
		)
		_finish_user_turn()
		return

	if duration_seconds < MIN_RECORDING_SECONDS:
		print(
            "MIKO MIC: recording too short, ignored"
		)
		_finish_user_turn()
		return

	var audio_data := recording.get_data()

	if audio_data.is_empty():
		print(
            "MIKO MIC ERROR: recording is empty"
		)
		_finish_user_turn()
		return

	var save_error := recording.save_to_wav(
		MIC_WAV_PATH
	)

	if save_error != OK:
		print(
			"MIKO MIC ERROR: could not save WAV. Error=",
			save_error
		)
		_finish_user_turn()
		return

	print(
		"MIKO MIC SAVED: ",
		ProjectSettings.globalize_path(
			MIC_WAV_PATH
		),
		" | seconds=",
		snapped(
			duration_seconds,
			0.01
		),
		" | bytes=",
		audio_data.size()
	)

	_send_recording_to_brain()


# ============================================================
# TRANSCRIBE
# ============================================================

func _setup_transcribe() -> void:
	transcribe_request = HTTPRequest.new()
	transcribe_request.name = "BrainTranscribeRequest"
	transcribe_request.timeout = 45.0
	add_child(transcribe_request)

	transcribe_request.request_completed.connect(
		_on_transcribe_request_completed
	)


func _send_recording_to_brain() -> void:
	if (
		transcribe_request.get_http_client_status()
		!= HTTPClient.STATUS_DISCONNECTED
	):
		print("MIKO TRANSCRIBE BUSY")
		_finish_user_turn()
		return

	var audio_bytes := FileAccess.get_file_as_bytes(
		MIC_WAV_PATH
	)

	if audio_bytes.is_empty():
		print(
            "MIKO TRANSCRIBE ERROR: WAV file is empty"
		)
		_finish_user_turn()
		return

	var boundary := (
        "----MikoGodotBoundary"
		+ str(
			Time.get_ticks_msec()
		)
	)

	var body := PackedByteArray()

	body.append_array(
		(
            "--"
			+ boundary
			+ "\r\n"
		).to_utf8_buffer()
	)

	body.append_array(
		(
            "Content-Disposition: form-data; "
			+ "name=\"audio\"; "
			+ "filename=\"miko_voice.wav\"\r\n"
		).to_utf8_buffer()
	)

	body.append_array(
        "Content-Type: audio/wav\r\n\r\n"
		.to_utf8_buffer()
	)

	body.append_array(
		audio_bytes
	)

	body.append_array(
		(
            "\r\n--"
			+ boundary
			+ "--\r\n"
		).to_utf8_buffer()
	)

	var headers := PackedStringArray([
        "Content-Type: multipart/form-data; boundary="
		+ boundary
	])

	print(
        "MIKO TRANSCRIBE: sending audio to Brain..."
	)

	var error := transcribe_request.request_raw(
		BRAIN_URL + "/transcribe",
		headers,
		HTTPClient.METHOD_POST,
		body
	)

	if error != OK:
		print(
			"MIKO TRANSCRIBE ERROR: could not start request. Error=",
			error
		)
		_finish_user_turn()


func _on_transcribe_request_completed(
	result: int,
	response_code: int,
	_headers: PackedStringArray,
	body: PackedByteArray
) -> void:
	var response_text := (
		body.get_string_from_utf8()
	)

	if (
		result != HTTPRequest.RESULT_SUCCESS
		or
		response_code != 200
	):
		print(
			"MIKO TRANSCRIBE FAILED: HTTP=",
			response_code,
			" result=",
			result,
			" body=",
			response_text
		)
		_request_miko_speech(
			"לא הצלחתי לשמוע אותך. תגיד שוב.",
			"curious",
            "look"
		)
		return

	var data = JSON.parse_string(
		response_text
	)

	if not (data is Dictionary):
		print(
            "MIKO TRANSCRIBE ERROR: invalid JSON"
		)
		_request_miko_speech(
			"לא הבנתי מה שמעתי. תגיד שוב.",
			"curious",
            "look"
		)
		return

	var transcript := str(
		data.get(
			"text",
            ""
		)
	).strip_edges()

	if transcript.is_empty():
		print("MIKO HEARD: [empty after Brain retry]")
		_request_miko_speech(
			"לא שמעתי אותך טוב. תגיד שוב.",
			"curious",
            "look"
		)
		return

	print(
		"MIKO HEARD: ",
		transcript
	)

	_send_message_to_brain(
		transcript
	)


# ============================================================
# THINK / CONVERSATION
# ============================================================

func _setup_think() -> void:
	think_request = HTTPRequest.new()
	think_request.name = "BrainThinkRequest"
	think_request.timeout = 35.0
	add_child(think_request)

	think_request.request_completed.connect(
		_on_think_request_completed
	)


func _send_message_to_brain(
	transcript: String
) -> void:
	if (
		think_request.get_http_client_status()
		!= HTTPClient.STATUS_DISCONNECTED
	):
		print("MIKO THINK BUSY")
		_finish_user_turn()
		return

	var payload := JSON.stringify({
		"message": transcript
	})

	var headers := PackedStringArray([
        "Content-Type: application/json"
	])

	print(
		"MIKO THINK: sending \"",
		transcript,
        "\""
	)

	var error := think_request.request(
		BRAIN_URL + "/think",
		headers,
		HTTPClient.METHOD_POST,
		payload
	)

	if error != OK:
		print(
			"MIKO THINK ERROR: could not start request. Error=",
			error
		)
		_finish_user_turn()


func _on_think_request_completed(
	result: int,
	response_code: int,
	_headers: PackedStringArray,
	body: PackedByteArray
) -> void:
	var response_text := (
		body.get_string_from_utf8()
	)

	if (
		result != HTTPRequest.RESULT_SUCCESS
		or
		response_code != 200
	):
		print(
			"MIKO THINK FAILED: HTTP=",
			response_code,
			" result=",
			result,
			" body=",
			response_text
		)
		_request_miko_speech(
			"נתקעתי לשנייה. תגיד לי שוב.",
			"curious",
            "look"
		)
		return

	var data = JSON.parse_string(
		response_text
	)

	if not (data is Dictionary):
		print(
            "MIKO THINK ERROR: invalid JSON"
		)
		_request_miko_speech(
			"משהו נתקע לי בראש. תגיד שוב.",
			"curious",
            "look"
		)
		return

	var message := str(
		data.get(
			"message",
            ""
		)
	).strip_edges()

	var emotion := str(
		data.get(
			"emotion",
            "happy"
		)
	).strip_edges()

	var action := str(
		data.get(
			"action",
            "look"
		)
	).strip_edges()

	var external_action = data.get(
		"external_action",
		null
	)

	if external_action is Dictionary:
		var external_dict: Dictionary = external_action
		var external_status: String = str(
			external_dict.get(
				"status",
                ""
			)
		)

		if external_status == "awaiting_confirmation":
			var action_data = external_dict.get(
				"action",
				null
			)

			if action_data is Dictionary:
				var action_dict: Dictionary = action_data
				var args_data = action_dict.get(
					"args",
					{}
				)

				if args_data is Dictionary:
					var args_dict: Dictionary = args_data
					print("========== MIKO EMAIL DRAFT ==========")
					print("TO: ", str(args_dict.get("to", "")))
					print("SUBJECT: ", str(args_dict.get("subject", "")))
					print("BODY: ", str(args_dict.get("body", "")))
					print("======================================")

	if message.is_empty():
		print("MIKO THINK ERROR: empty reply")
		_request_miko_speech(
			"לא יצאה לי תשובה. דבר איתי שוב.",
			"curious",
            "look"
		)
		return

	print(
		"MIKO REPLY: ",
		message,
		" | action=",
		action,
		" | emotion=",
		emotion
	)

	_set_face_emotion(
		emotion,
		8.0
	)

	# Keep the user's turn locked until TTS has at least
	# started. This prevents autonomous events from stealing
	# the action while Miko's voice is being generated.
	_request_miko_speech(
		message,
		emotion,
		action
	)


# ============================================================
# SPEAK / VOICE
# ============================================================

func _setup_voice() -> void:
	speak_request = HTTPRequest.new()
	speak_request.name = "BrainSpeakRequest"
	speak_request.timeout = 45.0
	add_child(speak_request)

	speak_request.request_completed.connect(
		_on_speak_request_completed
	)

	_setup_voice_spectrum()

	voice_player = AudioStreamPlayer.new()
	voice_player.name = "MikoVoicePlayer"
	voice_player.bus = VOICE_BUS_NAME
	add_child(voice_player)

	voice_player.finished.connect(
		_on_voice_finished
	)


func _request_miko_speech(
	message: String,
	emotion: String,
	action: String
) -> void:
	if realtime_voice != null:
		_set_face_emotion(emotion, 7.0)
		_play_brain_action(action)
		realtime_voice.send_spontaneous(message)
		return

	if message.strip_edges().is_empty():
		_finish_user_turn()
		return

	_interrupt_roam_for_interaction()

	if (
		speak_request.get_http_client_status()
		!= HTTPClient.STATUS_DISCONNECTED
	):
		print(
            "MIKO SPEAK BUSY: playing animation without voice"
		)
		_play_brain_action(
			action
		)
		_finish_user_turn()
		return

	speech_message = message
	speech_emotion = emotion
	speech_action = action
	tts_request_active = true

	var payload := JSON.stringify({
		"text": message,
		"emotion": emotion
	})

	var headers := PackedStringArray([
        "Content-Type: application/json"
	])

	var error := speak_request.request(
		BRAIN_URL + "/speak",
		headers,
		HTTPClient.METHOD_POST,
		payload
	)

	if error != OK:
		tts_request_active = false

		print(
			"MIKO SPEAK ERROR: could not start request. Error=",
			error
		)

		_play_brain_action(
			speech_action
		)

		_finish_user_turn()


func _on_speak_request_completed(
	result: int,
	response_code: int,
	_headers: PackedStringArray,
	body: PackedByteArray
) -> void:
	tts_request_active = false

	if (
		result != HTTPRequest.RESULT_SUCCESS
		or
		response_code != 200
	):
		print(
			"MIKO SPEAK FAILED: HTTP=",
			response_code,
			" result=",
			result
		)

		# Even when TTS fails, Miko still reacts physically.
		_play_brain_action(
			speech_action
		)

		_finish_user_turn()
		return

	if body.is_empty():
		print(
            "MIKO SPEAK ERROR: empty audio response"
		)

		_play_brain_action(
			speech_action
		)

		_finish_user_turn()
		return

	var mp3 := AudioStreamMP3.new()
	mp3.data = body

	voice_player.stream = mp3

	# Start voice and body together.
	_play_brain_action(
		speech_action
	)

	voice_active = true
	voice_player.play()

	print(
		"MIKO SYNC START: action=",
		speech_action,
        " | voice=yes"
	)

	# Conversation processing is complete.
	# _interaction_busy() still stays true while voice_player is playing.
	_finish_user_turn()


func _on_voice_finished() -> void:
	voice_active = false
	_reset_mouth()

	# If the user has already started talking, do not trigger
	# post-speech movement underneath their recording.
	if mic_recording or user_turn_active:
		print(
            "MIKO VOICE FINISHED: user already has the turn"
		)
		return

	# Resume autonomous life relatively soon after a conversation,
	# but not instantly enough to feel hyperactive.
	if (
		roam_timer != null
		and
		not sleeping_pose
	):
		_schedule_next_roam(
			1.8,
			3.6
		)

	_conversation_afterglow()

	print("MIKO VOICE FINISHED")


# ============================================================
# ROBOT FEATURES / REAL LIP SYNC
# ============================================================

func _find_animation_player() -> AnimationPlayer:
	var direct := get_node_or_null(
        "MikoScene/AnimationPlayer"
	)

	if direct is AnimationPlayer:
		return direct as AnimationPlayer

	var found := find_child(
		"AnimationPlayer",
		true,
		false
	)

	if found is AnimationPlayer:
		return found as AnimationPlayer

	return null


func _setup_robot_features() -> void:
	mouth_node = _find_named_node3d(
        "MikoMouth"
	)

	eyes_node = _find_named_node3d(
        "MikoEyes"
	)

	wheel_left_node = _find_named_node3d(
        "MikoWheelL"
	)

	wheel_right_node = _find_named_node3d(
        "MikoWheelR"
	)

	# BLACK tread-only meshes.
	wheel_left_tread_mesh = _find_named_mesh_instance(
        "MikoWheelLTreadMeshNode"
	)

	wheel_right_tread_mesh = _find_named_mesh_instance(
        "MikoWheelRTreadMeshNode"
	)

	# The compact Presence stage uses the original orange wheel housing.
	# Legacy room builds can still use the generated guard geometry.
	var old_left_orange := _find_named_mesh_instance(
        "MikoWheelLStaticMeshNode"
	)

	var old_right_orange := _find_named_mesh_instance(
        "MikoWheelRStaticMeshNode"
	)

	if old_left_orange != null:
		old_left_orange.visible = presence != null

	if old_right_orange != null:
		old_right_orange.visible = presence != null

	# Safety: imported tread pivot nodes always stay at their original pose.
	# They are NEVER physically spun.
	var left_spin := _find_named_node3d(
        "MikoWheelLTreadSpin"
	)

	var right_spin := _find_named_node3d(
        "MikoWheelRTreadSpin"
	)

	if left_spin != null:
		left_spin.rotation = Vector3.ZERO

	if right_spin != null:
		right_spin.rotation = Vector3.ZERO

	if mouth_node != null:
		mouth_base_scale = mouth_node.scale

		# Hide the original thin line; replace it with a rounded,
		# emissive mouth that can visibly open and close.
		var old_mouth := _find_named_mesh_instance(
            "MikoMouthMeshNode"
		)

		if old_mouth != null:
			old_mouth.visible = false

		_create_lipsync_mouth()

		print(
            "MIKO ROBOT: visible lip-sync mouth ready"
		)
	else:
		print(
            "MIKO ROBOT WARNING: MikoMouth node not found"
		)

	if eyes_node != null:
		print(
            "MIKO ROBOT: animated eyes ready"
		)

	if presence == null:
		_setup_clean_wheel_guards()
		_setup_tread_materials()


func _create_lipsync_mouth() -> void:
	if mouth_node == null:
		return

	lipsync_mouth_visual = MeshInstance3D.new()
	lipsync_mouth_visual.name = "MikoRuntimeLipSyncMouth"

	var mouth_mesh := SphereMesh.new()
	mouth_mesh.radius = 0.5
	mouth_mesh.height = 1.0
	mouth_mesh.radial_segments = 24
	mouth_mesh.rings = 12

	lipsync_mouth_visual.mesh = mouth_mesh
	lipsync_mouth_visual.scale = (
		lipsync_mouth_closed_scale
	)

	# The MikoMouth pivot is already centered on the original mouth.
	lipsync_mouth_visual.position = Vector3.ZERO

	var mouth_material := StandardMaterial3D.new()
	mouth_material.albedo_color = Color(
		0.12,
		0.92,
		1.0,
		1.0
	)
	mouth_material.metallic = 0.0
	mouth_material.roughness = 0.24
	mouth_material.emission_enabled = true
	mouth_material.emission = Color(
		0.10,
		0.84,
		1.0,
		1.0
	)

	lipsync_mouth_visual.material_override = (
		mouth_material
	)

	mouth_node.add_child(
		lipsync_mouth_visual
	)


func _setup_clean_wheel_guards() -> void:
	wheel_guard_material = StandardMaterial3D.new()
	wheel_guard_material.albedo_color = Color(
		0.82,
		0.245,
		0.045,
		1.0
	)
	wheel_guard_material.metallic = 0.48
	wheel_guard_material.roughness = 0.28

	if wheel_left_node != null:
		_create_clean_wheel_guard(
			wheel_left_node,
			-1.0,
            "L"
		)

	if wheel_right_node != null:
		_create_clean_wheel_guard(
			wheel_right_node,
			1.0,
            "R"
		)

	print(
        "MIKO ROBOT: clean rigid orange wheel housings ready"
	)


func _create_clean_wheel_guard(
	wheel_node: Node3D,
	side_sign: float,
	label: String
) -> void:
	var guard_root := Node3D.new()
	guard_root.name = (
        "MikoCleanWheelGuard"
		+ label
	)
	wheel_node.add_child(
		guard_root
	)

	# side_sign: L=-1, R=+1.
	# The body is inward from the wheel, so the inner support is
	# opposite the side sign.
	var inner_x := (
		-side_sign * 0.155
	)

	# Main rigid mounting plate behind the tread.
	_add_guard_box(
		guard_root,
		"MainPlate",
		Vector3(
			0.055,
			0.42,
			0.16
		),
		Vector3(
			inner_x,
			0.01,
			0.045
		)
	)

	# Upper and lower fork arms.
	_add_guard_box(
		guard_root,
		"UpperFork",
		Vector3(
			0.075,
			0.075,
			0.30
		),
		Vector3(
			inner_x,
			0.245,
			-0.005
		)
	)

	_add_guard_box(
		guard_root,
		"LowerFork",
		Vector3(
			0.075,
			0.075,
			0.30
		),
		Vector3(
			inner_x,
			-0.245,
			-0.005
		)
	)

	# Mechanical hub: stays fixed while the visual tread moves.
	var hub := MeshInstance3D.new()
	hub.name = "Hub"

	var cylinder := CylinderMesh.new()
	cylinder.top_radius = 0.105
	cylinder.bottom_radius = 0.105
	cylinder.height = 0.085
	cylinder.radial_segments = 24

	hub.mesh = cylinder
	hub.material_override = (
		wheel_guard_material
	)
	hub.position = Vector3(
		inner_x,
		0.0,
		0.02
	)

	# Godot cylinders point along Y; rotate the hub to the wheel axle (X).
	hub.rotation_degrees.z = 90.0

	guard_root.add_child(
		hub
	)


func _add_guard_box(
	parent: Node3D,
	box_name: String,
	size: Vector3,
	position_value: Vector3
) -> void:
	var piece := MeshInstance3D.new()
	piece.name = box_name

	var box_mesh := BoxMesh.new()
	box_mesh.size = size

	piece.mesh = box_mesh
	piece.position = position_value
	piece.material_override = (
		wheel_guard_material
	)

	parent.add_child(
		piece
	)


func _setup_tread_materials() -> void:
	if (
		wheel_left_tread_mesh == null
		or
		wheel_right_tread_mesh == null
	):
		print(
            "MIKO ROBOT WARNING: tread mesh nodes not found - tread animation disabled"
		)
		return

	var tread_shader := Shader.new()

	tread_shader.code = """
shader_type spatial;
render_mode cull_back, depth_draw_opaque;

uniform float tread_phase = 0.0;
uniform float center_y = 0.075652;
uniform float center_z = -0.690360;

varying vec3 tread_local_pos;

void vertex() {
    tread_local_pos = VERTEX;
}

void fragment() {
    // The source robot uses a tank-like black tread.
    // Instead of rotating the whole tread geometry (which makes it orbit
    // around the robot), animate a moving rubber-ridge pattern around
    // the tread's own Y/Z perimeter.
    float angle = atan(
        tread_local_pos.y - center_y,
        tread_local_pos.z - center_z
    );

    float main_wave = 0.5 + 0.5 * sin(
        angle * 26.0 + tread_phase
    );

    float fine_wave = 0.5 + 0.5 * sin(
        angle * 52.0 + tread_phase * 2.0
    );

    float main_ridge = smoothstep(
        0.58,
        0.91,
        main_wave
    );

    float fine_ridge = smoothstep(
        0.72,
        0.96,
        fine_wave
    );

    float ridge = clamp(
        main_ridge * 0.58
        + fine_ridge * 0.16,
        0.0,
        1.0
    );

    vec3 deep_rubber = vec3(
        0.010,
        0.012,
        0.015
    );

    vec3 raised_rubber = vec3(
        0.145,
        0.155,
        0.165
    );

    ALBEDO = mix(
        deep_rubber,
        raised_rubber,
        ridge
    );

    ROUGHNESS = 0.72;
    METALLIC = 0.08;
    SPECULAR = 0.34;
}
"""

	wheel_left_tread_material = ShaderMaterial.new()
	wheel_left_tread_material.shader = tread_shader

	wheel_right_tread_material = ShaderMaterial.new()
	wheel_right_tread_material.shader = tread_shader

	wheel_left_tread_mesh.material_override = (
		wheel_left_tread_material
	)

	wheel_right_tread_mesh.material_override = (
		wheel_right_tread_material
	)

	tread_left_phase = 0.0
	tread_right_phase = 0.0

	print(
        "MIKO ROBOT: SAFE tread animation ready - wheel geometry stays attached"
	)


func _find_named_mesh_instance(
	wanted_name: String
) -> MeshInstance3D:
	var found := find_child(
		wanted_name,
		true,
		false
	)

	if found is MeshInstance3D:
		return found as MeshInstance3D

	return null


func _find_named_node3d(
	wanted_name: String
) -> Node3D:
	var found := find_child(
		wanted_name,
		true,
		false
	)

	if found is Node3D:
		return found as Node3D

	return null





# ============================================================
# CONVERSATION AFTERGLOW
# ============================================================

func _conversation_afterglow() -> void:
	if (
		movement_root == null
		or
		drive_active
		or
		sleeping_pose
	):
		return

	var chance := roam_rng.randf()

	match face_emotion:
		"excited":
			if chance < 0.72:
				_start_choreo(
					"excited_reply",
					[
						_drive_step(
							_bounded_target(
								roam_rng.randf_range(
									-0.18,
									0.18
								),
								0.20
							),
							0.42,
							roam_rng.randf_range(
								-16.0,
								16.0
							),
							8.0,
							8.0,
							0.0
						),
						_drive_step(
							_bounded_target(
								roam_rng.randf_range(
									-0.10,
									0.10
								),
								0.10
							),
							0.40,
							0.0,
							-6.0,
							-6.0,
							0.0
						)
					]
				)

		"curious":
			if chance < 0.62:
				_start_choreo(
					"curious_reply",
					[
						_drive_step(
							_bounded_target(
								roam_rng.randf_range(
									-0.10,
									0.10
								),
								0.30
							),
							0.55,
							roam_rng.randf_range(
								-8.0,
								8.0
							),
							6.0,
							6.0,
							0.0
						)
					]
				)

		"shy":
			if chance < 0.48:
				_start_choreo(
					"shy_reply",
					[
						_drive_step(
							_bounded_target(
								roam_rng.randf_range(
									-0.16,
									0.16
								),
								-0.18
							),
							0.50,
							roam_rng.randf_range(
								-10.0,
								10.0
							),
							-5.0,
							-5.0,
							0.0
						)
					]
				)

		"angry":
			if chance < 0.50:
				_start_choreo(
					"angry_reply",
					[
						_drive_step(
							_bounded_target(
								0.0,
								-0.08
							),
							0.34,
							14.0,
							6.0,
							-6.0,
							4.0
						),
						_drive_step(
							_bounded_target(
								0.0,
								-0.08
							),
							0.30,
							-14.0,
							-6.0,
							6.0,
							-4.0
						)
					]
				)

		"sad", "sleepy":
			pass

		_:
			if chance < 0.28:
				_start_choreo(
					"friendly_reply",
					[
						_drive_step(
							_bounded_target(
								roam_rng.randf_range(
									-0.12,
									0.12
								),
								0.12
							),
							0.42,
							roam_rng.randf_range(
								-8.0,
								8.0
							),
							5.0,
							5.0,
							0.0
						)
					]
				)


# ============================================================
# EXPRESSIVE FACE / EYES / ANTENNA
# ============================================================

func _setup_expression_system() -> void:
	face_rng.randomize()

	eyes_mesh = _find_named_mesh_instance(
        "MikoEyesMeshNode"
	)

	if eyes_mesh != null:
		eyes_base_position = eyes_mesh.position
		eyes_base_scale = eyes_mesh.scale

		eyes_material = StandardMaterial3D.new()
		eyes_material.albedo_color = Color(
			0.12,
			0.92,
			1.0,
			1.0
		)
		eyes_material.emission_enabled = true
		eyes_material.emission = Color(
			0.08,
			0.82,
			1.0,
			1.0
		)
		eyes_material.metallic = 0.0
		eyes_material.roughness = 0.18

		eyes_mesh.material_override = (
			eyes_material
		)

		print(
            "MIKO FACE: expressive eyes ready"
		)

	antenna_mesh = _find_named_mesh_instance(
        "MikoAntennaLights"
	)

	if antenna_mesh != null:
		antenna_material = StandardMaterial3D.new()
		antenna_material.albedo_color = Color(
			0.10,
			0.86,
			1.0,
			1.0
		)
		antenna_material.emission_enabled = true
		antenna_material.emission = Color(
			0.08,
			0.80,
			1.0,
			1.0
		)
		antenna_material.metallic = 0.08
		antenna_material.roughness = 0.22

		antenna_mesh.material_override = (
			antenna_material
		)

		print(
            "MIKO FACE: antenna status light ready"
		)

	blink_wait = face_rng.randf_range(
		1.8,
		4.8
	)

	gaze_wait = face_rng.randf_range(
		0.7,
		2.2
	)

	_set_face_emotion(
		"happy",
		2.0
	)


func _set_face_emotion(
	emotion: String,
	seconds: float = 6.0
) -> void:
	var normalized := (
		emotion.strip_edges().to_lower()
	)

	match normalized:
		"happy", "sad", "angry", "sleepy", "excited", "curious", "shy":
			face_emotion = normalized
		_:
			face_emotion = "happy"

	face_emotion_seconds = maxf(
		seconds,
		0.25
	)
	if presence != null:
		presence.set_emotion(face_emotion)


func _emotion_eye_color() -> Color:
	match face_emotion:
		"excited":
			return Color(
				0.34,
				0.96,
				1.0,
				1.0
			)

		"curious":
			return Color(
				0.16,
				1.0,
				0.70,
				1.0
			)

		"sad":
			return Color(
				0.22,
				0.50,
				1.0,
				1.0
			)

		"angry":
			return Color(
				1.0,
				0.36,
				0.18,
				1.0
			)

		"sleepy":
			return Color(
				0.58,
				0.42,
				1.0,
				1.0
			)

		"shy":
			return Color(
				1.0,
				0.40,
				0.68,
				1.0
			)

		_:
			return Color(
				0.12,
				0.92,
				1.0,
				1.0
			)


func _emotion_eye_shape() -> Vector2:
	match face_emotion:
		"excited":
			return Vector2(
				1.10,
				1.12
			)

		"curious":
			return Vector2(
				1.04,
				1.10
			)

		"sad":
			return Vector2(
				0.96,
				0.80
			)

		"angry":
			return Vector2(
				1.04,
				0.68
			)

		"sleepy":
			return Vector2(
				1.02,
				0.46
			)

		"shy":
			return Vector2(
				0.94,
				0.90
			)

		_:
			return Vector2.ONE


func _update_expression_system(
	delta: float
) -> void:
	face_clock += delta

	if face_emotion_seconds > 0.0:
		face_emotion_seconds -= delta
	elif face_emotion != "happy":
		face_emotion = "happy"

	_update_blink(
		delta
	)

	_update_gaze(
		delta
	)

	_update_eye_visuals(
		delta
	)

	_update_antenna_visual(
		delta
	)


func _update_blink(
	delta: float
) -> void:
	if blink_active:
		blink_progress += (
			delta * 9.0
		)

		if blink_progress >= 1.0:
			blink_progress = 0.0
			blink_active = false
			blink_wait = (
				face_rng.randf_range(
					2.0,
					5.5
				)
			)

		return

	blink_wait -= delta

	if blink_wait <= 0.0:
		blink_active = true
		blink_progress = 0.0


func _blink_amount() -> float:
	if not blink_active:
		return 0.0

	return sin(
		blink_progress * PI
	)


func _update_gaze(
	delta: float
) -> void:
	# IMPORTANT:
	# The imported robot eye mesh is clipped by the face shell.
	# Moving that mesh sideways creates half-moon / disappearing eyes.
	# Keep the eye geometry centered at all times.
	gaze_target = Vector2.ZERO

	gaze_current = gaze_current.lerp(
		Vector2.ZERO,
		clampf(
			delta * 10.0,
			0.0,
			1.0
		)
	)


func _update_eye_visuals(
	delta: float
) -> void:
	if eyes_mesh == null:
		return

	# Never deform or slide the original eye mesh.
	# The face shell masks the edges of the eye geometry, so scaling or
	# translating it causes the broken crescent shapes seen in the video.
	eyes_mesh.position = eyes_base_position
	eyes_mesh.scale = eyes_base_scale

	if eyes_material == null:
		return

	var eye_color := (
		_emotion_eye_color()
	)

	# Natural blink without touching geometry:
	# briefly dim both eyes together so they "close" cleanly.
	var blink := clampf(
		_blink_amount(),
		0.0,
		1.0
	)

	var brightness := lerpf(
		1.0,
		0.018,
		blink
	)

	var target_albedo := Color(
		eye_color.r * brightness,
		eye_color.g * brightness,
		eye_color.b * brightness,
		1.0
	)

	# Emission is slightly brighter while open and almost off while blinking.
	var emission_strength := lerpf(
		1.18,
		0.005,
		blink
	)

	var target_emission := Color(
		eye_color.r * emission_strength,
		eye_color.g * emission_strength,
		eye_color.b * emission_strength,
		1.0
	)

	eyes_material.albedo_color = (
		eyes_material.albedo_color.lerp(
			target_albedo,
			clampf(
				delta * 24.0,
				0.0,
				1.0
			)
		)
	)

	eyes_material.emission = (
		eyes_material.emission.lerp(
			target_emission,
			clampf(
				delta * 28.0,
				0.0,
				1.0
			)
		)
	)


func _current_status_color() -> Color:
	if mic_recording:
		return Color(
			0.20,
			1.00,
			0.48,
			1.0
		)

	if user_turn_active:
		return Color(
			0.72,
			0.38,
			1.00,
			1.0
		)

	if _miko_is_currently_speaking():
		return Color(
			0.10,
			0.90,
			1.00,
			1.0
		)

	return _emotion_eye_color()


func _update_antenna_visual(
	delta: float
) -> void:
	if antenna_material == null:
		return

	var target := (
		_current_status_color()
	)

	var pulse := (
		0.76
		+ 0.24
		* sin(
			face_clock * (
				8.0
				if user_turn_active
				else 3.2
			)
		)
	)

	var emission_target := (
		target * (
			1.05
			+ 0.35 * pulse
		)
	)

	antenna_material.albedo_color = (
		antenna_material.albedo_color.lerp(
			target,
			clampf(
				delta * 7.0,
				0.0,
				1.0
			)
		)
	)

	antenna_material.emission = (
		antenna_material.emission.lerp(
			emission_target,
			clampf(
				delta * 9.0,
				0.0,
				1.0
			)
		)
	)



# ============================================================
# SOCIAL BODY LANGUAGE
# ============================================================

func _setup_social_body_language() -> void:
	social_body = _find_named_node3d(
        "Robot_008"
	)

	if social_body == null:
		print(
            "MIKO BODY WARNING: Robot_008 node not found"
		)
		return

	social_body_base_position = (
		social_body.position
	)

	social_body_base_rotation = (
		social_body.rotation
	)

	social_body_ready = true

	print(
        "MIKO BODY: social body language ready"
	)


func _social_emotion_intensity() -> float:
	match face_emotion:
		"excited":
			return 1.35

		"curious":
			return 1.10

		"angry":
			return 1.10

		"sad":
			return 0.72

		"sleepy":
			return 0.48

		"shy":
			return 0.82

		_:
			return 1.0


func _update_social_body_language(
	delta: float
) -> void:
	if (
		not social_body_ready
		or
		social_body == null
	):
		return

	var target_position := (
		social_body_base_position
	)

	var target_rotation := (
		social_body_base_rotation
	)

	var intensity := (
		_social_emotion_intensity()
	)

	# Do not fight the large movement choreography.
	# While driving, keep only a tiny suspension/body presence.
	if drive_active:
		target_rotation.z += (
			sin(face_clock * 5.0)
			* deg_to_rad(0.35)
		)

	# LISTENING:
	# lean in a little and stay comparatively still.
	elif mic_recording:
		target_rotation.x += (
			deg_to_rad(-3.0)
		)

		target_rotation.z += (
			sin(face_clock * 2.3)
			* deg_to_rad(0.45)
		)

		target_position.y += (
			0.006
			* sin(face_clock * 3.0)
		)

	# THINKING / TRANSCRIBING:
	# small "processing" side movement, without driving away.
	elif user_turn_active:
		target_rotation.y += (
			sin(face_clock * 2.0)
			* deg_to_rad(3.8)
		)

		target_rotation.z += (
			sin(face_clock * 3.1)
			* deg_to_rad(1.6)
		)

		target_position.y += (
			0.010
			* (
				0.5
				+ 0.5
				* sin(face_clock * 4.2)
			)
		)

	# SPEAKING:
	# use actual lip energy to make the whole body feel connected to voice.
	elif _miko_is_currently_speaking():
		var speech_energy := clampf(
			lip_open_amount,
			0.0,
			1.0
		)

		var nod_direction := -1.0

		if face_emotion == "sad":
			nod_direction = 1.0

		target_rotation.x += (
			deg_to_rad(
				nod_direction
				* (
					0.8
					+ 2.8 * speech_energy
				)
				* intensity
			)
		)

		target_rotation.z += (
			sin(face_clock * 4.4)
			* deg_to_rad(
				0.9
				* intensity
			)
		)

		target_position.y += (
			speech_energy
			* 0.012
			* intensity
		)

		# Emotion-specific speaking posture.
		match face_emotion:
			"excited":
				target_position.y += (
					0.012
					* absf(
						sin(
							face_clock * 7.0
						)
					)
				)

			"curious":
				target_rotation.z += (
					deg_to_rad(2.2)
				)

			"angry":
				target_rotation.x += (
					deg_to_rad(-2.0)
				)

			"sad":
				target_rotation.x += (
					deg_to_rad(2.6)
				)

			"shy":
				target_rotation.z += (
					deg_to_rad(-1.8)
				)

			"sleepy":
				target_rotation.x += (
					deg_to_rad(3.0)
				)

	# IDLE:
	# extremely small "breathing" / mechanical life.
	else:
		var idle_breath := (
			sin(face_clock * 1.55)
		)

		target_position.y += (
			idle_breath
			* 0.0045
		)

		target_rotation.z += (
			sin(face_clock * 0.72)
			* deg_to_rad(0.45)
		)

		match face_emotion:
			"curious":
				target_rotation.z += (
					deg_to_rad(1.2)
				)

			"sad":
				target_rotation.x += (
					deg_to_rad(1.4)
				)

			"sleepy":
				target_rotation.x += (
					deg_to_rad(2.0)
				)

			"excited":
				target_position.y += (
					0.004
					* absf(
						sin(
							face_clock * 4.8
						)
					)
				)

	# Smooth the additive body layer so state transitions do not snap.
	social_body.position = (
		social_body.position.lerp(
			target_position,
			clampf(
				delta * 7.5,
				0.0,
				1.0
			)
		)
	)

	social_body.rotation.x = lerp_angle(
		social_body.rotation.x,
		target_rotation.x,
		clampf(
			delta * 8.0,
			0.0,
			1.0
		)
	)

	social_body.rotation.y = lerp_angle(
		social_body.rotation.y,
		target_rotation.y,
		clampf(
			delta * 7.0,
			0.0,
			1.0
		)
	)

	social_body.rotation.z = lerp_angle(
		social_body.rotation.z,
		target_rotation.z,
		clampf(
			delta * 8.0,
			0.0,
			1.0
		)
	)


func _reset_social_body_language() -> void:
	if (
		not social_body_ready
		or
		social_body == null
	):
		return

	social_body.position = (
		social_body_base_position
	)

	social_body.rotation = (
		social_body_base_rotation
	)


# ============================================================
# LIVING MOVEMENT / AUTONOMOUS ROAMING
# ============================================================

func _setup_alive_movement() -> void:
	roam_rng.randomize()

	# World-space motion belongs to the whole imported Miko scene/holder.
	# Internal pieces (mouth, eyes, wheel tread) remain independent.
	var holder := get_node_or_null("MikoHolder")

	if holder is Node3D:
		movement_root = holder as Node3D
	else:
		var direct_scene := get_node_or_null("MikoScene")

		if direct_scene is Node3D:
			movement_root = direct_scene as Node3D
		else:
			var found_scene := find_child(
				"MikoScene",
				true,
				false
			)

			if found_scene is Node3D:
				movement_root = found_scene as Node3D

	if movement_root == null:
		print(
            "MIKO MOVEMENT WARNING: MikoScene/MikoHolder not found"
		)
		return

	movement_home_position = movement_root.position
	movement_home_rotation = movement_root.rotation

	roam_timer = Timer.new()
	roam_timer.name = "MikoRoamTimer"
	roam_timer.one_shot = true
	add_child(roam_timer)

	roam_timer.timeout.connect(
		_choose_next_roam_behavior
	)

	_schedule_next_roam()

	print(
        "MIKO MOVEMENT: cinematic roaming ready"
	)


func _schedule_next_roam(
	minimum_delay: float = ROAM_MIN_DELAY,
	maximum_delay: float = ROAM_MAX_DELAY
) -> void:
	if roam_timer == null:
		return

	roam_timer.wait_time = roam_rng.randf_range(
		minimum_delay,
		maximum_delay
	)
	roam_timer.start()


func _choose_next_roam_behavior() -> void:
	if movement_root == null:
		return

	if (
		_interaction_busy()
		or
		sleeping_pose
		or
		drive_active
		or
		not drive_queue.is_empty()
		or
		action_playing
	):
		_schedule_next_roam(0.9, 1.8)
		return

	var offset_from_home := (
		movement_root.position
		- movement_home_position
	)

	# If Miko gets too far away, return with a deliberate maneuver.
	if Vector2(offset_from_home.x, offset_from_home.z).length() > dynamic_roam_max_distance:
		_start_choreo(
			"return_home",
			[
				_drive_step(
					movement_home_position,
					1.25,
					0.0,
					7.0,
					7.0,
					0.0
				)
			]
		)
		return

	var roll := roam_rng.randi_range(0, 99)

	if roll < 15:
		_move_free_explore()
	elif roll < 29:
		_move_long_range_patrol()
	elif roll < 42:
		_move_scout_dash()
	elif roll < 54:
		_move_reverse_peek()
	elif roll < 66:
		_move_slalom()
	elif roll < 77:
		_move_orbit_arc()
	elif roll < 86:
		_move_showoff_spin()
	elif roll < 94:
		_move_side_reposition()
	else:
		_move_curiosity_approach()


func _bounded_target(
	x_offset: float,
	z_offset: float
) -> Vector3:
	return Vector3(
		clampf(
			movement_home_position.x + x_offset,
			movement_home_position.x - dynamic_roam_radius_x,
			movement_home_position.x + dynamic_roam_radius_x
		),
		movement_home_position.y,
		clampf(
			movement_home_position.z + z_offset,
			movement_home_position.z - dynamic_roam_radius_z,
			movement_home_position.z + dynamic_roam_radius_z
		)
	)


func _drive_step(
	target: Vector3,
	duration: float,
	yaw_degrees: float,
	left_wheel_speed: float,
	right_wheel_speed: float,
	lean_degrees: float = 0.0,
	spin_radians: float = 0.0
) -> Dictionary:
	return {
		"target": target,
		"duration": duration,
		"yaw": yaw_degrees,
		"left": left_wheel_speed,
		"right": right_wheel_speed,
		"lean": lean_degrees,
		"spin": spin_radians
	}


func _start_choreo(
	choreo_name: String,
	steps: Array
) -> void:
	if steps.is_empty():
		_schedule_next_roam()
		return

	drive_queue = steps.duplicate(true)
	active_choreo = choreo_name

	print(
		"MIKO MOVE: ",
		active_choreo
	)

	_start_next_drive_step()


func _start_next_drive_step() -> void:
	if drive_queue.is_empty():
		active_choreo = ""
		_schedule_next_roam()
		return

	var step: Dictionary = drive_queue.pop_front()

	_begin_drive_to(
		step.get("target", movement_root.position),
		active_choreo,
		float(step.get("duration", 1.0)),
		float(step.get("yaw", 0.0)),
		float(step.get("left", 0.0)),
		float(step.get("right", 0.0)),
		float(step.get("lean", 0.0)),
		float(step.get("spin", 0.0))
	)



func _move_free_explore() -> void:
	# A longer unscripted-feeling exploration: Miko moves to several
	# distinct areas instead of oscillating around one tiny spot.
	var side := (
		1.0
		if roam_rng.randf() > 0.5
		else -1.0
	)

	var x1 := roam_rng.randf_range(
		0.42,
		0.82
	) * side

	var z1 := roam_rng.randf_range(
		-0.50,
		0.10
	)

	var x2 := roam_rng.randf_range(
		0.20,
		0.65
	) * -side

	var z2 := roam_rng.randf_range(
		0.22,
		0.58
	)

	var x3 := roam_rng.randf_range(
		-0.30,
		0.30
	)

	var z3 := roam_rng.randf_range(
		-0.58,
		-0.24
	)

	_start_choreo(
		"free_explore",
		[
			_drive_step(
				_bounded_target(
					x1,
					z1
				),
				1.05,
				-24.0 * side,
				10.0,
				7.0,
				-2.0 * side
			),
			_drive_step(
				_bounded_target(
					x2,
					z2
				),
				1.30,
				26.0 * side,
				7.0,
				11.0,
				2.2 * side
			),
			_drive_step(
				_bounded_target(
					x3,
					z3
				),
				1.10,
				-10.0 * side,
				10.0,
				9.0,
				-1.5 * side
			),
			_drive_step(
				_bounded_target(
					x3 * 0.35,
					z3 * 0.30
				),
				0.72,
				0.0,
				-7.0,
				-7.0,
				0.0
			)
		]
	)


func _move_long_range_patrol() -> void:
	# Move far away, sweep to the other side, come close to the user,
	# then settle near the middle. This makes the whole space feel usable.
	var side := (
		1.0
		if roam_rng.randf() > 0.5
		else -1.0
	)

	_start_choreo(
		"long_range_patrol",
		[
			_drive_step(
				_bounded_target(
					0.68 * side,
					-0.60
				),
				1.20,
				-28.0 * side,
				11.0,
				8.0,
				-2.8 * side
			),
			_drive_step(
				_bounded_target(
					-0.72 * side,
					-0.32
				),
				1.35,
				32.0 * side,
				8.0,
				12.0,
				2.8 * side
			),
			_drive_step(
				_bounded_target(
					-0.34 * side,
					0.58
				),
				1.05,
				13.0 * side,
				10.0,
				10.0,
				1.2 * side
			),
			_drive_step(
				_bounded_target(
					0.08 * side,
					0.20
				),
				0.75,
				0.0,
				-7.0,
				-7.0,
				0.0
			)
		]
	)



func _move_scout_dash() -> void:
	var side := 1.0 if roam_rng.randf() > 0.5 else -1.0
	var x := roam_rng.randf_range(0.18, 0.34) * side
	var z := roam_rng.randf_range(-0.26, -0.16)

	_start_choreo(
		"scout_dash",
		[
			_drive_step(
				_bounded_target(x * 0.45, z * 0.35),
				0.38,
				-11.0 * side,
				8.0,
				11.0,
				-2.0 * side
			),
			_drive_step(
				_bounded_target(x, z),
				0.62,
				7.0 * side,
				12.0,
				10.0,
				2.5 * side
			),
			_drive_step(
				_bounded_target(x * 0.82, z * 0.90),
				0.32,
				0.0,
				6.0,
				6.0,
				0.0
			)
		]
	)


func _move_reverse_peek() -> void:
	var side := 1.0 if roam_rng.randf() > 0.5 else -1.0
	var x := roam_rng.randf_range(0.10, 0.22) * side

	_start_choreo(
		"reverse_peek",
		[
			_drive_step(
				_bounded_target(0.0, 0.17),
				0.55,
				0.0,
				-7.0,
				-7.0,
				0.0
			),
			_drive_step(
				_bounded_target(x, 0.10),
				0.48,
				24.0 * side,
				5.0,
				9.0,
				-2.0 * side
			),
			_drive_step(
				_bounded_target(x * 0.45, 0.02),
				0.52,
				0.0,
				8.0,
				8.0,
				0.0
			)
		]
	)


func _move_slalom() -> void:
	var side := 1.0 if roam_rng.randf() > 0.5 else -1.0

	_start_choreo(
		"slalom",
		[
			_drive_step(
				_bounded_target(0.22 * side, -0.08),
				0.48,
				-15.0 * side,
				7.0,
				11.0,
				-2.0 * side
			),
			_drive_step(
				_bounded_target(-0.20 * side, -0.17),
				0.58,
				16.0 * side,
				11.0,
				7.0,
				2.0 * side
			),
			_drive_step(
				_bounded_target(0.10 * side, -0.22),
				0.48,
				-8.0 * side,
				8.0,
				10.0,
				-1.5 * side
			),
			_drive_step(
				_bounded_target(0.0, -0.18),
				0.34,
				0.0,
				7.0,
				7.0,
				0.0
			)
		]
	)


func _move_orbit_arc() -> void:
	var side := 1.0 if roam_rng.randf() > 0.5 else -1.0

	_start_choreo(
		"orbit_arc",
		[
			_drive_step(
				_bounded_target(0.18 * side, -0.08),
				0.52,
				-18.0 * side,
				6.0,
				10.0,
				-2.0 * side
			),
			_drive_step(
				_bounded_target(0.34 * side, 0.04),
				0.60,
				-32.0 * side,
				5.0,
				11.0,
				-2.5 * side
			),
			_drive_step(
				_bounded_target(0.24 * side, 0.19),
				0.52,
				-18.0 * side,
				6.0,
				10.0,
				-1.5 * side
			),
			_drive_step(
				_bounded_target(0.08 * side, 0.15),
				0.42,
				0.0,
				8.0,
				8.0,
				0.0
			)
		]
	)


func _move_showoff_spin() -> void:
	var side := 1.0 if roam_rng.randf() > 0.5 else -1.0

	_start_choreo(
		"showoff_spin",
		[
			_drive_step(
				movement_root.position,
				0.92,
				0.0,
				-12.0 * side,
				12.0 * side,
				1.5 * side,
				TAU * side
			),
			_drive_step(
				_bounded_target(0.0, -0.10),
				0.30,
				0.0,
				8.0,
				8.0,
				0.0
			),
			_drive_step(
				_bounded_target(0.0, -0.04),
				0.26,
				0.0,
				-5.0,
				-5.0,
				0.0
			)
		]
	)


func _move_side_reposition() -> void:
	# A two-wheel robot cannot literally strafe. It turns sideways,
	# drives across, then faces the user again. Looks much more believable.
	var side := 1.0 if roam_rng.randf() > 0.5 else -1.0
	var x := roam_rng.randf_range(0.48, 0.82) * side

	_start_choreo(
		"side_reposition",
		[
			_drive_step(
				movement_root.position,
				0.40,
				-72.0 * side,
				-8.0 * side,
				8.0 * side,
				-2.0 * side,
				deg_to_rad(-72.0 * side)
			),
			_drive_step(
				_bounded_target(x, 0.0),
				0.72,
				-72.0 * side,
				10.0,
				10.0,
				0.0
			),
			_drive_step(
				_bounded_target(x, 0.0),
				0.40,
				0.0,
				8.0 * side,
				-8.0 * side,
				2.0 * side,
				deg_to_rad(72.0 * side)
			)
		]
	)


func _move_curiosity_approach() -> void:
	var side := roam_rng.randf_range(-0.08, 0.08)

	_start_choreo(
		"curious_approach",
		[
			_drive_step(
				_bounded_target(side, 0.52),
				0.58,
				side * 22.0,
				7.0,
				7.0,
				side * -4.0
			),
			# Tiny pause close to the user as if Miko is inspecting them.
			_drive_step(
				_bounded_target(side, 0.52),
				0.34,
				side * 12.0,
				0.0,
				0.0,
				0.0
			),
			_drive_step(
				_bounded_target(side * 0.35, -0.10),
				0.54,
				0.0,
				-6.0,
				-6.0,
				0.0
			)
		]
	)


func _begin_drive_to(
	target: Vector3,
	kind: String,
	duration: float,
	yaw_degrees: float,
	left_wheel_speed: float,
	right_wheel_speed: float,
	lean_degrees: float = 0.0,
	spin_radians: float = 0.0
) -> void:
	if movement_root == null:
		return

	drive_active = true
	drive_kind = kind
	drive_elapsed = 0.0
	drive_duration = clampf(
		duration,
		DRIVE_MIN_DURATION,
		DRIVE_MAX_DURATION
	)

	drive_start_position = movement_root.position
	drive_target_position = target

	drive_start_yaw = movement_root.rotation.y
	drive_target_yaw = (
		movement_home_rotation.y
		+ deg_to_rad(yaw_degrees)
	)

	drive_spin_radians = spin_radians
	drive_lean_radians = deg_to_rad(lean_degrees)
	drive_left_wheel_speed = left_wheel_speed
	drive_right_wheel_speed = right_wheel_speed


func _update_alive_movement(delta: float) -> void:
	if movement_root == null:
		return

	if not drive_active:
		return

	drive_elapsed += delta

	var t := clampf(
		drive_elapsed / maxf(drive_duration, 0.001),
		0.0,
		1.0
	)

	# Quintic smootherstep: cinematic acceleration/deceleration.
	var eased := (
		t * t * t
		* (t * (t * 6.0 - 15.0) + 10.0)
	)

	movement_root.position = drive_start_position.lerp(
		drive_target_position,
		eased
	)

	# Tiny chassis suspension: adds life while keeping both wheel
	# assemblies rigidly attached to the robot.
	var drive_energy := clampf(
		(
			absf(drive_left_wheel_speed)
			+ absf(drive_right_wheel_speed)
		) / 20.0,
		0.0,
		1.0
	)

	movement_root.position.y += (
		sin(t * PI * 2.0)
		* 0.008
		* drive_energy
		* sin(t * PI)
	)

	if absf(drive_spin_radians) > 0.001:
		movement_root.rotation.y = (
			drive_start_yaw
			+ drive_spin_radians * eased
		)
	else:
		movement_root.rotation.y = lerp_angle(
			drive_start_yaw,
			drive_target_yaw,
			eased
		)

	movement_root.rotation.z = (
		movement_home_rotation.z
		+ sin(t * PI) * drive_lean_radians
	)

	_update_tread_motion(delta)

	if t >= 1.0:
		_finish_drive()


func _update_tread_motion(delta: float) -> void:
	# DO NOT rotate MikoWheelL / MikoWheelR / tread pivot Node3Ds.
	# The orange wheel housings stay mechanically attached to the body.
	#
	# Only the procedural ridge pattern moves across the black rubber,
	# which creates the visual impression of a real moving tread.

	tread_left_phase += (
		drive_left_wheel_speed
		* delta
		* TREAD_SCROLL_SPEED
	)

	tread_right_phase += (
		drive_right_wheel_speed
		* delta
		* TREAD_SCROLL_SPEED
	)

	if wheel_left_tread_material != null:
		wheel_left_tread_material.set_shader_parameter(
			"tread_phase",
			tread_left_phase
		)

	if wheel_right_tread_material != null:
		# Opposite sign because the two wheels are mirrored.
		wheel_right_tread_material.set_shader_parameter(
			"tread_phase",
			-tread_right_phase
		)


func _finish_drive() -> void:
	if movement_root == null:
		drive_active = false
		drive_queue.clear()
		active_choreo = ""
		return

	movement_root.position = drive_target_position
	movement_root.rotation.z = movement_home_rotation.z

	# Full show-off spin ends facing the user again.
	if absf(drive_spin_radians) > PI:
		movement_root.rotation.y = movement_home_rotation.y

	drive_active = false
	drive_left_wheel_speed = 0.0
	drive_right_wheel_speed = 0.0
	drive_spin_radians = 0.0

	if not drive_queue.is_empty():
		_start_next_drive_step()
		return

	drive_kind = ""
	active_choreo = ""
	_schedule_next_roam()


func _interrupt_roam_for_interaction() -> void:
	drive_queue.clear()
	active_choreo = ""

	if drive_active:
		drive_active = false
		drive_kind = ""
		drive_left_wheel_speed = 0.0
		drive_right_wheel_speed = 0.0
		drive_spin_radians = 0.0

	if movement_root != null:
		movement_root.rotation.z = movement_home_rotation.z
		# Do not teleport position. Just face the user again smoothly enough
		# for the conversation to feel attentive.
		movement_root.rotation.y = movement_home_rotation.y

	_schedule_next_roam(2.8, 4.5)


func _setup_voice_spectrum() -> void:
	voice_bus_index = AudioServer.get_bus_index(
		VOICE_BUS_NAME
	)

	if voice_bus_index == -1:
		AudioServer.add_bus()
		voice_bus_index = AudioServer.bus_count - 1

		AudioServer.set_bus_name(
			voice_bus_index,
			VOICE_BUS_NAME
		)

	var analyzer_index := -1

	for effect_index in range(
		AudioServer.get_bus_effect_count(
			voice_bus_index
		)
	):
		var effect := AudioServer.get_bus_effect(
			voice_bus_index,
			effect_index
		)

		if effect is AudioEffectSpectrumAnalyzer:
			analyzer_index = effect_index
			break

	if analyzer_index == -1:
		var analyzer := AudioEffectSpectrumAnalyzer.new()
		analyzer.buffer_length = 2.0
		analyzer.fft_size = (
			AudioEffectSpectrumAnalyzer.FFT_SIZE_1024
		)

		AudioServer.add_bus_effect(
			voice_bus_index,
			analyzer
		)

		analyzer_index = (
			AudioServer.get_bus_effect_count(
				voice_bus_index
			)
			- 1
		)

	voice_spectrum = (
		AudioServer.get_bus_effect_instance(
			voice_bus_index,
			analyzer_index
		)
		as AudioEffectSpectrumAnalyzerInstance
	)

	if voice_spectrum != null:
		print("MIKO LIPSYNC: audio analyzer ready")
	else:
		print(
            "MIKO LIPSYNC WARNING: analyzer instance unavailable; using fallback motion"
		)


func _update_lipsync(
	delta: float
) -> void:
	# Character scenes without the robot mouth (e.g. the fox) still read the
	# lip_*_amount values computed here, so only the robot visuals are skipped.
	var has_robot_mouth := mouth_node != null and lipsync_mouth_visual != null

	lip_speech_clock += delta

	var target_open := 0.0
	var target_round := 0.0
	var target_wide := 0.0

	if _miko_is_currently_speaking():
		if voice_spectrum != null:
			var low_mag := (
				voice_spectrum
				.get_magnitude_for_frequency_range(
					90.0,
					520.0,
					AudioEffectSpectrumAnalyzerInstance.MAGNITUDE_AVERAGE
				)
			)

			var mid_mag := (
				voice_spectrum
				.get_magnitude_for_frequency_range(
					520.0,
					1800.0,
					AudioEffectSpectrumAnalyzerInstance.MAGNITUDE_AVERAGE
				)
			)

			var high_mag := (
				voice_spectrum
				.get_magnitude_for_frequency_range(
					1800.0,
					5200.0,
					AudioEffectSpectrumAnalyzerInstance.MAGNITUDE_AVERAGE
				)
			)

			var low_level: float = maxf(
				low_mag.x,
				low_mag.y
			)

			var mid_level: float = maxf(
				mid_mag.x,
				mid_mag.y
			)

			var high_level: float = maxf(
				high_mag.x,
				high_mag.y
			)

			var overall: float = maxf(
				low_level,
				maxf(
					mid_level,
					high_level
				)
			)

			var overall_db := linear_to_db(
				maxf(
					overall,
					0.000001
				)
			)

			target_open = clampf(
				inverse_lerp(
					LIPSYNC_MIN_DB,
					LIPSYNC_MAX_DB,
					overall_db
				),
				0.0,
				1.0
			)

			var spectral_total := (
				low_level
				+ mid_level
				+ high_level
				+ 0.000001
			)

			target_round = clampf(
				(
					low_level
					/ spectral_total
				)
				* 1.85,
				0.0,
				1.0
			)

			target_wide = clampf(
				(
					high_level
					/ spectral_total
				)
				* 2.0,
				0.0,
				1.0
			)

		if target_open < 0.10:
			var pulse_a: float = absf(
				sin(
					lip_speech_clock * 12.8
				)
			)

			var pulse_b: float = absf(
				sin(
					lip_speech_clock * 21.5
					+ 0.7
				)
			)

			target_open = clampf(
				0.18
				+ pulse_a * 0.43
				+ pulse_b * 0.14,
				0.0,
				0.88
			)

			target_round = clampf(
				0.18
				+ 0.42
				* absf(
					sin(
						lip_speech_clock * 4.6
					)
				),
				0.0,
				0.70
			)

			target_wide = clampf(
				0.12
				+ 0.38
				* absf(
					sin(
						lip_speech_clock * 6.9
						+ 1.2
					)
				),
				0.0,
				0.65
			)

	if realtime_voice != null and realtime_voice.external_voice_active:
		target_open = clampf(realtime_voice.voice_level * 5.0, 0.0, 1.0)
		target_round = target_open * (0.3 + 0.2 * sin(lip_speech_clock * 5.0))
		target_wide = target_open * (0.3 + 0.2 * cos(lip_speech_clock * 7.0))

	lip_open_amount = move_toward(
		lip_open_amount,
		target_open,
		delta
		* (
			15.0
			if target_open > lip_open_amount
			else 20.0
		)
	)

	lip_round_amount = move_toward(
		lip_round_amount,
		target_round,
		delta * 8.0
	)

	lip_wide_amount = move_toward(
		lip_wide_amount,
		target_wide,
		delta * 9.0
	)

	if not has_robot_mouth:
		return

	var closed_shape := (
		lipsync_mouth_closed_scale
	)

	var round_shape := Vector3(
		0.090,
		0.105,
		0.030
	)

	var wide_shape := Vector3(
		0.170,
		0.060,
		0.024
	)

	var open_shape := (
		lipsync_mouth_open_scale
	)

	var vowel_mix := clampf(
		lip_round_amount
		+ lip_wide_amount,
		0.0,
		1.0
	)

	var vowel_shape := open_shape

	if vowel_mix > 0.001:
		var round_ratio := (
			lip_round_amount
			/ maxf(
				vowel_mix,
				0.001
			)
		)

		vowel_shape = wide_shape.lerp(
			round_shape,
			round_ratio
		)

	var target_scale := closed_shape.lerp(
		vowel_shape,
		lip_open_amount
	)

	var micro := (
		sin(
			lip_speech_clock * 17.0
		)
		* 0.025
		* lip_open_amount
	)

	target_scale.x *= (
		1.0 + micro
	)

	lipsync_mouth_visual.scale = (
		lipsync_mouth_visual.scale.lerp(
			target_scale,
			clampf(
				delta * 20.0,
				0.0,
				1.0
			)
		)
	)

	lipsync_mouth_visual.position.y = lerpf(
		lipsync_mouth_visual.position.y,
		-0.012 * lip_open_amount,
		clampf(
			delta * 16.0,
			0.0,
			1.0
		)
	)


func _reset_mouth() -> void:
	lip_open_amount = 0.0
	lip_round_amount = 0.0
	lip_wide_amount = 0.0

	if lipsync_mouth_visual != null:
		lipsync_mouth_visual.scale = (
			lipsync_mouth_closed_scale
		)
		lipsync_mouth_visual.position = (
			Vector3.ZERO
		)

	if mouth_node != null:
		mouth_node.scale = (
			mouth_base_scale
		)


# ============================================================
# ANIMATION
# ============================================================

func _play_brain_action(
	action: String
) -> void:
	var normalized := action.strip_edges().to_lower()
	if presence != null and normalized != "idle":
		presence.perform_action(normalized)

	var animation_name := normalized

	match normalized:
		"jump":
			animation_name = "bounce"

		"happy":
			animation_name = "bounce"

		"excited":
			animation_name = "dance"

		"talk":
			animation_name = "look"

		"hide":
			animation_name = "look"

		"wave":
			animation_name = "wave"

		"roll":
			animation_name = "roll"

		"move":
			animation_name = "roll"

		"laugh":
			animation_name = "laugh"

		"sleep":
			animation_name = "sleep"

		"idle":
			animation_name = "idle"

		_:
			animation_name = normalized

	if not animation_player.has_animation(
		animation_name
	):
		print(
			"MIKO ANIMATION: ",
			animation_name,
            " not found -> using look"
		)

		animation_name = "look"

	if animation_name == "sleep":
		sleeping_pose = true
		_set_face_emotion(
			"sleepy",
			30.0
		)
	else:
		sleeping_pose = false

	if animation_name == "idle":
		action_playing = false
	else:
		action_playing = true

	animation_player.play(
		animation_name
	)


func _on_animation_finished(
	animation_name: StringName
) -> void:
	if animation_name == &"idle":
		if (
			not action_playing
			and
			not sleeping_pose
		):
			_play_idle()

		return

	action_playing = false

	if sleeping_pose:
		# Hold the final sleeping pose.
		animation_player.pause()
		return

	_play_idle()


func _play_idle() -> void:
	if sleeping_pose:
		return

	action_playing = false

	if animation_player.has_animation(
        "idle"
	):
		animation_player.play(
            "idle"
		)


# ============================================================
# STATE / SAFETY
# ============================================================

func _interaction_busy() -> bool:
	if mic_recording:
		return true

	if user_turn_active:
		return true

	if tts_request_active:
		return true

	if voice_active:
		return true

	if (
		voice_player != null
		and
		voice_player.playing
	):
		return true

	if (
		transcribe_request != null
		and
		transcribe_request.get_http_client_status()
		!= HTTPClient.STATUS_DISCONNECTED
	):
		return true

	if (
		think_request != null
		and
		think_request.get_http_client_status()
		!= HTTPClient.STATUS_DISCONNECTED
	):
		return true

	if (
		speak_request != null
		and
		speak_request.get_http_client_status()
		!= HTTPClient.STATUS_DISCONNECTED
	):
		return true

	return false


func _finish_user_turn() -> void:
	user_turn_active = false


# Native voice and WebRTC share conversation tools and the same Miko memory.
func _setup_realtime_voice() -> void:
	realtime_voice = RealtimeVoiceScript.new()
	realtime_voice.name = "MikoRealtimeVoice"
	realtime_voice.hands_free_toggle_key = KEY_F10
	realtime_voice.status_changed.connect(_on_realtime_status)
	realtime_voice.transcript.connect(_on_realtime_transcript)
	realtime_voice.final_transcript.connect(_on_realtime_final_transcript)
	realtime_voice.turn_started.connect(_on_realtime_turn_started)
	realtime_voice.speaking_changed.connect(_on_realtime_speaking)
	realtime_voice.tool_result.connect(_on_realtime_tool)
	realtime_voice.vision_update.connect(_on_realtime_vision)
	_setup_voice_controls()
	add_child(realtime_voice)


func _setup_voice_controls() -> void:
	var layer := CanvasLayer.new()
	layer.name = "MikoVoiceControls"
	add_child(layer)
	var panel := PanelContainer.new()
	realtime_voice_panel = panel
	panel.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_WIDE)
	panel.offset_top = -198.0
	panel.offset_left = 14.0
	panel.offset_right = -14.0
	panel.offset_bottom = -12.0
	layer.add_child(panel)
	var surface := StyleBoxFlat.new()
	surface.bg_color = Color(0.055, 0.085, 0.115, 0.95)
	surface.border_color = Color(0.30, 0.73, 0.81, 0.65)
	surface.set_border_width_all(1)
	surface.set_corner_radius_all(16)
	surface.set_content_margin_all(12)
	panel.add_theme_stylebox_override("panel", surface)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)
	panel.add_child(box)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	box.add_child(row)
	var talk_button := Button.new()
	realtime_talk_button = talk_button
	talk_button.text = "החזק כדי לדבר"
	talk_button.tooltip_text = "החזק כדי לדבר, או החזק SPACE"
	talk_button.focus_mode = Control.FOCUS_NONE
	talk_button.custom_minimum_size = Vector2(84, 38)
	talk_button.add_theme_font_size_override("font_size", 15)
	_style_voice_button(talk_button, Color(0.10, 0.37, 0.43, 1.0))
	if realtime_voice == null:
		talk_button.disabled = true
	else:
		talk_button.button_down.connect(func(): realtime_voice.set_pointer_ptt(true))
		talk_button.button_up.connect(func(): realtime_voice.set_pointer_ptt(false))
	row.add_child(talk_button)
	var open_button := Button.new()
	realtime_browser_button = open_button
	open_button.text = "שיחה בדפדפן · F8"
	open_button.tooltip_text = "פתח שיחה בדפדפן עם F8"
	# SPACE is the native push-to-talk key and also Godot's ui_accept.
	# A focused Button would otherwise launch a browser tab on every turn.
	open_button.focus_mode = Control.FOCUS_NONE
	open_button.disabled = miko_preview_mode
	open_button.custom_minimum_size = Vector2(78, 38)
	open_button.add_theme_font_size_override("font_size", 15)
	_style_voice_button(open_button, Color(0.14, 0.20, 0.27, 1.0))
	open_button.pressed.connect(_open_voice_conversation)
	row.add_child(open_button)
	realtime_camera_button = Button.new()
	realtime_camera_button.focus_mode = Control.FOCUS_NONE
	realtime_camera_button.custom_minimum_size = Vector2(70, 38)
	realtime_camera_button.add_theme_font_size_override("font_size", 14)
	realtime_camera_button.disabled = miko_preview_mode
	realtime_camera_button.pressed.connect(_toggle_vision)
	row.add_child(realtime_camera_button)
	_refresh_camera_button()
	realtime_status_label = Label.new()
	realtime_status_label.text = "תצוגה מקדימה" if miko_preview_mode else "SPACE: לדבר · F8: שיחה בלי לחצן"
	realtime_status_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	realtime_status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	realtime_status_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	realtime_status_label.text_direction = Control.TEXT_DIRECTION_RTL
	realtime_status_label.add_theme_font_size_override("font_size", 16)
	realtime_status_label.add_theme_color_override("font_color", Color(0.78, 0.94, 0.97))
	realtime_status_label.clip_text = true
	row.add_child(realtime_status_label)
	var separator := HSeparator.new()
	box.add_child(separator)
	var transcript_scroll := ScrollContainer.new()
	realtime_transcript_scroll = transcript_scroll
	transcript_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	transcript_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	transcript_scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	transcript_scroll.follow_focus = true
	box.add_child(transcript_scroll)
	realtime_transcript_label = Label.new()
	realtime_transcript_label.text_direction = Control.TEXT_DIRECTION_RTL
	# Godot mirrors Label alignment in RTL mode: LEFT draws at the visual right.
	realtime_transcript_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_LEFT
	realtime_transcript_label.vertical_alignment = VERTICAL_ALIGNMENT_TOP
	realtime_transcript_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	realtime_transcript_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	realtime_transcript_label.add_theme_font_size_override("font_size", 16)
	realtime_transcript_label.add_theme_color_override("font_color", Color(0.96, 0.98, 1.0))
	realtime_transcript_label.add_theme_constant_override("line_spacing", 5)
	if miko_preview_mode:
		realtime_transcript_label.text = "השיחה תופיע כאן אחרי שתדבר עם מיקו."
	transcript_scroll.add_child(realtime_transcript_label)
	transcript_scroll.resized.connect(_fit_realtime_transcript_width)
	get_viewport().size_changed.connect(_update_realtime_voice_layout)
	_update_realtime_voice_layout()
	call_deferred("_fit_realtime_transcript_width")


func _style_voice_button(button: Button, fill: Color) -> void:
	var normal := StyleBoxFlat.new()
	normal.bg_color = fill
	normal.set_corner_radius_all(9)
	normal.set_content_margin_all(7)
	button.add_theme_stylebox_override("normal", normal)
	var hover := normal.duplicate() as StyleBoxFlat
	hover.bg_color = fill.lightened(0.14)
	button.add_theme_stylebox_override("hover", hover)
	var pressed := normal.duplicate() as StyleBoxFlat
	pressed.bg_color = fill.darkened(0.12)
	button.add_theme_stylebox_override("pressed", pressed)
	button.add_theme_color_override("font_color", Color(0.96, 0.99, 1.0))
	button.add_theme_color_override("font_hover_color", Color.WHITE)


func _fit_realtime_transcript_width() -> void:
	if realtime_transcript_scroll == null or realtime_transcript_label == null:
		return
	# ScrollContainer does not stretch a Label beyond its intrinsic text width.
	# Reserve the scrollbar and give Hebrew right alignment the full card width.
	var available := maxf(0.0, realtime_transcript_scroll.size.x - 14.0)
	realtime_transcript_label.custom_minimum_size = Vector2(available, 0.0)


func _update_realtime_voice_layout() -> void:
	if realtime_voice_panel == null:
		return
	var viewport_size := get_viewport().get_visible_rect().size
	var width := viewport_size.x
	var was_compact := realtime_compact_ui
	realtime_compact_ui = width <= 320.0
	realtime_medium_ui = width > 320.0 and width <= 800.0
	realtime_narrow_ui = width > 320.0 and width < 500.0
	realtime_voice_panel.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_WIDE)
	if realtime_compact_ui:
		realtime_voice_panel.offset_top = -38.0
		realtime_voice_panel.offset_bottom = -6.0
		realtime_voice_panel.offset_left = 8.0
		realtime_voice_panel.offset_right = -8.0
	else:
		realtime_voice_panel.offset_top = -172.0 if viewport_size.y < 720.0 else -198.0
		realtime_voice_panel.offset_bottom = -12.0
		realtime_voice_panel.offset_left = 14.0
		realtime_voice_panel.offset_right = -14.0
	realtime_talk_button.visible = not realtime_compact_ui
	realtime_browser_button.visible = not realtime_compact_ui
	realtime_transcript_scroll.visible = not realtime_compact_ui
	realtime_talk_button.text = "החזק" if realtime_narrow_ui else ("החזק לדבר" if realtime_medium_ui else "החזק כדי לדבר")
	realtime_browser_button.text = "שיחה" if realtime_narrow_ui else ("שיחה חופשית" if realtime_medium_ui else "שיחה בדפדפן · F8")
	realtime_status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER if realtime_compact_ui else HORIZONTAL_ALIGNMENT_RIGHT
	_refresh_realtime_status_label()
	if was_compact and not realtime_compact_ui:
		call_deferred("_scroll_realtime_transcript_to_bottom")


func _refresh_realtime_status_label() -> void:
	if realtime_status_label == null:
		return
	var display_state := "speaking" if voice_active else realtime_display_status
	var tint := Color(0.76, 0.91, 0.94)
	match display_state:
		"listening", "finishing":
			tint = Color(0.43, 0.94, 0.96)
		"thinking":
			tint = Color(0.96, 0.83, 0.54)
		"speaking":
			tint = Color(0.54, 0.95, 0.80)
		"error", "disconnected":
			tint = Color(1.0, 0.68, 0.61)
	realtime_status_label.add_theme_color_override("font_color", tint)
	if miko_preview_mode:
		realtime_status_label.text = "תצוגה" if realtime_compact_ui else "תצוגה מקדימה"
		return
	if voice_active:
		realtime_status_label.text = "● מדבר" if realtime_compact_ui or realtime_medium_ui else "● מיקו מדבר · אפשר להפריע"
		return
	var pending_turn: bool = realtime_voice != null and realtime_voice.is_listening()
	var keep_listening: bool = pending_turn and realtime_display_status in ["idle", "ready"]
	if realtime_compact_ui or realtime_medium_ui:
		var compact_labels := {
			"idle": "מוכן", "ready": "מוכן", "connecting": "מתחבר",
			"listening": "מקשיב", "finishing": "קולט…", "thinking": "חושב", "speaking": "מדבר",
			"browser": "שיחה", "muted": "מושתק", "error": "תקלה",
			"disconnected": "מנותק",
		}
		var short_state := "מקשיב" if keep_listening else str(compact_labels.get(realtime_display_status, "מוכן"))
		if realtime_medium_ui and not realtime_narrow_ui and realtime_display_status in ["idle", "ready"] and not keep_listening:
			short_state = "SPACE · " + short_state
		realtime_status_label.text = "● " + short_state
		return
	var labels := {
		"idle": "מוכן · SPACE: לדבר · F8: שיחה פתוחה",
		"ready": "מוכן לשיחה",
		"connecting": "מתחבר לקול…",
		"listening": "מקשיב…",
		"finishing": "מסיים לקלוט…",
		"thinking": "חושב…",
		"speaking": "מיקו מדבר · אפשר להפריע",
		"browser": "שיחה פתוחה בדפדפן",
		"muted": "המיקרופון מושתק",
		"error": "תקלה בקול: ",
		"disconnected": "המוח מנותק — הפעל את Miko",
	}
	realtime_status_label.text = "● " + ("מקשיב…" if keep_listening else str(labels.get(realtime_display_status, realtime_display_detail)) + (realtime_display_detail if realtime_display_status == "error" else ""))


func _open_voice_conversation() -> void:
	if miko_preview_mode:
		return
	if Input.is_key_pressed(KEY_SPACE):
		return
	var now := Time.get_ticks_msec()
	if now - realtime_last_browser_open_msec < 1500:
		return
	realtime_last_browser_open_msec = now
	OS.shell_open(BRAIN_URL + "/voice")


func _on_realtime_status(status: String, detail: String) -> void:
	print("MIKO REALTIME STATUS: ", status, " ", detail)
	realtime_display_status = status
	realtime_display_detail = detail
	var pending_turn: bool = realtime_voice != null and realtime_voice.is_listening()
	var keep_listening: bool = pending_turn and status in ["idle", "ready"]
	mic_recording = status == "listening"
	user_turn_active = status in ["listening", "finishing", "thinking", "connecting"] or keep_listening
	_refresh_realtime_status_label()
	if status == "listening":
		_interrupt_roam_for_interaction()
		_set_face_emotion("curious", 4.0)
	if status in ["idle", "ready", "error", "disconnected"] and not keep_listening:
		_finish_user_turn()
	if presence != null:
		presence.set_listening(status in ["listening", "finishing"] or keep_listening)
		presence.set_conversation_active(user_turn_active or voice_active or (realtime_voice != null and realtime_voice.external_voice_active))


func _on_realtime_turn_started(turn_id: String, turn_number: int, session_id: String) -> void:
	if turn_id.is_empty():
		return
	_ensure_realtime_turn(turn_id, turn_number, session_id)
	_refresh_realtime_transcript()


func _ensure_realtime_turn(turn_id: String, turn_number: int, session_id: String) -> int:
	var session_key := session_id if not session_id.is_empty() else "unknown"
	var turn_key := turn_id
	if turn_key.is_empty():
		realtime_legacy_transcript_sequence += 1
		turn_key = "legacy_" + str(realtime_legacy_transcript_sequence)
	for index in range(realtime_transcript_turns.size()):
		var existing: Dictionary = realtime_transcript_turns[index]
		if existing.get("session_id") == session_key and existing.get("turn_id") == turn_key:
			return index
	if not realtime_session_order.has(session_key):
		realtime_session_order[session_key] = realtime_next_session_order
		realtime_next_session_order += 1
	var session_position: int = realtime_session_order[session_key]
	var slot: Dictionary = {
		"session_id": session_key, "turn_id": turn_key,
		"session_position": session_position, "turn_number": turn_number,
		"user": "", "assistant": [], "assistant_items": [],
	}
	var insertion := realtime_transcript_turns.size()
	if turn_number >= 0:
		for index in range(realtime_transcript_turns.size()):
			var existing: Dictionary = realtime_transcript_turns[index]
			if int(existing.get("session_position", 0)) > session_position:
				insertion = index
				break
			if int(existing.get("session_position", 0)) == session_position and int(existing.get("turn_number", -1)) > turn_number:
				insertion = index
				break
	realtime_transcript_turns.insert(insertion, slot)
	return insertion


func _on_realtime_transcript(role: String, text: String, turn_id: String, turn_number: int, session_id: String, item_id: String) -> void:
	var clean := text.strip_edges()
	if clean.is_empty() or role not in ["user", "assistant"]:
		return
	var index := _ensure_realtime_turn(turn_id, turn_number, session_id)
	var slot: Dictionary = realtime_transcript_turns[index]
	if role == "user":
		slot["user"] = clean
	else:
		var messages: Array = slot["assistant"]
		var item_ids: Array = slot["assistant_items"]
		var existing_index := item_ids.find(item_id) if not item_id.is_empty() else -1
		if existing_index >= 0:
			messages[existing_index] = clean
		else:
			messages.append(clean)
			item_ids.append(item_id)
		slot["assistant"] = messages
		slot["assistant_items"] = item_ids
	realtime_transcript_turns[index] = slot
	while realtime_transcript_turns.size() > 24:
		realtime_transcript_turns.pop_front()
	_refresh_realtime_transcript()


func _refresh_realtime_transcript() -> void:
	if realtime_transcript_label == null:
		return
	var follow_latest := true
	if realtime_transcript_scroll != null:
		var bar := realtime_transcript_scroll.get_v_scroll_bar()
		follow_latest = realtime_transcript_scroll.scroll_vertical + bar.page >= bar.max_value - 20.0
	var lines: Array[String] = []
	realtime_caption_offsets.clear()
	var character_offset := 0
	for index in range(realtime_transcript_turns.size()):
		var slot: Dictionary = realtime_transcript_turns[index]
		var user_text := str(slot.get("user", ""))
		var user_line := "אתה: " + (user_text if not user_text.is_empty() else "מתמלל…")
		lines.append(user_line)
		character_offset += user_line.length() + 1
		var answers: Array = slot.get("assistant", [])
		var item_ids: Array = slot.get("assistant_items", [])
		for answer_index in range(answers.size()):
			var answer_line := "מיקו: " + str(answers[answer_index])
			if answer_index < item_ids.size():
				var caption_key := str(slot.get("session_id", "")) + ":" + str(item_ids[answer_index])
				realtime_caption_offsets[caption_key] = Vector2i(character_offset, character_offset + answer_line.length() - 1)
			lines.append(answer_line)
			character_offset += answer_line.length() + 1
	realtime_transcript_label.text = "\n".join(lines)
	if realtime_transcript_scroll != null and follow_latest:
		call_deferred("_scroll_realtime_transcript_to_bottom")


func _on_realtime_final_transcript(item_id: String, session_id: String) -> void:
	# The final caption can be reviewed before the speaker finishes its tail.
	# Acknowledge actual visible UI, never receipt of partial text or hidden history.
	await get_tree().process_frame
	await get_tree().process_frame
	var caption_key := session_id + ":" + item_id
	if not realtime_presented_captions.has(caption_key) and _can_review_realtime_caption(item_id, session_id):
		realtime_voice.report_text_presented(item_id, session_id)
		realtime_presented_captions[caption_key] = true
		while realtime_presented_captions.size() > 128:
			realtime_presented_captions.erase(realtime_presented_captions.keys()[0])


func _can_review_realtime_caption(item_id: String, session_id: String) -> bool:
	if miko_preview_mode or realtime_compact_ui or realtime_voice == null:
		return false
	if realtime_voice.external_voice_active or not _realtime_caption_ui_focused():
		return false
	if realtime_transcript_label == null or realtime_transcript_scroll == null:
		return false
	if not realtime_transcript_label.is_visible_in_tree() or not realtime_transcript_scroll.is_visible_in_tree():
		return false
	var caption_key := session_id + ":" + item_id
	if not realtime_caption_offsets.has(caption_key):
		return false
	var offsets: Vector2i = realtime_caption_offsets[caption_key]
	var first := realtime_transcript_label.get_character_bounds(offsets.x)
	var last := realtime_transcript_label.get_character_bounds(offsets.y)
	if first.size == Vector2.ZERO or last.size == Vector2.ZERO:
		return false
	first.position += realtime_transcript_label.global_position
	last.position += realtime_transcript_label.global_position
	var viewport_rect := realtime_transcript_scroll.get_global_rect()
	return viewport_rect.encloses(first) and viewport_rect.encloses(last)


func _realtime_caption_ui_focused() -> bool:
	return get_window().has_focus()


func _scroll_realtime_transcript_to_bottom() -> void:
	await get_tree().process_frame
	if realtime_transcript_scroll == null or not is_instance_valid(realtime_transcript_scroll):
		return
	var bar := realtime_transcript_scroll.get_v_scroll_bar()
	realtime_transcript_scroll.scroll_vertical = roundi(maxf(0.0, bar.max_value - bar.page))


func _on_realtime_speaking(active: bool) -> void:
	voice_active = active
	_refresh_realtime_status_label()
	if active:
		mic_recording = false
		if presence != null:
			presence.set_speaking(true)
		_interrupt_roam_for_interaction()
		_play_brain_action("look")
	else:
		_reset_mouth()
		var listening: bool = realtime_voice != null and (realtime_voice.is_listening() or realtime_voice.external_voice_active)
		if presence != null:
			presence.set_speaking(false)
			presence.set_listening(listening)
			presence.set_conversation_active(listening)
		if not listening:
			_finish_user_turn()
			_play_idle()


func _on_realtime_tool(name: String, result: Variant) -> void:
	if name == "miko_set_expression" and result is Dictionary:
		_set_face_emotion(str(result.get("emotion", "curious")), 8.0)
		_play_brain_action(str(result.get("action", "look")))
	if name == "miko_perform_action" and result is Dictionary and result.get("ok", false):
		_perform_body_command(str(result.get("action", "")), int(result.get("times", 1)))


# Camera: perception runs locally in the host; Godot only gets derived facts.
func _on_realtime_vision(event: Dictionary) -> void:
	if str(event.get("type", "")) == "vision_status":
		vision_enabled = bool(event.get("enabled", false))
		vision_available = bool(event.get("available", false)) and str(event.get("source", "off")) != "off"
		_refresh_camera_button()
		return
	var character := get_node_or_null("MikoScene")
	if character != null and character.has_method("on_vision"):
		character.call("on_vision", event)


func _toggle_vision() -> void:
	if miko_preview_mode or realtime_voice == null:
		return
	vision_enabled = not vision_enabled
	realtime_voice.set_vision_enabled(vision_enabled)
	if not vision_enabled:
		_on_realtime_vision({"type": "vision", "seen": false})
	_refresh_camera_button()


func _refresh_camera_button() -> void:
	if realtime_camera_button == null:
		return
	var watching := vision_enabled and vision_available
	if watching:
		realtime_camera_button.text = "● מצלמה"
	elif vision_enabled:
		realtime_camera_button.text = "מצלמה…"      # starting, or no webcam found
	else:
		realtime_camera_button.text = "מצלמה כבויה"
	realtime_camera_button.tooltip_text = "F7: הפעל/כבה את הראייה של מיקו (מעובד רק במחשב הזה)"
	_style_voice_button(realtime_camera_button, Color(0.12, 0.32, 0.26, 1.0) if watching else Color(0.20, 0.20, 0.24, 1.0))


# Voice-commanded body actions ("תלך", "תקפוץ", "תעשה שלום") go straight to
# a character that implements perform_command(); older avatars fall back to
# the nearest animation cue.
func _perform_body_command(action: String, times: int) -> void:
	var character := get_node_or_null("MikoScene")
	if character != null and character.has_method("perform_command"):
		character.call("perform_command", action, times)
		return
	var fallback := {"jump": "bounce", "wave": "wave", "dance": "dance", "spin": "roll", "sleep": "sleep", "laugh": "laugh"}
	_play_brain_action(str(fallback.get(action, "look")))
