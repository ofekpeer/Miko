extends RefCounted
## Structured, greppable log lines for the Godot side, in the same style as
## the host: "<seconds> CATEGORY: message key=value ...". Categories used:
## ANIMATION, BEHAVIOR, PERCEPTION, VOICE, RECOVERY, PERF.


static func info(category: String, message: String, fields: Dictionary = {}) -> void:
	var parts := PackedStringArray()
	for key in fields:
		parts.append("%s=%s" % [key, str(fields[key])])
	var suffix := (" " + " ".join(parts)) if not parts.is_empty() else ""
	print("%.2f %s: %s%s" % [Time.get_ticks_msec() / 1000.0, category, message, suffix])
