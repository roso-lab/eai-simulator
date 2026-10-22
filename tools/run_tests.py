#!/usr/bin/env python3
"""Run HeteroCoord tests with an explicit dependency/plugin boundary.

The IsaacLab environment also exposes ROS/ament pytest entry points.  Those
plugins are useful for ROS integration tests, but they must not be imported by
the project's ordinary contract tests.  This wrapper configures the boundary
*before* pytest starts, so callers no longer need to remember a shell-specific
``PYTEST_DISABLE_PLUGIN_AUTOLOAD`` workaround.

Examples::

    python tools/run_tests.py --diagnose
    python tools/run_tests.py --profile contract benchmark task/tests
    python tools/run_tests.py --profile isaac benchmark/tests/test_project_runtime.py
    python tools/run_tests.py --profile ros tools/ros2/tests

The wrapper itself does not import pytest.  It checks availability, constructs
the project-local environment, and then executes ``python -m pytest`` in a
child process using the same interpreter that invoked this file.
"""

from __future__ import annotations

import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import NamedTuple, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = tuple(PROJECT_ROOT / "source" / name for name in ("EAI", "EAI_assets", "EAI_hmrs"))
DEFAULT_TARGETS = {
    "contract": ("task/tests", "benchmark", "algorithm/skills/tests", "algorithm/common_coordination/tests"),
    "isaac": ("benchmark", "task/tests"),
    "ros": (),
}


def _pytest_version() -> str | None:
    try:
        return metadata.version("pytest")
    except metadata.PackageNotFoundError:
        return None


def _pytest_entry_points() -> list[str]:
    try:
        entries = metadata.entry_points(group="pytest11")
    except TypeError:  # pragma: no cover - compatibility with old importlib.metadata
        entries = metadata.entry_points().get("pytest11", ())
    return sorted(f"{entry.name}={entry.value}" for entry in entries)


def _environment(profile: str) -> dict[str, str]:
    env = dict(os.environ)
    inherited = [item for item in env.get("PYTHONPATH", "").split(os.pathsep) if item]
    local = [str(PROJECT_ROOT), *(str(path) for path in SOURCE_ROOTS)]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys((*local, *inherited)))
    env["HETEROCOORD_PROJECT_ROOT"] = str(PROJECT_ROOT)
    env.pop("PYTEST_PLUGINS", None)
    if profile in {"contract", "isaac"}:
        # This must be present before pytest's plugin manager is initialized.
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    else:
        # A shell-level setting must not silently disable explicitly requested
        # ROS/launch_testing integration plugins.
        env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
    return env


def _diagnostic(profile: str) -> dict[str, object]:
    version = _pytest_version()
    return {
        "schema": "heterocoord.test_runner_diagnostic.v1",
        "project_root": str(PROJECT_ROOT),
        "python": str(Path(sys.executable).resolve()),
        "python_version": sys.version.split()[0],
        "profile": profile,
        "pytest_available": version is not None,
        "pytest_version": version,
        "pytest11_entry_points": _pytest_entry_points(),
        "plugin_policy": (
            "disable-third-party-autoload"
            if profile in {"contract", "isaac"}
            else "allow-explicit-ros-autoload"
        ),
        "default_targets": list(DEFAULT_TARGETS[profile]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(DEFAULT_TARGETS), default="contract")
    parser.add_argument("--diagnose", action="store_true", help="print interpreter/plugin diagnostics and exit")
    return parser


class _Arguments(NamedTuple):
    profile: str
    diagnose: bool
    pytest_args: tuple[str, ...]


def _parse_args(argv: Sequence[str] | None) -> _Arguments:
    """Parse wrapper options while preserving every pytest argument in order.

    Wrapper options must precede the first pytest argument.  A conventional
    ``--`` separator is accepted and removed.  This avoids argparse
    ``parse_known_args`` reordering option/value pairs such as ``-k EXPR``.
    """

    values = list(sys.argv[1:] if argv is None else argv)

    # ``argparse`` cannot express this wrapper's boundary cleanly: pytest
    # accepts arbitrary options and positional collection targets, while the
    # wrapper owns only ``--profile`` and ``--diagnose``.  Parse those two
    # options until the first pytest token, then forward the remainder byte for
    # byte.  In particular, do not use ``nargs=REMAINDER`` or
    # ``parse_known_args`` here; with a positional REMAINDER argparse consumes
    # ``-q task/tests`` as a single remainder and silently drops the target.
    profile = "contract"
    diagnose = False
    pytest_args: list[str] = []
    wrapper_prefix = True
    index = 0
    while index < len(values):
        token = values[index]
        if not wrapper_prefix:
            pytest_args.extend(values[index:])
            break
        if token == "--":
            # The separator belongs to this wrapper, not pytest.  Everything
            # after it is an opaque pytest argument sequence.
            pytest_args.extend(values[index + 1 :])
            break
        if token == "--diagnose":
            diagnose = True
            index += 1
            continue
        if token == "--profile":
            if index + 1 >= len(values):
                raise SystemExit("--profile requires one of: " + ", ".join(DEFAULT_TARGETS))
            profile = values[index + 1]
            if profile not in DEFAULT_TARGETS:
                raise SystemExit(f"invalid choice for --profile: {profile!r}")
            index += 2
            continue
        if token.startswith("--profile="):
            profile = token.partition("=")[2]
            if profile not in DEFAULT_TARGETS:
                raise SystemExit(f"invalid choice for --profile: {profile!r}")
            index += 1
            continue

        # First unknown token is pytest's boundary.  This includes options
        # such as ``-q`` and ``-k`` as well as an explicit collection path.
        wrapper_prefix = False
        pytest_args.extend(values[index:])
        break
    return _Arguments(profile, diagnose, tuple(pytest_args))


_PYTEST_VALUE_OPTIONS = frozenset({
    "-k", "--keyword", "-m", "--markexpr", "--maxfail", "--tb",
    "--rootdir", "--confcutdir", "--basetemp", "--override-ini",
    "-o", "--log-level", "--log-cli-level", "--log-file-level",
    "--log-file", "--import-mode", "--capture", "--color",
})


def _has_explicit_target(args: Sequence[str]) -> bool:
    """Return whether forwarded pytest args contain a collection path.

    Pytest options may have a separate value (``-k expr``).  Those values are
    skipped so a value coincidentally matching a project directory cannot
    disable the runner's safe default target list.
    """
    skip_value = False
    after_separator = False
    for token in args:
        if skip_value:
            skip_value = False
            continue
        if token == "--":
            after_separator = True
            continue
        if after_separator:
            # Anything after pytest's own separator is positional from the
            # wrapper's perspective.  An empty tail is not a target.
            return bool(token)
        if token in _PYTEST_VALUE_OPTIONS:
            skip_value = True
            continue
        if token.startswith("-"):
            continue
        # Pytest accepts paths, nodeids (``path::test``), and other
        # collection selectors as positional arguments.  Do not require the
        # path to exist here: pytest should own the resulting diagnostics,
        # while the runner must still avoid appending defaults beside an
        # explicit selector.
        return True
    return False


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.diagnose:
        print(json.dumps(_diagnostic(args.profile), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if _pytest_version() is not None else 2

    version = _pytest_version()
    if version is None:
        print(
            "HeteroCoord test runner: pytest is not installed in "
            f"{sys.executable}. Install the project test dependency with "
            "python -m pip install -r requirements-dev.txt, or invoke this "
            "tool with the env_isaaclab interpreter.",
            file=sys.stderr,
        )
        return 2

    forwarded = list(args.pytest_args)
    explicit_target = _has_explicit_target(forwarded)
    if args.profile == "ros" and not explicit_target:
        print("--profile ros requires explicit ROS/launch_testing test paths", file=sys.stderr)
        return 2
    if explicit_target:
        targets = forwarded
    else:
        # Keep caller options (``-q``, ``-k``...) while constraining collection
        # to project-owned default roots.  Never let pytest infer the repo root.
        targets = [*forwarded, *DEFAULT_TARGETS[args.profile]]

    env = _environment(args.profile)
    command = [sys.executable, "-m", "pytest", *targets]
    print(f"HeteroCoord test runner: profile={args.profile} python={sys.executable} pytest={version}")
    if args.profile in {"contract", "isaac"}:
        print("pytest plugin policy: third-party autoload disabled")
    else:
        print("pytest plugin policy: ROS/launch_testing autoload enabled")
    return subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
