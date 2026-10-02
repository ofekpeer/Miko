extends SceneTree
func _initialize() -> void:
    var world: Node3D = load("res://main.tscn").instantiate()
    world.set_script(null)
    get_root().add_child.call_deferred(world)
    await process_frame
    var presence: Node3D = world.get_node("Presence")
    presence.setup_stage()
    var actor: Node3D = world.get_node("MikoScene")
    var animation: AnimationPlayer = actor.get_node("AnimationPlayer")
    assert(animation.has_animation("idle"))
    assert(animation.has_animation("look"))
    assert(actor.get_node_or_null("MikoRig") != null)
    for i in range(4): await process_frame
    var initial_position := actor.position
    presence.set_listening(true)
    presence.set_attention(actor.to_global(Vector3(1.0, 1.0, 3.0)))
    animation.play("look")
    for i in range(120): await process_frame
    assert(absf(_tread_min_y(actor) - 0.152) < 0.002)
    assert(absf(actor.rotation.y) < deg_to_rad(10.0))
    presence.set_speaking(true)
    presence.set_emotion("curious")
    presence.perform_action("wave")
    for i in range(120): await process_frame
    assert(absf(_tread_min_y(actor) - 0.152) < 0.002)
    assert(absf(actor.rotation.z) < deg_to_rad(6.0))
    assert(animation.has_animation("idle"))
    print("PRESENCE_SMOKE_PASS tread_y=", _tread_min_y(actor), " actor_delta=", actor.position.distance_to(initial_position), " yaw=", rad_to_deg(actor.rotation.y), " roll=", rad_to_deg(actor.rotation.z))
    quit()

func _tread_min_y(actor: Node3D) -> float:
    var minimum := INF
    for name_value in ["MikoWheelLTreadMeshNode", "MikoWheelRTreadMeshNode"]:
        var wheel := actor.find_child(name_value, true, false) as MeshInstance3D
        for surface in range(wheel.mesh.get_surface_count()):
            var vertices: PackedVector3Array = wheel.mesh.surface_get_arrays(surface)[Mesh.ARRAY_VERTEX]
            for point in vertices:
                minimum = minf(minimum, (wheel.global_transform * point).y)
    return minimum
