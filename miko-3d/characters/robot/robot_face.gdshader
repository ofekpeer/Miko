shader_type spatial;
// Miko's live face on the robot visor. UV is the planar "FaceUV" map exported
// with the visor screen (0..1 across the visor). Eyes and mouth are signed
// distance shapes driven every frame by miko_robot_character.gd, so the face
// is always one drawing: no painted face underneath, no second mouth.

render_mode blend_mix, cull_disabled, unshaded;

uniform vec3 glass_color : source_color = vec3(0.010, 0.026, 0.048);
uniform vec3 face_color : source_color = vec3(0.32, 0.86, 1.0);
uniform float brightness = 2.6;
uniform float power = 1.0;            // 0 = screen asleep, 1 = awake
uniform float aspect = 1.332;         // visor width / height
uniform vec2 gaze = vec2(0.0);        // -1..1 (x right, y up)
uniform float eye_open_l = 1.0;       // 0 closed .. 1 open
uniform float eye_open_r = 1.0;
uniform float eye_size = 1.0;
uniform float happy = 0.0;            // ^ ^ arcs
uniform float sad = 0.0;              // outer corners drop
uniform float angry = 0.0;            // inner corners drop
uniform float surprise = 0.0;         // rounder, bigger eyes
uniform float mouth_open = 0.0;
uniform float mouth_round = 0.0;
uniform float mouth_wide = 0.0;
uniform float mouth_smile = 0.35;     // -1 frown .. 1 smile
uniform float mouth_visible = 1.0;
uniform vec2 eye_center = vec2(0.20, 0.50);   // offset of each eye from the middle (x * aspect units, y down)
uniform float mouth_y = 0.71;

float sd_round_box(vec2 p, vec2 b, float r) {
	vec2 q = abs(p) - b + r;
	return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

float sd_segment(vec2 p, vec2 a, vec2 b) {
	vec2 pa = p - a;
	vec2 ba = b - a;
	float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
	return length(pa - ba * h);
}

// Thick parabola y = k x^2 for |x| <= w (y grows downward in face space).
float sd_curve(vec2 p, float w, float k, float thickness) {
	float d = 1e9;
	vec2 previous = vec2(-w, k * w * w);
	for (int i = 1; i <= 14; i++) {
		float x = -w + 2.0 * w * float(i) / 14.0;
		vec2 current = vec2(x, k * x * x);
		d = min(d, sd_segment(p, previous, current));
		previous = current;
	}
	return d - thickness;
}

float eye(vec2 p, vec2 c, float open_amount, float side) {
	vec2 q = p - c;
	float w = 0.068 * eye_size * (1.0 + 0.15 * surprise);
	float h = 0.118 * eye_size * (1.0 + 0.12 * surprise);
	float hh = max(h * open_amount, 0.011);
	float d = sd_round_box(q, vec2(w, hh), min(w, hh) * (0.85 + 0.15 * surprise));
	// Sad / angry: slice the top with a tilted line (side = -1 left, +1 right).
	float tilt = (sad - angry) * 0.55 * side;
	float cut_strength = max(sad, angry);
	if (cut_strength > 0.001) {
		vec2 n = normalize(vec2(tilt, -1.0));
		float plane = dot(q - vec2(0.0, -hh * (1.0 - 0.55 * cut_strength)), n);
		d = max(d, plane);
	}
	// Happy: a "^" arc. Face y grows downward, so k > 0 drops the ends
	// below the apex; the apex sits a little above the eye's center.
	float arc = sd_curve(q - vec2(0.0, -0.025), w * 1.08, 7.5, 0.017);
	return mix(d, arc, smoothstep(0.0, 1.0, happy));
}

float mouth(vec2 p, vec2 c) {
	vec2 q = p - c;
	float o = clamp(mouth_open, 0.0, 1.0);
	float w = 0.070 * (1.0 + 0.55 * mouth_wide - 0.45 * mouth_round) * (1.0 - 0.25 * o * mouth_round);
	float h = 0.009 + o * (0.072 + 0.04 * mouth_round - 0.025 * mouth_wide);
	// Smile bends the shape; open speech shapes relax the curve.
	q.y += mouth_smile * 5.0 * q.x * q.x * (1.0 - 0.55 * o);
	return sd_round_box(q, vec2(w, h * 0.5), min(w, h * 0.5));
}

void fragment() {
	vec2 p = vec2(UV.x * aspect, UV.y);           // y grows downward (glTF/Godot UV)
	vec2 middle = vec2(0.5 * aspect, 0.0);
	vec2 look = gaze * vec2(0.055, -0.040);
	float d_left = eye(p, middle + vec2(-eye_center.x, eye_center.y) + look, eye_open_l, -1.0);
	float d_right = eye(p, middle + vec2(eye_center.x, eye_center.y) + look, eye_open_r, 1.0);
	float d_mouth = mouth(p, middle + vec2(0.0, mouth_y) + look * 0.35);
	float aa = fwidth(p.x) * 1.4;
	float eyes = 1.0 - smoothstep(-aa, aa, min(d_left, d_right));
	float mouth_fill = (1.0 - smoothstep(-aa, aa, d_mouth)) * mouth_visible;
	float nearest = min(min(d_left, d_right), mix(1.0, d_mouth, mouth_visible));
	float glow = exp(-max(nearest, 0.0) * 42.0) * 0.38;
	float stripes = 0.80 + 0.20 * step(0.45, fract(p.x * 58.0));
	float lit = (max(eyes, mouth_fill) * stripes + glow) * power;
	// Self-lit display: lamps and the helmet brim's shadow never touch it, so
	// no jagged highlight can cover the face. A fixed sheen and a soft edge
	// fresnel keep it reading as curved glass.
	float scan = 0.94 + 0.06 * sin(UV.y * 420.0);
	float fresnel = pow(1.0 - clamp(abs(dot(NORMAL, VIEW)), 0.0, 1.0), 3.0);
	vec2 s = UV - vec2(0.30, 0.16);
	float sheen = exp(-dot(s * vec2(2.2, 7.0), s * vec2(2.2, 7.0))) * 0.10;
	vec3 glass = glass_color * scan + vec3(0.55, 0.65, 0.75) * (fresnel * 0.22 + sheen);
	ALBEDO = glass + face_color * brightness * lit;
}
