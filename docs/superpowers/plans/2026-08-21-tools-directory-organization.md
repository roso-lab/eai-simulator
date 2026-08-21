# Tools Directory Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Reorganize every tracked root tools entry point by responsibility, add tools/README.md, and migrate maintained callers without changing behavior or staging unrelated local work.

**Architecture:** Keep tools as executable boundaries rather than a Python package. Use shallow setup, validation, ros2, and assets directories; keep ROS2 tests beside their clients; preserve the existing human_assets and github_oauth_worker components.

**Tech Stack:** Bash, Python 3, pytest, Node.js ES modules, Sphinx/MyST, Git.

---

## File Map

Create:

- tools/README.md: directory index, runtime boundaries, side effects, and validation commands.

Move and adjust:

- tools/install_packages.sh, setup-git-hooks.sh, configure_inotify_limits.sh, and ros_distro.sh to tools/setup/.
- the four root check scripts to tools/validation/.
- vis_sensors.py, send_cmd_vel.py, and send_manipulator_command.py to tools/ros2/.
- their three tests to tools/ros2/tests/.
- repair_env_diy_usd.py to tools/assets/.

Modify callers and docs:

- .gitignore, simulator.py, algorithm/nav2/run_nav2.sh, and algorithm/multi_robot_navigation/build_native.sh.
- README.md, CONTRIBUTING.md, Chinese mirrors, algorithm/README.md, affected docs/source pages, and AGENTS.md.

Protected and untouched:

- tools/asset_library_showcase.py and tools/test_asset_library_showcase.py.
- every unrelated tracked or untracked path present in the initial git status.

### Task 1: Move ROS2 Clients And Tests

**Files:**

- Move: tools/vis_sensors.py -> tools/ros2/vis_sensors.py
- Move: tools/send_cmd_vel.py -> tools/ros2/send_cmd_vel.py
- Move: tools/send_manipulator_command.py -> tools/ros2/send_manipulator_command.py
- Move: tools/test_vis_sensors.py -> tools/ros2/tests/test_vis_sensors.py
- Move: tools/test_send_cmd_vel.py -> tools/ros2/tests/test_send_cmd_vel.py
- Move: tools/test_send_manipulator_command.py -> tools/ros2/tests/test_send_manipulator_command.py
- Modify: .gitignore

- [ ] **Step 1: Move tests first and point them at the intended parent**

Use git mv, then make each test load its client with the corresponding filename:

    SCRIPT_PATH = Path(__file__).resolve().parents[1] / "vis_sensors.py"

- [ ] **Step 2: Verify tests fail before clients exist**

Run:

    PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --rootdir="$PWD" -q -p no:cacheprovider tools/ros2/tests/test_vis_sensors.py tools/ros2/tests/test_send_cmd_vel.py tools/ros2/tests/test_send_manipulator_command.py

Expected: collection fails with FileNotFoundError for tools/ros2 client paths.

- [ ] **Step 3: Move the implementations**

Use git mv. Do not change CLI flags, topics, node names, messages, return codes, or cleanup behavior.

- [ ] **Step 4: Update test exceptions**

Replace the three old exceptions with:

    !/tools/ros2/
    !/tools/ros2/tests/
    !/tools/ros2/tests/test_vis_sensors.py
    !/tools/ros2/tests/test_send_manipulator_command.py
    !/tools/ros2/tests/test_send_cmd_vel.py

Keep the showcase exception unchanged.

- [ ] **Step 5: Run the Step 2 command again**

Expected: all existing ROS2 client tests pass without a live ROS graph.

### Task 2: Move Setup And Host Scripts

**Files:**

- Move: tools/install_packages.sh -> tools/setup/install_packages.sh
- Move: tools/setup-git-hooks.sh -> tools/setup/setup-git-hooks.sh
- Move: tools/configure_inotify_limits.sh -> tools/setup/configure_inotify_limits.sh
- Move: tools/ros_distro.sh -> tools/setup/ros_distro.sh
- Modify: simulator.py
- Modify: algorithm/nav2/run_nav2.sh
- Modify: algorithm/multi_robot_navigation/build_native.sh

- [ ] **Step 1: Move the four tracked setup files**

Create tools/setup and use git mv.

- [ ] **Step 2: Repair repository-root and sibling resolution**

In install_packages.sh use:

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
    SOURCE_DIR="${PROJECT_ROOT}/source"
    # shellcheck source=tools/setup/ros_distro.sh
    source "${SCRIPT_DIR}/ros_distro.sh"

Change every installer help example to ./tools/setup/install_packages.sh.

In setup-git-hooks.sh use:

    PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

Make both algorithm scripts source tools/setup/ros_distro.sh. Update the simulator warning and generated inotify comment to tools/setup/configure_inotify_limits.sh.

- [ ] **Step 3: Verify safe setup paths**

Run:

    bash -n tools/setup/*.sh algorithm/nav2/run_nav2.sh algorithm/multi_robot_navigation/build_native.sh
    tools/setup/install_packages.sh --help | rg -F './tools/setup/install_packages.sh'
    tools/setup/configure_inotify_limits.sh --dry-run | rg -F 'Managed by eai-simulator tools/setup/configure_inotify_limits.sh'
    bash -c 'source tools/setup/ros_distro.sh; declare -F eai_resolve_ros_distro >/dev/null'

Expected: every command exits zero. Do not run setup-git-hooks.sh or non-dry-run host mutation.

### Task 3: Move Validation And Asset Scripts

**Files:**

- Move: tools/check_asset_download_errors.py -> tools/validation/check_asset_download_errors.py
- Move: tools/check_env_diy_exclusivity.py -> tools/validation/check_env_diy_exclusivity.py
- Move: tools/check_env_diy_runtime.mjs -> tools/validation/check_env_diy_runtime.mjs
- Move: tools/check_ros_distro_config.py -> tools/validation/check_ros_distro_config.py
- Move: tools/repair_env_diy_usd.py -> tools/assets/repair_env_diy_usd.py

- [ ] **Step 1: Move the five files**

Create tools/validation and tools/assets, then use git mv.

- [ ] **Step 2: Demonstrate the stale root calculation**

Run:

    PYTHONDONTWRITEBYTECODE=1 python tools/validation/check_ros_distro_config.py

Expected: import fails because the old parents[1] resolves to tools.

- [ ] **Step 3: Repair moved paths**

Use in all moved Python validators:

    REPO_ROOT = Path(__file__).resolve().parents[2]

Use in the Node validator:

    const repositoryRoot = path.resolve(scriptDirectory, "../..");
    console.error("Usage: node tools/validation/check_env_diy_runtime.mjs all");

Use in the USD repair tool:

    root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()

- [ ] **Step 4: Run all safe validation entry points**

Run:

    PYTHONDONTWRITEBYTECODE=1 python tools/validation/check_ros_distro_config.py
    PYTHONDONTWRITEBYTECODE=1 python tools/validation/check_asset_download_errors.py
    PYTHONDONTWRITEBYTECODE=1 python tools/validation/check_env_diy_exclusivity.py
    node tools/validation/check_env_diy_runtime.mjs all
    PYTHONDONTWRITEBYTECODE=1 python tools/assets/repair_env_diy_usd.py --help >/dev/null

Expected: validators print PASS, Node exits zero, and help does not import pxr or write USD.

### Task 4: Add README And Migrate References

**Files:**

- Create: tools/README.md
- Modify: every tracked caller/doc listed in the File Map

- [ ] **Step 1: Record stale references before documentation changes**

Run:

    git grep -n -E 'tools/(install_packages|setup-git-hooks|configure_inotify_limits|ros_distro|check_asset_download_errors|check_env_diy_exclusivity|check_env_diy_runtime|check_ros_distro_config|repair_env_diy_usd|vis_sensors|send_cmd_vel|send_manipulator_command|test_vis_sensors|test_send_cmd_vel|test_send_manipulator_command)' -- ':!docs/superpowers/**'

Expected: old maintained paths remain in source and docs.

- [ ] **Step 2: Add tools/README.md**

The README must include this directory table:

    | Path | Responsibility | Runtime |
    | --- | --- | --- |
    | setup/ | Installation, Git hooks, ROS distro selection, and inotify configuration | Bash/system tools |
    | validation/ | Lightweight regression checks | Python 3 or Node.js |
    | ros2/ | External sensor, mobile-base, and manipulator clients | System ROS2 Python |
    | assets/ | Targeted USD maintenance | Isaac Sim/OpenUSD Python |
    | human_assets/ | Human asset conversion, authoring, cache, validation, and demos | Component README |
    | github_oauth_worker/ | OAuth worker, config, and tests | Node.js/Wrangler |

Also include every public command path, runtime boundaries, side-effect warnings, safe validation commands, and links to component READMEs. State that generated assets, caches, downloads, and outputs are not maintained source.

- [ ] **Step 3: Apply exact path mappings**

Apply:

    tools/install_packages.sh -> tools/setup/install_packages.sh
    tools/setup-git-hooks.sh -> tools/setup/setup-git-hooks.sh
    tools/configure_inotify_limits.sh -> tools/setup/configure_inotify_limits.sh
    tools/ros_distro.sh -> tools/setup/ros_distro.sh
    tools/check_asset_download_errors.py -> tools/validation/check_asset_download_errors.py
    tools/check_env_diy_exclusivity.py -> tools/validation/check_env_diy_exclusivity.py
    tools/check_env_diy_runtime.mjs -> tools/validation/check_env_diy_runtime.mjs
    tools/check_ros_distro_config.py -> tools/validation/check_ros_distro_config.py
    tools/repair_env_diy_usd.py -> tools/assets/repair_env_diy_usd.py
    tools/vis_sensors.py -> tools/ros2/vis_sensors.py
    tools/send_cmd_vel.py -> tools/ros2/send_cmd_vel.py
    tools/send_manipulator_command.py -> tools/ros2/send_manipulator_command.py
    tools/test_vis_sensors.py -> tools/ros2/tests/test_vis_sensors.py
    tools/test_send_cmd_vel.py -> tools/ros2/tests/test_send_cmd_vel.py
    tools/test_send_manipulator_command.py -> tools/ros2/tests/test_send_manipulator_command.py

Exclude historical docs/superpowers files and protected showcase paths.

- [ ] **Step 4: Prove old paths are gone**

Run Step 1 again.

Expected: no output outside docs/superpowers.

### Task 5: Repository-Wide Verification And Delivery

**Files:**

- Verify all planned changes.
- Preserve all pre-existing unrelated worktree changes.

- [ ] **Step 1: Parse source**

Run:

    PYTHONDONTWRITEBYTECODE=1 python - <<'PY'
    import ast
    from pathlib import Path
    paths = [
        Path("simulator.py"),
        *sorted(Path("tools/ros2").glob("*.py")),
        *sorted(Path("tools/ros2/tests").glob("test_*.py")),
        *sorted(Path("tools/validation").glob("*.py")),
        Path("tools/assets/repair_env_diy_usd.py"),
    ]
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    print(f"parsed {len(paths)} Python files")
    PY
    bash -n tools/setup/*.sh algorithm/nav2/run_nav2.sh algorithm/multi_robot_navigation/build_native.sh
    node --check tools/validation/check_env_diy_runtime.mjs

Expected: all parsers exit zero.

- [ ] **Step 2: Run focused behavior checks**

Repeat all passing commands from Tasks 1-3.

- [ ] **Step 3: Run tracked lightweight baseline**

Build the pytest argument list only from git ls-files basenames matching test_*.py or *_test.py. Exclude the two documented defect modules and deselect the three installed-pack checksum parameters exactly as specified in AGENTS.md section 13. Set:

    PYTHONDONTWRITEBYTECODE=1
    PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs"
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

Run pytest with --rootdir="$PWD", -q, and -p no:cacheprovider. Expected: the maintained green baseline passes; record exact counts.

- [ ] **Step 4: Build strict documentation outside the repository**

Run:

    EAI_DOC_BUILD="$(mktemp -d)"
    python -m sphinx -E -a -W --keep-going -b html docs/source "$EAI_DOC_BUILD"

Expected: exit zero with no warnings.

- [ ] **Step 5: Review integrity**

Run:

    git diff --check
    git status --short
    git diff --name-status 199b72e4

Expected: only planned migrations, callers, docs, .gitignore, the README, design, and plan belong to this task. Existing local changes remain unstaged and unchanged.

- [ ] **Step 6: Commit**

Stage only explicit planned paths, inspect git diff --cached, and commit:

    git commit -m "#75 refactor: organize repository tools"

Do not stage showcase files, city-traffic work, provider assets/controllers, environment JSON changes, caches, or generated documentation.
