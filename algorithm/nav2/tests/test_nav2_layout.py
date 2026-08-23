import ast
import importlib.util
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml


NAV2_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
NAV2_SETUP = NAV2_DIR / "nav2_setup.py"
OLD_NAV2_DIR = REPO_ROOT / "algorithm" / "ros" / "nav2"

PROMOTED_FILES = {
    "README.md",
    "nav2.launch.py",
    "nav2_params.template.yaml",
    "nav2_profiles.yaml",
    "nav2_setup.py",
    "nav2_view.template.rviz",
    "pointcloud_to_laserscan.template.yaml",
    "run_nav2.sh",
    "rviz.launch.py",
    "send_goal.py",
    "tf_bridge.py",
}
PYTHON_ENTRYPOINTS = {
    "nav2.launch.py",
    "nav2_setup.py",
    "rviz.launch.py",
    "send_goal.py",
    "tf_bridge.py",
}
COMMAND_FILES = {
    "README.md",
    "nav2.launch.py",
    "run_nav2.sh",
    "rviz.launch.py",
    "send_goal.py",
    "tf_bridge.py",
}
GENERATED_FILES = {
    "meta.txt",
    "nav2_params.yaml",
    "pointcloud_to_laserscan.yaml",
    "view.rviz",
}
EXPECTED_LAYOUT = PROMOTED_FILES | {
    "tests/test_nav2_layout.py",
    "tests/test_nav2_plugin_names.py",
    "tests/test_send_goal.py",
}
GOAL_COMMAND_DOCS = {
    NAV2_DIR / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "docs" / "source" / "getting_started.md",
    REPO_ROOT / "docs" / "source" / "getting_started_en.md",
    REPO_ROOT / "docs" / "source" / "orsus_sensor.md",
    REPO_ROOT / "docs" / "source" / "orsus_sensor_en.md",
    REPO_ROOT / "docs" / "source" / "project_overview.md",
    REPO_ROOT / "docs" / "source" / "project_overview_en.md",
}


def load_nav2_setup():
    assert NAV2_SETUP.is_file(), f"missing promoted entrypoint: {NAV2_SETUP}"
    spec = importlib.util.spec_from_file_location("eai_nav2_setup", NAV2_SETUP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_tracked(path):
    relative_path = path.relative_to(REPO_ROOT)
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(relative_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_promoted_layout_contains_only_maintained_files():
    actual_files = {
        path.relative_to(NAV2_DIR).as_posix()
        for path in NAV2_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    old_files = {
        path.relative_to(OLD_NAV2_DIR).as_posix()
        for path in OLD_NAV2_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }

    assert actual_files == EXPECTED_LAYOUT
    assert not old_files
    assert not (NAV2_DIR / "carter_nav2.launch.py").exists()


def test_nav2_setup_uses_repository_root_and_runtime_snapshot():
    nav2_setup = load_nav2_setup()

    assert Path(nav2_setup.REPO_ROOT) == REPO_ROOT
    assert Path(nav2_setup.DEFAULT_RUNTIME_SNAPSHOT) == (
        REPO_ROOT / "tmp" / "runtime_interfaces.json"
    )


def test_promoted_launchers_use_the_promoted_directory_depth():
    launch_path = NAV2_DIR / "nav2.launch.py"
    launch_tree = ast.parse(
        launch_path.read_text(encoding="utf-8"), filename=str(launch_path)
    )
    root_assignment = next(
        node
        for node in launch_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "REPO_ROOT"
            for target in node.targets
        )
    )
    computed_root = eval(
        compile(ast.Expression(root_assignment.value), str(launch_path), "eval"),
        {"os": os, "THIS_DIR": str(NAV2_DIR)},
    )
    assert Path(computed_root) == REPO_ROOT

    root_line = next(
        line
        for line in (NAV2_DIR / "run_nav2.sh").read_text(encoding="utf-8").splitlines()
        if line.startswith("REPO_ROOT=")
    )
    assert root_line == 'REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"'


def test_factory_profile_resolves_to_tracked_map_and_image(tmp_path):
    nav2_setup = load_nav2_setup()
    profiles_path = NAV2_DIR / "nav2_profiles.yaml"
    profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))

    assert profiles["scene_maps"]["factory"] == (
        "demo/fire_rescue/assets/factory_map.yaml"
    )
    map_path = Path(
        nav2_setup.resolve_map(profiles, "factory", None, str(tmp_path))
    )
    expected_map = REPO_ROOT / "demo" / "fire_rescue" / "assets" / "factory_map.yaml"
    assert map_path == expected_map
    assert map_path.is_file()

    map_config = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    image_path = map_path.parent / map_config["image"]
    assert image_path.is_file()
    assert_tracked(map_path)
    assert_tracked(image_path)


def test_promoted_profiles_retain_latest_develop_robot_support():
    profiles = yaml.safe_load(
        (NAV2_DIR / "nav2_profiles.yaml").read_text(encoding="utf-8")
    )

    assert profiles["robot_profiles"]["Pepper"] == {
        "motion_model": "omni",
        "robot_radius": 0.35,
        "sensor_mounts": {
            "lidar": {"xyz": [0.0, 0.0, 1.45], "rpy": [0.0, 0.0, 0.0]}
        },
        "max_vel_x": 0.4,
        "max_vel_theta": 0.8,
        "acc_lim_x": 1.0,
        "acc_lim_theta": 1.5,
        "min_vel_x": -0.3,
        "min_speed_xy": 0.0,
        "xy_goal_tolerance": 0.4,
        "yaw_goal_tolerance": 0.8,
        "progress_required_movement_radius": 0.05,
        "progress_movement_time_allowance": 30.0,
        "scan_z_min": -0.10,
        "scan_z_max": 0.50,
        "scan_range_min": 0.35,
    }
    assert profiles["robot_profiles"]["G1"] == {
        "motion_model": "omni",
        "robot_radius": 0.40,
        "sensor_mounts": {
            "lidar": {"xyz": [0.0, 0.0, 0.72], "rpy": [0.0, 0.0, 0.0]}
        },
        "max_vel_x": 0.4,
        "max_vel_theta": 0.8,
        "acc_lim_x": 1.0,
        "acc_lim_theta": 1.5,
        "min_vel_x": -0.2,
        "min_speed_xy": 0.0,
        "xy_goal_tolerance": 0.6,
        "yaw_goal_tolerance": 3.14,
        "progress_required_movement_radius": 0.05,
        "progress_movement_time_allowance": 30.0,
        "scan_z_min": -0.10,
        "scan_z_max": 0.60,
        "scan_range_min": 0.40,
    }


def test_nav2_setup_generates_exact_offline_configuration_set(tmp_path):
    out_dir = tmp_path / "generated"
    result = subprocess.run(
        [
            sys.executable,
            str(NAV2_SETUP),
            "--robot",
            "carter_1",
            "--robot-type",
            "Carter",
            "--sensor",
            "orsus",
            "--scene",
            "factory",
            "--pose=-3,0,0",
            "--out",
            str(out_dir),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    assert {path.name for path in out_dir.iterdir()} == GENERATED_FILES
    assert isinstance(
        yaml.safe_load((out_dir / "nav2_params.yaml").read_text(encoding="utf-8")),
        dict,
    )
    assert isinstance(
        yaml.safe_load(
            (out_dir / "pointcloud_to_laserscan.yaml").read_text(encoding="utf-8")
        ),
        dict,
    )

    expected_keys = {"PARAMS", "PC2SCAN", "RVIZ", "MAP"}
    values = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in expected_keys:
            values[key] = value

    assert values == {
        "PARAMS": str(out_dir / "nav2_params.yaml"),
        "PC2SCAN": str(out_dir / "pointcloud_to_laserscan.yaml"),
        "RVIZ": str(out_dir / "view.rviz"),
        "MAP": str(
            REPO_ROOT / "demo" / "fire_rescue" / "assets" / "factory_map.yaml"
        ),
    }
    assert_tracked(Path(values["MAP"]))


def test_all_promoted_python_entrypoints_parse_without_importing_ros():
    for filename in sorted(PYTHON_ENTRYPOINTS):
        path = NAV2_DIR / filename
        assert path.is_file(), f"missing promoted entrypoint: {path}"
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_promoted_text_uses_current_nav2_command_paths():
    old_command_root = "algorithm" + "/ros"
    for filename in sorted(PROMOTED_FILES):
        text = (NAV2_DIR / filename).read_text(encoding="utf-8")
        assert old_command_root not in text, filename

    for filename in sorted(COMMAND_FILES):
        text = (NAV2_DIR / filename).read_text(encoding="utf-8")
        assert "algorithm/nav2/" in text, filename

    run_script = (NAV2_DIR / "run_nav2.sh").read_text(encoding="utf-8")
    assert "env -i 模板" not in run_script


def test_goal_commands_and_launcher_select_the_nav2_rmw():
    for path in sorted(GOAL_COMMAND_DOCS):
        text = path.read_text(encoding="utf-8")
        nav2_blocks = [
            block
            for block in re.findall(r"```bash\n(.*?)\n```", text, re.DOTALL)
            if "algorithm/nav2/send_goal.py" in block
            or "ros2 launch algorithm/nav2/nav2.launch.py" in block
        ]
        assert nav2_blocks, f"missing Nav2 Bash block: {path}"
        for block in nav2_blocks:
            assert "export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp" in block, path

    run_script = (NAV2_DIR / "run_nav2.sh").read_text(encoding="utf-8")
    assert 'echo "   export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp"' in run_script
    system_ros_env = re.search(
        r"SYSTEM_ROS_ENV=\(\n(.*?)\n\)", run_script, re.DOTALL
    )
    assert system_ros_env is not None
    assert '"RMW_IMPLEMENTATION=rmw_cyclonedds_cpp"' in system_ros_env.group(1)
    assert 'setsid env -i "${SYSTEM_ROS_ENV[@]}"' in run_script


def test_readme_explains_which_terminal_owns_workflow_shutdown():
    readme = (NAV2_DIR / "README.md").read_text(encoding="utf-8")

    assert "Ctrl+C in the `send_goal.py` terminal stops only the goal client" in readme
    assert "Ctrl+C in the `run_nav2.sh` terminal" in readme


def _extract_shell_function(name):
    lines = (NAV2_DIR / "run_nav2.sh").read_text(encoding="utf-8").splitlines()
    starts = [index for index, line in enumerate(lines) if line == f"{name}() {{"]
    assert starts, f"missing shell function: {name}"
    start = starts[0]
    end = next(
        index for index in range(start + 1, len(lines)) if lines[index] == "}"
    )
    return "\n".join(lines[start : end + 1])


def test_ros_discovery_environment_is_preserved_by_allowlist():
    append_function = _extract_shell_function("append_ros_discovery_environment")
    command = f"""
{append_function}
launch_env=("HOME=/tmp/eai-test")
ROS_DOMAIN_ID=37
ROS_LOCALHOST_ONLY=1
ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
ROS_STATIC_PEERS=10.0.0.4
CYCLONEDDS_URI=file:///tmp/cyclonedds.xml
append_ros_discovery_environment launch_env
printf '%s\\n' "${{launch_env[@]}}"
"""

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "HOME=/tmp/eai-test",
        "ROS_DOMAIN_ID=37",
        "ROS_LOCALHOST_ONLY=1",
        "ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST",
        "ROS_STATIC_PEERS=10.0.0.4",
        "CYCLONEDDS_URI=file:///tmp/cyclonedds.xml",
    ]


def test_post_readiness_nav2_exit_triggers_failure_and_process_group_cleanup():
    cleanup_function = _extract_shell_function("cleanup")
    monitor_function = _extract_shell_function("monitor_runtime")
    command = f"""
{cleanup_function}
{monitor_function}
SIM_LOG=/tmp/eai-nav2-test-sim.log
NAV2_LOG=/tmp/eai-nav2-test-stack.log
setsid sleep 30 &
SIM_PID=$!
printf 'sim_pid=%s\\n' "$SIM_PID"
setsid bash -c 'sleep 0.05; exit 7' &
NAV2_PID=$!
trap cleanup EXIT INT TERM
monitor_runtime
exit $?
"""

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 1, result.stderr
    assert "Nav2 进程意外退出" in result.stdout
    assert "清理完成" in result.stdout
    sim_pid_line = next(line for line in result.stdout.splitlines() if line.startswith("sim_pid="))
    sim_pid = int(sim_pid_line.partition("=")[2])
    with pytest.raises(ProcessLookupError):
        os.kill(sim_pid, signal.SIGCONT)


def test_repeated_sigint_cannot_interrupt_process_group_cleanup(tmp_path):
    cleanup_function = _extract_shell_function("cleanup")
    signal_function = _extract_shell_function("handle_signal")
    sim_ready_file = shlex.quote(str(tmp_path / "sim-ready"))
    command = f"""
{cleanup_function}
{signal_function}
SIM_READY_FILE={sim_ready_file}
setsid bash -c 'trap "" TERM; printf ready > "$1"; while :; do sleep 1; done' _ "$SIM_READY_FILE" &
SIM_PID=$!
setsid sleep 30 &
NAV2_PID=$!
trap cleanup EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
for _ in $(seq 1 100); do
    [ -s "$SIM_READY_FILE" ] && break
    sleep 0.01
done
[ -s "$SIM_READY_FILE" ] || exit 9
printf 'sim_pid=%s\nnav2_pid=%s\nready\n' "$SIM_PID" "$NAV2_PID"
wait "$SIM_PID"
"""
    process = subprocess.Popen(
        ["bash", "-c", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None

    sim_pid = int(process.stdout.readline().partition("=")[2])
    nav2_pid = int(process.stdout.readline().partition("=")[2])
    assert process.stdout.readline().strip() == "ready"

    try:
        os.killpg(process.pid, signal.SIGINT)
        time.sleep(0.2)
        assert process.poll() is None, "cleanup exited before its TERM wait window"

        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=5)
        assert process.returncode == 130

        for process_group in (sim_pid, nav2_pid):
            with pytest.raises(ProcessLookupError):
                os.killpg(process_group, signal.SIGCONT)
    finally:
        for process_group in (sim_pid, nav2_pid, process.pid):
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.wait(timeout=2)


def test_sigint_after_sim_start_exits_without_continuing_to_nav2():
    cleanup_function = _extract_shell_function("cleanup")
    signal_function = _extract_shell_function("handle_signal")
    command = f"""
{cleanup_function}
{signal_function}
setsid sleep 30 &
SIM_PID=$!
NAV2_PID=""
trap cleanup EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
printf 'sim_pid=%s\nready\n' "$SIM_PID"
wait "$SIM_PID"
printf 'continued-after-signal\n'
"""
    process = subprocess.Popen(
        ["bash", "-c", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None

    sim_pid = int(process.stdout.readline().partition("=")[2])
    assert process.stdout.readline().strip() == "ready"

    try:
        os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)

        assert process.returncode == 130, stderr
        assert "continued-after-signal" not in stdout
        with pytest.raises(ProcessLookupError):
            os.killpg(sim_pid, signal.SIGCONT)
    finally:
        for process_group in (sim_pid, process.pid):
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.wait(timeout=2)


@pytest.mark.parametrize(
    ("signal_name", "expected_status"), [("INT", 130), ("TERM", 143)]
)
def test_signal_during_process_group_launch_is_deferred_until_pid_is_recorded(
    signal_name, expected_status
):
    cleanup_function = _extract_shell_function("cleanup")
    signal_function = _extract_shell_function("handle_signal")
    begin_launch_function = _extract_shell_function("begin_process_group_launch")
    complete_launch_function = _extract_shell_function("complete_process_group_launch")
    command = f"""
{cleanup_function}
{signal_function}
{begin_launch_function}
{complete_launch_function}
SIM_PID=""
NAV2_PID=""
LAUNCH_IN_PROGRESS=false
PENDING_SIGNAL_STATUS=""
SIGNAL_NAME={shlex.quote(signal_name)}
trap cleanup EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
begin_process_group_launch
setsid sleep 30 >/dev/null 2>&1 &
for _ in $(seq 1 200); do
    kill -0 -- "-$!" 2>/dev/null && break
    sleep 0.001
done
kill -0 -- "-$!" 2>/dev/null || exit 9
printf 'sim_pid=%s\n' "$!"
kill -s "$SIGNAL_NAME" "$$"
complete_process_group_launch SIM_PID "$!"
printf 'continued-after-signal\n'
"""
    process = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    sim_pid = int(
        next(
            line for line in process.stdout.splitlines() if line.startswith("sim_pid=")
        ).partition("=")[2]
    )

    try:
        assert process.returncode == expected_status, process.stderr
        assert "continued-after-signal" not in process.stdout
        with pytest.raises(ProcessLookupError):
            os.killpg(sim_pid, signal.SIGCONT)
    finally:
        try:
            os.killpg(sim_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_cleanup_uses_owner_scoped_stale_snapshot_cleanup():
    run_script = (NAV2_DIR / "run_nav2.sh").read_text(encoding="utf-8")
    cleanup_start = run_script.index("cleanup() {")
    cleanup_end = run_script.index("\n}\n\nhandle_signal", cleanup_start)
    cleanup = run_script[cleanup_start:cleanup_end]

    assert "remove_stale_snapshot" in cleanup
    assert "pid=int(sys.argv[2])" in cleanup
    assert cleanup.index("wait \"$pid\"") < cleanup.index("remove_stale_snapshot")
    assert "rm -f" not in cleanup
    assert "runtime_interfaces.json" in cleanup


def test_simulator_and_nav2_launches_close_the_pid_capture_signal_window():
    run_script = (NAV2_DIR / "run_nav2.sh").read_text(encoding="utf-8")

    assert run_script.count("begin_process_group_launch") == 3
    assert run_script.count('complete_process_group_launch SIM_PID "$!"') == 1
    assert run_script.count('complete_process_group_launch NAV2_PID "$!"') == 1


def test_promoted_layout_test_is_not_ignored():
    ignore_lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ignore_lines.count("!/algorithm/nav2/tests/") == 1
    assert ignore_lines.count("!/algorithm/nav2/tests/test_nav2_layout.py") == 1
    assert ignore_lines.count("!/algorithm/nav2/tests/test_send_goal.py") == 1
