shader_type spatial;
render_mode blend_mix, cull_back, specular_schlick_ggx, depth_draw_opaque;
// Clear glass: the scene behind it, slightly bent, with a bright fresnel rim
// and sharp highlights (inspired by product shots of objects sealed in glass).
uniform sampler2D screen_tex : hint_screen_texture, filter_linear_mipmap;
uniform vec4 tint : source_color = vec4(0.97, 0.99, 1.0, 1.0);
uniform float refraction = 0.025;
uniform float rim = 0.55;
uniform float blur = 0.0;
uniform bool flat_normals = false;   // faceted shapes (a keycap) instead of smooth ones

void fragment() {
	vec3 n = NORMAL;
	if (flat_normals) {
		n = normalize(cross(dFdx(VERTEX), dFdy(VERTEX)));
		NORMAL = n;
	}
	float facing = clamp(abs(dot(n, VIEW)), 0.0, 1.0);
	float fresnel = pow(1.0 - facing, 3.0);
	vec2 offset = n.xy * refraction * (0.4 + fresnel);
	vec3 behind = textureLod(screen_tex, SCREEN_UV - offset, blur).rgb;
	ALBEDO = vec3(0.0);
	EMISSION = behind * tint.rgb * (1.0 - 0.35 * fresnel) + vec3(fresnel * rim);
	ROUGHNESS = 0.04;
	METALLIC = 0.0;
	SPECULAR = 1.0;
}
