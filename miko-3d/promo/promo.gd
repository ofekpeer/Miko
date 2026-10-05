extends SceneTree
## Miko promo film (~25 s, 1920x1080, 30 fps), rendered frame by frame in
## Godot: the robot is the real Miko character; every set is built here.
##
## xvfb-run godot --path <miko-3d> --rendering-driver vulkan --fixed-fps 30 \
##   --script res://promo/promo.gd -- --frames=<dir>   (then ffmpeg -framerate 30)
##
## Style: worlds sealed in glass, a soft cream studio, a dark lit keycap, an
## Apple-like product layout and thin, quiet typography.

const SHOTS := [
	["capsule", 5.2], ["hello", 5.0], ["product", 5.6], ["forest", 4.8], ["end", 4.6],
]
const CREAM := Color(0.91, 0.87, 0.82)
const FADE := 0.45

var world: Node3D
var cam: Camera3D
var env_node: WorldEnvironment
var env_light: Environment
var env_dark: Environment
var env_forest: Environment
var sun: DirectionalLight3D
var robot: Node3D
var font: FontVariation
var font_thin: FontVariation
var overlay: CanvasLayer
var fade_rect: ColorRect
var title: Label
var subtitle: Label
var sets := {}
var fireflies: Array = []
var t := 0.0
var shot_index := -1
var shot_start := 0.0
var total := 0.0
var events_done := {}
var stills: Array = []          # --stills=1.5,7,12 saves PNGs at those times (preview without a movie)
var stills_dir := ""
var frames_dir := ""            # --frames=<dir> saves every frame at 1920x1080 (Movie Maker keeps the game's window size)
var frame_no := 0


func _initialize() -> void:
	for s in SHOTS:
		total += float(s[1])
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--stills="):
			for v in arg.trim_prefix("--stills=").split(","):
				stills.append(float(v))
		elif arg.begins_with("--frames="):
			frames_dir = arg.trim_prefix("--frames=")
		elif arg.begins_with("--out="):
			stills_dir = arg.trim_prefix("--out=")
	# Full HD regardless of the game's own window size.
	DisplayServer.window_set_size(Vector2i(1920, 1080))
	root.content_scale_mode = Window.CONTENT_SCALE_MODE_DISABLED
	root.size = Vector2i(1920, 1080)
	_build_world()
	_build_overlay()


# ------------------------------------------------------------------ building

func _build_world() -> void:
	world = Node3D.new()
	root.add_child(world)
	env_light = Environment.new()
	env_light.background_mode = Environment.BG_COLOR
	env_light.background_color = CREAM
	env_light.background_energy_multiplier = 1.32      # match the lit floor: a soft horizon
	env_light.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env_light.ambient_light_color = Color(0.98, 0.94, 0.9)
	env_light.ambient_light_energy = 0.45
	env_light.tonemap_mode = Environment.TONE_MAPPER_AGX
	env_light.tonemap_exposure = 0.9
	env_light.glow_enabled = true
	env_light.glow_intensity = 0.6
	env_light.glow_bloom = 0.05
	env_light.ssao_enabled = true
	env_light.ssao_intensity = 1.4
	env_light.fog_enabled = true
	env_light.fog_light_color = CREAM
	env_light.fog_density = 0.016
	env_light.fog_sky_affect = 0.0
	env_dark = env_light.duplicate()
	env_dark.background_color = Color(0.035, 0.04, 0.05)
	env_dark.ambient_light_color = Color(0.35, 0.45, 0.6)
	env_dark.ambient_light_energy = 0.12
	env_dark.glow_intensity = 1.1
	env_dark.glow_bloom = 0.12
	env_dark.glow_hdr_threshold = 0.9
	env_dark.fog_enabled = true
	env_dark.fog_light_color = Color(0.05, 0.07, 0.1)
	env_dark.fog_density = 0.04
	env_forest = env_light.duplicate()
	env_forest.background_color = Color(0.25, 0.29, 0.25)
	env_forest.fog_light_color = Color(0.25, 0.29, 0.25)
	env_forest.ambient_light_color = Color(0.8, 0.9, 0.8)
	env_forest.glow_intensity = 0.9
	env_forest.fog_density = 0.03
	env_node = WorldEnvironment.new()
	env_node.environment = env_light
	world.add_child(env_node)
	sun = DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-48, 28, 0)
	sun.light_energy = 1.05
	sun.light_color = Color(1.0, 0.96, 0.9)
	sun.shadow_enabled = true
	sun.shadow_blur = 2.5
	sun.directional_shadow_max_distance = 30.0
	sun.shadow_normal_bias = 2.0
	world.add_child(sun)
	cam = Camera3D.new()
	cam.fov = 32
	world.add_child(cam)
	font = FontVariation.new()
	font.base_font = load("res://promo/Heebo.ttf")
	font.variation_opentype = {"wght": 500}
	font_thin = FontVariation.new()
	font_thin.base_font = font.base_font
	font_thin.variation_opentype = {"wght": 200}
	robot = load("res://characters/robot/miko_robot.tscn").instantiate()
	world.add_child(robot)
	sets["capsule"] = _build_capsule(Vector3(0, 0, 0))
	sets["hello"] = _build_hello(Vector3(1500, 0, 0))
	sets["product"] = _build_product(Vector3(3000, 0, 0))
	sets["forest"] = _build_forest(Vector3(4500, 0, 0))
	sets["end"] = _build_end(Vector3(6000, 0, 0))


func _mat(color: Color, rough := 0.8, metal := 0.0) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = color
	m.roughness = rough
	m.metallic = metal
	return m


func _glass(refraction := 0.025, rim := 0.55) -> ShaderMaterial:
	var m := ShaderMaterial.new()
	m.shader = load("res://promo/glass.gdshader")
	m.set_shader_parameter("refraction", refraction)
	m.set_shader_parameter("rim", rim)
	return m


func _mesh(parent: Node3D, mesh: Mesh, material: Material, pos := Vector3.ZERO, rot := Vector3.ZERO,
		scale := Vector3.ONE) -> MeshInstance3D:
	var m := MeshInstance3D.new()
	m.mesh = mesh
	m.material_override = material
	m.position = pos
	m.rotation_degrees = rot
	m.scale = scale
	parent.add_child(m)
	return m


func _studio(origin: Vector3, color: Color) -> Node3D:
	# A seamless studio: a wide floor that melts into a same-colour haze.
	var node := Node3D.new()
	node.position = origin
	world.add_child(node)
	var floor_mesh := PlaneMesh.new()
	floor_mesh.size = Vector2(600, 600)
	var floor_mat := _mat(color, 1.0)
	floor_mat.metallic_specular = 0.0
	_mesh(node, floor_mesh, floor_mat)
	return node


func _moss(parent: Node3D, center: Vector3, radius: float, count: int, height := 0.12, seed := 1) -> void:
	var rng := RandomNumberGenerator.new()
	rng.seed = seed
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_colors = true
	var sphere := SphereMesh.new()
	sphere.radius = 0.5
	sphere.height = 1.0
	sphere.radial_segments = 10
	sphere.rings = 6
	mm.mesh = sphere
	mm.instance_count = count
	for i in count:
		var a := rng.randf() * TAU
		var r := sqrt(rng.randf()) * radius
		var s := rng.randf_range(0.035, 0.085)
		var p := center + Vector3(cos(a) * r, rng.randf_range(0.0, height) * (1.0 - r / radius * 0.6), sin(a) * r)
		mm.set_instance_transform(i, Transform3D(Basis().scaled(Vector3(s, s * rng.randf_range(0.6, 1.0), s)), p))
		var g := rng.randf_range(0.0, 1.0)
		mm.set_instance_color(i, Color(0.12 + 0.16 * g, 0.26 + 0.2 * g, 0.08 + 0.06 * g))
	var inst := MultiMeshInstance3D.new()
	inst.multimesh = mm
	var mat := _mat(Color.WHITE, 0.95)
	mat.vertex_color_use_as_albedo = true
	inst.material_override = mat
	parent.add_child(inst)


func _tree(parent: Node3D, pos: Vector3, height: float, seed := 1) -> void:
	var rng := RandomNumberGenerator.new()
	rng.seed = seed
	var trunk := CylinderMesh.new()
	trunk.top_radius = height * 0.025
	trunk.bottom_radius = height * 0.04
	trunk.height = height * 0.35
	_mesh(parent, trunk, _mat(Color(0.32, 0.22, 0.15)), pos + Vector3(0, height * 0.17, 0))
	var green := Color(0.16, 0.36 + rng.randf() * 0.12, 0.14)
	for k in 4:
		var cone := CylinderMesh.new()
		cone.top_radius = 0.0
		cone.bottom_radius = height * (0.26 - k * 0.05)
		cone.height = height * 0.36
		cone.radial_segments = 12
		_mesh(parent, cone, _mat(green.lightened(k * 0.05), 0.9),
			pos + Vector3(0, height * (0.32 + k * 0.17), 0), Vector3(0, rng.randf() * 90.0, 0))


func _label3d(parent: Node3D, text: String, pos: Vector3, size: int, color: Color, thin := false,
		rot := Vector3.ZERO) -> Label3D:
	var l := Label3D.new()
	l.text = text
	l.font = font_thin if thin else font
	l.font_size = size
	l.pixel_size = 0.002
	l.modulate = color
	l.outline_size = 0
	l.shaded = false
	l.double_sided = true
	l.position = pos
	l.rotation_degrees = rot
	parent.add_child(l)
	return l


func _build_capsule(origin: Vector3) -> Node3D:
	var node := _studio(origin, CREAM)
	# Round white plinth, glass capsule standing on it, a moss world inside.
	var plinth := CylinderMesh.new()
	plinth.top_radius = 1.05
	plinth.bottom_radius = 1.1
	plinth.height = 0.16
	plinth.radial_segments = 64
	_mesh(node, plinth, _mat(Color(0.97, 0.96, 0.94), 0.35), Vector3(0, 0.08, 0))
	_moss(node, Vector3(0, 0.17, 0), 0.72, 1100, 0.1, 3)
	for i in 5:
		var a := float(i) / 5.0 * TAU + 0.6
		_tree(node, Vector3(cos(a) * 0.55, 0.17, sin(a) * 0.45 - 0.1), 0.55 + 0.15 * sin(i * 2.1), i + 10)
	var capsule := CapsuleMesh.new()
	capsule.radius = 0.82
	capsule.height = 2.7
	capsule.radial_segments = 64
	capsule.rings = 16
	_mesh(node, capsule, _glass(0.03, 0.5), Vector3(0, 1.5, 0))
	var light := OmniLight3D.new()
	light.position = Vector3(0.8, 2.6, 1.6)
	light.light_energy = 0.9
	light.omni_range = 6.0
	node.add_child(light)
	return node


func _build_hello(origin: Vector3) -> Node3D:
	var node := Node3D.new()
	node.position = origin
	world.add_child(node)
	var desk := PlaneMesh.new()
	desk.size = Vector2(600, 600)
	_mesh(node, desk, _mat(Color(0.05, 0.055, 0.065), 0.6))
	# A field of dark keycaps around one glass key.
	var key := CylinderMesh.new()
	key.radial_segments = 4
	key.rings = 1
	key.top_radius = 0.62
	key.bottom_radius = 0.82
	key.height = 0.55
	var dark := _mat(Color(0.09, 0.095, 0.11), 0.45)
	var rng := RandomNumberGenerator.new()
	rng.seed = 5
	for gx in range(-3, 4):
		for gz in range(-3, 2):
			if (gx == 0 and gz == 0) or (gz == 1 and absi(gx) <= 1):
				continue
			var p := Vector3(gx * 1.32, 0.275, gz * 1.32)
			_mesh(node, key, dark, p, Vector3(0, 45, 0))
	var key_glass := _glass(0.02, 0.16)
	key_glass.set_shader_parameter("flat_normals", true)
	var glass_key := _mesh(node, key, key_glass, Vector3(0, 0.84, 0), Vector3(0, 45, 0),
		Vector3(1.55, 3.05, 1.55))
	glass_key.name = "GlassKey"
	# Tiny desk + lamp inside the key.
	var top := BoxMesh.new()
	top.size = Vector3(0.62, 0.03, 0.34)
	var wood := _mat(Color(0.55, 0.38, 0.24), 0.6)
	_mesh(node, top, wood, Vector3(-0.32, 0.44, -0.32))
	var leg := BoxMesh.new()
	leg.size = Vector3(0.025, 0.42, 0.025)
	for lx in [-0.6, -0.04]:
		for lz in [-0.47, -0.17]:
			_mesh(node, leg, wood, Vector3(lx, 0.22, lz))
	var screen := BoxMesh.new()
	screen.size = Vector3(0.22, 0.14, 0.01)
	var screen_mat := _mat(Color(0.1, 0.25, 0.3), 0.3)
	screen_mat.emission_enabled = true
	screen_mat.emission = Color(0.3, 0.8, 1.0)
	screen_mat.emission_energy_multiplier = 1.4
	_mesh(node, screen, screen_mat, Vector3(-0.38, 0.54, -0.44))
	var lamp := OmniLight3D.new()
	lamp.position = Vector3(-0.12, 0.62, -0.38)
	lamp.light_color = Color(1.0, 0.72, 0.4)
	lamp.light_energy = 2.2
	lamp.omni_range = 1.8
	lamp.shadow_enabled = true
	node.add_child(lamp)
	var bulb := SphereMesh.new()
	bulb.radius = 0.035
	bulb.height = 0.07
	var bulb_mat := _mat(Color(1, 0.9, 0.7))
	bulb_mat.emission_enabled = true
	bulb_mat.emission = Color(1.0, 0.75, 0.45)
	bulb_mat.emission_energy_multiplier = 6.0
	_mesh(node, bulb, bulb_mat, lamp.position)
	# Glowing word on the glass top.
	_label3d(node, "HELLO", Vector3(0, 1.69, 0.12), 190, Color(0.8, 2.4, 3.0), false, Vector3(-62, 0, 0))
	var rim_light := SpotLight3D.new()
	rim_light.position = Vector3(0, 4.5, -2.5)
	rim_light.rotation_degrees = Vector3(-60, 0, 0)
	rim_light.light_color = Color(0.5, 0.8, 1.0)
	rim_light.light_energy = 3.0
	rim_light.spot_angle = 30
	node.add_child(rim_light)
	var key_light := SpotLight3D.new()
	key_light.position = Vector3(2.5, 3.5, 3.0)
	key_light.light_energy = 1.6
	key_light.spot_angle = 25
	node.add_child(key_light)
	key_light.look_at_from_position(origin + key_light.position, origin + Vector3(0, 0.6, 0))
	return node


func _build_product(origin: Vector3) -> Node3D:
	var node := _studio(origin, Color(0.93, 0.9, 0.86))
	var disc := CylinderMesh.new()
	disc.top_radius = 1.0
	disc.bottom_radius = 1.0
	disc.height = 0.22
	disc.radial_segments = 96
	_mesh(node, disc, _mat(Color(0.985, 0.98, 0.975), 0.25), Vector3(0, 0.11, 0))
	var ring := CylinderMesh.new()
	ring.top_radius = 1.04
	ring.bottom_radius = 1.04
	ring.height = 0.05
	ring.radial_segments = 96
	_mesh(node, ring, _mat(Color(0.2, 0.2, 0.22), 0.3), Vector3(0, 0.025, 0))
	# Floating tiles, like a product board: pastel gradients and a dark phone.
	var tiles := [
		[Vector3(-1.45, 1.55, -1.4), Color(1.0, 0.62, 0.48), Color(1.0, 0.86, 0.6), "מקשיב"],
		[Vector3(2.0, 1.25, -0.9), Color(0.55, 0.45, 0.95), Color(0.75, 0.62, 1.0), "זוכר"],
		[Vector3(1.75, 0.5, 0.6), Color(0.3, 0.75, 0.7), Color(0.45, 0.85, 0.75), "מגיב"],
	]
	var index := 0
	for tile in tiles:
		var quad := QuadMesh.new()
		quad.size = Vector2(0.95, 1.15)
		var m := ShaderMaterial.new()
		m.shader = load("res://promo/card.gdshader")
		m.set_shader_parameter("top_color", tile[1])
		m.set_shader_parameter("bottom_color", tile[2])
		m.set_shader_parameter("size", Vector2(0.95, 1.15))
		m.set_shader_parameter("radius", 0.14)
		var card := _mesh(node, quad, m, tile[0], Vector3(0, -18.0 if tile[0].x > 0 else 18.0, 0))
		card.name = "Tile%d" % index
		_label3d(card, tile[3], Vector3(0, -0.34, 0.01), 96, Color(1, 1, 1, 0.97))
		index += 1
	var phone := QuadMesh.new()
	phone.size = Vector2(0.9, 1.8)
	var pm := ShaderMaterial.new()
	pm.shader = load("res://promo/card.gdshader")
	pm.set_shader_parameter("top_color", Color(0.14, 0.14, 0.16))
	pm.set_shader_parameter("bottom_color", Color(0.1, 0.1, 0.12))
	pm.set_shader_parameter("size", Vector2(0.9, 1.8))
	pm.set_shader_parameter("radius", 0.13)
	var phone_node := _mesh(node, phone, pm, Vector3(-2.3, 1.2, 0.5), Vector3(0, 22, 0))
	phone_node.name = "Phone"
	var bubbles := [[-0.12, 0.55, Color(0.04, 0.52, 1.0), "היי מיקו!"], [0.1, 0.32, Color(0.25, 0.25, 0.28), "היי! מה קורה?"],
		[-0.1, 0.09, Color(0.04, 0.52, 1.0), "תזכיר לי מחר"], [0.12, -0.14, Color(0.25, 0.25, 0.28), "סגור, אזכיר ✓"]]
	for b in bubbles:
		var bq := QuadMesh.new()
		bq.size = Vector2(0.56, 0.16)
		var bm := ShaderMaterial.new()
		bm.shader = load("res://promo/card.gdshader")
		bm.set_shader_parameter("top_color", b[2])
		bm.set_shader_parameter("bottom_color", b[2])
		bm.set_shader_parameter("size", Vector2(0.56, 0.16))
		bm.set_shader_parameter("radius", 0.07)
		var bubble := _mesh(phone_node, bq, bm, Vector3(b[0], b[1], 0.005))
		_label3d(bubble, b[3], Vector3(0, 0, 0.004), 34, Color(1, 1, 1))
	return node


func _build_forest(origin: Vector3) -> Node3D:
	var node := _studio(origin, Color(0.27, 0.31, 0.27))
	# A tall glass pill, a whole forest inside, Miko at its feet.
	_moss(node, Vector3(0, 0.05, 0), 0.78, 1500, 0.22, 21)
	var rng := RandomNumberGenerator.new()
	rng.seed = 33
	for i in 11:
		var a := rng.randf() * TAU
		var r := rng.randf_range(0.25, 0.62)
		var z := sin(a) * r * 0.6 - 0.28
		_tree(node, Vector3(cos(a) * r, 0.1, z), rng.randf_range(1.5, 2.7), 40 + i)
	var pill := CapsuleMesh.new()
	pill.radius = 0.85
	pill.height = 4.6
	pill.radial_segments = 64
	pill.rings = 20
	_mesh(node, pill, _glass(0.03, 0.6), Vector3(0, 2.3, 0))
	_label3d(node, "MIKO", Vector3(0, 2.75, 0.87), 230, Color(1.4, 1.4, 1.35))
	_label3d(node, "YOUR LITTLE COMPANION", Vector3(0, 2.42, 0.87), 46, Color(1.2, 1.2, 1.15), true)
	var light := OmniLight3D.new()
	light.position = Vector3(1.4, 3.5, 2.2)
	light.light_energy = 1.2
	light.omni_range = 8.0
	node.add_child(light)
	# Fireflies.
	var bug := SphereMesh.new()
	bug.radius = 0.018
	bug.height = 0.036
	var glow := _mat(Color(1, 1, 0.7))
	glow.emission_enabled = true
	glow.emission = Color(1.0, 0.9, 0.45)
	glow.emission_energy_multiplier = 8.0
	for i in 26:
		var f := _mesh(node, bug, glow, Vector3.ZERO)
		fireflies.append([f, rng.randf() * TAU, rng.randf_range(0.2, 0.65), rng.randf_range(0.4, 3.4), rng.randf_range(0.3, 0.9)])
	return node


func _build_end(origin: Vector3) -> Node3D:
	var node := _studio(origin, CREAM)
	var disc := CylinderMesh.new()
	disc.top_radius = 0.75
	disc.bottom_radius = 0.75
	disc.height = 0.08
	disc.radial_segments = 96
	_mesh(node, disc, _mat(Color(0.985, 0.98, 0.975), 0.3), Vector3(0, 0.04, 0))
	return node


func _build_overlay() -> void:
	overlay = CanvasLayer.new()
	root.add_child(overlay)
	title = Label.new()
	title.add_theme_font_override("font", font_thin)
	title.add_theme_font_size_override("font_size", 132)
	title.add_theme_constant_override("outline_size", 0)
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.add_child(title)
	subtitle = Label.new()
	subtitle.add_theme_font_override("font", font)
	subtitle.add_theme_font_size_override("font_size", 40)
	subtitle.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	subtitle.text_direction = Control.TEXT_DIRECTION_RTL
	subtitle.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.add_child(subtitle)
	fade_rect = ColorRect.new()
	fade_rect.color = Color(CREAM.r, CREAM.g, CREAM.b, 1.0)
	fade_rect.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	fade_rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	overlay.add_child(fade_rect)


# ------------------------------------------------------------------ timeline

func _process(delta: float) -> bool:
	if frames_dir != "" and t > 0.0:
		root.get_texture().get_image().save_png("%s/f%05d.png" % [frames_dir, frame_no])
		frame_no += 1
	t += delta
	if not stills.is_empty() and t >= float(stills[0]) + 0.05:
		var at: float = stills.pop_front()
		root.get_texture().get_image().save_png("%s/still_%05.1f.png" % [stills_dir, at])
		if stills.is_empty():
			return true
	if t >= total:
		return true                                  # quit
	var acc := 0.0
	var index := 0
	for i in SHOTS.size():
		if t < acc + float(SHOTS[i][1]):
			index = i
			break
		acc += float(SHOTS[i][1])
	if index != shot_index:
		shot_index = index
		shot_start = acc
		_enter_shot(str(SHOTS[index][0]))
	var length := float(SHOTS[index][1])
	var u := t - shot_start
	# Soft fades between shots (cream, or black around the dark shot).
	var fade := 0.0
	if u < FADE:
		fade = 1.0 - u / FADE
	elif u > length - FADE and index < SHOTS.size() - 1:
		fade = (u - (length - FADE)) / FADE
	if index == SHOTS.size() - 1 and u > length - 0.8:
		fade = (u - (length - 0.8)) / 0.8
	if t < 0.9:
		fade = maxf(fade, 1.0 - t / 0.9)
	var dark := str(SHOTS[index][0]) == "hello"
	var fade_color := Color(0.02, 0.02, 0.025) if dark else CREAM
	fade_rect.color = Color(fade_color.r, fade_color.g, fade_color.b, smoothstep(0.0, 1.0, fade))
	call("_update_" + str(SHOTS[index][0]), u, length)
	return false


func _ease(x: float) -> float:
	return smoothstep(0.0, 1.0, clampf(x, 0.0, 1.0))


func _camera(from_pos: Vector3, to_pos: Vector3, from_look: Vector3, to_look: Vector3, k: float) -> void:
	var e := _ease(k)
	cam.global_position = from_pos.lerp(to_pos, e)
	cam.look_at(from_look.lerp(to_look, e))


func _text(main: String, sub: String, u: float, show_at: float, hide_at: float, main_y := 0.36,
		sub_y := 0.5, color := Color(0.12, 0.12, 0.13)) -> void:
	var a := _ease((u - show_at) / 0.7) * (1.0 - _ease((u - hide_at) / 0.6))
	title.text = main
	subtitle.text = sub
	var view := root.get_visible_rect().size
	var rise := (1.0 - _ease((u - show_at) / 0.9)) * 18.0
	title.position = Vector2(0, view.y * main_y - 90 + rise)
	title.size = Vector2(view.x, 180)
	subtitle.position = Vector2(0, view.y * sub_y - 30 + rise * 0.6)
	subtitle.size = Vector2(view.x, 70)
	title.add_theme_color_override("font_color", Color(color.r, color.g, color.b, a))
	subtitle.add_theme_color_override("font_color", Color(color.r, color.g, color.b, a * 0.85))


func _place_robot(origin: Vector3, scale: float, yaw_deg := 0.0) -> void:
	robot.recover("promo shot")
	robot.position = origin
	robot.scale = Vector3.ONE * scale
	robot._pos = Vector2.ZERO
	robot._target = Vector2.ZERO
	robot._yaw = deg_to_rad(yaw_deg)
	robot._face_yaw_goal = robot._yaw
	robot._behavior = "linger"
	robot._behavior_left = 999.0
	robot._asleep = false
	robot._sit = 0.0
	robot._sit_goal = 0.0
	robot._look_user = true
	robot.manual_state = {"emotion": "happy"}
	robot.reaction_delay_enabled = false
	events_done.clear()


func _once(key: String) -> bool:
	if events_done.has(key):
		return false
	events_done[key] = true
	return true


func _enter_shot(name: String) -> void:
	title.text = ""
	subtitle.text = ""
	var origin: Vector3 = (sets[name] as Node3D).position
	env_node.environment = env_dark if name == "hello" else (env_forest if name == "forest" else env_light)
	sun.visible = name != "hello"
	sun.light_energy = 0.55 if name == "forest" else 1.05
	match name:
		"capsule":
			_place_robot(origin + Vector3(0, 0.2, 0.05), 0.62)
			robot._asleep = true
			robot._sit = 1.0
			robot._sit_goal = 1.0
			robot._look_user = false
		"hello":
			_place_robot(origin + Vector3(0.12, 0.02, 0.28), 0.42, 10.0)
		"product":
			_place_robot(origin + Vector3(0, 0.22, 0), 0.95)
		"forest":
			_place_robot(origin + Vector3(0.0, 0.18, 0.42), 0.5)
		"end":
			_place_robot(origin + Vector3(0, 0.08, 0), 0.9)


func _update_capsule(u: float, length: float) -> void:
	var o: Vector3 = sets["capsule"].position
	var a := lerpf(-0.35, 0.25, _ease(u / length))
	var dist := lerpf(7.2, 5.4, _ease(u / length))
	_camera(o + Vector3(sin(a) * dist, 2.3, cos(a) * dist), o + Vector3(sin(a) * dist, 1.7, cos(a) * dist),
		o + Vector3(0, 1.45, 0), o + Vector3(0, 1.15, 0), u / length)
	if u > 2.4 and _once("wake"):
		robot._asleep = false
		robot._sit_goal = 0.0
		robot._look_user = true
		robot._queue_gesture("stretch", 2.4, 0.0, true)
	if u > 4.0 and _once("smile"):
		robot.manual_state = {"emotion": "excited"}
	_text("MIKO", "חבר קטן. עולם שלם.", u, 0.9, 4.3, 0.13, 0.22)


func _update_hello(u: float, length: float) -> void:
	var o: Vector3 = sets["hello"].position
	var k := u / length
	var a := lerpf(0.75, 0.05, _ease(k))
	var dist := lerpf(6.4, 3.9, _ease(k))
	var h := lerpf(4.4, 2.1, _ease(k))
	_camera(o + Vector3(sin(a) * dist, h, cos(a) * dist), o + Vector3(sin(a) * dist, h, cos(a) * dist),
		o + Vector3(0, 0.6, 0), o + Vector3(0, 0.75, 0), 0.0)
	if u > 1.6 and _once("wave"):
		robot._queue_gesture("wave", 2.6, 1.0, true)
	_text("", "הוא רואה אותך.", u, 2.2, 4.4, 0.18, 0.86, Color(0.92, 0.95, 1.0))


func _update_product(u: float, length: float) -> void:
	var o: Vector3 = sets["product"].position
	var k := u / length
	var a := lerpf(-0.5, 0.45, _ease(k))
	var dist := lerpf(8.6, 7.4, _ease(k))
	_camera(o + Vector3(sin(a) * dist, 3.6, cos(a) * dist), o + Vector3(sin(a) * dist, 3.0, cos(a) * dist),
		o + Vector3(0, 0.95, 0), o + Vector3(0, 0.9, 0), k)
	var node: Node3D = sets["product"]
	for i in 3:
		var tile := node.get_node("Tile%d" % i) as Node3D
		tile.position.y += sin(t * 1.3 + i * 1.7) * 0.0025
		tile.rotation_degrees.z = sin(t * 0.9 + i) * 2.0
	(node.get_node("Phone") as Node3D).position.y += sin(t * 1.1) * 0.002
	if u > 0.8 and _once("nod"):
		robot._queue_gesture("nod", 1.1, 0.0, true)
	if u > 2.2 and _once("dance"):
		robot.manual_state = {"emotion": "excited"}
		robot._queue_gesture("dance", 3.0, 0.0, true)
	_text("", "מקשיב. זוכר. מגיב.", u, 1.0, 5.0, 0.2, 0.9)


func _update_forest(u: float, length: float) -> void:
	var o: Vector3 = sets["forest"].position
	var k := u / length
	_camera(o + Vector3(0.5, 3.4, 10.5), o + Vector3(-0.25, 1.4, 6.2), o + Vector3(0, 2.6, 0), o + Vector3(0, 1.2, 0), k)
	for f in fireflies:
		var node: MeshInstance3D = f[0]
		var ang: float = f[1] + t * f[4]
		node.position = o + Vector3(cos(ang) * f[2], f[3] + sin(t * 1.7 + f[1]) * 0.15, sin(ang) * f[2])
	if u > 1.4 and _once("hop"):
		robot._queue_gesture("hop", 1.1, 0.0, true)
	if u > 2.8 and _once("hop2"):
		robot._queue_gesture("wave", 2.0, -1.0, true)


func _update_end(u: float, length: float) -> void:
	var o: Vector3 = sets["end"].position
	var k := u / length
	_camera(o + Vector3(0, 1.4, 6.8), o + Vector3(0, 1.3, 6.2), o + Vector3(0, 0.95, 0), o + Vector3(0, 0.9, 0), k)
	if u > 0.6 and _once("wave"):
		robot._queue_gesture("wave", 2.4, 1.0, true)
	_text("Miko", "מיקו · חבר שתמיד שם", u, 0.8, 99.0, 0.2, 0.3)
