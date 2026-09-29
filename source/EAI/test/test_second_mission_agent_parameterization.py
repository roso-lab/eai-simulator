"""Second-mission skills must run on whichever robot the dispatch names.

Every test swaps the executing robot for a same-type partner from the public
pool (or for another pool robot where the ruling makes the skill
instance-free) and asserts the skill receipt is unchanged: success stays
success and failure keeps the same cause. The kernel may key behavior on
machine type, pool capabilities, and geometry, never on an instance id.
These checks are offline: no Isaac, GPU, ROS, or network access.
"""

import math

from demo.benchmark.city_exit_task import build_city_exit_package
from demo.benchmark.factory_fire_task import build_factory_fire_package
from demo.benchmark.goal_house_episode import (
    HouseNativeGoalBackend,
    restaurant_pepper_stand,
    route_inflation_radius_m,
    second_mission_constraint_violation,
)
from demo.benchmark.house_delivery_task import build_house_delivery_package
from demo.benchmark.restaurant_service_task import build_restaurant_service_package
from demo.benchmark.second_mission_deployment import (
    build_second_mission_deployment,
    resolve_support_deploy,
)
from demo.benchmark.warehouse_inbound_task import build_warehouse_inbound_package


class _Pose:
    def __init__(self, xy, yaw=0.0):
        self.values = [xy[0], xy[1], 0.5, math.cos(yaw / 2.0), 0.0, 0.0,
                       math.sin(yaw / 2.0)]

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return list(self.values)


class _BodyState:
    def __init__(self, pose):
        self.pose = pose

    def __getitem__(self, index):
        assert index == (0, 1), index
        return self.pose


class _Articulations(dict):
    """Robot articulations addressable by runtime id, with pepper body parts."""

    def __missing__(self, key):
        row = type("Articulation", (), {})()
        row.data = type("Data", (), {
            "root_state_w": [_Pose((0.0, 0.0))],
            "body_state_w": _BodyState(_Pose((0.0, 0.0))),
        })()
        row.find_bodies = lambda pattern, preserve_order=True: ([1], ["base_link"])
        self[key] = row
        return row


def _session_stub(articulations=None, step_dt=0.05):
    class Env:
        def step(self, actions):
            del actions

    class Base:
        @staticmethod
        def set_command(*_args, **_kwargs):
            return None

    base = Base()
    base.step_dt = step_dt
    base.scene = type("Scene", (), {
        "articulations": articulations if articulations is not None else _Articulations(),
        "rigid_objects": {},
    })()

    class Session:
        device = "cpu"
        possible_agents = []
        env = Env()
        base_env = base

    return Session()


def _fake_follow_capture(captured):
    """Stand in for ``_follow_ground`` and record what the dispatch addressed."""

    def fake_follow(runtime_ids, goals, *, arrived, label, allow_reverse=True,
                    final_stop_m=0.35, refresh_goals=None, preset_paths=None,
                    final_gait=None, formation=False):
        del label, allow_reverse, final_stop_m, refresh_goals, final_gait, formation
        captured["runtime_ids"] = list(runtime_ids)
        captured["goals"] = {rid: tuple(goals[rid]) for rid in runtime_ids}
        captured["preset_paths"] = (
            None if preset_paths is None
            else {rid: [tuple(point) for point in path] for rid, path in preset_paths.items()})
        if preset_paths is not None:
            captured["arrived_at_rally"] = arrived({
                rid: [*preset_paths[rid][-1], 0.5] for rid in runtime_ids})
        return "failed", "navigation_target_not_reached:swapped:1.00m"

    return fake_follow


def _support_rally_presets(starts, sockets):
    from demo.benchmark.goal_house_episode import _RALLY_STANDOFF_M, _support_approach_paths

    return _support_approach_paths(
        dict(starts), sockets, [0.0, 0.0, 0.55], (0.45, 0.325),
        stand_m=_RALLY_STANDOFF_M)


def test_rally_preset_navigation_follows_the_dispatched_robots(monkeypatch):
    """A preset rally route records the dispatched robot's type, never crashes.

    Two same-type m20s run the real ``_follow_ground`` on exchanged preset
    route tables. Both dispatches succeed identically and each agent row
    reports the same machine type and route radius. Before the type lookup was
    hoisted this branch read unbound locals, so a paired support approach
    could only fail with a NameError receipt.
    """
    import demo.benchmark.goal_house_episode as gh

    package = build_warehouse_inbound_package()
    public = package["public_task"]
    plan = package["hidden_evaluator"]["witness_plans"][0]["plan"]
    deployment = build_second_mission_deployment(public, plan)
    starts = {"m20_1": (-1.3, -0.3), "m20_2": (-1.3, 0.3)}
    monkeypatch.setattr(
        gh, "_navigation_pose",
        lambda _session, runtime_id, _body: _Pose(starts[runtime_id]).tolist(),
    )
    sockets = {"m20_1": [0.0, -0.3, 0.55], "m20_2": [0.0, 0.3, 0.55]}
    presets = _support_rally_presets(starts, sockets)
    swapped = {"m20_2": presets["m20_1"], "m20_1": presets["m20_2"]}

    receipts = set()
    for table in (presets, swapped):
        backend = HouseNativeGoalBackend(public, session=_session_stub(), deployment=deployment)
        status, reason = backend._follow_ground(
            list(table), {rid: table[rid][-1] for rid in table},
            arrived=lambda _states: True, label="rally", preset_paths=table)
        rows = backend._last_navigation_diagnostics["agents"]
        receipts.add((status, reason))
        assert {rows[rid]["robot_type"] for rid in table} == {"m20"}
        assert len({rows[rid]["route_radius_m"] for rid in table}) == 1
        for rid, route in table.items():
            assert rows[rid]["path_length_m"] > 0.0
            assert rows[rid]["waypoint_count"] == len(route)
    assert receipts == {("succeeded", None)}


def test_occupancy_route_table_follows_the_dispatched_type(monkeypatch):
    """Same-type partners get identical route parameters; type changes them."""
    import demo.benchmark.goal_house_episode as gh

    package = build_warehouse_inbound_package()
    public = package["public_task"]
    plan = package["hidden_evaluator"]["witness_plans"][0]["plan"]
    deployment = build_second_mission_deployment(public, plan)
    origin = (-1.3, -0.3)
    goal = (-10.6, -11.75)
    captured = []

    def fake_waypoints(scene_key, start, goal_xy, radius):
        captured.append((scene_key, tuple(start), tuple(goal_xy), radius))
        return [tuple(start), tuple(goal_xy)]

    monkeypatch.setattr(gh, "occupancy_waypoints", fake_waypoints)
    monkeypatch.setattr(
        gh, "_navigation_pose", lambda _session, _rid, _body: _Pose(origin).tolist())

    queries = {}
    for agent_id, runtime_id in (("warehouse_m20_1", "m20_1"),
                                 ("warehouse_m20_2", "m20_2"),
                                 ("warehouse_carter_1", "carter_1")):
        backend = HouseNativeGoalBackend(public, session=_session_stub(), deployment=deployment)
        status, reason = backend._follow_ground(
            [runtime_id], {runtime_id: goal}, arrived=lambda _states: True,
            label=f"nav-{agent_id}")
        assert (status, reason) == ("succeeded", None)
        row = backend._last_navigation_diagnostics["agents"][runtime_id]
        queries[agent_id] = {"calls": list(captured), "robot_type": row["robot_type"],
                             "radius": row["route_radius_m"]}
        captured.clear()

    m20_rows = (queries["warehouse_m20_1"], queries["warehouse_m20_2"])
    assert all(row["robot_type"] == "m20" for row in m20_rows)
    assert m20_rows[0]["calls"] == m20_rows[1]["calls"]
    assert m20_rows[0]["radius"] == route_inflation_radius_m("m20")
    carter = queries["warehouse_carter_1"]
    assert carter["robot_type"] == "carter"
    assert carter["radius"] == route_inflation_radius_m("carter")
    assert carter["calls"] != m20_rows[0]["calls"]


def test_support_deploy_writes_whichever_same_type_carrier_is_dispatched():
    """The offline support stand is written onto the dispatched m20, only it."""
    import torch

    package = build_factory_fire_package()
    public = package["public_task"]
    plan = package["hidden_evaluator"]["witness_plans"][0]["plan"]
    deployment = build_second_mission_deployment(public, plan)
    deployment["support_deploy"] = resolve_support_deploy(public)

    class Root:
        @staticmethod
        def clone():
            return torch.zeros((1, 13))

    articulations = {}
    for runtime_id in ("m20_1", "m20_2", "carter_1"):
        row = type("Articulation", (), {})()
        row.data = type("Data", (), {"root_state_w": Root})()
        row.device = "cpu"
        row.written = []
        row.write_root_pose_to_sim = (
            lambda pose, env_ids=None, _row=row: _row.written.append(pose.tolist()))
        row.write_root_velocity_to_sim = (
            lambda vel, env_ids=None, _row=row: _row.written.append(vel.tolist()))
        articulations[runtime_id] = row

    class Artic(dict):
        def __missing__(self, key):
            return articulations[key]

    session = _session_stub(Artic())

    class Bodies:
        bindings = {"objects": {"factory_extinguisher_01": {"body_path": "/World/ext"}}}

        def __call__(self, _path):
            return {"position_m": [1.0, 2.0, 0.3], "rotation_wxyz": [1.0, 0.0, 0.0, 0.0]}

    stands = {}
    for agent_id, runtime_id in (("factory_m20_1", "m20_1"), ("factory_m20_2", "m20_2")):
        backend = HouseNativeGoalBackend(public, session=session, deployment=deployment)
        status, reason = backend._deploy_support_carrier(
            "factory_extinguisher_01", [agent_id], Bodies())
        assert status == "succeeded", reason
        assert backend._last_execution_diagnostics["runtime_ids"] == [runtime_id]
        stands[agent_id] = next(entry for entry in articulations[runtime_id].written
                                if len(entry[0]) == 7)[0]
    assert stands["factory_m20_1"] == stands["factory_m20_2"]
    assert articulations["carter_1"].written == []


def test_paired_support_rally_follows_the_socket_role_not_the_instance(monkeypatch):
    """Exchanging which m20 holds left/right keeps each role's rally in place."""
    import demo.benchmark.goal_house_episode as gh

    package = build_warehouse_inbound_package()
    public = package["public_task"]
    plan = package["hidden_evaluator"]["witness_plans"][0]["plan"]
    deployment = build_second_mission_deployment(public, plan)
    starts = {"m20_1": (-1.3, -0.3), "m20_2": (-1.3, 0.3)}
    monkeypatch.setattr(
        gh, "_root_state",
        lambda _session, runtime_id: _Pose(starts[runtime_id]).tolist())

    class Bodies:
        bindings = {"objects": {"order_crate_01": {"body_path": "/World/crate"}}}

        def __call__(self, _path):
            return {"position_m": [0.0, 0.0, 0.3], "rotation_wxyz": [1.0, 0.0, 0.0, 0.0]}

    to_runtime = {"warehouse_m20_1": "m20_1", "warehouse_m20_2": "m20_2"}
    runs = []
    for agents, points in (
        (("warehouse_m20_1", "warehouse_m20_2"),
         {"warehouse_m20_1": "support_left", "warehouse_m20_2": "support_right"}),
        # Same pairing with both the dispatch order and the socket roles exchanged.
        (("warehouse_m20_2", "warehouse_m20_1"),
         {"warehouse_m20_2": "support_left", "warehouse_m20_1": "support_right"}),
    ):
        captured = {}
        backend = HouseNativeGoalBackend(public, session=_session_stub(), deployment=deployment)
        backend._runtime_id = lambda agent_id: to_runtime[agent_id]
        backend._last_navigation_diagnostics = {"agents": {}}
        backend._follow_ground = _fake_follow_capture(captured)
        status, reason = backend._approach_object(
            "order_crate_01", list(agents), Bodies(), point_ids=points)
        runs.append({
            "status": status, "reason": reason,
            "arrived_at_rally": captured["arrived_at_rally"],
            "ends": {points[agent_id]: captured["preset_paths"][to_runtime[agent_id]][-1]
                     for agent_id in agents},
        })

    assert runs[0]["status"] == runs[1]["status"] == "failed"
    assert runs[0]["reason"].startswith("approach_object_not_reached:order_crate_01:")
    assert runs[0]["reason"].split(":")[:-1] == runs[1]["reason"].split(":")[:-1]
    assert runs[0]["arrived_at_rally"] is True
    assert runs[1]["arrived_at_rally"] is True
    assert runs[0]["ends"]["support_left"] == runs[1]["ends"]["support_left"]
    assert runs[0]["ends"]["support_right"] == runs[1]["ends"]["support_right"]


def _restaurant_pepper_twin():
    from copy import deepcopy

    package = build_restaurant_service_package()
    public = package["public_task"]
    plan = package["hidden_evaluator"]["witness_plans"][0]["plan"]
    deployment = build_second_mission_deployment(public, plan)
    twin_public = deepcopy(public)
    twin_row = deepcopy(next(row for row in twin_public["candidate_robot_pool"]
                             if row["id"] == "restaurant_pepper_1"))
    twin_row["id"] = "restaurant_pepper_2"
    twin_public["candidate_robot_pool"].append(twin_row)
    twin_deployment = deepcopy(deployment)
    twin_deployment["robot_instances"].append({
        "public_id": "restaurant_pepper_2", "runtime_id": "pepper_2", "type": "pepper",
        "command_interface": "base_velocity", "controller_cfg": None,
        "spawn_binding": {"deployment_directive": {
            "instance_id": "pepper_2", "position_m": [14.0, 21.0, 0.0],
            "rotation_wxyz": [1.0, 0.0, 0.0, 0.0]}},
    })
    return public, deployment, twin_public, twin_deployment


def test_restaurant_stand_and_record_skills_accept_any_pepper():
    """Pepper order/relay skills key on the pepper type, not one instance."""
    from copy import deepcopy

    from demo.benchmark.goal_house_episode import _region_geometry

    public, deployment, twin_public, twin_deployment = _restaurant_pepper_twin()
    types = {row["id"]: row["type"] for row in twin_public["candidate_robot_pool"]}
    stand = restaurant_pepper_stand(
        "RESTAURANT-SERVICE-01", "dining_table_a", ["restaurant_pepper_1"],
        robot_types=types)
    assert stand == restaurant_pepper_stand(
        "RESTAURANT-SERVICE-01", "dining_table_a", ["restaurant_pepper_2"],
        robot_types=types)

    plan = build_restaurant_service_package()["hidden_evaluator"]["witness_plans"][0]["plan"]
    table_nav = next(task for task in plan["tasks"]
                     if task["skill"] == "ground_navigation"
                     and task["arguments"]["region_id"] == "dining_table_a")
    center, _bounds = _region_geometry(deployment, "dining_table_a")
    # A pepper-type dispatch aims at the interaction stand; a different machine
    # type dispatched to the same region aims at the published region centre.
    for agent_id, pub, dep, expected in (
        ("restaurant_pepper_1", public, deployment, stand),
        ("restaurant_pepper_2", twin_public, twin_deployment, stand),
        ("restaurant_carter_1", public, deployment, tuple(float(v) for v in center[:2])),
    ):
        captured = {}
        backend = HouseNativeGoalBackend(pub, session=_session_stub(), deployment=dep)
        backend._follow_ground = _fake_follow_capture(captured)
        task = deepcopy(table_nav)
        task["assigned_agents"] = [agent_id]
        status, reason = backend._drive_to_region(task)
        assert status == "failed"
        assert reason.startswith("navigation_target_not_reached:")
        assert captured["goals"] == {captured["runtime_ids"][0]: tuple(expected)}

    relay_task = deepcopy(next(task for task in plan["tasks"] if task["skill"] == "relay"))
    measured = _Pose((stand[0], stand[1]))
    articulations = _Articulations()
    for runtime_id in ("pepper_1", "pepper_2", "carter_1"):
        articulations[runtime_id].data.root_state_w[0] = measured
        articulations[runtime_id].data.body_state_w = _BodyState(measured)
    session = _session_stub(articulations)
    receipts = set()
    for agent_id, pub, dep in (
        ("restaurant_pepper_1", public, deployment),
        ("restaurant_pepper_2", twin_public, twin_deployment),
        ("restaurant_carter_1", public, deployment),
    ):
        backend = HouseNativeGoalBackend(pub, session=session, deployment=dep)
        task = deepcopy(relay_task)
        task["assigned_agents"] = [agent_id]
        status, reason = backend._relay_from_region(task)
        position = backend._last_execution_diagnostics["relay"]["position_m"]
        receipts.add((status, reason, tuple(position)))
    assert receipts == {("succeeded", None, (stand[0], stand[1], 0.5))}


def test_constraint_reasons_key_on_capability_not_identity():
    """Assignment checks read the pool row, so a same-type swap changes nothing."""
    from copy import deepcopy

    fire = build_factory_fire_package()
    fire_public = fire["public_task"]
    fire_plan = fire["hidden_evaluator"]["witness_plans"][0]["plan"]
    pick = next(task for task in fire_plan["tasks"] if task["skill"] == "pick")

    def swap_agent(task, agent_id):
        swapped = deepcopy(task)
        swapped["assigned_agents"] = [agent_id]
        for binding in swapped["arguments"]["carrier_bindings"]:
            binding["agent_id"] = agent_id
        return swapped

    assert second_mission_constraint_violation(fire_public, pick) is None
    assert second_mission_constraint_violation(
        fire_public, swap_agent(pick, "factory_m20_2")) is None

    rejected = [second_mission_constraint_violation(
        fire_public, swap_agent(pick, agent))
        for agent in ("factory_carter_1", "factory_scout_1", "factory_iris_1")]
    assert all(reason and reason.startswith("constraint_violation:") for reason in rejected)
    shapes = {tuple(reason.split(":")[0:2] + reason.split(":")[3:]) for reason in rejected}
    assert shapes == {("constraint_violation", "capability", "pick")}
    unknown = second_mission_constraint_violation(
        fire_public, swap_agent(pick, "factory_m20_9"))
    assert unknown == "constraint_violation:unknown_agent:factory_m20_9"

    inbound = build_warehouse_inbound_package()
    inbound_public = inbound["public_task"]
    inbound_plan = inbound["hidden_evaluator"]["witness_plans"][0]["plan"]
    carry = next(task for task in inbound_plan["tasks"]
                 if task["skill"] == "cooperative_carry")
    assert second_mission_constraint_violation(inbound_public, carry) is None
    exchanged = deepcopy(carry)
    exchanged["assigned_agents"] = ["warehouse_m20_2", "warehouse_m20_1"]
    exchanged["arguments"]["carrier_bindings"] = [
        {"agent_id": "warehouse_m20_2", "point_id": "support_left"},
        {"agent_id": "warehouse_m20_1", "point_id": "support_right"},
    ]
    assert second_mission_constraint_violation(inbound_public, exchanged) is None

    delivery = build_house_delivery_package()
    delivery_public = delivery["public_task"]
    delivery_plan = delivery["hidden_evaluator"]["witness_plans"][1]["plan"]
    for task in delivery_plan["tasks"]:
        assert second_mission_constraint_violation(delivery_public, task) is None


class _MeasuredPeople:
    """Roster runtime: everyone walks toward the assembly when updated."""

    def __init__(self):
        self.poses = {
            "affected_person_00": (-42.30, 49.70),
            "affected_person_01": (-42.30, 50.30),
            "affected_person_02": (-41.70, 49.70),
            "affected_person_03": (-41.70, 50.30),
        }
        self.goals = {
            "affected_person_00": (-44.30, 45.70),
            "affected_person_01": (-44.30, 46.30),
            "affected_person_02": (-43.70, 45.70),
            "affected_person_03": (-43.70, 46.30),
        }

    def position(self, actor_id):
        return (*self.poses[actor_id], 0.30)

    def update(self, dt, *, actor_ids=None, **kwargs):
        del kwargs
        for actor_id in actor_ids:
            x, y = self.poses[actor_id]
            tx, ty = self.goals[actor_id]
            dx, dy = tx - x, ty - y
            distance = math.hypot(dx, dy)
            step = 1.2 * dt
            self.poses[actor_id] = (tx, ty) if distance <= step else (
                x + dx / distance * step, y + dy / distance * step)


def _city_regions():
    centres = {
        "dismissal_gathering": (-42.0, 50.0),
        "dismissal_assembly": (-44.0, 46.0),
    }
    return {"region_bindings": [
        {"id": key,
         "candidate_geometry": {"center_m": [value[0], value[1], 0.0],
                                "aabb_xy_m": [value[0] - 0.75, value[1] - 0.75,
                                              value[0] + 0.75, value[1] + 0.75]}}
        for key, value in centres.items()
    ]}


def test_city_exit_guide_and_observe_do_not_bind_the_dispatched_instance():
    """Either guide-capable robot produces the same guide/observe receipts."""
    from copy import deepcopy

    from demo.benchmark.goal_house_episode import _city_exit_observation

    package = build_city_exit_package()
    public = package["public_task"]
    guides = [row["id"] for row in public["candidate_robot_pool"]
              if "guide_people" in row["public_capabilities"]]
    assert set(guides) == {"go2_1", "carter_1"}
    witness_guides = []
    for witness in package["hidden_evaluator"]["witness_plans"]:
        guide = next(task for task in witness["plan"]["tasks"]
                     if task["skill"] == "guide_people")
        witness_guides.append(tuple(guide["assigned_agents"]))
    # The author witnesses themselves dispatch the guide role to different robots.
    assert len(set(witness_guides)) == len(witness_guides)
    assert {agents[0] for agents in witness_guides} == set(guides)

    guide_task = deepcopy(next(
        task for task in package["hidden_evaluator"]["witness_plans"][0]["plan"]["tasks"]
        if task["skill"] == "guide_people"))
    receipts = set()
    for agent in guides:
        people = _MeasuredPeople()
        deployment = _city_regions()
        deployment["city_exit_people"] = {
            "runtime": people, "person_ids": list(people.poses)}
        guide_task["assigned_agents"] = [agent]
        backend = HouseNativeGoalBackend(
            public, session=_session_stub(step_dt=0.1), deployment=deployment)
        status, reason = backend._skill_result(guide_task, None)
        diagnostics = backend._last_execution_diagnostics["guide_people"]
        positions = tuple(sorted((person, (xy[0], xy[1]))
                                 for person, xy in diagnostics["positions_m"].items()))
        receipts.add((status, reason, positions))
    # Same roster, same walk, same measured arrival for either dispatched robot.
    assert len(receipts) == 1
    assert receipts.pop()[:2] == ("succeeded", None)

    roster = {"arguments": {
        "region_id": "dismissal_gathering", "subject_ref": "dismissal_gathering",
        "observation_mode": "dismissal_roster"}}
    people = _MeasuredPeople()
    deployment = _city_regions()
    deployment["city_exit_people"] = {"runtime": people, "person_ids": list(people.poses)}
    by_robot = {
        agent: _city_exit_observation(roster, deployment, (-42.0, 50.0))
        for agent in guides
    }
    assert by_robot[guides[0]] == by_robot[guides[1]]
    assert by_robot[guides[0]][1] is None
