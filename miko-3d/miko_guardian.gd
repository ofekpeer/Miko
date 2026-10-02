extends Node
const MikoLog = preload("res://miko_log.gd")
## Keeps Miko's window alive and explains failures.
##
## * Robot heartbeat: the character increments `frame_serial` at the end of
##   every update. If it stops (a failing step, an impossible state), the
##   stage it stopped in is logged and the character is reset to idle.
## * Conversation states have maximum durations: "thinking", "connecting"
##   and "finishing" can never last forever (the host also retries/releases;
##   this is the client-side backstop).
## * Main loop stall detector: a tiny thread notices when the whole window
##   stops ticking (e.g. a GPU stall) and logs how long and in which state.
## * Performance guard: if frames get slow for several seconds, rendering
##   quality steps down (logged), so a weak GPU degrades gracefully instead
##   of looking frozen.

const ROBOT_STALL_SECONDS := 1.5
var state_limits := {"thinking": 45.0, "connecting": 30.0, "finishing": 4.0}
const SLOW_FRAME_MS := 70.0

var controller: Node
var robot: Node
var _last_serial := -1
var _serial_changed_at := 0.0
var _robot_recoveries := 0
var _status := ""
var _status_since := 0.0
var _check_timer: Timer
var _frame_ms_avg := 16.0
var _slow_since := -1.0
var _quality_level := 0
var _thread: Thread
var _thread_running := false
var _last_tick_msec := 0
var _stall_reported := false
var _last_stage := ""


func setup(owner_controller: Node) -> void:
	controller = owner_controller


func _ready() -> void:
	_check_timer = Timer.new()
	_check_timer.wait_time = 0.5
	_check_timer.autostart = true
	add_child(_check_timer)
	_check_timer.timeout.connect(_check)
	_last_tick_msec = Time.get_ticks_msec()
	if OS.get_cmdline_user_args().has("--no-stall-thread"):
		return
	_thread = Thread.new()
	_thread_running = true
	_thread.start(_watch_main_loop)


func _exit_tree() -> void:
	_thread_running = false
	if _thread != null and _thread.is_started():
		_thread.wait_to_finish()


func _process(delta: float) -> void:
	_last_tick_msec = Time.get_ticks_msec()
	if robot != null and is_instance_valid(robot):
		_last_stage = str(robot.get("stage"))
	_frame_ms_avg = lerpf(_frame_ms_avg, delta * 1000.0, 0.05)


func _watch_main_loop() -> void:
	# Runs on its own thread: only reads plain values and prints.
	while _thread_running:
		OS.delay_msec(250)
		var stalled := Time.get_ticks_msec() - _last_tick_msec
		if stalled > 2000 and not _stall_reported:
			_stall_reported = true
			print("RECOVERY: main loop stalled for %d ms (robot stage=%s, status=%s)" % [stalled, _last_stage, _status])
		elif stalled < 500 and _stall_reported:
			_stall_reported = false
			print("RECOVERY: main loop running again")


func _now() -> float:
	return Time.get_ticks_msec() / 1000.0


func _check() -> void:
	if robot == null or not is_instance_valid(robot):
		robot = controller.get_node_or_null("MikoScene") if controller != null else null
	_check_robot()
	_check_conversation_state()
	_check_performance()


func _check_robot() -> void:
	if robot == null or not robot.has_method("recover"):
		return
	var serial: int = int(robot.get("frame_serial"))
	if serial != _last_serial:
		_last_serial = serial
		_serial_changed_at = _now()
		return
	if _now() - _serial_changed_at < ROBOT_STALL_SECONDS or not robot.is_processing():
		return
	_robot_recoveries += 1
	MikoLog.info("RECOVERY", "robot update stalled", {"stage": robot.get("stage"),
		"seconds": snappedf(_now() - _serial_changed_at, 0.1), "count": _robot_recoveries,
		"hint": "a GDScript error is printed just above in the log"})
	robot.call("recover", "frame stalled in stage " + str(robot.get("stage")))
	_serial_changed_at = _now()


func _check_conversation_state() -> void:
	var status := str(controller.get("realtime_display_status")) if controller != null else ""
	if status != _status:
		_status = status
		_status_since = _now()
		return
	if not state_limits.has(status) or _now() - _status_since < float(state_limits[status]):
		return
	MikoLog.info("RECOVERY", "conversation state lasted too long", {"state": status,
		"seconds": snappedf(_now() - _status_since, 0.1)})
	var voice: Node = controller.get("realtime_voice")
	if voice != null and voice.has_method("recover"):
		voice.call("recover", "state %s timed out" % status)
	if controller.has_method("_on_realtime_status"):
		controller.call("_on_realtime_status", "idle", "אפשר לדבר שוב")
	_status_since = _now()


func _check_performance() -> void:
	if _frame_ms_avg < SLOW_FRAME_MS:
		_slow_since = -1.0
		return
	if _slow_since < 0.0:
		_slow_since = _now()
		return
	if _now() - _slow_since < 3.0 or _quality_level >= 3:
		return
	_quality_level += 1
	_slow_since = -1.0
	var viewport := get_viewport()
	match _quality_level:
		1:
			viewport.screen_space_aa = Viewport.SCREEN_SPACE_AA_DISABLED
			viewport.msaa_3d = Viewport.MSAA_2X
		2:
			RenderingServer.positional_soft_shadow_filter_set_quality(RenderingServer.SHADOW_QUALITY_SOFT_LOW)
			RenderingServer.directional_soft_shadow_filter_set_quality(RenderingServer.SHADOW_QUALITY_SOFT_LOW)
			viewport.msaa_3d = Viewport.MSAA_DISABLED
		3:
			viewport.scaling_3d_scale = 0.75
	MikoLog.info("PERF", "frames slow; lowering render quality", {"frame_ms": snappedf(_frame_ms_avg, 0.1),
		"level": _quality_level})
