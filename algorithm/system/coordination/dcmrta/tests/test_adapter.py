from types import SimpleNamespace

from algorithm.system.coordination.dcmrta.adapter import build_native_input_spec


def test_dcmrta_native_spec_marks_fire_rescue_domain_mismatch():
    spec = build_native_input_spec(
        {"m20_1": (0.0, 0.0, 0.0), "m20_2": (1.0, 0.0, 0.0), "scout_1": (2.0, 0.0, 0.0), "carter_1": (3.0, 0.0, 0.0)},
        ("red", "green", "blue", "yellow"),
    )
    assert spec.padded_agents == 10
    assert spec.padded_tasks == 20
    assert spec.decision_status == "cross_domain_mismatch"
