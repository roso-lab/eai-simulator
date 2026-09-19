"""No-launch contract checks for the concrete City service probe."""
import json
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from types import ModuleType, SimpleNamespace

import pytest

from demo.city_benchmark import service_probe


def test_prepare_writes_frozen_bounded_cpu_contract(tmp_path):
    result = service_probe.prepare_probe(tmp_path, device="cpu", seed=17)
    stored = json.loads((tmp_path / "service_probe_prepare.json").read_text())
    assert stored == result
    assert stored["bounded"]["max_episode_s"] == 45.0
    assert stored["source_hashes"]
    assert str(service_probe.ROOT / "usd/scene/city/city_static.usd") in stored["source_hashes"]
    assert str(service_probe.ROOT / "usd/scene/city/city_scenario.json") in stored["source_hashes"]
    assert stored["scoreable"] is False
    assert stored["formal_episode_binding"] is False
    assert stored["geometry_scope"] == "runtime_selected_candidate_diagnostic"
    with pytest.raises(FileExistsError):
        service_probe.prepare_probe(tmp_path, device="cpu", seed=17)


def test_execute_rejects_changed_prepare_contract_without_launch(monkeypatch, tmp_path):
    service_probe.prepare_probe(tmp_path, device="cpu", seed=17)
    monkeypatch.setattr(service_probe, "_serial_lock", nullcontext)
    with pytest.raises(ValueError, match="match prepare"):
        service_probe.execute_probe(tmp_path, device="cuda:0", seed=17)


def test_jsonable_converts_live_envelopes():
    class Evidence:
        def as_dict(self):
            return {"kind": "runtime", "ids": ("one",)}

    assert service_probe._jsonable({"evidence": Evidence()}) == {
        "evidence": {"kind": "runtime", "ids": ["one"]}}


def test_assembly_contains_full_offset_person_envelope_with_margin():
    region = service_probe._assembly_region((-42., 63.5, .3), (.45, .31, .94), (.075, -.006, .99))
    assert region["min"][0] < -42.+.075-.45
    assert region["max"][0] > -42.+.075+.45
    assert region["query_min_z"] > .3
    assert region["max"][2] > .3+.99+.94
    assert region["min"][0] > -42.65  # neighbouring lamp stays outside


def test_probe_import_does_not_import_kit_or_physx():
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; import demo.city_benchmark.service_probe; "
         "assert 'omni.physx' not in sys.modules; assert 'omni.usd' not in sys.modules"],
        check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_execute_enters_session_before_importing_city_or_usd(monkeypatch, tmp_path):
    class StopAfterLifecycleCheck(RuntimeError):
        pass

    class Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    @contextmanager
    def open_session(config):
        assert config.kwargs["selection_data"] == service_probe._selection(17)
        assert "demo.city.main" not in sys.modules
        assert "demo.city.runtime" not in sys.modules
        assert "demo.city.usd_scene" not in sys.modules
        raise StopAfterLifecycleCheck
        yield

    fake_simulator = ModuleType("simulator")
    fake_simulator.SimulatorLaunchConfig = Config
    fake_simulator.open_simulator_session = open_session
    monkeypatch.setitem(sys.modules, "simulator", fake_simulator)
    for name in ("demo.city.main", "demo.city.runtime", "demo.city.usd_scene"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    with pytest.raises(StopAfterLifecycleCheck):
        service_probe._execute(tmp_path, {"seed": 17, "device": "cpu"},
                               run_id="episode", headless=True)


def test_failure_report_preserves_partial_trace_and_diagnostic_scope(tmp_path):
    report = {
        "status": "starting",
        "full_city_service": "pending_formal_episode_geometry",
        "trace": {"geometry": {"region": "measured"}, "services": {
            "traffic_hold": {"status": "readback_recorded"}}},
    }
    path = tmp_path / "report.json"
    with pytest.raises(RuntimeError, match="later phase failed"):
        with service_probe._service_report_guard(path, report):
            raise RuntimeError("later phase failed")
    stored = json.loads(path.read_text())
    assert stored["status"] == "blocked"
    assert stored["full_city_service"] == "pending_formal_episode_geometry"
    assert stored["trace"]["services"]["traffic_hold"]["status"] == "readback_recorded"


@pytest.mark.parametrize("issue", ["outside", "stale", "empty_interval"])
def test_native_role_cannot_be_asserted_without_current_pose_interval(issue):
    calls = []
    runner = SimpleNamespace(adapter=SimpleNamespace(now_s=lambda: 1.),
        record_role=lambda *args, **kwargs: calls.append((args, kwargs)))
    samples = [{"time_s": .98, "position_m": [-69., 49., .6]},
               {"time_s": 1., "position_m": [-69., 49., .6], "physics_step": 52}]
    if issue == "outside": samples[0]["position_m"][0] = -60.
    elif issue == "stale": samples[1]["time_s"] = .99
    else: samples[0]["time_s"] = 1.
    session = SimpleNamespace(_city_diagnostic_role_interval=samples)
    with pytest.raises(RuntimeError, match="role region|stale"):
        service_probe._role(runner, "incident_observer", session, {})
    assert calls == []


def test_divert_selection_keeps_rejections_and_covers_actual_turn():
    route = SimpleNamespace(sample=lambda *args, **kw: ((0, 0), 0.))
    city = SimpleNamespace(episode_id="e", elapsed=2., following_vehicle_pose=lambda _: (0., 0., .2),
        _traffic_state={"car": {"active_route": route, "base_route": route, "distance": 0.}})
    calls = []
    def read(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "measured", "clear": len(calls) == 2}
    chosen, half, attempts = service_probe._divert_candidates(city, "car", SimpleNamespace(read_route=read),
                                                            (2.4, 1.2, 1.), (0, 0, 1.))
    assert len(attempts) == 2 and not attempts[0]["readback"]["clear"]
    assert half[0] > 2.4 and half[1] > 1.2
    assert chosen[0][1] < 0
    assert all(row[1]["moving_entity_id"] == "car" for row in calls)


@pytest.mark.parametrize("angle", [-2.7, -.3, 0., .3, 2.7])
def test_turn_bound_contains_all_intermediate_headings(angle):
    import math
    half = (1.2, 2.4, 1.)
    bound = service_probe._rotation_sweep_half(half, angle)
    for i in range(1001):
        a = angle*i/1000
        assert half[0]*abs(math.cos(a))+half[1]*abs(math.sin(a)) <= bound[0]+1e-12
        assert half[0]*abs(math.sin(a))+half[1]*abs(math.cos(a)) <= bound[1]+1e-12
    assert bound[2] == half[2]
