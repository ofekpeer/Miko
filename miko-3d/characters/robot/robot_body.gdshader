shader_type spatial;
// Miko's body: clean two-tone toy plastic. COLOR.r is a smooth per-vertex
// "whiteness" field baked by character_pipeline/tools/robot_clean_colors.py
// from the original atlas. Thresholding it here with screen-space AA gives
// crisp, curved part edges with no texture speckles or mip-map shimmer.

// cull_disabled: the decimated source mesh has hairline gaps; drawing the
// shell's inner side fills them instead of letting the room show through.
render_mode diffuse_burley, specular_schlick_ggx, cull_disabled;

uniform vec3 shell_color : source_color = vec3(0.90, 0.90, 0.88);
uniform vec3 trim_color : source_color = vec3(0.045, 0.050, 0.062);
uniform float shell_roughness = 0.36;
uniform float trim_roughness = 0.52;
uniform float edge = 0.5;

void fragment() {
	float v = COLOR.r;
	float w = max(fwidth(v) * 0.9, 0.012);
	float shell = smoothstep(edge - w, edge + w, v);
	ALBEDO = mix(trim_color, shell_color, shell);
	if (!FRONT_FACING) {
		ALBEDO *= 0.35;   // inside of the shell: a soft shadow tone, never bright
	}
	ROUGHNESS = mix(trim_roughness, shell_roughness, shell);
	METALLIC = 0.0;
	SPECULAR = 0.42;
	CLEARCOAT = 0.22 * shell;
	CLEARCOAT_ROUGHNESS = 0.3;
	// Soft rim keeps the white shell readable against the warm room.
	RIM = 0.12;
	RIM_TINT = 0.6;
}
