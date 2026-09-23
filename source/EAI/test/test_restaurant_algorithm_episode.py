import os
from pathlib import Path
from types import SimpleNamespace

import pytest


PRODUCTS = Path(os.environ.get(
    "RESTAURANT_PRODUCTS_ROOT", "tmp/restaurant_benchmark/products-candidate-v7"))


def executor_routes(module):
    """Plan-expansion fixture independent of downloaded occupancy products."""
    return {
        leg_id: {"route_id": leg_id, **definition, "navigation_window_s": 45.0}
        for leg_id, definition in module.AGENT_PHYSICAL_LEGS.items()
    }


def test_algorithm_pool_declares_heterogeneous_executable_members():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    assert m.ALGORITHM_POOL == (
        "restaurant_carter_1", "restaurant_carter_2", "restaurant_iris_1",
    )
    assert m.ALGORITHMS == ("teamweaver", "emos")
    # Both participants translate the identical mission contract. Relay is a
    # non-motion event-bus action and carries no invented mobility requirement.
    assert m.MISSION_REQUIREMENTS["readiness_relay"] == ("relay",)
    assert "readiness_relay" not in m.MISSION_TARGET_XY
    mobilities = {m.ROBOT_BINDINGS[public]["mobility"] for public in m.ALGORITHM_POOL}
    assert mobilities == {"ground", "aerial"}
    # Only the ground anchors have physical navigation legs; Iris is limited
    # to stationary relay from its hover-held overview anchor.
    assert {
        definition["agent"] for definition in m.AGENT_PHYSICAL_LEGS.values()
    } == {"carter_1", "carter_2"}
    # Public staging remains aerial+ground; adapter coverage must not rewrite it.
    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    staging = next(row for row in task["public_world"]["regions"]
                   if row["id"] == "restaurant_staging")
    assert set(staging["access_modes"]) == {"ground_navigation", "aerial_navigation"}


def test_algorithm_selection_spawns_pool_members_at_public_anchors():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.restaurant_benchmark import config

    selection = m._algorithm_selection()
    assert selection["scene_key"] == config.BENCHMARK_SCENE_KEY
    by_type = {}
    for robot in selection["robots"]:
        by_type.setdefault(robot["type"], []).append(robot)
    assert len(by_type["carter"]) == 2
    assert len(by_type["iris"]) == 1
    assert by_type["iris"][0]["controller"]["cfg"] == "PEGASUS_IRIS_POSITION_CFG"
    spawns = [tuple(robot["spawn_pose"]["position"]) for robot in selection["robots"]]
    assert (8.325, 7.675, 3.0) in spawns


def test_derived_public_task_filters_pool_and_spawns_together():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_contract import validate_goal_task_spec
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    report = validate_goal_task_spec(task)
    assert report["validated"] is True
    assert [row["id"] for row in task["candidate_robot_pool"]] == [
        "restaurant_carter_1", "restaurant_carter_2", "restaurant_iris_1",
    ]
    assert task["budgets"]["max_active_agents"] == 3
    assert set(task["public_world"]["robot_spawn_positions"]) == set(m.ALGORITHM_POOL)


def test_leg_matrix_resolves_unique_region_transitions_per_agent():
    from demo.benchmark import goal_restaurant_algorithm_episode as m

    products = PRODUCTS
    if not (products / "restaurant_benchmark_map.yaml").is_file():
        pytest.skip("downloaded Restaurant occupancy product is unavailable")
    routes = m._leg_routes(products)
    assert set(routes) == set(m.AGENT_PHYSICAL_LEGS)
    for native, expected_transitions in (
        ("carter_1", {("restaurant_staging", "kitchen_service"),
                      ("kitchen_service", "restaurant_staging"),
                      ("restaurant_staging", "dining_service"),
                      ("dining_service", "restaurant_staging")}),
        ("carter_2", {("restaurant_staging", "kitchen_service"),
                      ("kitchen_service", "restaurant_staging"),
                      ("restaurant_staging", "dining_service"),
                      ("dining_service", "restaurant_staging")}),
    ):
        transitions = {(route["from_region_id"], route["to_region_id"])
                       for route in routes.values() if route["agent"] == native}
        assert transitions == expected_transitions
        for transition in expected_transitions:
            leg_id, route = m._leg_for(native, *transition, routes)
            assert leg_id and route["navigation_window_s"] >= 45.0


def test_mission_expansion_accepts_ground_assignments_within_budget():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    routes = executor_routes(m)
    cases = (
        {"kitchen_inspection": "restaurant_carter_1",
         "dining_inspection": "restaurant_carter_2",
         "readiness_relay": "restaurant_carter_1"},
        {"kitchen_inspection": "restaurant_carter_1",
         "dining_inspection": "restaurant_carter_1",
         "readiness_relay": "restaurant_carter_2"},
    )
    for assignment in cases:
        plan = m._expand_mission_plan(
            task, assignment, relay_agent=assignment["readiness_relay"],
            routes=routes, plan_id="test")
        last = plan["tasks"][-1]["execution"]
        assert last["start_s"] + last["duration_s"] <= task["budgets"]["simulation_s"]
        # The plan's route ids live on the task's public route graph while
        # the backend resolves agent-specific physical legs.
        for row in plan["tasks"]:
            for route_id in row["arguments"].get("route_ids", []):
                assert any(entry["id"] == route_id
                           for entry in task["public_world"]["routes"])


def test_mission_expansion_rejects_aerial_ground_inspection_but_accepts_relay():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    routes = executor_routes(m)
    invalid = {"kitchen_inspection": "restaurant_iris_1",
               "dining_inspection": "restaurant_carter_2",
               "readiness_relay": "restaurant_carter_1"}
    with pytest.raises(ValueError, match="requires ground access"):
        m._expand_mission_plan(
            task, invalid, relay_agent=invalid["readiness_relay"],
            routes=routes, plan_id="reject")

    valid = {"kitchen_inspection": "restaurant_carter_1",
             "dining_inspection": "restaurant_carter_2",
             "readiness_relay": "restaurant_iris_1"}
    plan = m._expand_mission_plan(
        task, valid, relay_agent=valid["readiness_relay"],
        routes=routes, plan_id="aerial-relay")
    relay = plan["tasks"][-1]
    assert relay["skill"] == "relay"
    assert relay["required_capabilities"] == ["relay"]
    assert relay["assigned_agents"] == ["restaurant_iris_1"]
    assert relay["arguments"]["region_id"] == "restaurant_overview"

    ground_relay = m._expand_mission_plan(
        task,
        {"kitchen_inspection": "restaurant_carter_1",
         "dining_inspection": "restaurant_carter_2",
         "readiness_relay": "restaurant_carter_1"},
        relay_agent="restaurant_carter_1",
        routes=routes, plan_id="ground-relay")
    assert ground_relay["tasks"][-1]["arguments"]["region_id"] == "restaurant_staging"


def test_missing_native_driver_fails_closed_without_fake_success():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    routes = executor_routes(m)
    assignment = {"kitchen_inspection": "restaurant_carter_1",
                  "dining_inspection": "restaurant_carter_2",
                  "readiness_relay": "restaurant_iris_1"}
    plan = m._expand_mission_plan(
        task, assignment, relay_agent="restaurant_iris_1",
        routes=routes, plan_id="aerial-relay")
    relay = plan["tasks"][-1]
    backend = m.RestaurantMultiAgentGoalBackend(
        {}, routes, task["public_world"]["routes"], episode_id="episode")
    trusted = {"session": {
        "episode_id": "episode", "current_time_s": relay["execution"]["start_s"],
        "current_plan_id": plan["plan_id"],
        "tasks": {relay["task_id"]: {
            "status": "pending", "task": relay, "plan_id": plan["plan_id"],
            "dispatch_action_id": "dispatch-relay",
        }},
    }}
    with pytest.raises(RuntimeError, match="no driver"):
        backend.execute(relay, episode_id="episode", trusted_report=trusted)
    assert backend.evidence == []


def test_stationary_iris_relay_uses_native_clock_and_readiness_service():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    routes = executor_routes(m)
    assignment = {"kitchen_inspection": "restaurant_carter_1",
                  "dining_inspection": "restaurant_carter_2",
                  "readiness_relay": "restaurant_iris_1"}
    plan = m._expand_mission_plan(
        task, assignment, relay_agent="restaurant_iris_1",
        routes=routes, plan_id="aerial-relay")
    relay = plan["tasks"][-1]
    current = relay["execution"]["start_s"]

    class RelayDriver:
        def __init__(self):
            self.episode_time_s = current
            self.steps = 20

        def step_once(self):
            self.steps += 1
            self.episode_time_s += 0.02
            return {"simulation_step": self.steps, "time_s": self.episode_time_s,
                    "root_state_w": [8.325, 7.675, 3.0, 1.0, 0.0, 0.0, 0.0,
                                     0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}

        def wait_until(self, target):
            start = self.episode_time_s
            self.episode_time_s = target
            return {"kind": "native_window_hold", "start_s": start, "end_s": target}

    driver = RelayDriver()
    backend = m.RestaurantMultiAgentGoalBackend(
        {"iris_1": driver}, routes, task["public_world"]["routes"], episode_id="episode")
    backend.readiness.record_observation(
        region_id="kitchen_service", observation_id="kitchen", simulation_step=10,
        time_s=current - 2.0, producer_id="restaurant_carter_1", evidence_digest="k")
    backend.readiness.record_observation(
        region_id="dining_service", observation_id="dining", simulation_step=11,
        time_s=current - 1.0, producer_id="restaurant_carter_2", evidence_digest="d")
    trusted = {"session": {
        "episode_id": "episode", "current_time_s": current,
        "current_plan_id": plan["plan_id"],
        "tasks": {relay["task_id"]: {
            "status": "pending", "task": relay, "plan_id": plan["plan_id"],
            "dispatch_action_id": "dispatch-relay",
        }},
    }}
    result = backend.execute(relay, episode_id="episode", trusted_report=trusted)
    assert result["status"] == "succeeded"
    assert result["receipt"]["status"] == "succeeded"
    assert result["runtime_state"]["robot_regions"]["restaurant_iris_1"] == "restaurant_overview"
    evidence = backend.evidence[-1]
    assert evidence["producer_id"] == "restaurant_iris_1"
    assert evidence["native_agent_id"] == "iris_1"
    assert evidence["publication_stamp"]["root_state_w"][:3] == [8.325, 7.675, 3.0]
    assert (evidence["publication_stamp"]["simulation_step"]
            < evidence["receipt_stamp"]["simulation_step"])
    assert m._verify_archived_relay_pose(evidence, relay) == "restaurant_overview"
    outside = {**evidence,
               "publication_stamp": {**evidence["publication_stamp"],
                                     "root_state_w": [0.0] * 13}}
    with pytest.raises(ValueError, match="outside its bound producer region"):
        m._verify_archived_relay_pose(outside, relay)


def test_stationary_relay_driver_steps_real_clock_with_idle_hold_commands():
    from demo.benchmark import goal_restaurant_algorithm_episode as m

    class Tensor:
        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return [8.325, 7.675, 3.0, 1.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    class Sim:
        current_time_step_index = 100

        def get_physics_dt(self):
            return 0.005

    sim = Sim()

    class Env:
        def step(self, commands):
            assert commands == {"iris_1": "hover", "carter_1": "hold"}
            sim.current_time_step_index += 4

    class Holds:
        def __init__(self):
            self.exclusions = []

        def commands(self, exclude):
            self.exclusions.append(exclude)
            return {"iris_1": "hover", "carter_1": "hold"}

    articulation = SimpleNamespace(
        data=SimpleNamespace(root_state_w=[Tensor()]))
    session = SimpleNamespace(
        env=Env(),
        base_env=SimpleNamespace(
            step_dt=0.02, sim=sim,
            scene=SimpleNamespace(articulations={"iris_1": articulation})))
    holds = Holds()
    driver = m.SharedClockRelayDriver(
        session, "iris_1", episode_step_origin=100, idle_holds=holds)

    first = driver.step_once()
    window = driver.wait_until(0.06)
    assert first["simulation_step"] == 104
    assert first["time_s"] == pytest.approx(0.02)
    assert window["end"]["simulation_step"] == 112
    assert driver.episode_time_s == pytest.approx(0.06)
    assert holds.exclusions == [None, None, None]


def test_request_contract_fails_closed_on_incomplete_requests():
    from demo.benchmark import goal_restaurant_algorithm_episode as m

    request = {
        "schema": "eai.restaurant.formal_goal_algorithm_episode_request",
        "version": 1, "run_id": "0" * 32, "episode_id": "restaurant-algo:" + "0" * 32,
        "algorithm": "teamweaver",
    }
    with pytest.raises(ValueError):
        m._validate_request(request)


def test_real_allocators_assign_iris_relay_and_reject_aerial_inspection():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    routes = executor_routes(m)

    emos = m.RestaurantEmosParticipant(task, routes)
    emos_assignment = emos._allocate()
    assert set(emos_assignment) == set(m.MISSION_REQUIREMENTS)
    assert emos_assignment["readiness_relay"] == "restaurant_iris_1"
    assert emos_assignment["kitchen_inspection"] != "restaurant_iris_1"
    assert emos_assignment["dining_inspection"] != "restaurant_iris_1"
    emos_plan = m._expand_mission_plan(
        task, emos_assignment, relay_agent=emos_assignment["readiness_relay"],
        routes=routes, plan_id="emos-allocate")
    assert emos_plan["tasks"][-1]["arguments"]["region_id"] == "restaurant_overview"
    assert emos_plan["tasks"][-1]["assigned_agents"] == ["restaurant_iris_1"]

    teamweaver = m.RestaurantTeamWeaverParticipant(task, routes)
    tw_assignment = teamweaver._allocate()
    assert set(tw_assignment) == set(m.MISSION_REQUIREMENTS)
    assert tw_assignment["readiness_relay"] == "restaurant_iris_1"
    assert tw_assignment["kitchen_inspection"] != "restaurant_iris_1"
    assert tw_assignment["dining_inspection"] != "restaurant_iris_1"
    tw_plan = m._expand_mission_plan(
        task, tw_assignment, relay_agent=tw_assignment["readiness_relay"],
        routes=routes, plan_id="teamweaver-allocate")
    assert tw_plan["tasks"][-1]["arguments"]["region_id"] == "restaurant_overview"

    invalid = {**emos_assignment, "kitchen_inspection": "restaurant_iris_1"}
    with pytest.raises(ValueError, match="requires ground access"):
        m._expand_mission_plan(
            task, invalid, relay_agent=invalid["readiness_relay"],
            routes=routes, plan_id="reject-iris-inspect")


def test_carter_relay_pose_accepts_both_anchors_and_rejects_beyond_tolerance():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.restaurant_benchmark import config, navigation_probe

    tolerance = navigation_probe.FINAL_TOLERANCE_M
    assert tolerance == pytest.approx(0.25)
    for public_id, native in (
        ("restaurant_carter_1", "carter_1"),
        ("restaurant_carter_2", "carter_2"),
    ):
        spawn = list(config.BENCHMARK_ROBOT_SPAWN_XYZ_M[native])
        task = {
            "skill": "relay",
            "assigned_agents": [public_id],
            "arguments": {"region_id": "restaurant_staging"},
        }
        for offset in (0.0, tolerance):
            root = list(spawn) + [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            root[0] = spawn[0] + offset
            row = {
                "producer_id": public_id,
                "native_agent_id": native,
                "publication_stamp": {"root_state_w": list(root)},
                "receipt_stamp": {"root_state_w": list(root)},
            }
            assert m._verify_archived_relay_pose(row, task) == "restaurant_staging"
        outside = list(spawn) + [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        outside[0] = spawn[0] + tolerance + 1e-6
        bad = {
            "producer_id": public_id,
            "native_agent_id": native,
            "publication_stamp": {"root_state_w": list(outside)},
            "receipt_stamp": {"root_state_w": list(outside)},
        }
        with pytest.raises(ValueError, match="outside its bound producer region"):
            m._verify_archived_relay_pose(bad, task)


def _bound_relay_archive_pair(*, publication_step: int, receipt_step: int,
                              service_publication_step: int | None = None,
                              service_receipt_step: int | None = None):
    """Build a matching native/service relay pair for binding checks."""
    from demo.restaurant_benchmark.service_backend import SCHEMA

    pub_service_step = (publication_step if service_publication_step is None
                        else service_publication_step)
    rec_service_step = (receipt_step if service_receipt_step is None
                        else service_receipt_step)
    publication = {
        "schema": SCHEMA,
        "episode_id": "episode",
        "kind": "relay_publication",
        "message_id": "episode:readiness-relay",
        "observation_refs": ["kitchen", "dining"],
        "simulation_step": pub_service_step,
        "time_s": float(pub_service_step) * 0.02,
    }
    receipt = {
        "schema": SCHEMA,
        "episode_id": "episode",
        "kind": "readiness_receipt",
        "receipt_id": "episode:readiness-receipt",
        "observation_refs": ["dining", "kitchen"],
        "simulation_step": rec_service_step,
        "time_s": float(rec_service_step) * 0.02,
        "receipt_digest": "placeholder",
    }
    # Digest is recomputed by evaluate_readiness_receipt; for binding we only
    # require native/service equality, so keep identical bodies.
    native = {
        "kind": "relay_and_receipt",
        "producer_id": "restaurant_iris_1",
        "native_agent_id": "iris_1",
        "publication_stamp": {
            "simulation_step": publication_step,
            "time_s": float(publication_step) * 0.02,
            "root_state_w": [8.325, 7.675, 3.0] + [0.0] * 10,
        },
        "receipt_stamp": {
            "simulation_step": receipt_step,
            "time_s": float(receipt_step) * 0.02,
            "root_state_w": [8.325, 7.675, 3.0] + [0.0] * 10,
        },
        "relay_publication": dict(publication),
        "business_receipt": dict(receipt),
    }
    # Force the native embedded copies to use the native stamp steps when the
    # service archive is intentionally divergent.
    native["relay_publication"] = {
        **publication,
        "simulation_step": publication_step,
        "time_s": float(publication_step) * 0.02,
    }
    native["business_receipt"] = {
        **receipt,
        "simulation_step": receipt_step,
        "time_s": float(receipt_step) * 0.02,
    }
    service = {
        "relay_publications": [publication],
        "receipt": receipt,
    }
    return native, service


def test_relay_service_binding_rejects_divergent_publication_steps():
    from demo.benchmark import goal_restaurant_algorithm_episode as m

    # Reviewer minimal repro: native publication step=20, service step=30.
    native, service = _bound_relay_archive_pair(
        publication_step=20, receipt_step=21,
        service_publication_step=30, service_receipt_step=31)
    with pytest.raises(ValueError, match="relay_publication diverges|publication step/time"):
        m._verify_archived_relay_service_binding(native, service)

    matched_native, matched_service = _bound_relay_archive_pair(
        publication_step=20, receipt_step=21)
    m._verify_archived_relay_service_binding(matched_native, matched_service)

    tampered = {
        **matched_service,
        "receipt": {**matched_service["receipt"], "simulation_step": 99},
    }
    with pytest.raises(ValueError, match="business_receipt diverges|receipt step/time"):
        m._verify_archived_relay_service_binding(matched_native, tampered)


def test_relay_service_binding_rejects_string_and_bool_stamp_tampering():
    from demo.benchmark import goal_restaurant_algorithm_episode as m

    native, service = _bound_relay_archive_pair(publication_step=20, receipt_step=21)
    string_step = {
        **native,
        "publication_stamp": {
            **native["publication_stamp"],
            "simulation_step": "20",
        },
    }
    with pytest.raises(ValueError, match="non-bool int"):
        m._verify_archived_relay_service_binding(string_step, service)

    bool_step = {
        **native,
        "receipt_stamp": {
            **native["receipt_stamp"],
            "simulation_step": True,
        },
    }
    with pytest.raises(ValueError, match="non-bool int"):
        m._verify_archived_relay_service_binding(bool_step, service)

    string_time = {
        **native,
        "publication_stamp": {
            **native["publication_stamp"],
            "time_s": "0.4",
        },
    }
    with pytest.raises(ValueError, match="non-bool finite numeric"):
        m._verify_archived_relay_service_binding(string_time, service)

    bool_time = {
        **native,
        "receipt_stamp": {
            **native["receipt_stamp"],
            "time_s": False,
        },
    }
    with pytest.raises(ValueError, match="non-bool finite numeric"):
        m._verify_archived_relay_service_binding(bool_time, service)

    for bad_time in (float("nan"), float("inf"), float("-inf")):
        nan_time = {
            **native,
            "publication_stamp": {
                **native["publication_stamp"],
                "time_s": bad_time,
            },
        }
        with pytest.raises(ValueError, match="finite|non-bool finite numeric"):
            m._verify_archived_relay_service_binding(nan_time, service)

    # Both native embedded record and service archive forged to the same illegal
    # type still fail closed — equality alone is not sufficient.
    both_bad_native = {
        **native,
        "relay_publication": {
            **native["relay_publication"],
            "simulation_step": "20",
        },
        "publication_stamp": {
            **native["publication_stamp"],
            "simulation_step": "20",
        },
    }
    both_bad_service = {
        **service,
        "relay_publications": [{
            **service["relay_publications"][0],
            "simulation_step": "20",
        }],
    }
    with pytest.raises(ValueError, match="non-bool int"):
        m._verify_archived_relay_service_binding(both_bad_native, both_bad_service)


def test_mission_order_is_stable_catalogue_not_a_set():
    from demo.benchmark import goal_restaurant_algorithm_episode as m

    assert m.MISSION_ORDER == (
        "kitchen_inspection", "dining_inspection", "readiness_relay",
    )
    assert tuple(m.MISSION_REQUIREMENTS) == m.MISSION_ORDER


def test_emos_and_teamweaver_allocators_are_hash_seed_stable_for_seeds_0_to_9():
    """Reproduce Sol P1-A: PYTHONHASHSEED must not reshuffle restaurant allocation."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    script = r"""
import json
import os
import sys
sys.path[:0] = [
    os.environ["REPO_ROOT"],
    os.path.join(os.environ["REPO_ROOT"], "algorithm"),
    os.path.join(os.environ["REPO_ROOT"], "source/EAI"),
    os.path.join(os.environ["REPO_ROOT"], "source/EAI_assets"),
    os.path.join(os.environ["REPO_ROOT"], "source/EAI_hmrs"),
]
from demo.benchmark import goal_restaurant_algorithm_episode as m
from demo.benchmark.goal_driven_tasks import build_goal_task_package
task = m._algorithm_public_task(build_goal_task_package("restaurant"))
routes = {
    leg_id: {"route_id": leg_id, **definition, "navigation_window_s": 45.0}
    for leg_id, definition in m.AGENT_PHYSICAL_LEGS.items()
}
out = {}
for name, cls in (
    ("teamweaver", m.RestaurantTeamWeaverParticipant),
    ("emos", m.RestaurantEmosParticipant),
):
    participant = cls(task, routes)
    participant.initialize({}, {})
    assignment = participant._allocate()
    assert set(assignment) == set(m.MISSION_ORDER)
    assert len(set(assignment.values())) == len(assignment)
    caps = {row["id"]: set(row["public_capabilities"]) for row in task["candidate_robot_pool"]}
    for mission, agent in assignment.items():
        assert set(m.MISSION_REQUIREMENTS[mission]) <= caps[agent]
        if mission != "readiness_relay":
            assert m.ROBOT_BINDINGS[agent]["mobility"] == "ground"
    # Expand must accept the assignment (no aerial inspection).
    m._expand_mission_plan(
        task, assignment, relay_agent=assignment["readiness_relay"],
        routes=routes, plan_id=f"hash-{name}")
    out[name] = dict(assignment)
print(json.dumps(out, sort_keys=True))
"""
    results = {}
    for seed in range(10):
        env = {
            **os.environ,
            "PYTHONHASHSEED": str(seed),
            "PYTHONDONTWRITEBYTECODE": "1",
            "REPO_ROOT": str(root),
        }
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"hash-seed {seed} failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        results[seed] = json.loads(proc.stdout.strip().splitlines()[-1])

    emos_assignments = {seed: results[seed]["emos"] for seed in range(10)}
    tw_assignments = {seed: results[seed]["teamweaver"] for seed in range(10)}
    assert len({json.dumps(v, sort_keys=True) for v in emos_assignments.values()}) == 1
    assert len({json.dumps(v, sort_keys=True) for v in tw_assignments.values()}) == 1
    # Capability + uniqueness + MISSION_ORDER stably selects Iris for relay
    # without hard-coding Iris in the policy.
    assert emos_assignments[0]["readiness_relay"] == "restaurant_iris_1"
    assert set(emos_assignments[0].values()) == {
        "restaurant_carter_1", "restaurant_carter_2", "restaurant_iris_1",
    }


def test_emos_allocates_with_labeled_public_spawn_anchors_not_origin():
    """Focused EMOS path injects public_spawn_anchor poses (not PhysX zeros)."""
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package
    from demo.restaurant_benchmark import config as restaurant_config

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    routes = executor_routes(m)
    emos = m.RestaurantEmosParticipant(task, routes)
    assignment = emos._allocate()

    pos_events = [row for row in emos.trace if row.get("event") == "emos_robot_positions"]
    assert len(pos_events) == 1
    event = pos_events[0]
    assert event["position_source"] == m.POSITION_SOURCE_PUBLIC_SPAWN_ANCHOR
    positions = event["robot_positions"]
    assert set(positions) == set(m.ALGORITHM_POOL)
    for public_id, xyz in positions.items():
        assert xyz != [0.0, 0.0, 0.0]
        expected = list(m.restaurant_public_spawn_xyz_m(public_id))
        assert xyz == pytest.approx(expected)
    iris = positions["restaurant_iris_1"]
    assert iris == pytest.approx(list(restaurant_config.BENCHMARK_ROBOT_SPAWN_XYZ_M["iris_1"]))
    assert iris[:2] == pytest.approx([8.325, 7.675])
    carter_1 = positions["restaurant_carter_1"]
    carter_2 = positions["restaurant_carter_2"]
    assert carter_1[:2] == pytest.approx(list(m.ROBOT_BINDINGS["restaurant_carter_1"]["spawn_xy_m"]))
    assert carter_2[:2] == pytest.approx(list(m.ROBOT_BINDINGS["restaurant_carter_2"]["spawn_xy_m"]))
    assert assignment["readiness_relay"] == "restaurant_iris_1"
    assert assignment["kitchen_inspection"] != "restaurant_iris_1"
    assert assignment["dining_inspection"] != "restaurant_iris_1"


def test_emos_native_root_state_callback_takes_priority_over_public_spawn():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    routes = executor_routes(m)
    native = {
        "restaurant_carter_1": (101.0, 102.0, 0.2),
        "restaurant_carter_2": (103.0, 104.0, 0.2),
        "restaurant_iris_1": (105.0, 106.0, 3.0),
    }
    calls = []

    def _native(name):
        calls.append(name)
        return native[name]

    emos = m.RestaurantEmosParticipant(
        task, routes, get_robot_pos_fn=_native,
        position_source=m.POSITION_SOURCE_NATIVE_ROOT_STATE)
    assignment = emos._allocate()
    pos_events = [row for row in emos.trace if row.get("event") == "emos_robot_positions"]
    assert pos_events[0]["position_source"] == m.POSITION_SOURCE_NATIVE_ROOT_STATE
    assert pos_events[0]["robot_positions"] == {
        name: list(xyz) for name, xyz in native.items()
    }
    assert set(calls) == set(native)
    assert assignment["readiness_relay"] == "restaurant_iris_1"
    assert set(assignment.values()) == set(m.ALGORITHM_POOL)


def test_formal_native_root_state_provider_maps_public_ids_and_archives_step():
    from demo.benchmark import goal_restaurant_algorithm_episode as m

    class Tensor:
        def __init__(self, values):
            self.values = values

        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return list(self.values)

    articulations = {
        native: SimpleNamespace(data=SimpleNamespace(root_state_w=[Tensor(
            [index + 1.0, index + 2.0, index + 3.0, 1.0] + [0.0] * 9)]))
        for index, native in enumerate(("carter_1", "carter_2", "iris_1"))
    }
    base_env = SimpleNamespace(
        scene=SimpleNamespace(articulations=articulations),
        sim=SimpleNamespace(current_time_step_index=321),
    )
    provider = m.RestaurantNativeRootStateProvider(base_env)
    assert provider("restaurant_carter_1") == pytest.approx((1.0, 2.0, 3.0))
    assert provider("restaurant_carter_2") == pytest.approx((2.0, 3.0, 4.0))
    assert provider("restaurant_iris_1") == pytest.approx((3.0, 4.0, 5.0))
    assert provider.metadata() == {
        "position_source": m.POSITION_SOURCE_NATIVE_ROOT_STATE,
        "capture": "settled_articulation_root_state_w",
        "physics_step": 321,
    }

    missing = SimpleNamespace(
        scene=SimpleNamespace(articulations={"carter_1": articulations["carter_1"]}),
        sim=SimpleNamespace(current_time_step_index=321),
    )
    with pytest.raises(RuntimeError, match="missing articulations"):
        m.RestaurantNativeRootStateProvider(missing)


def test_formal_native_root_state_provider_rejects_nonfinite_or_missing_step():
    from demo.benchmark import goal_restaurant_algorithm_episode as m

    class Tensor:
        def __init__(self, values):
            self.values = values

        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return list(self.values)

    bad = [Tensor([1.0, 2.0, 3.0, 1.0] + [0.0] * 8 + [float("nan")])]
    base_env = SimpleNamespace(
        scene=SimpleNamespace(articulations={
            native: SimpleNamespace(data=SimpleNamespace(root_state_w=bad))
            for native in ("carter_1", "carter_2", "iris_1")}),
        sim=SimpleNamespace(current_time_step_index=42),
    )
    provider = m.RestaurantNativeRootStateProvider(base_env)
    with pytest.raises(RuntimeError, match="root state is invalid"):
        provider("restaurant_carter_1")

    class BoolStep:
        current_time_step_index = True

    good_state = [Tensor([1.0, 2.0, 3.0, 1.0] + [0.0] * 9)]
    no_step = SimpleNamespace(
        scene=SimpleNamespace(articulations={
            native: SimpleNamespace(data=SimpleNamespace(root_state_w=good_state))
            for native in ("carter_1", "carter_2", "iris_1")}),
        sim=BoolStep(),
    )
    with pytest.raises(RuntimeError, match="integer physics step"):
        m.RestaurantNativeRootStateProvider(no_step)


def test_emos_rejects_missing_position_provider_without_inventing_zeros():
    from algorithm.emos.engine import RobotPositionUnavailableError
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    routes = executor_routes(m)

    def _missing(_name):
        raise RobotPositionUnavailableError("position_unavailable")

    emos = m.RestaurantEmosParticipant(
        task, routes, get_robot_pos_fn=_missing,
        position_source=m.POSITION_SOURCE_NATIVE_ROOT_STATE)
    with pytest.raises(RobotPositionUnavailableError, match="position_unavailable"):
        emos._allocate()

    orphan = m.RestaurantEmosParticipant(
        task, routes, position_source="native_root_state")
    with pytest.raises(RuntimeError, match="requires an explicit get_robot_pos_fn"):
        orphan._allocate()


def test_emos_build_from_agent_specs_strict_env_none_fails_without_callback():
    from algorithm.emos.engine import EMOSDiscussionManager, RobotPositionUnavailableError
    from algorithm.emos.types import (
        EMOSRobotAgentSpec,
        EMOSScenarioConfig,
        PositionRule,
        SubtaskSpec,
    )

    scenario = EMOSScenarioConfig(
        scenario_id="strict", scene_title="S", anchor_label="a",
        task_description="t",
        subtasks={"red": SubtaskSpec("red", "red", "r", ["nav"])},
        position_rules={"red": PositionRule(kind="fixed", xy=(1.0, 2.0))},
        robot_profiles={}, preferred_fallback={},
        yellow_subtask_id="", green_subtask_id="")
    specs = {"r1": EMOSRobotAgentSpec(agent_name="r1", robot_type="ground",
                                      capabilities=["nav"])}
    manager = EMOSDiscussionManager.build_from_agent_specs(
        None, specs, scenario, use_env_resume=False,
        strict_robot_positions=True)
    with pytest.raises(RobotPositionUnavailableError, match="no base_env scene"):
        manager._get_pos("r1")
