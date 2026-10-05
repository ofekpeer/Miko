shader_type spatial;
render_mode cull_disabled, specular_schlick_ggx;
// A soft, rounded card with a vertical gradient (Apple-style product tiles).
uniform vec4 top_color : source_color = vec4(1.0, 0.75, 0.6, 1.0);
uniform vec4 bottom_color : source_color = vec4(1.0, 0.9, 0.7, 1.0);
uniform float radius = 0.12;
uniform vec2 size = vec2(1.0, 1.0);
uniform float glow = 0.0;

void fragment() {
	vec2 p = (UV - 0.5) * size;
	vec2 q = abs(p) - (size * 0.5 - vec2(radius));
	float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
	if (d > 0.0) {
		discard;
	}
	vec3 c = mix(top_color.rgb, bottom_color.rgb, UV.y);
	ALBEDO = c;
	EMISSION = c * glow;
	ROUGHNESS = 0.35;
	SPECULAR = 0.6;
}
