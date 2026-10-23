from types import SimpleNamespace

from tools import run_tests
from tools.run_tests import DEFAULT_TARGETS, _has_explicit_target, _parse_args


def test_parser_keeps_pytest_options_without_dropping_default_targets():
    args = _parse_args(("--profile", "contract", "-q", "--collect-only"))
    assert args.profile == "contract"
    assert args.pytest_args == ("-q", "--collect-only")
    assert not _has_explicit_target(args.pytest_args)
    assert DEFAULT_TARGETS[args.profile][0] == "task/tests"


def test_parser_preserves_explicit_collection_path():
    args = _parse_args(("--profile", "contract", "-q", "task/tests"))
    assert args.pytest_args == ("-q", "task/tests")
    assert _has_explicit_target(args.pytest_args)


def test_keyword_expression_is_not_mistaken_for_collection_path():
    args = _parse_args(("--profile", "contract", "-k", "benchmark"))
    assert args.pytest_args == ("-k", "benchmark")
    assert not _has_explicit_target(args.pytest_args)


def test_separator_preserves_pytest_arguments_and_explicit_target():
    args = _parse_args(("--profile", "contract", "--", "-q", "task/tests"))
    assert args.pytest_args == ("-q", "task/tests")
    assert _has_explicit_target(args.pytest_args)


def test_unknown_nodeid_is_still_an_explicit_collection_selector():
    args = _parse_args(("--profile", "contract", "-q", "task/tests::test_future"))
    assert _has_explicit_target(args.pytest_args)


def test_ros_profile_rejects_options_without_explicit_collection_path(monkeypatch, capsys):
    monkeypatch.setattr(run_tests, "_pytest_version", lambda: "test-version")
    monkeypatch.setattr(
        run_tests.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pytest must not run")),
    )

    assert run_tests.main(("--profile", "ros", "-q")) == 2
    assert "requires explicit ROS/launch_testing test paths" in capsys.readouterr().err


def test_ros_profile_accepts_explicit_collection_path(monkeypatch):
    observed = {}
    monkeypatch.setattr(run_tests, "_pytest_version", lambda: "test-version")

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_tests.subprocess, "run", fake_run)

    assert run_tests.main(("--profile", "ros", "-q", "tools/ros2/tests")) == 0
    assert observed["command"][-2:] == ["-q", "tools/ros2/tests"]
