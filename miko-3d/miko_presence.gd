extends Node3D
## A compact physical home for the original Miko GLB.
## Add this as `Presence`, a sibling of MikoScene and Camera3D, then call
## setup_stage() after the controller has initialized its robot features.

@export var actor_path: NodePath = ^"../MikoScene"
@export var camera_path: NodePath = ^"../Camera3D"

const DOCK_TOP_Y := 0.152
const DESK_TOP_Y := -0.12
## Open-stage characters walk freely on the desk: no glass case or dock, and
## the camera follows them. Used when the actor implements wants_open_stage().
var open_stage := false
var camera_focus := Vector3(0.0, 0.62, 0.08)
var camera_base_position := Vector3.ZERO

var actor: Node3D
var stage_root: Node3D
var camera: Camera3D
var accent_light: OmniLight3D
var listening := false
var speaking := false
var conversation_active := false
var emotion := "calm"
var motion_enabled := true
var attention_position := Vector3(0.0, 1.1, 5.0)
var clock := 0.0
var gesture_clock := 0.0
var gesture_strength := 0.0
var gesture_kind := ""
var base_position := Vector3.ZERO
var base_rotation := Vector3.ZERO
var base_scale := Vector3.ONE
var smooth_yaw := 0.0
var smooth_pitch := 0.0
var smooth_roll := 0.0
var random_generator := RandomNumberGenerator.new()
var glance_target := 0.0
var glance_value := 0.0
var glance_wait := 2.0
var ground_meshes: Array[MeshInstance3D] = []
var ground_vertices: Array = []
var imported_animation: AnimationPlayer


func _ready() -> void:
	actor = get_node_or_null(actor_path) as Node3D
	camera = get_node_or_null(camera_path) as Camera3D
	random_generator.seed = 42177


func setup_stage() -> void:
	if stage_root != null:
		return
	if actor == null:
		actor = get_node_or_null(actor_path) as Node3D
	if camera == null:
		camera = get_node_or_null(camera_path) as Camera3D
	if actor == null or camera == null:
		push_warning("Miko Presence needs MikoScene and Camera3D siblings.")
		return

	open_stage = actor.has_method("wants_open_stage") and bool(actor.call("wants_open_stage"))
	# The model's own bounding box is about 1.33 x 1.84 x 1.15 units. Its
	# lowest point is -0.164, so this position rests its wheels on the plinth.
	# Open-stage characters stand directly on the desk instead.
	actor.position = Vector3(0.0, DESK_TOP_Y, 0.12) if open_stage else Vector3(0.0, 0.32, 0.12)
	actor.rotation = Vector3.ZERO
	actor.scale = Vector3.ONE
	base_position = actor.position
	base_rotation = actor.rotation
	base_scale = actor.scale
	attention_position = actor.to_global(Vector3(0.0, 0.9, 5.0))
	imported_animation = actor.get_node_or_null("AnimationPlayer") as AnimationPlayer
	for name_value in ["MikoWheelLTreadMeshNode", "MikoWheelRTreadMeshNode"]:
		var candidate := actor.find_child(name_value, true, false) as MeshInstance3D
		if candidate != null:
			ground_meshes.append(candidate)
			ground_vertices.append(_cache_wheel_vertices(candidate))

	stage_root = Node3D.new()
	stage_root.name = "PresenceStage"
	add_child(stage_root)
	_build_materials_and_geometry()
	_setup_lighting()
	_tune_source_materials()

	# A slight product-camera angle keeps both wheels and antennae balanced.
	camera.position = Vector3(0.62, 1.68, 4.45)
	camera.fov = 40.0
	camera_base_position = camera.position
	_frame_camera()
	get_viewport().size_changed.connect(_frame_camera)
	camera.current = true
	var old_light := get_parent().get_node_or_null("DirectionalLight3D") as DirectionalLight3D
	if old_light != null:
		old_light.visible = false
	print("MIKO PRESENCE: desk-scale 3D stage ready")


func _frame_camera() -> void:
	if camera == null:
		return
	# Give the desktop conversation card room beneath Miko. The tiny ESP
	# preview retains its original framing and compact status chip.
	var target_y := 1.07 if get_viewport().get_visible_rect().size.x <= 320.0 else 0.72
	camera.look_at(get_parent().to_global(Vector3(0.0, target_y, 0.08)), Vector3.UP)


func set_listening(value: bool) -> void:
	listening = value
	if value:
		conversation_active = true
		speaking = false


func set_speaking(value: bool) -> void:
	speaking = value
	if value:
		conversation_active = true
		listening = false


func set_conversation_active(value: bool) -> void:
	conversation_active = value
	if not value:
		listening = false
		speaking = false


func set_emotion(value: String) -> void:
	emotion = value.strip_edges().to_lower()
	if accent_light == null:
		return
	match emotion:
		"happy", "excited":
			accent_light.light_color = Color(0.40, 0.88, 1.0)
		"curious", "thinking":
			accent_light.light_color = Color(0.62, 0.76, 1.0)
		"sad", "sleepy":
			accent_light.light_color = Color(0.42, 0.55, 0.92)
		"angry":
			accent_light.light_color = Color(1.0, 0.64, 0.51)
		_:
			accent_light.light_color = Color(0.45, 0.79, 1.0)


func set_attention(world_position: Vector3) -> void:
	attention_position = world_position


func perform_action(action: String) -> void:
	gesture_kind = action.strip_edges().to_lower()
	gesture_clock = 0.0
	gesture_strength = 1.0


func set_motion_enabled(value: bool) -> void:
	motion_enabled = value
	if not value and actor != null and stage_root != null:
		actor.position = base_position
		actor.rotation = base_rotation
		actor.scale = base_scale


func _process(delta: float) -> void:
	if stage_root == null or actor == null:
		return
	if open_stage:
		# The character owns its own body motion; the stage only follows it.
		_follow_camera(delta)
		return
	if not motion_enabled:
		return
	clock += delta
	glance_wait -= delta
	if glance_wait <= 0.0:
		glance_target = random_generator.randf_range(-0.6, 0.6)
		glance_wait = random_generator.randf_range(3.2, 6.0)
	glance_value = lerpf(glance_value, glance_target, 1.0 - exp(-delta * 1.2))

	# A robot with mass settles and anticipates a turn; it does not patrol
	# continually. All motion is below a few degrees and stays on the plinth.
	var local_attention := actor.to_local(attention_position)
	var attention_yaw := clampf(atan2(local_attention.x, local_attention.z), -0.16, 0.16)
	var target_yaw := attention_yaw * (0.64 if conversation_active else 0.36)
	if not conversation_active:
		target_yaw += deg_to_rad(glance_value * 1.2)
	var target_pitch := 0.0
	if listening:
		target_pitch = deg_to_rad(-0.65)
	elif speaking:
		target_pitch = deg_to_rad(0.18) + sin(clock * 4.4) * deg_to_rad(0.16)
	else:
		target_pitch = sin(clock * 0.9) * deg_to_rad(0.12)
	# No steady bank: curiosity is shown by the eyes and a small head nod.
	var target_roll := 0.0
	if emotion == "curious" or emotion == "thinking":
		target_pitch -= deg_to_rad(0.18)
	if emotion == "sleepy":
		target_pitch += deg_to_rad(2.0)

	if gesture_strength > 0.0:
		gesture_clock += delta
		var envelope := sin(minf(gesture_clock / 0.78, 1.0) * PI)
		match gesture_kind:
			"wave", "greet", "hello":
				target_roll += sin(gesture_clock / 0.78 * TAU) * deg_to_rad(0.65)
				target_yaw += envelope * deg_to_rad(1.8)
			"laugh", "happy":
				target_pitch -= envelope * deg_to_rad(1.6)
			"think", "curious":
				target_pitch -= envelope * deg_to_rad(0.8)
			_:
				target_pitch -= envelope * deg_to_rad(0.7)
		if gesture_clock >= 0.78:
			gesture_strength = 0.0

	var weight := 1.0 - exp(-delta * (4.2 if conversation_active else 2.0))
	smooth_yaw = lerpf(smooth_yaw, target_yaw, weight)
	smooth_pitch = lerpf(smooth_pitch, target_pitch, weight)
	smooth_roll = lerpf(smooth_roll, target_roll, weight)
	actor.rotation = base_rotation + Vector3(smooth_pitch, smooth_yaw, smooth_roll)
	var vertical_breath := sin(clock * 1.24) * 0.0035
	actor.position = base_position + Vector3(0.0, vertical_breath, 0.0)
	_ground_wheels()


func _follow_camera(delta: float) -> void:
	if camera == null or not actor.has_method("focus_point"):
		return
	var focus: Vector3 = get_parent().to_local(actor.call("focus_point"))
	var compact := get_viewport().get_visible_rect().size.x <= 320.0
	var desired := Vector3(clampf(focus.x, -1.3, 1.3) * 0.8, focus.y + (0.18 if compact else -0.02), clampf(focus.z, -0.8, 0.9) * 0.45 + 0.05)
	camera_focus = camera_focus.lerp(desired, 1.0 - exp(-delta * 1.4))
	camera.position = camera_base_position + Vector3(camera_focus.x * 0.4, 0.0, 0.0)
	camera.look_at(get_parent().to_global(camera_focus), Vector3.UP)


func _ground_wheels() -> void:
	if ground_meshes.is_empty():
		return
	var active_animation := ""
	if imported_animation != null:
		active_animation = imported_animation.current_animation.to_lower()
		if active_animation.is_empty():
			active_animation = imported_animation.assigned_animation.to_lower()
	# Preserve deliberate movement and lift in the source's action clips.
	if active_animation in ["bounce", "roll", "dance", "laugh"]:
		return
	var minimum_y := INF
	var world_to_stage := global_transform.affine_inverse()
	for wheel_index in range(ground_meshes.size()):
		var wheel := ground_meshes[wheel_index]
		if not is_instance_valid(wheel):
			continue
		var points: PackedVector3Array = ground_vertices[wheel_index]
		var wheel_to_stage := world_to_stage * wheel.global_transform
		var down_row := Vector3(
			wheel_to_stage.basis.x.y,
			wheel_to_stage.basis.y.y,
			wheel_to_stage.basis.z.y
		)
		var local_minimum := INF
		for point in points:
			local_minimum = minf(local_minimum, down_row.dot(point))
		if local_minimum != INF:
			minimum_y = minf(minimum_y, wheel_to_stage.origin.y + local_minimum)
	if minimum_y == INF:
		return
	var correction := DOCK_TOP_Y - minimum_y
	# Sleep tilts Miko onto one side. Prevent the wheel from cutting into
	# the dock, without forcing a resting pose downward if already above it.
	if active_animation == "sleep":
		correction = maxf(correction, 0.0)
	actor.position.y += correction


func _cache_wheel_vertices(wheel: MeshInstance3D) -> PackedVector3Array:
	var vertices := PackedVector3Array()
	if wheel.mesh == null:
		return vertices
	for surface in range(wheel.mesh.get_surface_count()):
		var arrays := wheel.mesh.surface_get_arrays(surface)
		if arrays.size() > Mesh.ARRAY_VERTEX:
			vertices.append_array(arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array)
	return vertices


func _build_materials_and_geometry() -> void:
	var oak := _material(Color(0.32, 0.235, 0.185), 0.76, 0.0)
	var walnut := _material(Color(0.25, 0.17, 0.135), 0.72, 0.0)
	var wall := _material(Color(0.48, 0.425, 0.38), 0.91, 0.0)
	var plaster := _material(Color(0.64, 0.625, 0.59), 0.93, 0.0)
	var ceramic := _material(Color(0.50, 0.505, 0.495), 0.61, 0.02)
	var rubber := _material(Color(0.075, 0.088, 0.100), 0.82, 0.0)
	var metal := _material(Color(0.51, 0.55, 0.55), 0.42, 0.55)
	var brass := _material(Color(0.53, 0.39, 0.24), 0.47, 0.52)
	var linen := _material(Color(0.80, 0.72, 0.59), 0.94, 0.0)
	var sage := _material(Color(0.19, 0.31, 0.23), 0.91, 0.0)
	var leaf_light := _material(Color(0.30, 0.40, 0.29), 0.90, 0.0)
	var glass := _material(Color(0.76, 0.92, 1.0, 0.12), 0.09, 0.0)
	glass.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	glass.refraction_enabled = true
	glass.refraction_scale = 0.015
	glass.cull_mode = BaseMaterial3D.CULL_DISABLED
	var luminous := _material(Color(0.18, 0.56, 0.72), 0.26, 0.02)
	luminous.emission_enabled = true
	luminous.emission = Color(0.20, 0.67, 1.0)
	luminous.emission_energy_multiplier = 0.42

	# Plaster, inset limestone and solid walnut ribs give the room real depth.
	# The shelf and small household objects sit behind the protective case.
	var rear_wall := _add_box("RearWall", Vector3(14.0, 10.0, 0.14), Vector3(0.0, 2.0, -2.45), wall)
	var inset_wall := _add_box("WallInset", Vector3(3.26, 3.42, 0.055), Vector3(0.0, 1.72, -2.34), plaster)
	# Wall lighting is deliberately separated from the close robot key light.
	# This avoids a clipped white pool on the pale plaster behind Miko.
	rear_wall.layers = 2
	inset_wall.layers = 2
	_add_box("InsetTopTrim", Vector3(3.34, 0.035, 0.075), Vector3(0.0, 3.44, -2.28), linen)
	for side in [-1.0, 1.0]:
		for rib_index in range(4):
			var rib_x: float = float(side) * (1.71 + rib_index * 0.145)
			_add_box("WalnutRib", Vector3(0.065, 3.38, 0.095), Vector3(rib_x, 1.73, -2.25), walnut)
	_add_box("DeskTop", Vector3(7.0, 0.20, 4.4), Vector3(0.0, -0.22, 0.0), oak)
	_add_box("DeskFront", Vector3(7.0, 0.46, 0.12), Vector3(0.0, -0.54, 2.13), oak)
	_add_box("RearShelf", Vector3(3.46, 0.075, 0.34), Vector3(0.0, 1.23, -1.90), oak)
	_add_box("ShelfLip", Vector3(3.46, 0.025, 0.035), Vector3(0.0, 1.19, -1.70), walnut)
	_add_plant(Vector3(-1.05, 1.35, -1.90), ceramic, sage, leaf_light)
	_add_box("BookOne", Vector3(0.11, 0.31, 0.20), Vector3(1.12, 1.42, -1.86), linen)
	_add_box("BookTwo", Vector3(0.085, 0.25, 0.20), Vector3(1.23, 1.39, -1.86), sage)
	_add_box("BookThree", Vector3(0.13, 0.28, 0.20), Vector3(1.36, 1.41, -1.86), walnut)
	_add_wall_lamp(Vector3(-1.55, 1.95, -2.20), brass, linen)
	_add_wall_art(Vector3(1.16, 1.96, -2.21), walnut, linen, sage)

	if open_stage:
		# Clean open desk: no case, dock or pad.
		return

	# Low, weighted charging dock. Two stepped ceramic solids and a real
	# metal trim ring provide contact scale and a plausible support surface.
	_add_cylinder("DockBase", 1.34, 0.13, Vector3(0.0, -0.005, 0.12), rubber)
	_add_cylinder("DockCeramic", 1.27, 0.10, Vector3(0.0, 0.102, 0.12), ceramic)
	_add_torus("BaseRoundedEdge", 1.315, 0.025, Vector3(0.0, 0.052, 0.12), rubber)
	_add_torus("CeramicRoundedEdge", 1.245, 0.027, Vector3(0.0, 0.148, 0.12), ceramic)
	_add_torus("DockTrim", 1.25, 0.022, Vector3(0.0, 0.145, 0.12), metal)
	_add_torus("DockGlow", 1.10, 0.008, Vector3(0.0, 0.157, 0.12), luminous)

	# Acrylic side wings suggest a protective case without putting a cloudy
	# sheet in front of Miko's face. Their edge rails give visible thickness.
	_add_box("LeftGlass", Vector3(0.025, 1.87, 1.20), Vector3(-1.18, 1.09, -0.32), glass)
	_add_box("RightGlass", Vector3(0.025, 1.87, 1.20), Vector3(1.18, 1.09, -0.32), glass)
	# The rear remains open: a full refraction sheet distorted the home wall
	# and threw an opaque white highlight across the robot's background.
	_add_rod("LeftRail", Vector3(-1.18, 0.16, 0.28), Vector3(-1.18, 2.025, 0.28), 0.017, metal)
	_add_rod("RightRail", Vector3(1.18, 0.16, 0.28), Vector3(1.18, 2.025, 0.28), 0.017, metal)
	_add_rod("TopRearRail", Vector3(-1.18, 2.025, -0.91), Vector3(1.18, 2.025, -0.91), 0.017, metal)
	_add_rod("TopLeftRail", Vector3(-1.18, 2.025, -0.91), Vector3(-1.18, 2.025, 0.28), 0.017, metal)
	_add_rod("TopRightRail", Vector3(1.18, 2.025, -0.91), Vector3(1.18, 2.025, 0.28), 0.017, metal)


func _setup_lighting() -> void:
	var world := WorldEnvironment.new()
	world.name = "PresenceWorld"
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.075, 0.098, 0.115)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.57, 0.57, 0.55)
	env.ambient_light_energy = 0.70
	# A soft studio sky only for reflections (the background stays a flat
	# color): glossy plastic picks up gentle gradients instead of hard blots.
	var sky_material := ProceduralSkyMaterial.new()
	sky_material.sky_top_color = Color(0.86, 0.84, 0.80)
	sky_material.sky_horizon_color = Color(0.62, 0.55, 0.48)
	sky_material.ground_horizon_color = Color(0.40, 0.30, 0.23)
	sky_material.ground_bottom_color = Color(0.20, 0.15, 0.12)
	sky_material.sun_angle_max = 0.0
	var sky := Sky.new()
	sky.sky_material = sky_material
	sky.radiance_size = Sky.RADIANCE_SIZE_128
	env.sky = sky
	env.reflected_light_source = Environment.REFLECTION_SOURCE_SKY
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	# Only the self-lit visor face exceeds the threshold, so it alone blooms.
	env.glow_enabled = true
	env.glow_intensity = 0.55
	env.glow_bloom = 0.0
	env.glow_hdr_threshold = 1.15
	env.glow_blend_mode = Environment.GLOW_BLEND_MODE_SOFTLIGHT
	env.ssao_enabled = true
	env.ssao_intensity = 0.8
	world.environment = env
	stage_root.add_child(world)

	# Directional key: a spotlight here painted a bright oval in the middle of
	# the desk. Even light reads as a real room and keeps soft, stable shadows.
	var key := DirectionalLight3D.new()
	key.name = "SoftKey"
	stage_root.add_child(key)
	key.position = Vector3(-1.8, 4.2, 1.25)
	key.look_at(stage_root.to_global(Vector3(0.0, 0.8, 0.0)))
	key.light_color = Color(1.0, 0.94, 0.86)
	key.light_energy = 0.95
	key.light_cull_mask = 1
	key.shadow_enabled = true
	key.light_angular_distance = 1.2
	key.shadow_blur = 1.4
	key.shadow_bias = 0.04
	key.shadow_normal_bias = 1.6
	key.shadow_opacity = 0.62
	key.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_2_SPLITS
	key.directional_shadow_max_distance = 9.0
	key.directional_shadow_blend_splits = true

	var fill := OmniLight3D.new()
	fill.name = "SoftFill"
	fill.position = Vector3(-2.4, 2.4, 2.8)
	fill.light_color = Color(0.78, 0.86, 0.95)
	fill.light_energy = 0.78
	fill.omni_range = 6.0
	fill.light_cull_mask = 1
	stage_root.add_child(fill)

	var rim := SpotLight3D.new()
	rim.name = "WarmRim"
	stage_root.add_child(rim)
	rim.position = Vector3(-1.5, 2.7, -1.7)
	rim.look_at(stage_root.to_global(Vector3(0.0, 1.1, 0.0)))
	rim.light_color = Color(1.0, 0.69, 0.47)
	rim.light_energy = 1.06
	rim.spot_range = 5.0
	rim.spot_angle = 59.0
	rim.light_cull_mask = 1

	accent_light = OmniLight3D.new()
	accent_light.name = "DockBounce"
	accent_light.position = Vector3(0.0, 0.24, 0.22)
	accent_light.light_color = Color(0.45, 0.79, 1.0)
	accent_light.light_energy = 0.22
	accent_light.omni_range = 1.7
	accent_light.light_cull_mask = 1
	stage_root.add_child(accent_light)
	if open_stage:
		# The dock bounce light painted a bright disc on the open desk.
		accent_light.visible = false

	var lamp := OmniLight3D.new()
	lamp.name = "HomeLamp"
	lamp.position = Vector3(-1.55, 1.84, -1.94)
	lamp.light_color = Color(1.0, 0.74, 0.50)
	lamp.light_energy = 0.58
	lamp.omni_range = 2.5
	lamp.light_cull_mask = 1
	stage_root.add_child(lamp)

	var wall_bounce := OmniLight3D.new()
	wall_bounce.name = "WallBounce"
	wall_bounce.position = Vector3(-3.0, 3.5, -1.15)
	wall_bounce.light_color = Color(1.0, 0.83, 0.70)
	wall_bounce.light_energy = 0.36
	wall_bounce.omni_range = 8.0
	wall_bounce.light_cull_mask = 2
	stage_root.add_child(wall_bounce)


func _tune_source_materials() -> void:
	# Duplicate shared imported materials rather than mutating Miko.glb.
	# The texture maps, UVs and original facial markings remain intact.
	for node in actor.find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := node as MeshInstance3D
		if mesh_instance == null or mesh_instance.mesh == null:
			continue
		mesh_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
		for surface in mesh_instance.mesh.get_surface_count():
			var source := mesh_instance.get_active_material(surface) as StandardMaterial3D
			if source == null:
				continue
			var copy := source.duplicate() as StandardMaterial3D
			var name_lower := mesh_instance.name.to_lower()
			if "glass" in name_lower:
				copy.metallic = 0.0
				copy.metallic_texture = null
				copy.roughness_texture = null
				copy.roughness = 0.40
				copy.metallic_specular = 0.04
				copy.clearcoat_enabled = false
				copy.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
				copy.albedo_color.a = 0.35
			else:
				# The original roughness/metal texture contains very glossy spots.
				# Keep the worn paint/normal maps and use a satin coating instead.
				if "eye" not in name_lower and "mouth" not in name_lower and "antenna" not in name_lower:
					copy.roughness_texture = null
					copy.metallic_texture = null
					copy.roughness = 0.52
					copy.metallic = 0.10
					copy.metallic_specular = 0.23
				if "eye" in name_lower or "mouth" in name_lower or "antenna" in name_lower:
					copy.emission_energy_multiplier = 1.16
			mesh_instance.set_surface_override_material(surface, copy)


func _material(color: Color, roughness_value: float, metallic_value: float) -> StandardMaterial3D:
	var result := StandardMaterial3D.new()
	result.albedo_color = color
	result.roughness = roughness_value
	result.metallic = metallic_value
	return result


func _add_box(name_value: String, size: Vector3, position_value: Vector3, material_value: Material) -> MeshInstance3D:
	var mesh := BoxMesh.new()
	mesh.size = size
	var node := MeshInstance3D.new()
	node.name = name_value
	node.mesh = mesh
	node.material_override = material_value
	node.position = position_value
	stage_root.add_child(node)
	return node


func _add_cylinder(name_value: String, radius_value: float, height_value: float, position_value: Vector3, material_value: Material) -> MeshInstance3D:
	var mesh := CylinderMesh.new()
	mesh.top_radius = radius_value
	mesh.bottom_radius = radius_value
	mesh.height = height_value
	mesh.radial_segments = 128
	var node := MeshInstance3D.new()
	node.name = name_value
	node.mesh = mesh
	node.material_override = material_value
	node.position = position_value
	stage_root.add_child(node)
	return node


func _add_torus(name_value: String, radius_value: float, tube_radius: float, position_value: Vector3, material_value: Material) -> MeshInstance3D:
	var mesh := TorusMesh.new()
	mesh.inner_radius = radius_value - tube_radius
	mesh.outer_radius = radius_value + tube_radius
	mesh.ring_segments = 128
	mesh.rings = 20
	var node := MeshInstance3D.new()
	node.name = name_value
	node.mesh = mesh
	node.material_override = material_value
	node.position = position_value
	stage_root.add_child(node)
	return node


func _add_sphere(name_value: String, size: Vector3, position_value: Vector3, material_value: Material) -> MeshInstance3D:
	var mesh := SphereMesh.new()
	mesh.radius = 0.5
	mesh.height = 1.0
	var node := MeshInstance3D.new()
	node.name = name_value
	node.mesh = mesh
	node.material_override = material_value
	node.scale = size * 2.0
	node.position = position_value
	stage_root.add_child(node)
	return node


func _add_rod(name_value: String, from_point: Vector3, to_point: Vector3, radius_value: float, material_value: Material) -> MeshInstance3D:
	var vector := to_point - from_point
	var mesh := CylinderMesh.new()
	mesh.top_radius = radius_value
	mesh.bottom_radius = radius_value
	mesh.height = vector.length()
	mesh.radial_segments = 20
	var node := MeshInstance3D.new()
	node.name = name_value
	node.mesh = mesh
	node.material_override = material_value
	node.position = (from_point + to_point) * 0.5
	node.quaternion = Quaternion(Vector3.UP, vector.normalized())
	stage_root.add_child(node)
	return node


func _add_plant(base: Vector3, pot: Material, dark_leaf: Material, pale_leaf: Material) -> void:
	_add_cylinder("ShelfPlanter", 0.12, 0.17, base, pot)
	_add_cylinder("PlanterSoil", 0.105, 0.012, base + Vector3(0.0, 0.092, 0.0), dark_leaf)
	var stem_start := base + Vector3(0.0, 0.10, 0.0)
	for leaf_index in range(5):
		var angle := leaf_index * TAU / 5.0
		var direction := Vector3(cos(angle), 0.0, sin(angle))
		var stem_end := stem_start + direction * 0.12 + Vector3(0.0, 0.27 + (leaf_index % 2) * 0.08, 0.0)
		_add_rod("PlantStem", stem_start, stem_end, 0.012, dark_leaf)
		var leaf := _add_sphere("PlantLeaf", Vector3(0.052, 0.12, 0.032), stem_end, pale_leaf if leaf_index % 2 == 0 else dark_leaf)
		leaf.rotation.z = -direction.x * 0.43
		leaf.rotation.x = direction.z * 0.35


func _add_wall_lamp(position_value: Vector3, mount: Material, shade: Material) -> void:
	var backplate := _add_cylinder("WallLampBackplate", 0.17, 0.035, position_value, mount)
	backplate.rotation.x = PI * 0.5
	_add_rod("WallLampArm", position_value + Vector3(0.0, -0.01, 0.02), position_value + Vector3(0.0, -0.09, 0.19), 0.015, mount)
	var globe := _material(Color(0.88, 0.72, 0.51), 0.86, 0.0)
	globe.emission_enabled = true
	globe.emission = Color(1.0, 0.57, 0.23)
	globe.emission_energy_multiplier = 0.12
	_add_sphere("WallLampGlobe", Vector3(0.115, 0.125, 0.10), position_value + Vector3(0.0, -0.14, 0.20), globe)
	_add_box("LampShadeTop", Vector3(0.27, 0.023, 0.18), position_value + Vector3(0.0, 0.01, 0.19), shade)


func _add_wall_art(position_value: Vector3, frame: Material, matte: Material, relief: Material) -> void:
	_add_box("WallArtFrame", Vector3(0.62, 0.66, 0.065), position_value, frame)
	_add_box("WallArtMatte", Vector3(0.54, 0.58, 0.066), position_value + Vector3(0.0, 0.0, 0.036), matte)
	var shape := _add_sphere("WallArtRelief", Vector3(0.14, 0.21, 0.028), position_value + Vector3(-0.07, 0.06, 0.085), relief)
	shape.rotation.z = -0.35
	_add_sphere("WallArtPebble", Vector3(0.08, 0.095, 0.025), position_value + Vector3(0.12, -0.15, 0.083), frame)
