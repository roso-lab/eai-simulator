# Native BT adapter evidence — 2026-10-28

**Executed: original ROS1 ParallelAuctionManager, Parallel and MemorySequence + EAI input/output leaves.**
The original linear protocol suite passed **14/14: 10 contract tests and 4 real ROS runtime tests**.
The subsequent DAG suite and same-scene two-robot acceptance are recorded separately below.
Protocol tests establish native auction/BT integration; physical acceptance requires the separate Isaac receipts.

The original ten evidence files now reside at
`/home/airs/hetrocoord/records/algorithm_adapters/bt_capabilities/ros_protocol_20261028`.
Migration preserved every file's SHA-256. Historical command/output paths inside
the original run receipt and logs are retained as recorded; they identify the
location used when the tests ran, not the current artifact location.
The separate Isaac follow-proxy run and its report reside at
`/home/airs/hetrocoord/records/algorithm_adapters/bt_capabilities/native_20261028/REPORT.md`.

## Integration entry points

```python
from algorithm.system.coordination.bt_capabilities.native_adapter import (
    NativeBTCapabilitiesAdapter, NativeBTFeedbackExecutor,
)

provider = NativeBTCapabilitiesAdapter(
    source_root="/tmp/heterocoord-algorithms-20261028/bt-capabilities",
    endpoint="http://172.17.0.2:18765",  # current isolated container; inspect after restart
)
# provider(task: EaiTaskPackage, robots: Sequence[RobotState]) -> AlgorithmDecision
# executor = NativeBTFeedbackExecutor(provider, actual_eai_executor,
#                                     success_predicate=explicit_terminal_success)
# TaskRunner(executor=executor).run(TaskRunRequest(task), provider)
```

Constructor defaults: source root from `HETEROCOORD_BT_SOURCE_ROOT`, otherwise
`/home/airs/.cache/heterocoord/upstream/bt-capabilities`; endpoint from
`HETEROCOORD_BT_NATIVE_ENDPOINT` (required). No fallback implementation exists.
The HTTP client runs without ROS dependencies. The isolated endpoint imports real
`rospy`, original generated ROS messages, and original upstream Python modules.

Each task requires `metadata={"bt_native": {"bids": {"robot_a": 9.0, "robot_b": 2.0}}}`.
Costs are supplied public input; lower cost wins **inside upstream code**. Missing,
negative, nonnumeric or nonfinite costs fail closed. Inactive/incapable robots do
not bid. No default zero costs, learned weights, synthetic rospy modules, local
winner selection, or legacy `CapabilityMission` execution are used.

`replan(task, robots, state=None)` reads the native tree's current frontier after
feedback. It does not invent a replanner. A changed roster/mission is rejected;
no `initialize(H_r,o_r)` or `update_observation` semantics are invented.
For `run_orchestrated`, wrap the executor with `NativeBTFeedbackExecutor(provider, executor)`
and pass `replan=provider.replan`. The wrapper preserves actual `ExecutionUpdate`
records, checks their identities and terminal outcomes, and submits a whole batch
atomically. Single feedback remains available through
`provider.feedback(feedback, succeeded=<explicit bool>)`.
`provider.close()` releases the mission and its EAI slot reservations.

## Recorded runtime

- Upstream HEAD: `5438f6e8d7599e39ec12104c0a9f0a8f1543838d`.
- Read-only original checkout: `/tmp/heterocoord-algorithms-20261028/bt-capabilities`.
- Shared `verify_source` passed: 335 source/config files;
  SHA-256 `b130e4665a99f40475df3950cad1a5b5c1ec29722e51169db792e8fdd4a3d621`.
- Original message package built with catkin; no upstream source edits.
- Ubuntu 22.04 container `heterocoord-bt-native-20261028`, isolated ROS master;
  ROS1 rospy/rosmaster/roslaunch `1.15.14+ds-2build2`, catkin `0.8.10-7`.
- Python `3.10.12`; container venv
  `/home/airs/.cache/heterocoord/envs/bt-capabilities` uses container ROS system
  packages. Host ROS Humble and existing services were not changed.
- One exact upstream `assignment_manager_node.py` auctioneer process and two
  independent robot processes, each importing original `ParallelAuctionManager`.
  Actual ANNOUNCEMENT/BID/CLOSE/RESULT ROS messages and native service responses
  are retained in the evidence JSON.
- The recorded endpoint was `http://172.17.0.2:18765`; no host port was published.
  This protocol suite used `robot_a/robot_b`. The later Isaac run reconfigured
  that endpoint for `ur5_1`; use a separate two-robot endpoint for protocol tests.

| Case | Native result | EAI/TaskRunner result |
|---|---|---|
| One task, explicit bids 9 and 2 | robot_b; 5 auction messages; BT SUCCEEDED | one protocol acknowledgement; physical verdict N/A |
| Two-task chain, costs then 1 and 8 | robot_b then robot_a; 10 messages; both native leaves SUCCEEDED | both dependency states completed; physical verdict N/A |
| Failed first execution | BT FAILED; second leaf IDLE | wrong identity and duplicate feedback rejected |
| Both robots inactive | native auction reports no valid bids; BT FAILED | no assignments; task remains unassigned |

The execution fixture explicitly returns `protocol_ack`, not simulated sensor
or contact evidence. `simulation_evidence` and `physical_evidence` remain
`not_evaluated`. Both successful TaskRunner runs report
`status=N/A, reason=physical_evidence_required`.

## Scope and remaining integration

The adapter now accepts acyclic task graphs, including forks/joins and arbitrary
input order. Linear chains use original `MemorySequence`; other DAGs use original
`Parallel`, with one original `MemorySequence` per task. An EAI prerequisite leaf
observes predecessor execution feedback, and an EAI action leaf requests a native
auction then waits for real executor feedback. Neither leaf ranks bids or rewrites
the upstream tree algorithms. A busy public robot slot causes waiting; no eligible
bids still reach the original auction and its failure path.

There is one EAI slot per robot and one active mission per endpoint. Failed trees
do not dispatch cancelled sibling assignments; a host must cancel any external
in-flight work and close the mission. Terminal state polling does not restart the
tree. Invalid feedback batches change no leaf or slot. Client receipts bind the
mission, canonical task payload, tree type, native source hashes and eligible IDs.

Automatic retry/reauction after terminal failure, full `MissionControl` capability
repository/utility generation, remote capability action servers, arbitrary skill
implementations and coalition execution remain outside this acceptance scope.
General DAG input support does not establish every scene/skill as physically validated.
The native GetLocalBid and slot services are environmental I/O adapters with
explicit costs; they do not replace auction ranking or BT control flow.
The adapter does not choose a representative from a native coalition: duplicate
task assignments raise a scope error and retain the raw native output.

Initial environment startup failures (missing container git and catkin setup's
unset ROS_DISTRO under `set -u`) were resolved in container/bootstrap setup. No
upstream bug or algorithm was patched. Upstream logs successful bid/result
operations at FATAL level; this is original logging behavior, not a failing test.

## Reproduce

Run against an endpoint configured with `--robots robot_a robot_b` (the default
for a fresh runtime). The later `ur5_1` Isaac endpoint cannot run this two-robot
suite without reconfiguration. The current protocol endpoint is
`http://172.17.0.2:18766`; for a fresh container with the port mapping below,
substitute `http://127.0.0.1:18765`:

```bash
cd /home/airs/hetrocoord
python3 algorithm/system/coordination/bt_capabilities/scripts/run_native_smoke.py \
  --source-root /tmp/heterocoord-algorithms-20261028/bt-capabilities \
  --endpoint http://172.17.0.2:18766 \
  --evidence-dir /home/airs/hetrocoord/records/algorithm_adapters/bt_capabilities/ros_protocol_recheck
```

For a fresh isolated runtime (the Dockerfile records the installed dependencies;
the equivalent installation/build commands were executed in the recorded container):

```bash
cd /home/airs/hetrocoord
docker build -t heterocoord-bt-native:local algorithm/system/coordination/bt_capabilities/scripts
docker run -d --name heterocoord-bt-native-fresh \
  -p 127.0.0.1:18765:18765 \
  --mount type=bind,source=/tmp/heterocoord-algorithms-20261028/bt-capabilities,target=/upstream,readonly \
  --mount type=bind,source=/home/airs/hetrocoord,target=/workspace,readonly \
  heterocoord-bt-native:local
# Wait for ROS message compilation and /health readiness, then use
# HETEROCOORD_BT_NATIVE_ENDPOINT=http://127.0.0.1:18765
```

To restart the existing prepared container runtime after it has stopped:

```bash
docker start heterocoord-bt-native-20261028
docker exec -d heterocoord-bt-native-20261028 bash -c \
  'bash /workspace/algorithm/system/coordination/bt_capabilities/scripts/start_runtime.sh > /tmp/native-endpoint.log 2>&1'
docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' heterocoord-bt-native-20261028
```

## Changed paths

Source, scripts, tests, and maintained configs are under
`algorithm/system/coordination/bt_capabilities/`:

- `native_adapter.py`: typed client and EAI feedback forwarding.
- `scripts/native_endpoint.py`: real ROS services/processes and native BT leaves.
- `scripts/start_runtime.sh`, `scripts/Dockerfile`: isolated runtime instructions.
- `scripts/run_native_smoke.py`: live tests and source receipt capture.
- `tests/test_native_adapter.py`: contract and real runtime tests.
- `configs/isaac_ur5_task.json`, `configs/isaac_ur5_adapter.json`: maintained
  Isaac inputs, explicitly exempted by `configs/.gitignore` from the global JSON ignore.
- `NATIVE_ADAPTER_REPORT.md`: this report.

Run evidence is outside the source tree:

- `/home/airs/hetrocoord/records/algorithm_adapters/bt_capabilities/ros_protocol_20261028/`:
  original ten files (JUnit, run/source receipt, four native traces, TaskRunner
  results, runtime log and environment/build receipts).
- `/home/airs/hetrocoord/records/algorithm_adapters/bt_capabilities/native_20261028/`:
  Isaac integration report, physical receipt, BT feedback, and retained failed attempt.

No shared registry, legacy benchmark adapter, existing method implementation,
upstream source, or unrelated dirty file was edited. No commit or push.

## DAG and same-scene multi-robot acceptance

Final maintained protocol suite: **66 passed = 59 contract + 7 actual ROS tests**,
with unchanged frozen source before/after. This count excludes the two legacy
rewrite-adapter tests. Evidence: `records/algorithm_adapters/bt_capabilities/dag_20261028/final_endpoint_smoke/`.
All seven receipts identify the current endpoint adapter SHA-256
`18b0e354428ab7687a7b2d8a21a9f1e202be1144d2c939ecf804bddb03b662f7`.
Earlier runs against a prior endpoint revision remain historical records.

Added live cases cover non-topological four-node fork/join with two robot slots,
two independent tasks sharing one eligible robot, atomic rejection of duplicate
or wrong-owner batch feedback, and failure blocking the dependent join. Native
auction/BT implementations are imported from the frozen read-only source.

The dedicated host `tools/algorithm_adapters/isaac_bt_multi_robot.py` uses two
unchanged `PickPlaceExecutor` instances in one Isaac SimulationContext. Both arms
yield at existing advance callbacks; a single host thread steps physics once.
The original auction assigns two independent moves; real measured batch feedback
unblocks a third ground-truth inspection task. The host never repairs a winner.

**Physical acceptance passed** in `records/algorithm_adapters/bt_capabilities/multi_robot_20261028/physical_002/`:
3/3 tasks completed, original BT SUCCEEDED, process exit 0, 5,053 physics ticks,
4,453 shared active ticks and 3,728 measured simultaneous-motion ticks (18.64 s).
Both pick/place operations and the 200-tick post-release join inspection passed.
All four arm/table clearance checks report zero overlap over 5,053 samples each.
Raw trajectory recomputation, artifact/source hashes and empty post-run native
mission state were independently checked in `independent_audit.json`.

The first physical attempt completed its measured tasks but blocked in Kit close
before writing the aggregate receipt; it is retained as `physical_001/`, not full
acceptance. The host now cleans up execution/native state, persists the receipt,
then uses the existing standalone-host Kit shutdown policy. A regression test
covers Kit terminating Python after receipt persistence. Dedicated host tests:
**24 passed**, in `multi_robot_20261028/host_contract/shutdown_regression_tests.xml`.

Physical scope remains follow-proxy manipulation, not contact grasp. Inspection
is a host simulator ground-truth query, not onboard vision. Self-collision and
arm gravity are disabled; environment collisions remain enabled. Cross-arm mesh
collision certification and paper Benchmark scores are not claimed.

To start the dedicated two-UR5 endpoint alongside the protocol endpoint in the
already prepared container (one shared ROS master, distinct namespaces):

```bash
docker exec -d heterocoord-bt-native-20261028 bash -c '
  source /catkin_ws/devel/setup.bash
  export ROS_MASTER_URI=http://127.0.0.1:11311 ROS_HOSTNAME=localhost
  export ROS_NAMESPACE=/bt_multi_physical PYTHONDONTWRITEBYTECODE=1
  export PYTHONPATH=/upstream/ros_bt_py/src:${PYTHONPATH:-}
  /home/airs/.cache/heterocoord/envs/bt-capabilities/bin/python \
    /workspace/algorithm/system/coordination/bt_capabilities/scripts/native_endpoint.py \
    --source-root /upstream --bind 0.0.0.0 --prefix /heterocoord_bt_physical \
    --port 18767 --robots ur5_1 ur5_2 > /tmp/native-multi-physical-endpoint.log 2>&1'
```

Do not start a duplicate if that endpoint is already running. The host command,
dependency version and fresh-output requirements are in `tools/algorithm_adapters/README.md`.
Full measurements and remaining scope are in
`records/algorithm_adapters/bt_capabilities/multi_robot_20261028/REPORT.zh-CN.md`.
