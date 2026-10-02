extends RefCounted
const MikoLog = preload("res://miko_log.gd")
## Decides whether a perception event becomes a visible reaction, and how big.
##
## Levels (same names as the host's ResponsePolicy):
##   0 NO_REACTION, 1 MICRO (eyes/face nuance, no gesture), 2 FACIAL
##   (expression, no body gesture), 3 ANIMATION_ONLY / 4 SHORT_VOCAL /
##   5 FULL_SPOKEN (a body gesture; words are the host's business).
## The host's level is an upper bound. On top of it the arbiter applies what
## only the body knows: what it is doing right now (a higher-priority gesture
## is not interrupted, a gesture plays for its minimum time), per-family
## cooldowns, a repetition penalty over recent history, and the conversation
## state (talking wins over minor sensor events).

const LEVELS := ["NO_REACTION", "MICRO", "FACIAL", "ANIMATION_ONLY", "SHORT_VOCAL", "FULL_SPOKEN"]
const PRIORITY := {"USER_BARGE_IN": 7, "USER_SPEECH": 6, "CRITICAL": 5, "HIGH_CONFIDENCE_GESTURE": 4,
	"STRONG_PHYSICAL": 3, "CONVERSATION_GESTURE": 2, "AUTONOMOUS": 1, "IDLE": 0}

## kind -> [family, max level, priority, family cooldown seconds]
const EVENTS := {
	"wave": ["greeting", 3, "HIGH_CONFIDENCE_GESTURE", 6.0],
	"arrived": ["arrival", 3, "CONVERSATION_GESTURE", 20.0],
	"left": ["presence", 1, "AUTONOMOUS", 10.0],
	"approached": ["presence", 2, "AUTONOMOUS", 12.0],
	"covered": ["covered", 3, "STRONG_PHYSICAL", 4.0],
	"uncovered": ["uncovered", 3, "STRONG_PHYSICAL", 4.0],
	"shake_started": ["physical", 3, "STRONG_PHYSICAL", 2.0],
	"shake_active": ["physical", 1, "STRONG_PHYSICAL", 0.0],
	"shake_ended": ["physical_end", 3, "STRONG_PHYSICAL", 3.0],
	"shaken": ["physical", 3, "STRONG_PHYSICAL", 4.0],
	"orientation_changed": ["physical", 3, "STRONG_PHYSICAL", 4.0],
	"device_moved": ["physical_minor", 1, "AUTONOMOUS", 6.0],
	"device_nudged": ["physical_minor", 1, "IDLE", 8.0],
	"smiled": ["joy", 2, "CONVERSATION_GESTURE", 8.0],
	"laughing": ["joy", 3, "CONVERSATION_GESTURE", 10.0],
	"surprised": ["surprise", 2, "CONVERSATION_GESTURE", 8.0],
	"frowned": ["concern", 3, "CONVERSATION_GESTURE", 30.0],
	"yawned": ["sleepy", 3, "AUTONOMOUS", 30.0],
	"winked": ["wink", 2, "CONVERSATION_GESTURE", 8.0],
	"eyes_closed": ["attention", 2, "AUTONOMOUS", 20.0],
	"eyes_opened": ["attention", 1, "AUTONOMOUS", 10.0],
	"looked_at_miko": ["attention", 2, "CONVERSATION_GESTURE", 12.0],
	"looked_away": ["attention", 1, "AUTONOMOUS", 12.0],
	"looked_somewhere": ["attention", 1, "AUTONOMOUS", 8.0],
	"nodded": ["head", 2, "CONVERSATION_GESTURE", 6.0],
	"shook_head": ["head", 2, "CONVERSATION_GESTURE", 6.0],
	"tilted_head": ["head", 1, "AUTONOMOUS", 10.0],
	"gesture": ["hand_sign", 3, "HIGH_CONFIDENCE_GESTURE", 5.0],
	"someone_joined": ["people", 3, "CONVERSATION_GESTURE", 20.0],
	"someone_left": ["people", 1, "AUTONOMOUS", 20.0],
	"light_changed": ["room", 1, "AUTONOMOUS", 15.0],
	"scene_changed": ["room", 1, "AUTONOMOUS", 15.0],
	"motion": ["room", 1, "AUTONOMOUS", 10.0],
}
## Minimum seconds a gesture plays before something of equal priority may
## replace it (a reaction that is cut off immediately looks glitchy).
const MIN_GESTURE_SECONDS := 0.7
const HISTORY_SECONDS := 60.0

var _family_until: Dictionary = {}
var _history: Array = []                 # [time, family]
var _current_priority := -1
var _current_started := -100.0
var decisions := 0
var last_decision: Dictionary = {}


func level_name(level: int) -> String:
	return LEVELS[clampi(level, 0, LEVELS.size() - 1)]


func host_level(event: Dictionary) -> int:
	var name := str(event.get("level", ""))
	var index := LEVELS.find(name)
	return index if index >= 0 else 5


## context: now, user_speaking, miko_speaking, gesture_active, busy_command
func decide(kind: String, event: Dictionary, context: Dictionary) -> Dictionary:
	var now: float = float(context.get("now", 0.0))
	var info: Array = EVENTS.get(kind, ["other", 1, "IDLE", 10.0])
	var family: String = info[0]
	if kind == "gesture":
		family += ":" + str(event.get("gesture", ""))     # each hand sign is its own signal
	var priority: int = PRIORITY.get(info[2], 0)
	var level: int = mini(int(info[1]), host_level(event))
	var reasons := PackedStringArray()
	if level < int(info[1]):
		reasons.append("host_" + level_name(level))
	# Conversation wins: while the owner talks only critical things move the body.
	if bool(context.get("user_speaking", false)) and priority < PRIORITY["CRITICAL"]:
		level = mini(level, 1)
		reasons.append("owner_speaking")
	elif bool(context.get("miko_speaking", false)) and priority < PRIORITY["STRONG_PHYSICAL"]:
		level = mini(level, 2)
		reasons.append("miko_speaking")
	# Doing something the owner asked for, or a stronger gesture in progress.
	if bool(context.get("busy_command", false)) and priority < PRIORITY["STRONG_PHYSICAL"]:
		level = mini(level, 1)
		reasons.append("busy_command")
	if bool(context.get("gesture_active", false)) and level >= 3:
		var young := now - _current_started < MIN_GESTURE_SECONDS
		if priority < _current_priority or (young and priority <= _current_priority):
			level = 2
			reasons.append("gesture_in_progress")
	# Per-family cooldown (an escalation, e.g. a laugh after a smile, is
	# allowed through) and repetition penalty.
	var cooling: Array = _family_until.get(family, [-1.0, 0])
	if level >= 2 and now < float(cooling[0]) and int(info[1]) <= int(cooling[1]):
		level = 1
		reasons.append("family_cooldown")
	var recent := 0
	var kept: Array = []
	for item in _history:
		if now - float(item[0]) <= HISTORY_SECONDS:
			kept.append(item)
			if item[1] == family:
				recent += 1
	_history = kept
	if recent >= 3 and level >= 2:
		level -= 1
		reasons.append("repeated_x%d" % (recent + 1))
	if level >= 2:
		_family_until[family] = [now + float(info[3]), level]
		_history.append([now, family])
	if level >= 3:
		_current_priority = priority
		_current_started = now
	decisions += 1
	var reason := ",".join(reasons) if not reasons.is_empty() else "allowed"
	last_decision = {"event": kind, "level": level, "reason": reason}
	MikoLog.info("BEHAVIOR", "body reaction", {"event": kind, "level": level_name(level), "reason": reason})
	return last_decision


## Autonomous idle behaviour asks before using a family (e.g. an idle wave
## right after reacting to a real wave would look like a double reaction).
func family_ready(family: String, now: float) -> bool:
	return now >= float(_family_until.get(family, [-1.0, 0])[0])


func note_autonomous(family: String, now: float, cooldown: float) -> void:
	var current: Array = _family_until.get(family, [-1.0, 3])
	_family_until[family] = [maxf(float(current[0]), now + cooldown), maxi(int(current[1]), 3)]
	_history.append([now, family])


func gesture_finished() -> void:
	_current_priority = -1
