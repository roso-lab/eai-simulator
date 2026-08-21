# Tools Directory Organization Design

**Date:** 2026-08-21
**Status:** Approved for implementation planning

## Objective

Organize the tracked root `tools/` files by responsibility, add a single
directory guide, and make every maintained command discoverable without
changing tool behavior. Existing command paths move directly to their new
locations; no compatibility wrappers remain at the `tools/` root.

The change must preserve unrelated tracked and untracked work. In particular,
the untracked asset-library showcase files are not part of this change and must
not be moved, staged, or deleted.

## Audit Result

There is no tracked file that is currently safe to delete:

- the ROS2 clients and their tests are the only maintained public copies;
- the validation scripts provide regression coverage not duplicated by the
  tracked pytest suite;
- the USD repair script is the repository's only B2/Lite3 canonical repair and
  check entry point;
- setup scripts are public commands or sourced runtime dependencies;
- `human_assets/` and `github_oauth_worker/` are already coherent components.

This refactor therefore moves and documents files but does not delete tracked
functionality or tests. Converting standalone validation scripts into pytest
modules, or retiring the USD repair workflow after provider ownership is
confirmed, requires a separate behavior-changing change.

## Target Tracked Layout

```text
tools/
  README.md
  assets/
    repair_env_diy_usd.py
  github_oauth_worker/
    ...
  human_assets/
    ...
  ros2/
    send_cmd_vel.py
    send_manipulator_command.py
    vis_sensors.py
    tests/
      test_send_cmd_vel.py
      test_send_manipulator_command.py
      test_vis_sensors.py
  setup/
    configure_inotify_limits.sh
    install_packages.sh
    ros_distro.sh
    setup-git-hooks.sh
  validation/
    check_asset_download_errors.py
    check_env_diy_exclusivity.py
    check_env_diy_runtime.mjs
    check_ros_distro_config.py
```

`human_assets/` and `github_oauth_worker/` keep their existing internal
layouts. The new hierarchy is deliberately shallow: one responsibility per
directory, with tests nested only where they directly exercise the ROS2 public
clients.

## Path Migration Contract

All tracked callers and examples switch to the new paths in the same change.
This includes:

- repository setup commands in `README.md`, `CONTRIBUTING.md`, their Chinese
  counterparts, and the Sphinx documentation;
- `simulator.py` and troubleshooting text that locate the inotify helper;
- `algorithm/nav2/run_nav2.sh` and
  `algorithm/multi_robot_navigation/build_native.sh`, which source the ROS
  distribution helper;
- sensor, interface, mobile-base, and manipulator documentation that invokes
  the ROS2 clients;
- `AGENTS.md` architecture, workflow, verification, and source-of-truth paths;
- `.gitignore` exceptions for maintained tests;
- relative repository-root discovery and usage text inside moved scripts.

Tracked references to the old paths must be absent after migration. The move
does not rename CLI flags, topics, environment variables, ROS node names,
output formats, or exit-code behavior.

## Tools README

`tools/README.md` is the public directory index. It will contain:

- a responsibility table for every immediate subdirectory;
- the supported command path for each public entry point;
- runtime boundaries for ordinary Python, system ROS2 Python, Node.js, and
  Isaac Sim/OpenUSD tools;
- side-effect warnings for package installation, Git hook setup, inotify
  configuration, asset repair, OAuth deployment, and human-asset authoring;
- focused validation commands and a reminder that generated assets, caches,
  downloads, and test output are not maintained source.

The README describes and routes to existing component documentation rather
than duplicating detailed command manuals.

## Failure Handling And Compatibility

Moving a script one directory deeper changes repository-root calculations and
relative imports. Each moved file must derive the same repository root as
before, and sourced shell libraries must use the new absolute path from their
callers. Tests under `tools/ros2/tests/` must load their sibling client from the
parent directory instead of assuming the implementation is in the test file's
own directory.

No wrapper is added for an old command. A stale invocation should fail with a
missing-file error, making incomplete documentation or automation updates
visible during validation.

## Verification

The implementation must run lightweight checks that do not start Isaac Sim or
a live ROS graph:

- verify the Git index contains the intended layout and no old tracked paths;
- scan tracked source and documentation for stale command references;
- run the three ROS2 client pytest modules with ambient plugin autoload and
  repository cache writes disabled;
- execute the four standalone validation scripts, including the Node.js Env
  DIY check;
- parse or compile moved Python entry points and run `bash -n` on moved and
  affected shell scripts;
- run the maintained tracked lightweight test inventory because setup and
  validation paths are repository-wide contracts;
- build the Sphinx documentation in strict mode;
- run `git diff --check` and inspect final status to confirm unrelated work was
  neither staged nor changed.

Live ROS2, Isaac Sim, GPU, provider download, OAuth deployment, privileged
inotify mutation, and destructive USD repair are outside automated validation.
Their commands are documented but must not be invoked as part of this path-only
refactor.

## Out Of Scope

- deleting tracked tests or validation coverage;
- changing ROS2 client behavior or fixing independent runtime findings;
- moving or committing `tools/asset_library_showcase.py` and
  `tools/test_asset_library_showcase.py`;
- modifying provider-managed USD or controller assets;
- changing the internal structure of `human_assets/` or
  `github_oauth_worker/`;
- introducing a Python package API for `tools/`.
