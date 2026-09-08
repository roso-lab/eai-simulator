from types import SimpleNamespace

import pytest

from demo.fire_rescue.checks import parallel_replay


def job(pid, start, end, passed=True, exit_code=0):
    return {"passed": passed, "exit_code": exit_code,
            "task": {"pid": pid, "timing": {"first_step": start, "last_step": end}}}


def test_parallel_acceptance_requires_overlapping_real_step_intervals():
    report = parallel_replay.summarize_round([job(1, 0, 5), job(2, 2, 6)], 2)
    assert report["passed"]
    assert report["task_step_interval_overlap_s"] == 3


@pytest.mark.parametrize("jobs", [
    [job(1, 0, 1), job(2, 2, 3)],
    [job(1, 0, 4), job(1, 1, 3)],
    [job(1, 0, 4), job(2, 1, 3, passed=False)],
    [job(1, 0, 4), job(2, 1, 3, exit_code=1)],
    [job(1, 0, 4), {"passed": True, "exit_code": 0}],
    [job(1, 0, 4)],
])
def test_partial_sequential_or_failed_runs_are_not_parallel_passes(jobs):
    assert not parallel_replay.summarize_round(jobs, 2)["passed"]


def test_single_worker_is_explicitly_allowed():
    assert parallel_replay.summarize_round([job(1, 0, 2)], 1)["passed"]


def test_round_supervisor_starts_fresh_processes_and_keeps_outputs_separate(tmp_path, monkeypatch):
    from pathlib import Path

    started = []
    stopped = []

    def launch(command, **options):
        seed = int(command[command.index("--seed") + 1])
        output = Path(command[command.index("--output") + 1])
        assert options["start_new_session"]
        assert options["env"]["PYTHONHASHSEED"] == str(seed)
        assert not output.joinpath("job.json").exists()
        parallel_replay.write_report(output / "job.json", job(seed + 10, 1, 5))
        process = SimpleNamespace(pid=seed + 10, returncode=0, poll=lambda: 0)
        started.append((seed, output))
        return process

    monkeypatch.setattr(parallel_replay.subprocess, "Popen", launch)
    monkeypatch.setattr(parallel_replay, "stop_process_group", lambda process: stopped.append(process.pid))
    monkeypatch.setattr(parallel_replay.time, "sleep", lambda duration: None)
    args = parallel_replay.build_parser().parse_args(["--output", str(tmp_path / "suite")])
    report = parallel_replay.run_parallel(args)
    assert report["passed"]
    assert not report["full_task_physical_rewind_verified"]
    assert [seed for seed, output in started] == [0, 1, 2, 3]
    assert len({output for seed, output in started}) == 4
    assert stopped == [10, 11, 12, 13]
    with pytest.raises(FileExistsError):
        parallel_replay.run_parallel(args)


def test_timeout_cannot_become_a_pass_without_a_worker_report(tmp_path, monkeypatch):
    from itertools import count

    clock = count(step=10)
    process = SimpleNamespace(pid=123, returncode=None)
    process.poll = lambda: process.returncode
    stopped = []

    def stop(target):
        stopped.append(target.pid)
        target.returncode = -15

    monkeypatch.setattr(parallel_replay.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(parallel_replay, "stop_process_group", stop)
    monkeypatch.setattr(parallel_replay.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(parallel_replay.time, "sleep", lambda duration: None)
    args = parallel_replay.build_parser().parse_args([
        "--workers", "1", "--rounds", "1", "--timeout", "1", "--output", str(tmp_path / "suite"),
    ])
    report = parallel_replay.run_parallel(args)
    assert not report["passed"]
    assert report["rounds"][0]["jobs"][0]["timed_out"]
    assert stopped == [123, 123]


def test_cleanup_ends_its_child_process_group(tmp_path):
    import subprocess
    import sys
    import time
    from pathlib import Path

    if not Path("/proc").is_dir():
        pytest.skip("requires Linux process inspection")
    pid_file = tmp_path / "child_pid"
    program = (
        "import subprocess, sys, time; from pathlib import Path; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
    )
    process = subprocess.Popen([sys.executable, "-c", program, str(pid_file)], start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists()
        child_status = Path(f"/proc/{int(pid_file.read_text())}/status")
        parallel_replay.stop_process_group(process)
        assert process.returncode is not None
        deadline = time.monotonic() + 5
        while child_status.exists() and time.monotonic() < deadline:
            try:
                status = child_status.read_text()
            except FileNotFoundError:
                break
            state = next(line for line in status.splitlines() if line.startswith("State:")).split()[1]
            if state in {"Z", "X"}:
                break
            time.sleep(0.01)
        else:
            assert not child_status.exists(), "owned child process is still running"
    finally:
        parallel_replay.stop_process_group(process)


def test_job_does_not_accept_exit_zero_without_task_pass(tmp_path, monkeypatch):
    gate = tmp_path / "gate"
    gate.mkdir()
    output = tmp_path / "job"
    output.mkdir()
    args = parallel_replay.build_parser().parse_args([
        "--output", str(output), "--gate", str(gate), "--job-worker",
    ])

    def run(command, **kwargs):
        parallel_replay.write_report(output / "task/task.json", {"logic_passed": False})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(parallel_replay.subprocess, "run", run)
    assert parallel_replay.run_job_worker(args) == 1
    assert (gate / "abort.json").is_file()
    assert not (output / "fidelity").exists()


@pytest.mark.parametrize("flags", [["--workers", "0"], ["--rounds", "0"], ["--steps", "0"],
                                   ["--timeout", "nan"], ["--task-worker"]])
def test_invalid_parallel_cli_does_not_launch_isaac(flags, monkeypatch):
    monkeypatch.setattr("sys.argv", ["parallel_replay", *flags])
    with pytest.raises(SystemExit) as caught:
        parallel_replay.main()
    assert caught.value.code == 2
