from kinova_mount_smoke import authored_physics_status, mode_config, apply_explicit_zero_mass_link


def test_missing_authored_com_or_inertia_is_unknown_without_filling_values():
    inertials = {"/World/link": {"mass_kg": 1.0, "centerOfMass": None, "diagonalInertia": [1, 2, 3]}}
    status, reasons = authored_physics_status(inertials, [])
    assert status == "unknown"
    assert "centerOfMass" in reasons[0]
    assert inertials["/World/link"]["centerOfMass"] is None


def test_complete_authored_physics_is_declared():
    status, reasons = authored_physics_status({"/World/link": {
        "centerOfMass": [0, 0, 0], "diagonalInertia": [1, 2, 3], "principalAxes": [1, 0, 0, 0]}}, [])
    assert (status, reasons) == ("declared", [])


def test_zero_axes_does_not_silently_certify_complete_inertia():
    status, reasons = authored_physics_status({"/World/link": {
        "centerOfMass": [0, 0, 0], "diagonalInertia": [1, 2, 3], "principalAxes": [0, 0, 0, 0]}}, [])
    assert status == "unknown"
    assert reasons == ["missing or invalid principalAxes: /World/link"]


def test_modes_are_explicit_and_unscaled():
    zero = mode_config("zero-drive")
    constrained = mode_config("constrained-targets")
    assert zero["effort_limit_sim"] == zero["stiffness"] == zero["damping"] == 0.0
    assert constrained["target"] == "authored initial joint position"


def test_zero_mass_link_is_recorded_without_invalid_physx_override():
    class Prim:
        def IsValid(self): return True
        def HasAPI(self, _api): return True
    class Stage:
        def GetPrimAtPath(self, _path): return Prim()
    result = apply_explicit_zero_mass_link(Stage(), "/gen3/end_effector_link")
    assert result["applied"] is False
    assert result["source_mass_kg"] == 0.0
