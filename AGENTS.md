# EAI Simulator Agent Development Guide

## 1. Purpose and Scope

This guide is the offline development entry point for first-time EAI Simulator developers and coding agents. Hosted documentation is optional supporting material, not a prerequisite for using this guide. `README.md` provides project and user orientation, while `CONTRIBUTING.md` owns contribution policy. When documentation and implementation disagree, source code, configuration, and tests are authoritative.

All repository paths in this guide are relative to the repository root. All commands assume the repository root as the working directory unless a section explicitly says otherwise.

"Offline guide" means that the development instructions live in the repository. It does not mean installation or asset use is network-free: dependency installation, gated assets, and large models can still require network access and external services.

## 2. Non-Negotiable Development Rules

- Run `git status --short` before editing. Preserve unrelated tracked and untracked work, including changes that overlap files you need to inspect.
- Inspect the relevant code, configuration, and tests before making assumptions about behavior.
- Use `rg` and `rg --files` for repository searches when available.
- Use structured parsers and serializers for structured data such as JSON, YAML, TOML, and USD metadata; do not rely on ad hoc text manipulation.
- Keep each change focused on the requested behavior. Avoid unrelated refactors and formatting churn.
- Use `apply_patch` for deliberate manual file edits.
- Never run destructive Git commands, including `git reset --hard` or `git checkout --`, without an explicit request that identifies the intended scope.
- Never commit secrets, credentials, private notes, runtime snapshots, caches, downloaded assets or weights, experiment outputs, or generated files that a repository workflow does not explicitly designate as tracked source or maintained fixtures. Preserve tracked generated environment JSON and other maintained fixtures when their workflow requires them.
- Prefer lightweight static and unit checks before checks that start Isaac Sim, require a GPU, load ROS2, download assets, or call external services.
- Report the commands actually run, their results, and any validation limitations. Do not imply that an unrun check passed.
- Update documentation in the same change whenever public behavior, configuration, or workflows change.

## 3. System Requirements and Supported Versions

### Core Simulator Environment

- Ubuntu 22.04 is the supported host platform.
- Isaac Sim 5.1 is the simulator baseline.
- Isaac Lab 2.x must be installed with its Conda environment named `env_isaaclab`.
- A CUDA-capable NVIDIA GPU is normally expected for simulator workflows. CPU execution may work only for paths that explicitly support it.

### Development Tooling

- Node.js 20 LTS or a newer LTS release is required for the tracked Env DIY and OAuth worker checks.

### ROS2 and Python Boundaries

ROS2 Humble is optional for the core simulator and required only for ROS2 or Nav2 workflows. ROS2 command-line tools and Python programs that import `rclpy` often need the system Python 3.10 supplied with ROS2 Humble rather than the Python interpreter in `env_isaaclab`. Keep the simulator and system ROS Python environments distinct unless a workflow explicitly integrates them.

### Animated Humans and PhysX

Selections containing animated humans force CPU PhysX. Isaac Sim 5.1 cannot safely perform the required animated pose writes with GPU PhysX, so a requested CUDA physics device is replaced with CPU for those selections.

Human rendering remains on the CUDA GPU, but the `UsdHumanStageRuntime` pose/retarget path is CPU work. Schedule it deliberately for large deployments:

- `UsdHumanStageRuntime.update(dt, *, context=None, actor_ids=None, animate_while_idle=False)` updates only the ids passed in `actor_ids`; unselected actors keep their clocks and pending events for later ticks.
- The default `animate_while_idle=False` freezes idle locomotion sampling for actors with no active action and a paused/finished path. This is intentional CPU saving, not a missing capability; pass `animate_while_idle=True` only where always-animate behavior is required.
- `HumanMotionController.update(dt, *, actor_ids=None, locomotion_actor_ids=None)` exposes the same controls at the controller layer; active actions still advance for processed actors.
- Do not regress the hot path by re-adding per-tick validation or recomputing plan-derived indices inside `retarget_pose`; runtime samples are validated at plan/cache build time and the cached hot-path helpers exist precisely for this cost profile.

### External Assets

The default gated Hugging Face dataset is `HuangQIjun/eai-simulator-assets`; large assets and model files from that dataset are not all stored in Git. Relevant workflows can require approved dataset access, Hugging Face authentication, network access, local disk capacity, and acceptance of the upstream asset or model terms. Asset resolution uses this dataset by default and reads `EAI_ASSETS_HF_REPO` only as an optional repository-ID override.

The runtime resolver defaults `EAI_ASSETS_HF_REVISION` to the provider's moving `main` revision on `HuangQIjun/eai-simulator-assets`, so provider-backed asset resolution uses the latest published assets by default. Reproducible or release workflows should explicitly set `EAI_ASSETS_HF_REVISION` to an immutable tag or commit.

## 4. First-Time Repository Setup

### Clone and Enter the Repository

```bash
git clone https://github.com/roso-lab/eai-simulator.git
cd eai-simulator
```

### Configure Repository Hooks

**Repository mutation and partial-failure risk.** Review section 16 before running this helper:

```bash
./tools/setup-git-hooks.sh
```

The script changes repository configuration by setting `core.hooksPath` to `.githooks`, makes the repository hooks executable, and thereby activates the current `post-checkout` warning. That warning prints destructive delete/recreate advice for nonconforming branch names; never copy that advice.

After running the helper, diagnose the resulting configuration without running hooks:

```bash
git config --get core.hooksPath
find .githooks -maxdepth 1 -type f -perm -u+x -print | sort
git lfs version
```

### Initialize and Activate Conda

Isaac Lab must already be installed and must provide the `env_isaaclab` environment. Initialize Conda for the current shell and activate that environment:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
```

The package installer invokes bare `pip`, so verify both the executable and module forms before installing repository packages:

```bash
command -v python
command -v pip
pip --version
python -m pip --version
```

`command -v pip`, `pip --version`, and `python -m pip --version` must all resolve to the intended `env_isaaclab`. Do not run the package installer if they identify different environments.

### Install Repository Packages

The standard installer installs the three repository packages in editable mode:

```bash
./tools/install_packages.sh
```

The current script also checks for the Ubuntu package `libxcb-cursor0`. If it is missing, the script runs `apt-get update` and `apt-get install -y libxcb-cursor0`, using `sudo` when the current user is not root. Review this system-level change before running the script in a controlled or shared environment.

When system packages are managed separately, or when bare `pip` cannot be verified as belonging to `env_isaaclab`, use these controlled editable installs instead:

```bash
python -m pip install -e source/EAI
python -m pip install -e source/EAI_assets
python -m pip install -e source/EAI_hmrs
```

This alternative does not install `libxcb-cursor0`; provision or verify that dependency separately when Qt UI workflows need it.

### Authenticate for Gated Assets

Request access to the gated `HuangQIjun/eai-simulator-assets` dataset before launching a workflow that needs it. Authenticate the Hugging Face CLI in the same user environment that will run the simulator:

```bash
hf auth login
```

Authentication does not download every asset in advance. Asset resolvers can fetch gated or large files later when a selected environment requires them.

### Run Lightweight Checks

These checks do not start Isaac Sim, use a GPU, load ROS2, or download assets:

```bash
git config --get core.hooksPath
bash -n tools/setup-git-hooks.sh tools/install_packages.sh
python -m pip show EAI EAI_assets EAI_hmrs
git status --short
```

The hook path should be `.githooks`, the shell syntax check should exit successfully, and `pip show` should report all three editable packages. Review `git status --short` rather than assuming setup left the worktree unchanged.

### Launch the First Simulator Environment

```bash
python simulator.py --env robo
```

`robo` is the required first full launch. It follows the moving provider `main` revision unless `EAI_ASSETS_HF_REVISION` explicitly pins an immutable tag or commit.

Remember that `robo` is not a minimal smoke test. Its environment selects ten robots and their controllers, making it a broad, resource-intensive Isaac Sim integration launch. The selection includes an animated human, so a requested CUDA physics device falls back to CPU PhysX; rendering and controller workloads can still require the configured CUDA GPU. The launch can also require network access for gated assets that are not already cached. Run it only after the simulator environment, package installs, Hugging Face access, display or headless configuration, and asset storage are ready.

## 5. Repository Architecture

```text
simulator.py                         launcher, CLI, lifecycle, runtime loop
source/
  EAI/EAI/                            core environment, controllers, ROS and interfaces
  EAI_assets/EAI_assets/              asset configuration and requirement resolution
  EAI_hmrs/EAI_hmrs/                  saved environments and dynamic environment builder
  EAI_env_diy/EAI_env_diy/            Isaac Kit Env DIY 3D extension
algorithm/                            optional EMOS, planning, keyboard, ROS/Nav2 programs
demo/fire_rescue/                     Fire Rescue integration demo
tools/                                setup, validation, conversion, and repair entry points
usd/                                  tracked manifests and UI thumbnails; downloaded USD is runtime data
tmp/                                  transient preflight, Env DIY, and interface-snapshot output
```

### Root Launcher And Repository Boundary

`simulator.py` is the executable boundary for the normal simulator, the interface-catalog subcommand, asset preflight, Env DIY hand-off, application launch, control loop, and shutdown. Its reusable API is `SimulatorLaunchConfig` plus `open_simulator_session(...)`; callers that need an EAI scene should use that context manager rather than construct a separate application lifecycle. It depends on the four packages under `source/` and imports Isaac Lab/Isaac Sim only after the relevant startup branch. The saved-environment branch reads the selected environment JSON; the interface fast path dispatches before Isaac imports and uses the input chosen by its subcommand, while in-memory DIY passes serialized selection data directly.

The repository root also owns package-install and setup helpers in `tools/`, optional algorithms in `algorithm/`, demos in `demo/`, and maintained USD metadata/thumbnails in `usd/`. `tmp/` is runtime output, not source input.

### EAI Core Environment And Controllers

`source/EAI/EAI/controllers/base.py` defines the `ControllerCfg` contract: controllers load resources, turn command tensors into actions, and apply actions. A controller entry may be a primary controller or `(primary, auxiliary...)`; auxiliary callables run after primary actions.

`source/EAI/EAI/hmrs_env/multi_robot_direct_env.py` implements `MultiRobotDirectEnv`, which owns the Isaac Lab `DirectMARLEnv` integration, first-reset controller loading, per-agent commands, observation dispatch, and action application. `source/EAI/EAI/hmrs_env/multi_robot_direct_env_cfg.py` derives agent names and initial spaces from the controller mapping. These modules depend on Isaac Lab and controller configurations generated by the builder; they do not choose a scene or register Gym tasks.

### Env DIY Shared Selection Vocabulary

`source/EAI/EAI/hmrs_env/env_diy/catalog.py` is the shared vocabulary and compatibility authority for selectable scene keys, `ROBOT_KEYS`, default controller names, attachments, tools, spawn-pose normalization, and attachment host compatibility. `flow.py` owns the immutable `InteractiveSelection` model, terminal selection flow, and selection serialization/parsing. `storage.py` owns saved JSON names, `source/EAI_hmrs/EAI_hmrs/envs/<name>.json` paths, and normalized JSON serialization.

The lightweight visual and terminal front ends depend on these pure modules. Do not infer selectable or compatible robots from asset files: `ROBOT_KEYS` and the catalog remain the shared selection contract.

### ROS Bridges And Interface Catalog

`source/EAI/EAI/hmrs_ros/` contains optional Isaac-side ROS2 bridges: `cmd_vel_bridge.py` and `twist_subscriber.py` provide `/<robot>/cmd_vel` input. `manipulator_omnigraph.py` provides the shared native ROS2 Bridge/OmniGraph manipulator manager; `ur5_omnigraph.py` contains UR5 model/topic definitions and a dedicated manager implementation; and `z1_omnigraph.py` exposes Z1 model/topic helpers and aliases for the shared manager. `simulator.py` imports the UR5 model specification and registers selected UR5 attachments with the shared manager, but it does not register selected Z1 attachments with `setup_robot(...)`.

`source/EAI/EAI/interface_catalog/` owns declared communication interfaces. YAML declarations under `interfaces/` are loaded and validated by `loader.py`; `query.py` resolves declarations against a selected scene; `snapshot.py` serializes a runtime view; and `cli.py` exposes the `interfaces` command. The catalog describes interfaces, while the ROS bridge and selected runtime decide whether an interface is actually active.

### Assets Package And Download Boundary

`source/EAI_assets/EAI_assets/robots/`, `scene/`, `sensor/`, and `humans/` define Isaac configuration objects and human runtime helpers. `asset_requirements.py` expands a serialized selection into scene, robot, payload, sensor, tool, and controller requirements. `asset_resolver.py` discovers paths referenced by a built configuration, verifies local availability, and downloads and installs missing selected files from the configured asset repository.

Asset configuration presence is not runnable registration. This package contains more robot and scene configuration files than the selection vocabulary and `ROBOT_OPTIONS` wire into the interactive builder. Runnable interactive scenes/robots are selected by `source/EAI_hmrs/EAI_hmrs/env_builder.py`, not by file discovery.

Controller Python files and model weights live below the resolver's controller root and can be downloaded or ignored by Git. `CONTROLLER_CFG_IMPORTS` in `env_builder.py`, not a controller directory listing, is the runtime name-to-file mapping; `controller_loader.py` lazily imports its selected file and triggers controller-asset resolution when needed.

### HMRS Saved Environments And Dynamic Builder

`source/EAI_hmrs/EAI_hmrs/envs/` contains tracked saved selections. `source/EAI_hmrs/EAI_hmrs/env_builder.py` owns `SCENE_OPTIONS`, `ROBOT_OPTIONS`, `CONTROLLER_CFG_IMPORTS`, attachment assembly, deterministic instance naming, and dynamic `InteractiveSceneCfg`/`MultiRobotDirectEnvCfg` class construction. It depends on the core selection model, asset config objects, Isaac Lab, and `controller_loader.py`.

`source/EAI_hmrs/EAI_hmrs/controller_loader.py` is the lazy module loader for controller attributes under the controller asset root. Its responsibility is loading selected controller code, not deciding which names are valid; that decision remains in `CONTROLLER_CFG_IMPORTS` and the shared Env DIY catalog.

### Env DIY 3D Extension

`source/EAI_env_diy/EAI_env_diy/` is the Kit extension for 3D authoring. `model.py` is a pure selection-state model; `ui.py` is the Kit window; `preview_stage.py` creates and replaces preview-stage objects; `placement.py` and `drop.py` handle viewport placement; `assets.py` provides threaded download coordination; and `protocol.py` carries completion/cancellation results. `extension.py` owns extension lifecycle and cleanup. `source/EAI_env_diy/config/extension.toml` declares the extension module and Kit dependencies.

The extension reuses the core catalog and selection parser, then returns a serialized selection to `simulator.py`. It previews an authoring stage; the launcher is responsible for replacing that stage before building the formal simulation.

### Stable Algorithms

Tracked reusable algorithm entry points are `algorithm/emos/` for scenario-driven multi-agent discussion/task allocation, `algorithm/global_planner/` for independent 2D planning and tracking, `algorithm/keyboard/keyboard.py` for ROS Twist input, and `algorithm/ros/` for ROS2/Nav2 launch, bridge, and diagnostic tooling. `algorithm/ros/tools/vis_sensors.py` visualizes ROS2 camera, depth, and point-cloud topics; non-8-bit images (for example RealSense depth `32FC1`, meters) can contain `inf`/`NaN` out-of-range pixels, so they are scaled for display by `SensorVisualizer._scale_to_uint8` (1st-99th percentile clip of finite values; non-finite pixels render black). `EMOSDiscussionManager` receives a caller-supplied scenario and Isaac Lab-compatible `base_env`; `build_from_agent_specs()` installs a position callback backed by `_get_robot_pos()`, which reads articulation or rigid-object state from that environment's scene. EMOS does not construct the simulator scene. The global planner is independent of Isaac Sim, EMOS, Torch, and ROS; adapters such as the Fire Rescue adapter convert poses and command tensors at the integration edge. Keyboard and Nav2 publish the same cmd_vel interface consumed by `EAI.hmrs_ros`.

### Demo Boundary

`demo/fire_rescue/` is the tracked Fire Rescue integration demo. `main.py` builds a `SimulatorLaunchConfig` and calls `open_simulator_session(...)`; `experiment.py` applies its demo-specific environment hook; `runtime/algorithm_adapter.py` bridges EAI state/commands to the global planner. The demo depends on the reusable launcher session API, its own map/configuration, and optional EMOS/planner dependencies. It does not create a Gym environment or its own Isaac application.

### Tools

`tools/` owns operational entry points for package installation, hook setup, Env DIY checks, OAuth worker tests, human asset authoring/import/validation, and USD repair. Each tracked script is the public command boundary for its own arguments, prerequisites, and side effects; inspect that script before invoking it rather than treating the directory as one uniform API. These tools depend on the relevant shell, Python, or Node.js runtime and, depending on the command, repository packages, system package managers, Git LFS, provider access, or external source assets.

### USD Assets

`usd/` owns maintained human-pack manifests/checksums and UI image assets used by catalog, packaging, and validation workflows. The tracked metadata and images are the repository boundary; `source/EAI_assets/EAI_assets/asset_resolver.py` owns resolution and installation of selected production assets. Production USD may instead be represented through Git LFS or supplied from the gated provider, so presence below `usd/` is not by itself a complete runnable-asset inventory. This boundary depends on asset configuration and requirement mappings, Git LFS for tracked large objects, and the configured provider revision for resolver-managed content.

### Runtime Data

`tmp/` holds generated preflight payloads, Env DIY results, session state, and interface snapshots such as `tmp/runtime_interfaces.json`. Public workflows reach these files through `simulator.py`, asset preflight, and Env DIY entry points; each producer owns its output schema, lifetime, and cleanup, while consumers must treat it as transient runtime state rather than a source authority. This data depends on the active selection and process/session lifecycle, is not maintained configuration, and must not be committed unless a workflow explicitly designates a fixture.

## 6. Runtime Data Flow

### Interface Catalog Fast Path

```text
python simulator.py interfaces <subcommand>
  -> _dispatch_interface_cli()
  -> EAI.interface_catalog.cli
  -> exit
```

`main()` dispatches this form before Isaac imports or `AppLauncher` creation. The command can list/query declarations, resolve a saved scene, inspect the runtime snapshot, or run a read-only probe without launching a simulator.

### CLI Resolution And Asset Preflight

For normal startup, `simulator.py` first parses its base arguments and resolves one of `saved-env` (`--env`), Env DIY (no `--env`), or `diy-3d` (`--diy-3d` or a no-argument choice). The normal saved-env and lightweight-DIY branches invoke `_run_asset_preflight()`, which starts a separate Python subprocess with `--preflight-output`.

The preflight subprocess resolves the request and, when it will run, starts a headless `AppLauncher`. Building the selected environment configuration lazily imports controller code: `controller_loader.py` can download a missing selected controller Python module during that build, and the worker can ensure a missing transitive `EAI_assets.controller` module and retry the build before producing its JSON payload. After a successful build, the worker collects USD paths and controller asset paths, including model weights, and closes the temporary application. The parent then reads the payload and separately ensures those collected paths. Network, authentication, or gated-access failures can therefore occur first inside the worker while resolving controller code and again in the parent while ensuring the collected assets. The worker's headless application is short-lived and is not the final simulator application.

### Saved JSON Environment Path

```text
--env=<name>
  -> source/EAI_hmrs/EAI_hmrs/envs/<name>.json
  -> storage.load_task() and normalization
  -> flow.interactive_selection_from_dict()
  -> env_builder.build_interactive_env_cfg_from_selection()
  -> MultiRobotDirectEnv
```

The name is supplied without `.json`. `storage.validate_task_name()` rejects the suffix and invalid filename characters, and `resolve_task_source()` rejects a missing file. The loader normalizes the serialized selection rather than treating a JSON filename as a Gym registration key.

### No-Argument Env DIY Path

With no `--env`, the preflight worker asks for a lightweight visual chooser, terminal chooser, or 3D authoring. The visual/terminal paths create an `InteractiveSelection`; either path can save the normalized selection through `storage.save_task()` under the environments directory and decide whether to run it. A run request uses `EAI-Interactive-v0` as the transient task name and feeds the in-memory selection through the same requirement and builder path as saved JSON.

### In-Process 3D Authoring Transition

`--diy-3d`, or the third no-argument choice, launches Env DIY 3D in the Kit process that will later run the formal scene. The launcher creates or reuses the application, records the in-process result callback plus initial-selection or restore-error context, and enables the extension through Kit's lifecycle. Kit then calls `EnvDiyExtension.on_startup()`, which consumes that context and constructs the `AuthoringModel`, `PreviewStage`, `AssetDownloadManager`, and window; the extension later delivers an `AuthoringResult` through the callback. Before formal environment creation, `simulator.py` stops the timeline, drains updates, creates a fresh USD stage, verifies that no old physics scene remains, and sets the requested physics device. The resulting `open_simulator_session()` reuses that same application instead of launching a second formal simulator process.

### Environment Assembly And Lazy Controller Loading

```text
serialized selection
  -> storage/flow InteractiveSelection
  -> env_builder: scene + named robots + attachments + controller entries
  -> dynamic InteractiveSceneCfg and MultiRobotDirectEnvCfg
  -> MultiRobotDirectEnv.reset()
  -> primary ControllerCfg.load() on first reset
```

The builder derives instance names by robot type and occurrence, creates mounted payloads/sensors, and stores one controller entry per robot. A named controller resolves through `CONTROLLER_CFG_IMPORTS`; `controller_loader.load_controller_attr()` imports that file lazily, ensuring its controller assets first if they are absent. Controller code/model directories are not a static package inventory or a registration mechanism.

### Reusable Session API

`open_simulator_session(SimulatorLaunchConfig)` skips preflight exactly when `resolved_env_name` is non-`None`; a caller using that skip must also supply matching `selection_data`, because dynamic environment loading rejects a missing selection. It then launches or reuses the application, builds the environment, resets it, configures graphs for selected UR5 attachments, and yields `SimulatorSession`. It does not perform the equivalent `setup_robot(...)` registration for selected Z1 attachments. On exit it closes the UR5-configured shared manipulator manager and environment, and closes the application only when the context manager created it. Demos use this boundary instead of duplicating launcher lifecycle logic.

### Control, Bridges, And Manipulators

The launcher allocates one command tensor per possible agent. Each frame it optionally reads active `ROS2CmdVelBridge` subscribers, updates velocity or goal commands, constructs per-agent external command tensors, and calls `env.step(actions)`. In `MultiRobotDirectEnv._pre_physics_step()`, each primary controller converts the command to an action; `_apply_action()` calls the controller's action function, then each auxiliary controller and any post-apply hook. `_setup_ur5_graph_manager()` registers selected UR5 attachments with the shared `ManipulatorOmniGraphManager` for manipulator commands. `z1_omnigraph.py` defines `Z1_MODEL_SPEC`, topic helpers, and aliases for that manager, but no tracked entry point registers the Z1 model with `setup_robot(...)`; selecting a Z1 attachment alone therefore does not activate an equivalent session graph. Cmd_vel bridges are enabled by selected `ros`/`keyboard` tools or `--enable-cmd-vel-bridge`.

### Runtime Interface Snapshot And Shutdown

After the final scene starts, the launcher resolves declared interfaces for the selection and writes `tmp/runtime_interfaces.json`, filtering cmd_vel entries to bridges that actually started. During the loop it refreshes the heartbeat and robot poses about every two seconds. On loop exit it removes only its own snapshot, closes the UR5-configured shared manipulator manager and cmd_vel bridges, then exits the session context. The session owns environment cleanup; it owns application cleanup only when it created the application.

## 7. Source-of-Truth Map

| Concern | Authoritative path(s) | Ownership and update warning |
| --- | --- | --- |
| CLI, startup modes, application lifecycle, main loop | `simulator.py` | Owns argument dispatch, preflight, AppLauncher ownership, Env DIY transition, bridges, snapshot lifetime, and shutdown. Update here when launcher behavior changes. |
| Reusable simulator session API | `simulator.py` | `SimulatorLaunchConfig`, `SimulatorSession`, and `open_simulator_session(...)` are the programmatic application/environment boundary. Keep demos on this API. |
| JSON filenames, storage, serialization, and schema behavior | `source/EAI/EAI/hmrs_env/env_diy/storage.py`; `source/EAI/EAI/hmrs_env/env_diy/flow.py`; `source/EAI_hmrs/EAI_hmrs/envs/` | Owns names without suffix, JSON location, normalization, and `InteractiveSelection` serialization. Change readers/writers together when changing the payload. |
| Shared selectable vocabulary and compatibility | `source/EAI/EAI/hmrs_env/env_diy/catalog.py` | Owns scenes, `ROBOT_KEYS`, default controller names, tools, attachments, payload limits, and host compatibility. Do not substitute asset-file discovery for this contract. |
| Runnable scenes and robots | `source/EAI_hmrs/EAI_hmrs/env_builder.py` | `SCENE_OPTIONS` and `ROBOT_OPTIONS` own the interactive builder wiring. A catalog entry or an assets config may still be visual-only or not runnable until wired here. |
| Controller name-to-file mapping and lazy loading | `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_hmrs/EAI_hmrs/controller_loader.py` | `CONTROLLER_CFG_IMPORTS` maps runtime names to controller attributes; the loader imports the chosen path lazily. Keep the mapping, requirement paths, and downloaded controller bundle compatible. |
| Controller contract and environment action dispatch | `source/EAI/EAI/controllers/base.py`; `source/EAI/EAI/hmrs_env/multi_robot_direct_env.py`; `source/EAI/EAI/hmrs_env/multi_robot_direct_env_cfg.py` | Owns primary/auxiliary controller semantics, first-reset resource loading, command-to-action conversion, and per-agent spaces. |
| Attachment, payload, and tool compatibility | `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py` | Catalog validates supported combinations; builder owns mount profiles, assets, sensor setup, and controller attachment wiring. Update both only when behavior actually changes. |
| Environment construction and instance naming | `source/EAI_hmrs/EAI_hmrs/env_builder.py` | Builds dynamic `InteractiveSceneCfg` and `MultiRobotDirectEnvCfg`, including `<robot_type>_<occurrence>` names. Consumers must not assume a separate Gym registry. |
| Requirement graph and asset download/install behavior | `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI_assets/EAI_assets/asset_resolver.py` | Resolves selected dependencies, inspects local paths, downloads staged files, and installs verified content. Preserve its selected-requirement and integrity behavior when adding assets. |
| Robot, scene, sensor, and human asset configurations | `source/EAI_assets/EAI_assets/robots/`; `source/EAI_assets/EAI_assets/scene/`; `source/EAI_assets/EAI_assets/sensor/`; `source/EAI_assets/EAI_assets/humans/` | Owns asset configuration objects and human runtime support. Presence here alone does not make an item selectable or runnable. |
| ROS cmd_vel and manipulator bridges | `source/EAI/EAI/hmrs_ros/`; `simulator.py` | Core package owns the shared and UR5-specific bridge/OmniGraph implementations plus UR5/Z1 model helpers. The launcher owns selection-driven cmd_vel setup and the current shared-manager UR5 graph registration/cleanup; it does not register `Z1_MODEL_SPEC`. Keep ROS-specific dependencies outside the core selection model. |
| Interface declarations, query, and runtime snapshots | `source/EAI/EAI/interface_catalog/interfaces/`; `source/EAI/EAI/interface_catalog/`; `tmp/runtime_interfaces.json` | YAML declares interfaces; catalog modules validate/query/resolve them; the JSON file is a transient runtime snapshot, not a source declaration. |
| Env DIY lightweight UI and 3D extension | `source/EAI/EAI/hmrs_env/env_diy/`; `source/EAI_env_diy/EAI_env_diy/`; `source/EAI_env_diy/config/extension.toml` | Core Env DIY modules own portable selection behavior; the extension owns Kit UI, preview, downloads, result protocol, and lifecycle declaration. |
| Stable algorithm entry points | `algorithm/emos/`; `algorithm/global_planner/`; `algorithm/keyboard/keyboard.py`; `algorithm/ros/` | EMOS, 2D planning, keyboard Twist publishing, and ROS/Nav2 tooling are optional clients/integrations. Keep planner and EMOS independent of simulator construction. |
| Fire Rescue demo | `demo/fire_rescue/main.py`; `demo/fire_rescue/experiment.py`; `demo/fire_rescue/runtime/` | Uses the reusable session API and adapts simulator state to demo algorithms. Do not turn its scenario-specific behavior into a core launcher default. |
| Setup, conversion, validation, and repair tools | `tools/` | Scripts are their own operational authority. Inspect current arguments and side effects before documenting or invoking one. |
| Maintained USD metadata and runtime/generated data | `usd/`; `tmp/`; `source/EAI_assets/EAI_assets/asset_resolver.py` | `usd/` keeps tracked manifests/thumbnails; resolver-managed production assets and `tmp/` output are runtime data. Do not commit resolver downloads or transient output by default. |
| Tests | `source/EAI/test/`; `source/EAI_assets/test/`; package-local test files discovered by `git ls-files` | Tests are authoritative behavioral evidence for their covered paths. Keep tests lightweight unless an Isaac/ROS integration boundary specifically requires otherwise. |

## 8. Common Development Workflows

**Provider-Backed Asset Handoff.**

Changes that add or replace resolver-managed USD, controller code, configuration, or weights are incomplete until those files are published to an asset-provider revision. Provider publication is maintainer-owned: the repository has no tracked upload command, and publication requires dataset write access. The handoff must name the repository ID, an immutable tag/revision, exact remote paths below `usd/` or `controller/`, file sizes and hashes, license/provenance, and the matching catalog, builder, requirement, or controller mappings. Publish or tag the intended immutable revision, update the runtime default when appropriate, and verify that exact revision from a clean checkout before declaring an asset-backed feature complete. Follow the provider publication and clean-root verification procedure in section 11; do not merge or release a source mapping that points only to a maintainer's local files.

`asset_resolver.py` defaults `EAI_ASSETS_HF_REVISION` to the moving `main` revision on `HuangQIjun/eai-simulator-assets`. Reading the guide and running checks explicitly described as lightweight or offline do not require provider/network access; asset-backed implementation and integration workflows can require it. The following checks are provider/network-dependent and use the latest provider revision by default. Reproducible checks and releases should explicitly select an immutable tag or commit.

First perform a non-mutating provider path check. Then, when an actual isolated download is appropriate, explicitly set `EAI_ASSETS_HF_REVISION=main` for clarity and use temporary roots so existing user assets are not overwritten. This matches the source default:

```bash
(
  set -eu
  EAI_ASSET_CANDIDATE_REVISION=main
  hf download HuangQIjun/eai-simulator-assets \
    --type dataset \
    --revision "$EAI_ASSET_CANDIDATE_REVISION" \
    --include usd/robot/carter/carter.usd \
    --include controller/traditional/carter_diff/carter_diff.py \
    --dry-run

  EAI_ASSET_CHECK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/eai-assets-check.XXXXXX")"
  case "$EAI_ASSET_CHECK_ROOT" in
    "${TMPDIR:-/tmp}"/eai-assets-check.*) ;;
    *) printf 'Unexpected temporary path: %s\n' "$EAI_ASSET_CHECK_ROOT" >&2; exit 1 ;;
  esac
  readonly EAI_ASSET_CHECK_ROOT
  cleanup_asset_check() {
    case "${EAI_ASSET_CHECK_ROOT:-}" in
      "${TMPDIR:-/tmp}"/eai-assets-check.*) rm -rf -- "$EAI_ASSET_CHECK_ROOT" ;;
      *) printf 'Refusing to remove unexpected path: %s\n' "${EAI_ASSET_CHECK_ROOT:-}" >&2; return 1 ;;
    esac
  }
  trap cleanup_asset_check EXIT
  trap 'exit 130' INT
  trap 'exit 129' HUP
  trap 'exit 143' TERM

  export EAI_ASSETS_HF_REPO=HuangQIjun/eai-simulator-assets
  export EAI_ASSETS_HF_REVISION="$EAI_ASSET_CANDIDATE_REVISION"
  export EAI_ASSETS_AUTO_DOWNLOAD=1
  export EAI_USD_ROOT="$EAI_ASSET_CHECK_ROOT/usd"
  export EAI_CONTROLLER_ROOT="$EAI_ASSET_CHECK_ROOT/controller"
  PYTHONPATH=source/EAI_assets python - <<'PY'
from EAI_assets.asset_resolver import (
    ensure_controller_assets_for_paths,
    ensure_usd_assets_for_paths,
)

ensure_usd_assets_for_paths(["robot/carter/carter.usd"])
ensure_controller_assets_for_paths(["traditional/carter_diff/carter_diff.py"])
PY
)
```

The subshell contains every export. Its cleanup trap validates the generated path prefix and removes only the exact directory returned by `mktemp -d`; it never targets an existing asset root.

### Modify or Add an Environment JSON

#### Goal

Create or update a saved selection that can be loaded with `python simulator.py --env <name>`; the CLI value is the case-sensitive filename stem under `source/EAI_hmrs/EAI_hmrs/envs/`.

#### Authoritative files

`source/EAI/EAI/hmrs_env/env_diy/storage.py` owns filenames and normalized persistence, `source/EAI/EAI/hmrs_env/env_diy/flow.py` owns parsing and selection serialization, `source/EAI/EAI/hmrs_env/env_diy/catalog.py` owns compatibility, and `source/EAI_hmrs/EAI_hmrs/env_builder.py` builds the runtime configuration. `source/EAI_assets/EAI_assets/asset_requirements.py` owns selected asset requirements.

#### Related registration/compatibility points

Use only scene, robot, attachment, tool, and controller names connected through the catalog, builder, and requirement maps. Saved JSON is not a Gym registration key. Runtime instances are named `<robot-type>_<occurrence-of-that-type>`, not by global list index. Do not copy `source/EAI_hmrs/EAI_hmrs/envs/123.json`: it currently contains attachment combinations rejected by shared compatibility validation.

#### Implementation steps

1. Choose a valid filename stem and start from a small tracked compatible selection such as `source/EAI_hmrs/EAI_hmrs/envs/keyboard.json`.
2. Edit through the Env DIY save path when possible; otherwise preserve the contract in section 9 and format JSON with a JSON serializer.
3. Load through `storage.load_task()`, then parse through `flow.interactive_selection_from_dict()`; do not invent a Gym registration.
4. Resolve the normalized payload through `asset_requirements.resolve_selection()` and review every required scene, robot, payload, sensor, tool, and controller entry.
5. Check that every attachment is compatible with its host and that at most one manipulator type is attached to a robot.

#### Minimum verification

Set `TASK_NAME` to the changed filename stem, then parse, normalize, resolve, and print the per-type instance names and requirement paths:

```bash
TASK_NAME=keyboard
python -m json.tool "source/EAI_hmrs/EAI_hmrs/envs/$TASK_NAME.json" >/dev/null
TASK_NAME="$TASK_NAME" PYTHONPATH=source/EAI:source/EAI_assets python - <<'PY'
import os
from collections import Counter

from EAI.hmrs_env.env_diy.flow import (
    interactive_selection_from_dict,
    interactive_selection_to_dict,
)
from EAI.hmrs_env.env_diy.storage import load_task
from EAI_assets.asset_requirements import resolve_selection

payload = load_task(os.environ["TASK_NAME"])
selection = interactive_selection_from_dict(payload)
normalized = interactive_selection_to_dict(selection)
graph = resolve_selection(normalized)
counts = Counter()
for robot in selection.robots:
    counts[robot.type] += 1
    print(f"instance={robot.type}_{counts[robot.type]}")
for requirement in graph.requirements:
    print(requirement.id, *requirement.remote_paths)
PY
```

#### Full integration verification

In a prepared Isaac environment with required assets already available or approved for download, launch the exact stem with `python simulator.py --env <name>` and verify spawn poses, controllers, attachments, and shutdown. This is an Isaac/GPU-or-CPU-PhysX/network-capable check, not a lightweight unit check.

#### Common omissions

Including `.json` in `--env`, changing `task_name` without renaming the file, relying on `visual.x/y` as a physical pose, using a globally numbered instance name, omitting a requirement-map entry, or treating a tracked but incompatible JSON file as a valid example.

### Add a Robot

#### Goal

Make a robot selectable, previewable, asset-resolvable, buildable, controllable, and accurately declared to interface consumers.

#### Authoritative files

Robot asset configuration lives in `source/EAI_assets/EAI_assets/robots/`; selection vocabulary is in `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; runtime wiring is in `source/EAI_hmrs/EAI_hmrs/env_builder.py`; asset paths are in `source/EAI_assets/EAI_assets/asset_requirements.py`; previews use `source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html`, `source/EAI_env_diy/EAI_env_diy/ui.py`, and `usd/picture/`.

#### Related registration/compatibility points

An asset cfg does not register a robot. Synchronize `ROBOT_KEYS`, `ROBOT_LABELS`, `_DEFAULT_CONTROLLER_CFG`, relevant attachment/tool host lists, builder imports and `ROBOT_OPTIONS`, `_ROBOT_PATHS`, controller maps, mount profiles, interface YAML `models` aliases, HTML/3D cards, thumbnails, and focused tests. Add manipulator or sensor host support only when a verified mount exists.

#### Implementation steps

1. Add or update the Isaac asset cfg with the correct USD path, prim layout, initial state, actuators, and physics properties.
2. Add the canonical lowercase key, public label, and default controller to the shared catalog.
3. Import the cfg in `env_builder.py` and add a `RobotOption` with default height and only verified mount links, offsets, rotations, and profiles.
4. Add the requirement entry points to `_ROBOT_PATHS`; add controller entries through the controller workflow and complete the provider-backed asset handoff at the start of this section.
5. Add the robot to compatible attachment/tool host sets, interface model aliases, lightweight HTML data, 3D UI exposure, and `usd/picture/processed/robot/` using the exact UI filename convention.
6. Add focused tests that compare the catalog, builder source, requirement graph, UI data, interfaces, and maintained example JSON where applicable.

#### Minimum verification

Use the changed key and its exact case-sensitive image stem. This checks the pure registries, requirement seed paths, builder source entry, HTML integrity, and tracked thumbnail without importing Isaac:

```bash
ROBOT_KEY=carter
ROBOT_IMAGE_STEM=carter
ROBOT_KEY="$ROBOT_KEY" PYTHONPATH=source/EAI:source/EAI_assets python - <<'PY'
import os
from EAI.hmrs_env.env_diy import catalog
from EAI_assets.asset_requirements import _ROBOT_PATHS

key = os.environ["ROBOT_KEY"]
assert key in catalog.robot_keys()
assert catalog.default_controller_cfg(key)
assert key in _ROBOT_PATHS
print(key, catalog.default_controller_cfg(key), _ROBOT_PATHS[key])
PY
rg -n -U 'RobotOption\([[:space:]]*"'"$ROBOT_KEY"'"' source/EAI_hmrs/EAI_hmrs/env_builder.py
test -e "usd/picture/processed/robot/$ROBOT_IMAGE_STEM.png"
node tools/check_env_diy_runtime.mjs all
```

The `rg` result is navigation/static-presence evidence only; it does not prove that `ROBOT_OPTIONS` builds or behaves correctly. Run any new pure registration tests by their exact path, and use the full builder integration check below for behavioral proof. Static checks must not import Isaac-dependent builder modules outside the intended environment.

#### Full integration verification

With Isaac and the selected assets available, author or load a one-robot environment, reset it, step its controller, inspect the prim and instance name, and verify each supported attachment. Exercise ROS interfaces only when the ROS2 bridge and system ROS environment are configured.

#### Common omissions

Adding only the asset cfg, forgetting a default controller or requirement path, copying an unverified mount from another robot, omitting the HTML duplicate or thumbnail, declaring an interface alias with no runtime bridge, or assuming file discovery updates `ROBOT_OPTIONS`.

### Add a Scene

#### Goal

Make a scene selectable, resolvable, previewable, and runnable with correct world and robot spawn transforms.

#### Authoritative files

`source/EAI/EAI/hmrs_env/env_diy/catalog.py` owns `SCENE_CHOICES`; scene cfg modules are under `source/EAI_assets/EAI_assets/scene/`; `source/EAI_hmrs/EAI_hmrs/env_builder.py` owns `SCENE_OPTIONS`; and `source/EAI_assets/EAI_assets/asset_requirements.py` owns `_SCENE_PATHS`.

#### Related registration/compatibility points

Keep the catalog key, scene cfg export, builder option, required files, preview card, lightweight HTML duplicate, `usd/picture/scene/<key>.png`, and focused tests synchronized. A scene module by itself is neither selectable nor runnable.

#### Implementation steps

1. Add the scene cfg with its actual root layer and any required sublayers or companion files.
2. Add the canonical key and label to `SCENE_CHOICES` and a matching `SceneOption` with the correct prim path, world offset, and robot spawn origin.
3. List the requirement entry points in `_SCENE_PATHS`, including separately addressed layers, and complete the provider-backed asset handoff at the start of this section.
4. Add the scene to lightweight and 3D UI exposure and supply the expected image.
5. Add focused tests for selection, requirement expansion, preview lookup, and transforms that can be checked without starting Isaac.

#### Minimum verification

Use the changed key to check the catalog, requirement paths, builder source entry, HTML integrity, and thumbnail without importing Isaac:

```bash
SCENE_KEY=plane
SCENE_KEY="$SCENE_KEY" PYTHONPATH=source/EAI:source/EAI_assets python - <<'PY'
import os
from EAI.hmrs_env.env_diy import catalog
from EAI_assets.asset_requirements import _SCENE_PATHS

key = os.environ["SCENE_KEY"]
assert key in dict(catalog.scene_choices())
assert key in _SCENE_PATHS
print(key, _SCENE_PATHS[key])
PY
rg -n -U 'SceneOption\([[:space:]]*"'"$SCENE_KEY"'"' source/EAI_hmrs/EAI_hmrs/env_builder.py
test -e "usd/picture/scene/$SCENE_KEY.png"
node tools/check_env_diy_runtime.mjs all
```

The `rg` result is navigation/static-presence evidence only; it does not prove scene construction or transforms. Parse changed JSON or YAML metadata with `python -m json.tool <path>` or the owning YAML loader, run newly added focused tests by their exact path, and use the full builder integration check below for behavioral proof.

#### Full integration verification

Launch a single compatible robot in the scene, verify the composed stage, collision and lighting behavior, world offset, robot spawn origin, rendering, and cleanup. This can require Isaac, a GPU, gated assets, and substantial memory.

#### Common omissions

Updating only `SCENE_CHOICES`, missing a sublayer in `_SCENE_PATHS`, using a preview transform that differs from the formal builder, forgetting the image/HTML duplicate, or testing an empty preview without a runnable robot.

### Add a Traditional Controller

#### Goal

Add a deterministic controller implementation that satisfies `ControllerCfg` and can be selected and loaded lazily from the controller asset root.

#### Authoritative files

`source/EAI/EAI/controllers/base.py` defines the callback and resource-loading contract. `source/EAI/EAI/hmrs_env/multi_robot_direct_env_cfg.py` owns initial space specifications and `source/EAI/EAI/hmrs_env/multi_robot_direct_env.py` updates them from loaded controllers and computed actions. `source/EAI/EAI/hmrs_env/env_diy/catalog.py` owns selectable cfg names/defaults, `source/EAI_hmrs/EAI_hmrs/env_builder.py` owns `CONTROLLER_CFG_IMPORTS`, `source/EAI_hmrs/EAI_hmrs/controller_loader.py` performs lazy loading, and `source/EAI_assets/EAI_assets/asset_requirements.py` supplies `_CONTROLLER_PATHS` requirement seeds.

#### Related registration/compatibility points

Controller source normally belongs to the resolver-managed, Git-ignored `source/EAI_assets/EAI_assets/controller/` tree and may be absent in a clean checkout. `CONTROLLER_CFG_IMPORTS` selects a cfg name's module and attribute; `_CONTROLLER_PATHS` seeds requirement/preflight resolution but is not an exhaustive inventory of every helper, model, or configuration file. The loader ensures the selected module when absent, the resolver downloads the corresponding `controller/<family>/<bundle>/**` pattern and known shared bundle dependencies, and post-build preflight traverses the cfg with `collect_controller_asset_paths()` before the parent ensures those paths. Synchronize the catalog name/default, import mapping, sufficient requirement seeds, bundle dependencies, provider contents, and tests. Directory presence is not registration.

#### Implementation steps

1. Implement `load(robot_name, task_name, device, env)` plus any `observation_func(env, robot_name)`, `compute_action_from_command_func(cfg, env, robot_name, command, controller_dict)`, and `apply_action_func(env, robot_name, action, controller_dict)` callbacks. Returned observations and actions must have leading `num_envs`; command conversion defaults to returning the command unchanged, and observation computation defaults to shape `(num_envs, 0)` when its callback is absent.
2. Publish the module and all imported helper files in the controller asset provider at the exact relative paths expected by the loader, following the provider-backed handoff at the start of this section.
3. Add the cfg name to the shared catalog, the lazy module/attribute mapping to `CONTROLLER_CFG_IMPORTS`, and enough module entry paths to `_CONTROLLER_PATHS` to seed the correct provider bundle. Add a shared bundle dependency in the resolver only when the selected bundle depends on another provider bundle.
4. Set or expose the controller only for compatible robots and update examples or public workflow documentation when behavior changes.
5. Add lightweight callback/mapping tests and a focused space-inference test. `ControllerCfg` does not declare command or action spaces; `MultiRobotDirectEnvCfg` starts with observation placeholders `100` or `0` and action placeholder `20`, then `MultiRobotDirectEnv` updates actual spaces after controller loading and, when necessary, the first computed action.

#### Minimum verification

Check the pure catalog/requirement seed and the static import mapping without downloading assets:

```bash
CONTROLLER_CFG=CARTER_DIFF_CFG
CONTROLLER_CFG="$CONTROLLER_CFG" PYTHONPATH=source/EAI:source/EAI_assets python - <<'PY'
import os
from EAI.hmrs_env.env_diy import catalog
from EAI_assets.asset_requirements import _CONTROLLER_PATHS

name = os.environ["CONTROLLER_CFG"]
assert name in catalog.controller_cfg_names()
assert name in _CONTROLLER_PATHS
print(name, _CONTROLLER_PATHS[name])
PY
rg -n "\"$CONTROLLER_CFG\"[[:space:]]*:" source/EAI_hmrs/EAI_hmrs/env_builder.py
```

The commands above are the current clean-checkout baseline; the dimension suite does not yet exist and must not be invoked as though it were maintained. A controller-space change must add `source/EAI/test/test_controller_space_inference.py`, covering callback defaults, placeholder values, inference priority, and first-action replacement. The broad `test_*.py` rule currently ignores that path, so add this negation after the last matching ignore rule in `.gitignore` as part of the same change:

```gitignore
!/source/EAI/test/test_controller_space_inference.py
```

Then verify that `git check-ignore` returns nonzero before running the exact suite:

```bash
CONTROLLER_SPACE_TEST=source/EAI/test/test_controller_space_inference.py
test -f "$CONTROLLER_SPACE_TEST"
if git check-ignore -q --no-index -- "$CONTROLLER_SPACE_TEST"; then
  printf 'Required test is still ignored: %s\n' "$CONTROLLER_SPACE_TEST" >&2
  exit 1
fi
python -m pytest -q "$CONTROLLER_SPACE_TEST"
```

After the bundle is available in the configured controller root, parse the current traditional-controller example exactly with:

```bash
CONTROLLER_BUNDLE=source/EAI_assets/EAI_assets/controller/traditional/carter_diff
test -d "$CONTROLLER_BUNDLE"
PYTHONDONTWRITEBYTECODE=1 python - "$CONTROLLER_BUNDLE" <<'PY'
import ast
import sys
from pathlib import Path

root = Path(sys.argv[1])
files = sorted(root.rglob("*.py"))
assert files, f"No Python files found under {root}"
for path in files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
```

Test missing-file diagnostics against an isolated `EAI_CONTROLLER_ROOT`; neither check should download production assets.

#### Full integration verification

In Isaac with the exact provider revision available, force a fresh lazy import and call `env.reset()` so `load()` runs. Inspect `base_env.cfg.observation_spaces`, `base_env.cfg.action_spaces`, `base_env.observation_spaces`, and `base_env.action_spaces`, then send a representative command and confirm the first returned action's final dimension matches the updated action space. Verify application on the intended device and cleanup.

#### Common omissions

Committing downloaded controller bundles, treating `_CONTROLLER_PATHS` as an exhaustive manifest, omitting a required shared bundle dependency, importing controllers eagerly, assuming placeholder spaces are actual dimensions, forgetting auxiliary-controller semantics, or claiming a clean checkout contains ignored controller files.

### Add an RL Controller or Model

#### Goal

Add an inference controller whose Python cfg, framework configuration, and model weights resolve as one version-compatible runtime unit.

#### Authoritative files

Use the same catalog, builder, loader, and requirement authorities as traditional controllers. RL runtime adapters are under `source/EAI/EAI/controllers/`, while provider-owned cfg code, framework configuration, and weights are referenced below the resolver controller root.

#### Related registration/compatibility points

Treat controller code, RL configuration, and weights as distinct provider artifacts. `_CONTROLLER_PATHS` must seed the correct provider bundle; after cfg construction, `collect_controller_asset_paths()` must discover the model/config fields actually opened at runtime so the parent preflight can ensure them. Preserve the implementation's framework boundary, such as RSL-style ONNX inference versus SKRL with Torch checkpoints and YAML configuration; do not label all RL controllers as interchangeable.

#### Implementation steps

1. Define observation ordering, normalization, command scaling, action post-processing, recurrent state, reset behavior, device, dtype, and expected tensor shapes.
2. Implement or select the correct runtime adapter and keep framework-specific imports lazy enough for supported startup paths.
3. Publish the cfg module, package initializers, framework config, weights, and transitive helpers in the controller asset provider, then complete the provider-backed handoff at the start of this section.
4. Synchronize `_CONTROLLER_CFG_NAMES`, robot defaults as applicable, `CONTROLLER_CFG_IMPORTS`, sufficient `_CONTROLLER_PATHS` seeds, known shared bundle dependencies, and cfg path fields discoverable by `collect_controller_asset_paths()`.
5. Record model provenance and compatibility outside secrets, then add deterministic tests for preprocessing/post-processing plus mapping and missing-weight failures.

#### Minimum verification

Check the pure catalog/requirement seed and static import mapping, then run deterministic preprocessing/post-processing tests against a maintained small fixture or mocked policy:

```bash
CONTROLLER_CFG=GO2_VELOCITY_RSL_CFG
CONTROLLER_CFG="$CONTROLLER_CFG" PYTHONPATH=source/EAI:source/EAI_assets python - <<'PY'
import os
from EAI.hmrs_env.env_diy import catalog
from EAI_assets.asset_requirements import _CONTROLLER_PATHS

name = os.environ["CONTROLLER_CFG"]
assert name in catalog.controller_cfg_names()
assert name in _CONTROLLER_PATHS
print(name, _CONTROLLER_PATHS[name])
PY
rg -n "\"$CONTROLLER_CFG\"[[:space:]]*:" source/EAI_hmrs/EAI_hmrs/env_builder.py
```

The mapping commands above are the current clean-checkout baseline. For an RL change, first add and unignore the required `source/EAI/test/test_controller_space_inference.py` suite as described in `Add a Traditional Controller`; do not invoke the absent/ignored path unconditionally. Verify the path and run it with:

```bash
CONTROLLER_SPACE_TEST=source/EAI/test/test_controller_space_inference.py
test -f "$CONTROLLER_SPACE_TEST"
if git check-ignore -q --no-index -- "$CONTROLLER_SPACE_TEST"; then
  printf 'Required test is still ignored: %s\n' "$CONTROLLER_SPACE_TEST" >&2
  exit 1
fi
python -m pytest -q "$CONTROLLER_SPACE_TEST"
```

The required suite must assert observation ordering, normalization, command scaling, reset behavior, tensor shapes, and post-reset space inference. Keep production weights out of this tier; verify collected cfg paths during the provider-backed integration check.

#### Full integration verification

Load the exact provider revision in Isaac, reset and inspect both cfg and environment observation/action spaces, then run representative commands long enough to observe stability and confirm the first action's final dimension. Verify CPU/GPU placement, framework versions, collected model/config paths, and failure messages for missing or incompatible weights.

#### Common omissions

Listing only the Python cfg, silently substituting a different framework, mismatching observation/action order, failing to reset recurrent state, storing large weights in Git, or reporting inference success after only a requirement-graph check.

### Add a Sensor

#### Goal

Add a physical sensor attachment that is host-compatible, asset-resolvable, correctly mounted, previewable, and connected to any declared runtime output.

#### Authoritative files

Sensor cfgs live under `source/EAI_assets/EAI_assets/sensor/`; catalog category and host compatibility live in `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; formal mount/spawn logic is in `source/EAI_hmrs/EAI_hmrs/env_builder.py`; requirements are in `_PAYLOAD_PATHS`; preview logic is in `source/EAI_env_diy/EAI_env_diy/preview_stage.py`; declarations are under `source/EAI/EAI/interface_catalog/interfaces/`.

#### Related registration/compatibility points

Synchronize the catalog entry, host list, category, asset cfg name, `_PAYLOAD_PATHS`, per-host builder mount fields and spawn branch, preview spawn branch, hardcoded terminal/3D/HTML exposure, image, interface YAML, package data, and required Kit/ROS runtime extension setup. A YAML declaration does not start a publisher.

#### Implementation steps

1. Add the sensor cfg and verify its USD prim, mount origin, physics behavior, namespaces, render products, and cleanup.
2. Add a sensor-category entry returned by `_attachment_entries()` with only verified hosts.
3. Add the provider entry paths to `_PAYLOAD_PATHS`, add per-host mount link, position, rotation, or specialized flags to `RobotOption`, and complete the provider-backed asset handoff at the start of this section.
4. Build the formal scene attributes and implement equivalent preview-stage behavior.
5. Add the terminal, 3D UI, HTML duplicate, and `usd/picture/processed/sensor/` image.
6. Add or update interface YAML only for real topics/methods and ensure the responsible runtime enables required extensions and publishers. Orsus uses independent gates: `orsus` plus `camera` enables its left/right image graphs through `OrsusCfg(enable_camera_publish=True)`, while `orsus` plus `ros` enables its point-cloud/odometry graph through `OrsusCfg(enable_ros_publish=True)` and declares the downstream scan capability. Iris, Pegasus, and CF2X always carry the built-in monocular camera, `Example_Rotary` RTX LiDAR, and base IMU/GPS/magnetometer/barometer models; `camera` enables only the image publishers, while `ros` enables only the LiDAR and base-sensor publishers. `keyboard` only enables cmd_vel bridge consideration. RealSense D455 uses the same independent gates: `realsense_d455` plus `camera` enables its RGB/depth/camera-info graphs through `RealSenseD455Cfg(enable_camera_publish=True)`, while `realsense_d455` plus `ros` enables only its IMU graph through `RealSenseD455Cfg(enable_imu_publish=True)`; the IMU payload is synthesized each env step by `EAI.hmrs_ros.realsense_d455_imu.RealSenseD455ImuManager` from robot root state rather than read from a physical IMU, and a MuSHR v2 carrying `realsense_d455` does not spawn the built-in monocular camera.

#### Minimum verification

Use a supported host to check catalog compatibility, requirement expansion, interface loading, and UI/image integrity without Isaac or ROS:

```bash
ATTACHMENT=orsus
HOST_ROBOT=carter
ATTACHMENT="$ATTACHMENT" HOST_ROBOT="$HOST_ROBOT" PYTHONPATH=source/EAI:source/EAI_assets python - <<'PY'
import os
from EAI.hmrs_env.env_diy import catalog
from EAI.interface_catalog.loader import load_catalog
from EAI_assets.asset_requirements import resolve_selection

attachment = os.environ["ATTACHMENT"]
host = os.environ["HOST_ROBOT"]
entry = catalog.attachment_entry(attachment)
assert entry.supports(host)
selection = {
    "scene_key": "plane",
    "robots": [{
        "type": host,
        "controller": {"mode": "default", "cfg": catalog.default_controller_cfg(host)},
        "attachments": [
            {"type": attachment},
            {"type": "camera"},
            {"type": "ros"},
        ],
    }],
}
graph = resolve_selection(selection)
assert any(item.id == f"sensor:{attachment}" for item in graph.requirements)
load_catalog().interface("ros.orsus.left_image")
print(*(item.id for item in graph.requirements), sep="\n")
PY
test -e "usd/picture/processed/sensor/$ATTACHMENT.png"
node tools/check_env_diy_runtime.mjs all
```

Add exact rejected-host and duplicate-normalization cases to the focused tests changed by the feature.

#### Full integration verification

In Isaac, attach the sensor to every declared host family, inspect mount transforms and prim validity, and start its real publisher. For Orsus, verify the independent gate matrix: `orsus` plus `camera` publishes only the left/right images; `orsus` plus `ros` publishes point cloud and odometry but no images; selecting all three publishes both groups, with `scan` still requiring the external conversion pipeline. For Iris, Pegasus, and CF2X, verify `camera`-only, `ros`-only, and combined selections: camera topics require `camera`, while LiDAR and base sensor topics require `ros`. Then inspect the runtime snapshot and run `python simulator.py interfaces status --probe` plus appropriate read-only probes in a configured ROS2 environment. A `orsus` plus `keyboard` selection leaves cmd_vel available but enables neither Orsus publisher group. For RealSense D455, verify the same camera/ros matrix: `camera` alone publishes rgb/depth/camera_info, `ros` alone publishes only the IMU topic (about 23 Hz in GUI mode), and no built-in monocular camera is spawned on MuSHR v2 hosts.

#### Common omissions

Adding a generic asset cfg without host mount data, forgetting a transitive USD, exposing a card unsupported by the terminal or HTML, declaring a topic that no runtime starts, missing cleanup for render products, or assuming a preview reference proves physics correctness.

### Add a Manipulator or Payload

#### Goal

Add a mounted physical payload as a validated host assembly, applying controller and runtime bridge work only when the payload has controlled behavior.

#### Authoritative files

All physical payloads use the catalog, asset cfg, `_PAYLOAD_PATHS`, builder mount/spawn, preview, UI, and image authorities described by the sensor workflow. Sensors follow `Add a Sensor`. Controlled manipulators additionally use `source/EAI_assets/EAI_assets/robots/ur5_mount.py`, `source/EAI_assets/EAI_assets/robots/z1_mount.py`, and shared mount helpers; controller tuple construction is in `source/EAI_hmrs/EAI_hmrs/env_builder.py`, launcher graph activation is in `simulator.py`, and manipulator bridge code is in `source/EAI/EAI/hmrs_ros/`.

#### Related registration/compatibility points

Treat payload as an umbrella. A passive payload needs an asset, validated per-host mount/spawn data, preview assembly, `_PAYLOAD_PATHS`, UI/images, and provider publication, but no controller tuple, `<instance>_arm`, OmniGraph registration, or interface declaration unless it has real runtime behavior. A sensor follows `Add a Sensor`. A controlled manipulator additionally needs a catalog controller, mount profiles, host articulation changes, `<instance>_arm`, a base-plus-auxiliary controller tuple, flow controller retention, launcher graph activation, and accurate interface YAML. Current main-session registration calls `setup_robot(...)` for UR5 only; Z1 model helpers and declarations are not equivalent runtime activation.

#### Implementation steps

1. Classify the change as a passive payload, sensor, or controlled manipulator and define verified mount/spawn data for every supported host.
2. For a passive payload, add only the asset cfg, catalog compatibility, `_PAYLOAD_PATHS`, formal and preview spawn, UI, image, and any actual runtime behavior it owns.
3. For a sensor, follow `Add a Sensor`, including publisher and interface verification.
4. For a controlled manipulator, define measured mount profiles and host articulation changes, add the controller cfg, enforce the one-manipulator rule, build `<instance>_arm` and the controller tuple, and preserve attachment controller data through flow/model serialization.
5. Complete the provider-backed handoff at the start of this section for all resolver-managed payload and controller files.
6. Add interface declarations and explicit launcher graph registration/cleanup only for behavior the runtime activates; do not infer activation from YAML or a model constant.

#### Minimum verification

For a controlled manipulator, use a supported host to verify catalog compatibility, payload/controller requirements, interface parsing, and UI integrity without importing Isaac:

```bash
ATTACHMENT=ur5
HOST_ROBOT=go2
ATTACHMENT="$ATTACHMENT" HOST_ROBOT="$HOST_ROBOT" PYTHONPATH=source/EAI:source/EAI_assets python - <<'PY'
import os
from EAI.hmrs_env.env_diy import catalog
from EAI.interface_catalog.loader import load_catalog
from EAI_assets.asset_requirements import resolve_selection

attachment = os.environ["ATTACHMENT"]
host = os.environ["HOST_ROBOT"]
entry = catalog.attachment_entry(attachment)
assert entry.supports(host) and entry.controller_cfg
graph = resolve_selection({
    "scene_key": "plane",
    "robots": [{
        "type": host,
        "controller": {"cfg": catalog.default_controller_cfg(host)},
        "attachments": [{"type": attachment, "controller": {"cfg": entry.controller_cfg}}],
    }],
})
ids = {item.id for item in graph.requirements}
assert f"payload:{attachment}" in ids
assert f"controller:{entry.controller_cfg}" in ids
load_catalog().interface("ros.ur5.joint_states")
print(*sorted(ids), sep="\n")
PY
node tools/check_env_diy_runtime.mjs all
```

Add exact supported-host, rejected-host, deduplication, round-trip, mount-profile, and builder cases to the focused tests changed by the feature. For a passive payload, run the same catalog/requirement/UI checks but do not assert an arm controller or graph interface.

#### Full integration verification

In Isaac, inspect each passive payload's mounted prim, transform, physics, and cleanup without requiring arm behavior. For a controlled manipulator, load each supported host, inspect the fixed assembly and arm articulation, reset both controllers, command the arm, verify native/ROS graph topics, and close the manager. For Z1, treat the missing main-session `setup_robot(...)` path as a known gap until it is implemented and verified.

#### Common omissions

Requiring arm behavior from a passive payload, allowing UR5 and Z1 together, defining only a visual mount, forgetting controlled-manipulator host articulation changes, losing the attachment controller during parsing, using a single controller instead of a base/auxiliary tuple, or mistaking inactive Z1 declarations for a live graph.

### Add a Camera, Keyboard, or ROS2 Tool

#### Goal

Add a non-physical tool selection that enables a concrete runtime consumer or external control workflow for compatible robots.

#### Authoritative files

`tool_catalog()` in `source/EAI/EAI/hmrs_env/env_diy/catalog.py` owns tool names and hosts. Terminal and lightweight selection live in `flow.py` and `env_diy_app.html`; 3D UI/model code is under `source/EAI_env_diy/`; launcher consumption is in `simulator.py`; keyboard publishing is in `algorithm/keyboard/keyboard.py`; ROS bridges are under `source/EAI/EAI/hmrs_ros/`.

#### Related registration/compatibility points

Tools are separate from `attachment_catalog()`. Synchronize host compatibility, hardcoded terminal/3D/HTML lists, `usd/picture/processed/tool/`, the actual runtime consumer, and optional interface declarations. The current tools are `camera`, `keyboard`, and `ros`. `keyboard` and `ros` both cause the launcher to consider that robot for a cmd_vel bridge; the external keyboard program publishes Twist. `camera` independently enables Orsus image graphs when the same robot has `orsus`, or the built-in monocular-camera publishers on Iris, Pegasus, and CF2X. `ros` enables the Orsus point-cloud/odometry graph and each aerial robot's RTX LiDAR and base-sensor publishers; the aerial sensor resources themselves exist without either tool. Declaration alone does not activate behavior.

#### Implementation steps

1. Define the tool entry and exact host set in `tool_catalog()` with category `tool`.
2. Add it to terminal, lightweight HTML, and 3D UI selection paths and supply the expected image.
3. Implement the launcher or algorithm-side behavior that consumes the selection, including extension/environment requirements and cleanup.
4. Add interfaces only for endpoints the runtime can actually activate; filter runtime snapshots when setup fails.
5. Add focused compatibility and selection-to-runtime tests without requiring ROS for pure logic.

#### Minimum verification

Check Camera/Keyboard/ROS tool compatibility and selection round-trip, parse the keyboard publisher, resolve the tracked keyboard scene interfaces, and validate the HTML without starting ROS2:

```bash
PYTHONPATH=source/EAI python - <<'PY'
from EAI.hmrs_env.env_diy import catalog
from EAI.hmrs_env.env_diy.flow import (
    interactive_selection_from_dict,
    interactive_selection_to_dict,
)

assert catalog.tool_catalog()["keyboard"].supports("carter")
assert catalog.tool_catalog()["camera"].supports("iris")
selection = interactive_selection_from_dict({
    "scene_key": "plane",
    "robots": [{"type": "carter", "attachments": [{"type": "keyboard"}]}],
})
assert interactive_selection_to_dict(selection)["robots"][0]["attachments"][0]["type"] == "keyboard"
PY
PYTHONDONTWRITEBYTECODE=1 python - algorithm/keyboard/keyboard.py <<'PY'
import ast
import sys
from pathlib import Path

path = Path(sys.argv[1])
ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
python simulator.py interfaces scene --env keyboard --json >/dev/null
node tools/check_env_diy_runtime.mjs all
```

#### Full integration verification

Launch a compatible saved environment, start the configured ROS2 bridge, run the keyboard or ROS publisher with system ROS Python, verify the exact `/<instance>/cmd_vel` endpoint, and confirm the runtime snapshot lists only bridges that started. For a `orsus` host, independently verify that `camera` enables only image topics and `ros` enables only point-cloud/odometry topics; `keyboard` enables neither sensor publisher group. For Iris, Pegasus, and CF2X, verify that `camera` enables monocular image/CameraInfo topics while `ros` enables LiDAR and, where supported, the base sensor topics.

#### Common omissions

Adding a card without a runtime consumer, confusing a tool with a physical payload, allowing an unsupported command shape, forgetting the external publisher, declaring a topic without bridge setup, or mixing ROS system Python with the Isaac Conda interpreter.

### Modify Env DIY

#### Goal

Change portable selection behavior and all affected lightweight/3D authoring front ends without splitting their vocabulary or result protocol.

#### Authoritative files

Start with shared `catalog.py`, `flow.py`, and `storage.py` under `source/EAI/EAI/hmrs_env/env_diy/`. The lightweight application is `env_diy_app.html` plus `webview_app.py`; the 3D extension uses `model.py`, `ui.py`, `preview_stage.py`, `placement.py`, `drop.py`, `assets.py`, `protocol.py`, `extension.py`, and `source/EAI_env_diy/config/extension.toml`.

#### Related registration/compatibility points

The HTML embeds duplicate scene/robot/payload/tool/controller data. Terminal steps and 3D UI cards/categories are also partly hardcoded. Keep builder wiring, asset requirements, images, package data, download status handling, in-process result semantics, and cleanup synchronized. `node tools/check_env_diy_runtime.mjs all` checks required/retired markup, duplicate IDs, local images, and inline JavaScript syntax; it does not compare embedded HTML catalog data with the Python catalog. There is no maintained structured-equality test at present.

#### Implementation steps

1. Change the pure shared catalog/flow/storage contract first and preserve normalization and compatibility behavior.
2. Update terminal prompts and selection transitions, then update the HTML duplicate and pywebview bridge payload.
3. Update the 3D authoring model, cards, preview assembly, placement/drop behavior, download manager, result protocol, extension lifecycle, and Kit dependency declaration as required.
4. Update builder/requirement maps, thumbnails, and `source/EAI/setup.py` package data for newly shipped files.
5. Add focused pure tests instead of relying only on visual acceptance. Put the missing structured HTML/Python catalog equality coverage in `source/EAI/test/test_env_diy_catalog_sync.py`, and add an explicit `.gitignore` negation after the last matching ignore rule so the test can be tracked.

#### Minimum verification

Run the maintained HTML checks, parse the portable Python modules, and round-trip a tracked selection without importing Isaac:

```bash
node tools/check_env_diy_runtime.mjs all
PYTHONDONTWRITEBYTECODE=1 python - \
  source/EAI/EAI/hmrs_env/env_diy \
  source/EAI_env_diy/EAI_env_diy <<'PY'
import ast
import sys
from pathlib import Path

for root_value in sys.argv[1:]:
    root = Path(root_value)
    files = sorted(root.rglob("*.py"))
    assert files, f"No Python files found under {root}"
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
PYTHONPATH=source/EAI python - <<'PY'
import json
from pathlib import Path
from EAI.hmrs_env.env_diy.flow import interactive_selection_from_dict, interactive_selection_to_dict

payload = json.loads(Path("source/EAI_hmrs/EAI_hmrs/envs/keyboard.json").read_text(encoding="utf-8"))
selection = interactive_selection_from_dict(payload)
assert interactive_selection_to_dict(selection)["scene_key"] == payload["scene_key"]
PY
```

The commands above are the current clean-checkout baseline. Because the Node checker does not establish catalog equality, an affected change must add the currently missing `source/EAI/test/test_env_diy_catalog_sync.py`. The broad `test_*.py` rule currently ignores it, so add this negation after the last matching ignore rule in `.gitignore`:

```gitignore
!/source/EAI/test/test_env_diy_catalog_sync.py
```

Then verify that `git check-ignore` returns nonzero before running the exact suite:

```bash
ENV_DIY_SYNC_TEST=source/EAI/test/test_env_diy_catalog_sync.py
test -f "$ENV_DIY_SYNC_TEST"
if git check-ignore -q --no-index -- "$ENV_DIY_SYNC_TEST"; then
  printf 'Required test is still ignored: %s\n' "$ENV_DIY_SYNC_TEST" >&2
  exit 1
fi
python -m pytest -q "$ENV_DIY_SYNC_TEST"
```

#### Full integration verification

Exercise the lightweight visual save/run path and then `python simulator.py --diy-3d` in a prepared Isaac environment. Verify downloads, preview replacement, viewport placement, save/run/cancel/error results, fresh formal stage creation, and extension cleanup.

#### Common omissions

Changing only the shared catalog while leaving HTML data stale, adding a 3D card without preview support, confusing preview pose with formal spawn pose, writing UI callbacks from the download thread, forgetting package data or extension dependencies, or claiming focused Python coverage that is not tracked.

### Modify Human Assets and Animation

#### Goal

Maintain the registry-driven human packs, actions, path following, and stage runtime.

#### Authoritative files

Registry-driven behavior lives in `source/EAI_assets/EAI_assets/humans/`; maintained metadata is `usd/human/manifest.json`, `manifest.schema.json`, `pack-checksums.json`, and `audit-summary.json`; authoring, demo, and maintenance entry points are under `tools/human_assets/`. `asset_placement.py` owns orientation, scale, and grounding from the current visible pose. Human actors use the dedicated stage runtime and unified demo outside the Env DIY robot and traditional controller catalogs.

#### Related registration/compatibility points

Large character, activity, motion, texture, cache, action, and custom-action files are provider-managed or ignored, but every runtime payload is installed below the active `usd/human/` root and every manifest runtime path remains relative to that root. Use `HumanAssetRegistry`, `HumanActionPublisher`, conversion/migration tools, validation, and cache builders rather than hand-editing generated registries. `tools/human_assets/migrate_assets.py` writes `manifest.json` and `audit-summary.json`; `tools/human_assets/convert_gltf_assets.py` writes conversion diagnostics. No tracked tool regenerates `usd/human/pack-checksums.json`, so checksum publication remains an external maintainer/provider release step. Animated human stage workflows use CPU PhysX on Isaac Sim 5.1 when pose writes require it.

For live runtime scheduling, `UsdHumanStageRuntime.update` and `HumanMotionController.update` accept `actor_ids` / `locomotion_actor_ids`, and `UsdHumanStageRuntime.update` defaults to `animate_while_idle=False`. Unselected actors keep their clocks and queued events for later ticks, and idle actors skip locomotion resampling; do not treat the unified demo's initially static idle humans as an animation regression, and pass `animate_while_idle=True` explicitly when old always-animate behavior is required. The retarget hot path relies on `_vector3_fast` and the cached plan-derived helpers (`_parent_indices_cached`, `_semantic_indices_cached`, `_unit_scales_cached`, `_rest_global_quats_cached`): keep all finite/hierarchy validation in plan and cache build/load paths, never in the per-tick sampling path, and preserve the non-mutating read invariant for cached `Gf.Quatd` objects.

#### Implementation steps

1. Decide whether the change affects registry assets, canonical motions, custom actions, retarget caches, path following, or stage runtime.
2. For registry content, run the appropriate conversion or migration tool and review licensing/redistribution fields. The migration interface is `python tools/human_assets/migrate_assets.py --source-root <approved-source-root> --target-root usd/human --dry-run`; omit `--dry-run` only after reviewing the plan. It regenerates the manifest and audit summary, not pack checksums.
3. Validate schema, skeleton signatures, motion compatibility, human-root-relative paths, current-pose grounding, and cache invalidation; do not commit external packs or caches. Give the provider maintainer the exact pack roots, sizes, hashes, license/provenance, and immutable revision needed to publish matching `pack-checksums.json`; newcomers cannot reproduce that release metadata with a tracked command.
4. Update resolver stable-pack behavior and tests only when the distribution contract changes.
5. For live animation changes, keep the manifest motion contract, retarget cache, facing offset, path policy, stage runtime, placement behavior, and `tools/human_assets/run_demo.py` synchronized.

#### Minimum verification

Use the unified demo for the complete human capability check. GUI mode loads all 44 actors and supports `Q` selection plus action numbers `1-12`:

```bash
python -u tools/human_assets/run_demo.py
```

For automated validation, run the same backend and control state machine headlessly:

```bash
conda run --no-capture-output -n env_isaaclab \
  python -u tools/human_assets/run_demo.py --headless
```

The expected final summary is `Verified unified human matrix: 39x12 + 4 + 1`. Both modes require Isaac Sim 5.1 and every manifest-referenced payload below the default `usd/` or configured `EAI_USD_ROOT`; missing payloads are failures, not skipped coverage.

#### Full integration verification

The headless command above verifies 39 x 12 skeletal actions, four rigid outbound-return movements, one static actor, animation samples, retarget cache use, path policies, current-pose grounding, bounds, exact position restoration, and cleanup with real packs. Use GUI mode to inspect visible facing, hand poses, props, and motion quality that cannot be established from numeric assertions alone.

#### Common omissions

Hand-editing generated manifest records, committing large packs/caches, adding absolute runtime paths, claiming the conversion/migration tools generate pack checksums, changing provider assets without matching external checksum publication, ignoring license fields or skeleton signatures, relying on bounds that ignore the current skinned pose, expecting GPU PhysX for animated humans, registering human actors as Env DIY robots/controllers, re-adding per-tick finite validation or per-call plan index computation in the retarget hot path, unconditionally resampling idle human actors, dropping deferred events or pending reground state for unselected `actor_ids`, or treating the default idle static pose as an animation regression.

### Add or Modify an Algorithm

#### Goal

Develop a reusable algorithm behind a pure contract and keep simulator, ROS, credentials, optional dependencies, and generated native output at explicit integration edges.

#### Authoritative files

Stable tracked algorithm areas are `algorithm/emos/`, `algorithm/global_planner/`, `algorithm/keyboard/keyboard.py`, and `algorithm/ros/`. Their code and tracked module READMEs own their local contracts. The only tracked city-traffic implementation is `algorithm/city_traffic/human_bridge.py`, with its focused test at `source/EAI/test/test_city_traffic_human_bridge.py`; there is no tracked `algorithm.city_traffic` package API.

#### Related registration/compatibility points

EMOS accepts a caller-supplied scenario and compatible `base_env`; it does not build the simulator. The global planner is pure Python with an optional generated C++ extension and should remain independent of Isaac, Torch, ROS, and EMOS. Adapters own pose/command conversion. Keep requirements, LLM credentials, ROS environment boundaries, and ignored generated `.so`/reports explicit. No dedicated tracked EMOS/global-planner/keyboard/ROS unit suites currently exist.

#### Implementation steps

1. Define or preserve a small input/output contract and keep environment-specific state access in an adapter or callback.
2. Add optional dependencies to the algorithm-local requirements file and load credentials from environment/configuration without committing them.
3. Keep EMOS scenarios caller-owned, planner poses/commands framework-neutral, keyboard output on the shared Twist contract, and ROS/Nav2 code outside core imports.
4. Treat `algorithm/global_planner/build_cpp.sh` output as generated; preserve the Python fallback.
5. Add focused pure tests for new reusable logic and an adapter test for every changed integration boundary.

#### Minimum verification

Parse the tracked city-traffic boundary without imports, bytecode writes, Isaac, ROS, or network access:

```bash
PYTHONDONTWRITEBYTECODE=1 python - algorithm/city_traffic/human_bridge.py <<'PY'
import ast
import sys
from pathlib import Path

path = Path(sys.argv[1])
ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
```

The focused tracked test is `source/EAI/test/test_city_traffic_human_bridge.py`; run it from a clean tracked package boundary because untracked `algorithm/city_traffic` package files can change Python import resolution. Run every new pure test added for the changed algorithm by its exact file path. For global-planner changes, exercise a deterministic maintained map through the Python fallback with `_planner_cpp` unavailable before testing the optional generated extension; do not claim a dedicated tracked suite where none exists.

#### Full integration verification

Run through the owning integration with configured dependencies: an EAI `base_env` for EMOS, a demo adapter for planning, system ROS2/Nav2 for ROS programs, and real LLM calls only with approved credentials and cost/network expectations.

#### Common omissions

Constructing a simulator inside EMOS, importing Isaac/Torch into the global planner, leaking credentials, committing native binaries or reports, bypassing coordinate/command adapters, treating `algorithm/city_traffic/human_bridge.py` as a tracked package API, or claiming test suites that do not exist.

### Add or Modify a Demo

#### Goal

Build a tracked end-to-end example that composes the reusable simulator session API with scenario-specific algorithms and presentation code.

#### Authoritative files

The stable tracked demo is `demo/fire_rescue/`: `main.py` owns CLI/session entry, `config.py` configuration, `scenario.py` EMOS scenario construction, `experiment.py` environment hooks and run setup, `runtime/` adapters and loop behavior, `dashboard/` presentation/server code, and `assets/` maintained map inputs.

#### Related registration/compatibility points

Use `SimulatorLaunchConfig` and `open_simulator_session(...)` for application/environment lifecycle. Keep scene hooks, algorithm adapters, map coordinates, robot names, dashboard state, optional dependencies, and cleanup in the demo boundary. Some Fire Rescue runtime branches import untracked `robot_nav` or `data_server` modules; do not claim those branches work in a clean checkout.

#### Implementation steps

1. Put CLI/defaults in `main.py`/`config.py`, scenario data in `scenario.py`, environment mutation in the cfg hook, and simulator-to-algorithm conversion in `runtime/` adapters.
2. Enter through `open_simulator_session()` and let its context own environment/application cleanup.
3. Keep demo assets small and maintained; resolve large simulator assets through the repository asset workflow.
4. Make optional dashboard, LLM, ROS, and external-algorithm branches fail clearly when their prerequisites are absent.
5. Add focused tests for pure config/scenario/adapter logic and document heavy prerequisites without duplicating launcher setup.

#### Minimum verification

Check the CLI boundary, parse the demo, and parse its map metadata without starting Isaac or making LLM calls:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m demo.fire_rescue.main --help >/dev/null
PYTHONDONTWRITEBYTECODE=1 python - demo/fire_rescue <<'PY'
import ast
import sys
from pathlib import Path

root = Path(sys.argv[1])
files = sorted(root.rglob("*.py"))
assert files, f"No Python files found under {root}"
for path in files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
python - <<'PY'
from pathlib import Path
import yaml

payload = yaml.safe_load(Path("demo/fire_rescue/assets/factory_map.yaml").read_text(encoding="utf-8"))
assert isinstance(payload, dict) and "image" in payload and "resolution" in payload
PY
```

Run each new pure config/scenario/adapter test by its exact file path.

#### Full integration verification

Launch the demo in the supported Isaac environment with its saved scene, assets, algorithms, and optional services; exercise normal completion, failure, and shutdown. Audit `robot_nav`/`data_server` branches and external credentials before including them in the claimed coverage.

#### Common omissions

Creating a second AppLauncher lifecycle, embedding generic behavior in the demo, bypassing the session context, hardcoding an untracked local module, committing runtime/dashboard output, omitting `--help`, or presenting optional branches as clean-checkout functionality.

### Modify the Interface Catalog

#### Goal

Keep searchable interface declarations accurate while preserving the distinction between declared capability, selection resolution, and live runtime activation.

#### Authoritative files

YAML declarations are under `source/EAI/EAI/interface_catalog/interfaces/`; `models.py` defines records, `loader.py` validates YAML, `query.py` searches/resolves selections, `snapshot.py` handles runtime state, `cli.py` exposes commands, and `probes.py` implements safe read-only probes. Actual bridges live in `source/EAI/EAI/hmrs_ros/` and `simulator.py`.

#### Related registration/compatibility points

Synchronize YAML model aliases/endpoints with real robot, sensor, tool, and manipulator names; synchronize launcher filtering and bridge setup; and include new YAML directories in `source/EAI/setup.py` package data. A declaration does not activate a bridge. Z1 declarations are currently visible although the main session does not register a Z1 graph.

#### Implementation steps

1. Add or edit YAML through a YAML serializer, satisfying required device/interface fields and globally unique IDs.
2. Use model aliases for catalog search only; implement the actual publisher/subscriber/OmniGraph setup in its owning runtime.
3. Add `requires_attachment`, read-only probe metadata, endpoint templates, examples, and descriptions that match observed runtime behavior.
4. Update snapshot filtering so failed or disabled runtime interfaces are not reported active.
5. Add loader/query/CLI/probe tests for new schema or resolution behavior and update package data for new subdirectories.

#### Minimum verification

Run the interface fast path and the owning structured loader; these commands dispatch before Isaac startup:

```bash
python simulator.py interfaces list --json >/dev/null
python simulator.py interfaces search --protocol ros2 --json >/dev/null
python simulator.py interfaces show ros.cmd_vel --json >/dev/null
python simulator.py interfaces scene --env keyboard --json >/dev/null
PYTHONPATH=source/EAI python - <<'PY'
from EAI.interface_catalog.loader import load_catalog

catalog = load_catalog()
ids = [interface.id for device in catalog.devices for interface in device.interfaces]
assert len(ids) == len(set(ids))
print(len(ids))
PY
```

Do not treat the static scene result as live status.

#### Full integration verification

Launch the relevant environment and bridge, then run `python simulator.py interfaces status --probe` and targeted `interfaces test` commands for read-only endpoints in the correct ROS/system environment. Compare the snapshot with actual topics or methods.

#### Common omissions

Adding YAML without runtime setup, omitting package data, probing a write interface, reporting a failed bridge as active, or assuming aliases change builder registration. `resolve_scene_interfaces()` currently falls back to `<type>_<global-index>` while the builder uses per-type occurrence; mixed-type saved selections can therefore resolve wrong instance names until this is corrected.

### Add or Update a User Documentation Page

#### Goal

Add or update an external-facing user documentation page under `docs/source/` and make it reachable from the hosted docs navigation.

#### Authoritative files

`docs/source/*.md` pages (Sphinx with `myst_parser` and the Furo theme, Chinese by default; English mirrors use `*_en.md`), `docs/source/index.rst` / `docs/source/index_en.rst` toctrees, `docs/source/_templates/sidebar/navigation.html` / `navigation_en.html`, `docs/source/conf.py`, and media under `docs/source/assets/media/`.

#### Related registration/compatibility points

The left sidebar navigation is NOT driven by the `index.rst` toctree: `_templates/sidebar/navigation.html` (Chinese) and `navigation_en.html` (English) hardcode every entry through the `nav_item(target, label)` macro, and each page is separately registered in the matching `index.rst` toctree. Updating only the toctree leaves the page unreachable from the sidebar, and updating only the template leaves the page missing from the document structure. Media is referenced as `assets/media/<file>.png` and Sphinx copies it into `_images/` at build time. The RealSense D455 topic is currently a single external-facing page at `docs/source/realsense_tutorial.md` (title `RealSense D455`), registered in the 开发与扩展 toctree and sidebar; its former orphan page `realsense_d455.md` was merged into it and removed.

#### Implementation steps

1. Write or update the page under `docs/source/` as a single user-facing page per topic; do not keep orphan pages (`orphan: true`) for topics that should be navigable.
2. Register the page in the matching toctree in `index.rst` (and `index_en.rst` when an English mirror exists) and add the matching `nav_item(...)` entry to `_templates/sidebar/navigation.html` (and `navigation_en.html`).
3. Commit any referenced images under `docs/source/assets/media/` as maintained fixtures with the page.
4. Keep the page external-facing: describe behavior and workflows for users, not internal fix history, refactors, or development summaries.

#### Minimum verification

Build the docs with the `env_isaaclab` environment (it provides Sphinx 9 and `myst_parser`), expect zero warnings, and confirm the page and its sidebar link exist in the output:

```bash
conda activate env_isaaclab
make -C docs clean && make -C docs html
grep -cE 'WARNING|ERROR' <(make -C docs html 2>&1) || true
grep -o 'href="<page>.html"' docs/build/html/index.html
```

#### Full integration verification

Serve `docs/build/html` (git-ignored build output) and open the page plus its sidebar link in a browser; verify images load and links between pages resolve.

#### Common omissions

Updating only `index.rst`, forgetting the hardcoded sidebar template, referencing images outside `assets/media/`, deleting an orphan page that other pages still link to, committing built output under `docs/build/`, or letting internal fix history leak into user-facing pages.

## 9. Environment JSON Contract

Saved environment files have no separate formal JSON Schema file. The executable contract is the normalization and parsing code in `storage.py`, `flow.py`, `catalog.py`, the 3D `model.py`, and the dynamic builder.

### Canonical Shape and Example

The persisted top-level shape is `version`, `task_name`, `scene_key`, and `robots`. This small example is compatible with the current catalog:

```json
{
  "version": 1,
  "task_name": "carter_keyboard",
  "scene_key": "plane",
  "robots": [
    {
      "type": "carter",
      "controller": {
        "mode": "default",
        "cfg": "CARTER_DIFF_CFG"
      },
      "visual": {
        "x": 0.5,
        "y": 0.5
      },
      "attachments": [
        {
          "type": "keyboard",
          "controller": null
        }
      ]
    }
  ]
}
```

### Filename and Save/Load Rules

- Files live at `source/EAI_hmrs/EAI_hmrs/envs/<task-name>.json`. A task name may contain ASCII letters, digits, `_`, and `-`; callers supply the case-sensitive stem without `.json`.
- `storage.save_task()` and `load_task()` normalize the payload and overwrite its `task_name` with the validated filename stem. Renaming the file therefore changes the loaded `task_name` regardless of the embedded value.
- Saving uses sorted keys, two-space JSON indentation, UTF-8, and a trailing newline. Loading uses `json.loads()` before normalization.
- `version` defaults to `TASK_SCHEMA_VERSION` (`1`) and is converted with `int()`. The current loader does not reject unsupported version numbers, `InteractiveSelection` does not retain the version, and the builder ignores it. Treat version changes as a coordinated migration even though validation is currently absent.

### Scene and Robot Rules

- `scene_key` is required by the flow parser and must match a current `SCENE_CHOICES` key after trimming/lowercasing. Storage alone accepts legacy top-level `scene` as an alias and writes it back as `scene_key`; bypassing storage loses that compatibility path.
- A saved payload passing storage normalization must contain at least one robot. The lower-level flow parser can construct an empty selection, but the saved-environment and 3D export workflows require robots.
- Each robot requires `type`. It is stripped and lowercased through `canonical_robot_type()`; the historical `M20` spelling becomes `m20`. The resulting key must be in the shared robot catalog and later in `ROBOT_OPTIONS` to run.
- `controller` is optional or may be `null`; direct flow parsing supplies the robot's catalog default. `mode` defaults to `default`, but it is editor metadata rather than a separate runtime execution mode: the builder selects by `cfg`.
- Storage normalization preserves a manual choice with an empty cfg as the sentinel `{"mode": "manual", "cfg": "manual"}`. Direct `flow.interactive_selection_from_dict()` parsing behaves differently for a known robot: a manual choice with an empty cfg becomes that robot's catalog default because `_controller_from_dict()` selects `cfg or default_cfg or "manual"`.
- The builder's `_resolve_controller()` falls back to the `RobotOption` controller when the choice is absent, its cfg is empty, or its cfg is exactly the `manual` sentinel. Do not infer the direct-parser result from the storage-only representation.

### Visual and Physical Pose

- `visual` is optional editor metadata. Storage and flow normalize `visual.x` and `visual.y` to floats, defaulting to `0.0`. The formal builder does not use them for world placement.
- `spawn_pose`, when present, is the physical pose. It must be an object containing exactly three finite numeric `position` values and four finite numeric `rotation` values. The quaternion cannot be zero and is normalized to unit length. Serialization emits arrays.
- When `spawn_pose` is absent, the builder uses its deterministic grid, scene spawn origin, robot default height, and default rotation; the legacy human uses its dedicated initial rotation.

### Attachments and Controllers

- Each attachment requires a `type`, normalized to lowercase. Storage removes later duplicates of the same type and rejects a mixture of UR5 and Z1, but host compatibility is enforced by the flow catalog parser and requirement resolver.
- Catalog validation rejects unknown visual-only attachment names and attachments unsupported by the canonical host. It deduplicates while preserving first-type order and allows only one manipulator type. When duplicate raw entries reach the flow parser, the normalized type order is retained while the last raw entry of that type supplies its data.
- Only `ur5` and `z1` retain an attachment `controller`; their missing controller defaults to `UR5_IK_CFG` or `Z1_IK_CFG`. Sensor and tool controller fields are discarded by flow parsing and serialize as `null`.
- The builder names robot instances `<canonical-type>_<per-type-occurrence>`. An attached arm is stored in the scene cfg as `<instance>_arm`, and that robot's controller entry becomes `(base_controller, arm_controller)`; primary base action runs before the auxiliary arm action.

### Serialization Paths

- `flow.interactive_selection_to_dict()` serializes scene, robots, normalized controllers, visual metadata, attachments, optional spawn poses, and optional `task_name`; it does not add `version`.
- `storage.save_task()` supplies the persisted `version` and filename-derived `task_name`, canonicalizes types, normalizes controller dictionaries and visual coordinates, deduplicates attachments, and validates the physical pose.
- The lightweight editor primarily records normalized `visual.x/y` placement. The 3D `AuthoringModel` stores physical position/quaternion, exports `spawn_pose`, and emits empty visual metadata that the flow parser normalizes to zero coordinates. Both converge through the same flow parser before formal building.
- A JSON filename is never registered as a Gym task. Saved selections are loaded, normalized, parsed into `InteractiveSelection`, and passed to the dynamic builder, which produces the transient `EAI-Interactive-v0` configuration.

## 10. Naming, Registration, and Compatibility Rules

### Manual Synchronization Authorities

The repository intentionally uses explicit registration maps. When extending a selection, compare these authorities rather than scanning directories:

| Concern | Selection authority | Runnable/asset authority |
| --- | --- | --- |
| Scenes | `SCENE_CHOICES` in `catalog.py` | `SCENE_OPTIONS` in `env_builder.py`; `_SCENE_PATHS` in `asset_requirements.py` |
| Robots | `ROBOT_KEYS`, `ROBOT_LABELS`, `_DEFAULT_CONTROLLER_CFG` | `ROBOT_OPTIONS`; `_ROBOT_PATHS` |
| Controllers | `_CONTROLLER_CFG_NAMES` and robot defaults | `CONTROLLER_CFG_IMPORTS`; `_CONTROLLER_PATHS` requirement seeds; resolver bundle dependencies and cfg path collection |
| Physical attachments | `attachment_catalog()`, built on demand from `_attachment_entries()` | Builder mount/spawn branches; `_PAYLOAD_PATHS` |
| Non-physical tools | separate `tool_catalog()` | Launcher or algorithm consumer; optional interface YAML |
| Manipulator mounts | attachment host lists | `UR5_MOUNT_PROFILES`, `Z1_MOUNT_PROFILES`, and matching `RobotOption` fields |
| Communication names | independent `models` aliases in interface YAML | Actual bridge/publisher/OmniGraph setup in `simulator.py` and `hmrs_ros/` |

There is no `ATTACHMENT_CATALOG` constant: `attachment_catalog()` reconstructs its mapping from `_attachment_entries()`. Do not merge tools into it or infer any of these registries from filenames.

Use lightweight queries when reviewing volatile lists:

```bash
PYTHONPATH=source/EAI python -c 'from EAI.hmrs_env.env_diy import catalog; print(catalog.scene_choices()); print(catalog.robot_keys()); print(catalog.controller_cfg_names())'
PYTHONPATH=source/EAI python - <<'PY'
from EAI.hmrs_env.env_diy import catalog

for name, entry in catalog.attachment_catalog().items():
    print(name, entry.supported_robots)
for name, entry in catalog.tool_catalog().items():
    print(name, entry.supported_robots)
PY
python simulator.py interfaces list --json
```

### Selection, Runnable, and Activation States

These are distinct states:

1. A USD or Python asset may exist without a catalog key.
2. A catalog key may be selectable but still fail unless it is in the builder and asset-requirement maps.
3. A builder option may construct a scene but still lack its controller/provider files or required extensions.
4. An interface declaration may resolve for a selection without a live publisher, subscriber, or OmniGraph.

Therefore, `asset present != selectable != runnable != runtime activated`. Verify each boundary explicitly.

### Compatibility Rules

- Attachment and tool host lists are source-controlled and expected to evolve. Query `attachment_catalog()` and `tool_catalog()` with the command above; do not copy a snapshot of the host lists into new code or documentation.
- A robot may contain several distinct sensors/tools, but only one manipulator type. Repeated identical attachments are deduplicated; a UR5/Z1 mixture is rejected. Unknown attachments are returned by `attachment_entry()` as visual-only compatibility records, then rejected by validation and requirements.
- `camera`, `keyboard`, and `ros` are non-physical tools with separate host sets. Camera is available on Iris/Pegasus/CF2X and on Orsus-capable ground robots, where normal validation requires the `orsus` attachment. Normal saved/DIY flows must obey the catalog; registry-driven humans use their independent demo and stage runtime rather than these Env DIY tools.
- Physical compatibility is not established by adding a host name alone. Sensor hosts require valid mount links/offsets; manipulator hosts require a mount profile and formal/preview assembly support.

### User Interface and Image Synchronization

The terminal flow hardcodes its payload/tool steps, the 3D UI hardcodes card/category presentation, and `env_diy_app.html` duplicates the complete vocabulary and host lists in JavaScript. Update all affected front ends even when the pure catalog is correct. The lightweight HTML uses tracked images below `usd/picture/scene/` and `usd/picture/processed/{robot,manipulator,sensor,tool}/`; preserve the exact referenced spelling, including existing case-sensitive filenames. `node tools/check_env_diy_runtime.mjs all` validates markup, IDs, local images, and inline JavaScript syntax, but not equality with the Python catalog. The equality suite is not part of the current baseline: an affected change must add and unignore `source/EAI/test/test_env_diy_catalog_sync.py` following the `Modify Env DIY` workflow before running its pytest command.

### Instance and Interface Naming Limitations

The builder and launcher name robots by per-type occurrence, for example an ordered Carter, Go2, Carter selection becomes `carter_1`, `go2_1`, `carter_2`. `resolve_scene_interfaces()` currently defaults non-explicit names using the global robot index, producing `carter_1`, `go2_2`, `carter_3` for that selection. Static `interfaces scene` output and some runtime-resolved endpoints can therefore disagree with actual instances in mixed-type environments. Fix query resolution or supply authoritative instance names before relying on those endpoints.

Interface YAML `models` values are search aliases, not selection registrations. Likewise, UR5 and Z1 YAML can both be listed while only UR5 is registered by the main session's current `setup_robot(...)` path. Report declared and active status separately.

## 11. Asset and Large-File Handling

### Git LFS and Gated Provider Boundaries

`.gitattributes` assigns Git LFS filters to many binary extensions, including images, archives, model weights, and USD formats. Those rules apply only to files that Git actually tracks. They do not override `.gitignore`, make an ignored file eligible for a commit, or cause resolver-managed files to appear in a checkout. Use `git lfs ls-files` to inspect the current tracked LFS inventory rather than inferring it from filename extensions.

Production robot, scene, payload, human, and controller bundles are normally outside Git. The resolver's default gated dataset is `HuangQIjun/eai-simulator-assets`, with provider roots `usd/` and `controller/`. Their default local roots are `usd/` and `source/EAI_assets/EAI_assets/controller/`. The tracked `usd/` inventory is principally UI images and human metadata; do not describe production robot USD or controller bundles as LFS-tracked unless `git ls-files` and `git lfs ls-files` both prove that exact file is in the index. In particular, later `.gitignore` rules currently override the earlier MuSHR negations, so the old exception comments do not establish tracked MuSHR USD or controller source.

### Requirement Graph and Semantic IDs

`source/EAI_assets/EAI_assets/asset_requirements.py` converts a serialized selection into a deduplicated `RequirementGraph`. Requirement states are `READY`, `MISSING`, `DOWNLOADING`, `AUTH_REQUIRED`, `ACCESS_PENDING`, and `FAILED`; kinds are `scene`, `robot`, `payload`, `sensor`, `tool`, and `controller`. Stable semantic IDs use forms such as `scene:<key>`, `robot:<type>`, `payload:<attachment>`, `sensor:<attachment>`, `tool:<tool>@<host>`, and `controller:<cfg>`. The graph's selection ID currently defaults to `selection:current`.

Selection resolution validates scene and robot keys through its seed maps, uses the catalog's default controller when a robot controller is absent, validates attachment host compatibility, adds a manipulator's default controller when required, and deduplicates by semantic ID. Tools and the `plane` scene deliberately have empty path tuples; inspection reports those requirements as ready because there is no provider file to materialize. Empty paths mean "no resolver-managed file," not proof that an external executable, ROS node, or other runtime behavior is active.

`resolve_card_requirement()` supports scene, robot, payload, and sensor cards. For an attachment card it chooses the catalog's first supported host so compatibility can be evaluated. It does not resolve tool or controller card IDs and does not prove that every supported host, builder branch, transitive reference, or runtime extension works.

`_SCENE_PATHS`, `_ROBOT_PATHS`, `_PAYLOAD_PATHS`, and `_CONTROLLER_PATHS` are requirement seeds, not complete provider manifests. After configuration construction, the resolver also discovers `usd_path`, declared dependency attributes, and controller `model_path`, `nav_model_path`, and `locomotion_model_path` values. Controller bundle expansion can add shared modules. A seed-map check therefore cannot replace configuration construction, provider inventory review, or clean-root integration validation.

### Resolver Environment and Authentication Inputs

The resolver recognizes these environment variables:

- `EAI_ASSETS_HF_REPO` overrides the dataset repository ID; the default is `HuangQIjun/eai-simulator-assets`.
- `EAI_ASSETS_HF_REVISION` selects the branch, tag, or commit. Missing or whitespace-only values fall back to `main`.
- `EAI_ASSETS_AUTO_DOWNLOAD` defaults to enabled. The case-insensitive values `0`, `false`, `no`, and `off` disable automatic downloads; other values enable them.
- `EAI_USD_ROOT` and `EAI_CONTROLLER_ROOT` replace the local USD and controller roots. Relative configured values are expanded and resolved by the current process, so use intentional locations and inspect them before launch. Human downloads read checksum metadata from `<active USD root>/human/pack-checksums.json`; a fresh custom `EAI_USD_ROOT` must be provisioned with metadata matching the selected revision before requesting a human pack.
- `HF_TOKEN` and `HUGGING_FACE_HUB_TOKEN` are recognized credential inputs. Otherwise the resolver looks under `HF_HOME`, defaulting to the Hugging Face user cache, for `token` or `stored_tokens`. `HF_HOME` is a Hugging Face credential/cache boundary; it does not replace either EAI asset root.

Dataset access approval and CLI authentication are separate. Request access to the gated dataset, wait for approval, and then run `hf auth login` in the same user environment that launches the simulator. Never print, echo, commit, or paste a token into a command line or diagnostic report, and do not use token-display commands as a health check.

The runtime default `main` follows the latest provider commit. Pin an immutable provider tag or commit when reproducibility is required.

### Download, Installation, and Integrity Semantics

Controller loading is lazy. The primary `controller_loader._load_controller_module()` resolves the file named by `CONTROLLER_CFG_IMPORTS` and calls `ensure_controller_assets_for_paths()` only when that mapped file is absent. A different simulator-preflight recovery path catches a transitive `ModuleNotFoundError` below `EAI_assets.controller`, calls `ensure_controller_module_available()` for the missing module, clears controller/package caches, and retries the environment build once. The tracked `_CONTROLLER_BUNDLE_DEPENDENCIES` map currently adds `traditional/manipulator_ik/manipulator_ik.py` for Z1 IK; do not infer other shared dependencies from a provider directory or a dirty worktree. After a configuration is built, collected controller paths ensure weights and other files referenced by `model_path`, `nav_model_path`, or `locomotion_model_path`. USD allow patterns similarly expand a requested file to the resolver's scene, robot, payload, or human bundle granularity. Review the actual allow patterns before treating a download as narrowly scoped.

Ordinary non-human bundles do not use the human-pack checksum transaction. Depending on whether the configured local root is named exactly `usd` or `controller`, the resolver downloads into that root's parent or into a temporary directory and then merges an external root with `copytree(..., dirs_exist_ok=True)`. It performs a requested-file postcheck, but it does not checksum every ordinary bundle and does not promise rollback of a partial ordinary install. Do not describe ordinary bundle downloads as checksum-verified or fully transactional.

Requests containing stable human pack patterns are different. The resolver reads `human/pack-checksums.json` below the active USD root, requires its revision to match `EAI_ASSETS_HF_REVISION`, stages below that root's `human/` directory so replacement stays on the same filesystem, rejects unsafe paths, symlinks, special files, and overlapping transaction roots, verifies the requested human packs by path-and-content checksum, validates requested staged files, and replaces complete roots with backups and rollback. The repository tracks this metadata under the default `usd/` root, but it is not automatically copied into a custom root. When a request mixes human and ordinary roots, all staged roots participate in replacement and rollback, but checksum verification still applies specifically to the named human packs.

### Human Metadata, Caches, and Custom Actions

The tracked human files have distinct roles:

- `usd/human/manifest.json` is the versioned canonical catalog of assets and motions.
- `usd/human/manifest.schema.json` defines the strict JSON structure and enums.
- `usd/human/audit-summary.json` records migration/audit decisions, source references, duplicates, and validation summaries.
- `usd/human/pack-checksums.json` binds stable provider pack roots to an immutable revision, file counts, sizes, and aggregate path/content hashes.

Tracked metadata does not mean the referenced character, activity, motion, texture, cache, or action payload is present locally. It also does not grant redistribution rights: fields such as `review_required` still require a maintainer's provenance and license decision.

`tools/human_assets/build_motion_cache.py` generates per-asset retarget caches, defaulting below `usd/human/motions/cache/`, and records source and target hashes. Current runtime verification recomputes and enforces those two hashes. The cache format can hold an optional dependency hash, but it is checked only when a caller supplies an expected dependency hash; the current stage runtime does not provide one end to end. Rebuild caches through the tool after changing source motion or target skeleton data; do not hand-edit cache JSON.

`HumanActionPublisher` stages an authored action, writes its content hash and a custom overlay manifest, then uses several atomic renames for the old action backup, new action directory, and catalog. Its caught-exception path performs in-process rollback, but the directory and catalog are not one crash-atomic filesystem transaction. It refuses canonical IDs such as `idle`, `walk`, and `greeting`; custom overlays extend the registry and cannot replace canonical records. External packs, caches, custom-action payloads, and their generated overlay live under ignored human directories unless a maintained workflow explicitly adds a narrow tracked exception.

There is no tracked command that generates or publishes `pack-checksums.json` for a provider release. Producing checksum metadata from the exact provider payload and publishing both atomically is a provider-maintainer responsibility, not a local consumer step.

### Error Classification and Safe Diagnosis

Keep these failures distinct:

- `AUTH_REQUIRED` or a 401/credential message means usable credentials were not found or were rejected.
- `ACCESS_PENDING` or a gated 403 means the account may be authenticated but lacks approved dataset access.
- `Revision Not Found` means the requested branch, tag, or commit is absent; changing credentials does not create it.
- `MISSING` or a requested-file postcheck names local/provider path coverage that is incomplete.
- `AssetIntegrityError` means trusted human checksum metadata, staged contents, or path-safety checks failed. Do not bypass it by disabling validation or merging staged files manually.
- Other download, network, CLI, or provider errors remain `FAILED` and retain their diagnostic output.

The following provider command is read-only but requires network access, the `hf` CLI, and approved gated-dataset credentials. It verifies that the moving default `main` revision resolves. For reproducible validation, replace `main` with an immutable tag or commit:

```bash
EAI_ASSET_DEFAULT_REVISION=main
hf download HuangQIjun/eai-simulator-assets \
  --type dataset \
  --revision "$EAI_ASSET_DEFAULT_REVISION" \
  --include usd/robot/carter/carter.usd \
  --dry-run

```

### Commit Exclusions and Maintained Exceptions

Never commit resolver downloads, weights, external human packs, retarget caches, custom-action output, Hugging Face caches or credentials, temporary preflight output, or local experiment artifacts. Use both `.gitignore` and the index as evidence:

```bash
git check-ignore -v --no-index -- usd/human/motions/cache/example.json
git ls-files -- usd/human/motions/cache/example.json
git status --short
```

Broad rules currently ignore `test_*.py`, `tests/`, the controller root, most of `usd/`, and most human payload directories. Ignore rules do not untrack files already in the index. The final `test_human_*.py` negation allows new matching human tests to be admitted and discovered; other intended new tests or package data need their own narrow negation after the last matching rule. Verify `git check-ignore -q --no-index -- source/EAI/test/test_env_diy_catalog_sync.py` returns nonzero after adding that exception and confirm `git status --short` exposes the file. Do not generalize a source exception to generated output.

### Provider Publication and Local Verification

The repository has no tracked provider uploader. Follow the provider-backed handoff in section 8: publication requires dataset write access, exact remote paths below `usd/` or `controller/`, sizes and hashes, provenance and license review, an immutable tag or commit, any matching source maps, an intentional runtime-default update, and verification from clean local roots. Local files or a successful `main` dry-run are not publication evidence.

These checks are local and lightweight. They require Git LFS for `git lfs ls-files` and Node.js for the Env DIY checker; the Python commands use the current environment. They inventory attributes/LFS, parse exactly the four maintained human metadata files structurally, exercise selected tracked pure tests, and enforce the Env DIY HTML/runtime barrier without launching Isaac or downloading assets:

```bash
git check-attr filter diff merge -- \
  usd/human/manifest.json \
  usd/picture/robot/carter.png \
  source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py
git lfs ls-files
python - <<'PY'
import json
from pathlib import Path

for name in (
    "manifest.json",
    "manifest.schema.json",
    "audit-summary.json",
    "pack-checksums.json",
):
    path = Path("usd/human") / name
    json.loads(path.read_text(encoding="utf-8"))
    print(path)
PY
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=source/EAI:source/EAI_assets \
  python -m pytest -q -p no:cacheprovider \
  source/EAI_assets/test/test_human_asset_distribution.py::test_human_paths_map_to_stable_pack_patterns_in_first_seen_order \
  source/EAI_assets/test/test_human_asset_distribution.py::test_non_human_pattern_resolution_is_unchanged \
  source/EAI_assets/test/test_human_asset_registry.py::test_repository_default_catalog_is_strict_v2_metadata
node tools/check_env_diy_runtime.mjs all
```

Do not turn a local human-pack checksum mismatch, missing ignored payload, or dirty cache into a repository contract. First distinguish tracked metadata, the selected provider revision, and the current machine's ignored asset roots; report local drift as local drift.

## 12. ROS2 and Nav2 Development

### Simulator and System ROS Process Boundaries

Run `simulator.py` in `env_isaaclab`. Run external programs that import ROS Humble `rclpy`, Nav2 nodes, RViz2, and ROS CLI tools in a separate shell using the system ROS Python 3.10 after sourcing the system path `/opt/ros/humble/setup.bash`. Sourcing ROS into the Isaac Conda shell does not change the ABI of its Python interpreter and can mix incompatible Python and shared libraries.

`algorithm/keyboard/keyboard.py` detects the common `_rclpy_pybind11`/Python-version mismatch and tells the operator to use `/usr/bin/python3`; it does not re-exec automatically. `algorithm/ros/nav2/tf_bridge.py` does re-exec to `EAI_NAV2_ROS_PYTHON` or `/usr/bin/python3` when it detects Conda or a non-3.10 ABI, unless `EAI_NAV2_NO_REEXEC=1`. The unified Nav2 launch also uses `EAI_NAV2_ROS_PYTHON` for that bridge. Treat these as process boundaries, not permission to merge the simulator and system ROS environments.

Before application launch, `simulator.py` looks for the Isaac ROS2 bridge under `ISAAC_ROS_PATH`, then `EAI_ISAACSIM_ROOT` or `ISAACSIM_ROOT`, followed by conventional user install locations. When found, it sets `ISAAC_ROS_PATH` and prepends the bridge `lib` and prefix paths. The launcher itself uses `setdefault()` for `ROS_DISTRO=humble` and `RMW_IMPLEMENTATION=rmw_fastrtps_cpp`, but importing the current Orsus or LiDAR modules normally assigns those values directly and can overwrite caller choices with Fast DDS unless their sensor ROS-environment setup is disabled. Diagnose the process's final environment rather than assuming an explicit earlier RMW value survived sensor imports.

`algorithm/ros/nav2/run_nav2.sh` launches system Nav2 under `env -i` with CycloneDDS but does not forward `ROS_DOMAIN_ID` or other discovery variables. Its Nav2 side therefore uses domain 0, while its simulator child can inherit a caller's nonzero domain; the script currently requires the simulator/default domain to remain 0. A manual launch can inherit an explicitly matched `ROS_DOMAIN_ID` and relevant discovery/network settings in both prepared process environments. Confirm the actual domain, RMW, and discovery configuration on each side.

### Cmd_vel Activation and Command Flow

The input path is:

```text
external geometry_msgs/msg/Twist on /<instance>/cmd_vel
  -> ROS2TwistSubscriber OmniGraph
  -> ROS2CmdVelBridge
  -> simulator command tensor or goal-position update
  -> MultiRobotDirectEnv controller
  -> robot action
```

A selected `keyboard` or `ros` tool causes the launcher to consider that robot for a cmd_vel subscriber. `--enable-cmd-vel-bridge` forces consideration for every robot; `--enable-nav2-bridge` is only a deprecated alias for the same flag. A bridge is retained, printed as enabled, and included in `tmp/runtime_interfaces.json` only when `setup()` succeeds. The bridge has no stale-command watchdog: the latest Twist remains effective until another message, including zero, replaces it or the bridge is cleaned up. Publishers that may stop unexpectedly must send zero and live tests must confirm the robot stops.

Robot instance topics use per-type occurrence names from the builder, for example `/carter_1/cmd_vel`, `/go2_1/cmd_vel`, and `/carter_2/cmd_vel`. On the direct action-tensor path, the current bridge reads `linear.x` and `angular.z` and sets `linear.y` to zero. Scout angular velocity is additionally multiplied by `SCOUT_CMD_VEL_ANGULAR_SCALE`. Do not promise lateral A/D motion for ordinary holonomic bases: although the keyboard message contains `linear.y`, that component is dropped by the direct path. Goal-controlled robots use the full Twist and integrate linear components into `goal_position`; quadcopter goal updates use a fixed keyboard step scale rather than frame `dt`, and supported yaw goals are integrated separately.

The keyboard publisher emits one Twist per key event and sends zero when switching or exiting; its `--rate` argument is currently parsed but does not create periodic publication. `algorithm/ros/tools/ros2_send_cmd_vel.py` is the separate rate-based test publisher when `--rate` is positive, but its Ctrl+C cleanup does not publish zero. Because the bridge has no watchdog, stop a continuous motion test by running a sustained zero publisher in another sourced ROS Humble terminal, wait until repeated zeros have reached the existing subscriber, and then stop that zero publisher. Do not use the tool's one-shot mode as a safety stop. The keyboard publisher's connected cleanup path is another option.

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/ros/tools/ros2_send_cmd_vel.py \
  --robot carter_1 --linear 0 --angular 0 --rate 10
```

Presence of either publisher does not prove the Isaac subscriber graph is active.

### Sensor Publishers and Topic Collisions

Orsus and standalone LiDAR own their ROS publishers in their asset/OmniGraph implementations, not in `ROS2CmdVelBridge`. Orsus has two independent builder flags: the `camera` tool sets `enable_camera_publish=True` for its left/right image graphs, while the `ros` tool sets `enable_ros_publish=True` for its point-cloud/odometry graph; both require the physical `orsus` attachment. The catalog associates the derived Orsus scan with `ros`, but samples appear only when the external pointcloud-to-laserscan pipeline runs. `keyboard` enables cmd_vel consideration but neither Orsus graph. A standalone ground-robot `lidar` attachment creates its own ROS LiDAR asset publisher independently of the `ros` tool.

Iris, Pegasus, and CF2X use a separate built-in aerial sensor suite. Their `camera` tool enables the monocular image and CameraInfo publishers, while `ros` enables the Pegasus `Example_Rotary` RTX LiDAR point cloud plus noisy IMU, GPS, magnetometer, and barometer publishers.

When enabled, both sensors use `/<instance>/cloud` and `/<instance>/odometry`. The LiDAR attachment publishes independently; Orsus collides with it only when Orsus ROS publication is enabled by the same robot's `ros` tool. `nav2_setup.py --sensor auto` conservatively rejects both attachments from the runtime snapshot regardless of whether the Orsus graph is enabled. Keep one publisher active, or explicitly select one only after disabling the other. `/<instance>/scan_cloud` is a topic: `tf_bridge.py` republishes `/<instance>/cloud` there with `header.frame_id` changed to `lidar_link`, and `pointcloud_to_laserscan` consumes it to produce `/<instance>/scan`.

Publisher presence is weaker than data flow. RTX/render-dependent graphs can register topics while producing no samples, especially in headless or incompletely rendered sessions. Sensor acceptance therefore requires live sampling and rate checks for `/clock`, Orsus odometry/cloud/images and derived scan where selected, plus aerial camera, LiDAR, and applicable base-sensor topics, in the actual GUI/headless mode being supported. Camera-only and ROS-only selections must also confirm that topics from the other gate are absent.

### UR5 and Z1 Manipulator Interfaces

The shared formal topic family is `/<instance>/<model>/...`. UR5 declares `target_pose`, `joint_command`, `joint_states`, and `ee_pose` below `/<instance>/ur5/`. Z1 declares the same endpoints below `/<instance>/z1/` plus `gripper_command` and `gripper_state`. Pose commands accept the formal `world` or `base_link` frames; joint commands use each model's canonical joint order/names.

The main simulator session currently calls `ManipulatorOmniGraphManager.setup_robot(...)` only for selected UR5 attachments and closes that shared manager during cleanup. `Z1_MODEL_SPEC`, `z1_topic_names()`, aliases, YAML declarations, and a selected Z1 attachment do not activate an equivalent graph. `algorithm/ros/tools/manipulator_command.py` is an external rclpy client for the formal topic families; it cannot create a missing simulator graph.

`algorithm/ros/tools/diagnose_ur5_multi_scene.py` is stale against the shared manager: it inspects `_ur5_ros2_manager`, while current setup attaches `_manipulator_ros2_manager`. It is also an Isaac integration diagnostic with defaults and file-output coordination, not a general lightweight ROS check. The standalone `algorithm/ros/z1/run_z1_ros2_bridge.py` workflow loads a separate converted Z1 stage and exposes legacy `/z1/joint_commands` and `/z1/joint_states` topics in another process; success there is not evidence that a Z1 attached in the main multi-robot session exposes the formal endpoints.

### Nav2 Profiles, Generation, and Launch

`algorithm/ros/nav2/nav2_profiles.yaml` maps controller-facing robot types to motion models, footprint/radius, velocity and acceleration limits, sensor mounts, scan filters, and optional planner/controller plugins. It also maps scenes to occupancy maps. `nav2_setup.py` combines a profile, sensor choice, scene or explicit map, and explicit or live initial pose to generate `nav2_params.yaml`, `pointcloud_to_laserscan.yaml`, `view.rviz`, and `meta.txt`. With `sensor=auto` or no explicit pose it requires a fresh `tmp/runtime_interfaces.json`, a live owning simulator PID, a matching scene, and the named robot.

This static generation check uses an explicit tracked map, explicit pose and sensor, and a unique system temporary directory that is removed on exit. It needs Python plus PyYAML but does not start ROS, Nav2, Isaac, or a network request:

```bash
(
  set -eu
  EAI_NAV2_CHECK_OUT="$(mktemp -d "${TMPDIR:-/tmp}/eai-nav2-static-check.XXXXXX")"
  case "$EAI_NAV2_CHECK_OUT" in
    "${TMPDIR:-/tmp}"/eai-nav2-static-check.*) ;;
    *) printf 'Unexpected temporary path: %s\n' "$EAI_NAV2_CHECK_OUT" >&2; exit 1 ;;
  esac
  readonly EAI_NAV2_CHECK_OUT
  cleanup_nav2_check() {
    case "${EAI_NAV2_CHECK_OUT:-}" in
      "${TMPDIR:-/tmp}"/eai-nav2-static-check.*) rm -rf -- "$EAI_NAV2_CHECK_OUT" ;;
      *) printf 'Refusing to remove unexpected path: %s\n' "${EAI_NAV2_CHECK_OUT:-}" >&2; return 1 ;;
    esac
  }
  trap cleanup_nav2_check EXIT
  trap 'exit 130' INT
  trap 'exit 129' HUP
  trap 'exit 143' TERM

  python algorithm/ros/nav2/nav2_setup.py \
    --robot carter_1 \
    --robot-type Carter \
    --sensor orsus \
    --scene factory \
    --map demo/fire_rescue/assets/factory_map.yaml \
    --pose 0,0,0 \
    --out "$EAI_NAV2_CHECK_OUT"
)
```

The unified `nav2.launch.py` runs that generator, starts the TF bridge, converts `/<instance>/scan_cloud` to `/<instance>/scan`, starts map server and AMCL, then controller, smoother, planner, behavior, BT navigator, waypoint follower, velocity smoother, lifecycle manager, and optional RViz. `tf_bridge.py` publishes dynamic `odom -> base_link` and static `base_link -> lidar_link`; the selected robot/sensor profile supplies the base offset and LiDAR mount values passed to that node. AMCL supplies `map -> odom`. Controller/behavior output is remapped to `cmd_vel_nav`; the velocity smoother publishes the final `/<instance>/cmd_vel`. The ROS package `pointcloud_to_laserscan` is therefore a runtime dependency, not an optional visualization tool.

The profile's implicit Factory map is `usd/scene/factory/factory_map.yaml`, which is not tracked and is not reliable in a clean checkout. `run_nav2.sh` has no map argument and relies on that absent implicit file, so it is not a clean-checkout-capable launcher. Use the manual launch path with an explicit valid map. This example assumes a running simulator selection with `carter_1`, exactly one Orsus point-cloud/odometry graph enabled through `orsus` plus `ros`, independently enabled Orsus images through `camera`, matching ROS discovery settings, and a fresh runtime snapshot:

Before launching, run this non-mutating preflight for the exact predictable output directory:

```bash
EAI_NAV2_OUT=/tmp/eai_nav2_carter_1
if [[ -e "$EAI_NAV2_OUT" || -L "$EAI_NAV2_OUT" ]]; then
  if [[ -L "$EAI_NAV2_OUT" || ! -d "$EAI_NAV2_OUT" ]]; then
    printf 'Refusing unsafe Nav2 output path: %s\n' "$EAI_NAV2_OUT" >&2
    exit 1
  fi
  if [[ "$(stat -c '%u' -- "$EAI_NAV2_OUT")" != "$(id -u)" ]]; then
    printf 'Nav2 output directory is not owned by the current user: %s\n' "$EAI_NAV2_OUT" >&2
    exit 1
  fi
  for EAI_NAV2_FILE in nav2_params.yaml pointcloud_to_laserscan.yaml view.rviz meta.txt; do
    if [[ -L "$EAI_NAV2_OUT/$EAI_NAV2_FILE" ]]; then
      printf 'Refusing symlinked Nav2 output: %s\n' "$EAI_NAV2_OUT/$EAI_NAV2_FILE" >&2
      exit 1
    fi
  done
fi
```

`nav2_setup.py` does not enforce ownership or symlink safety before creating and overwriting these outputs. The preflight is subject to a time-of-check/time-of-use race; on an untrusted or shared host, do not launch this workflow and use an isolated session or host instead. Do not delete or replace an existing path merely to make the check pass.

```bash
source /opt/ros/humble/setup.bash
EAI_NAV2_MAP="$(pwd)/demo/fire_rescue/assets/factory_map.yaml"
ros2 launch algorithm/ros/nav2/nav2.launch.py \
  robot_name:=carter_1 \
  robot_type:=Carter \
  sensor:=orsus \
  scene:=factory \
  map:="$EAI_NAV2_MAP" \
  rviz:=true
```

The launch uses global node names, `map`/`odom`/`base_link`/`lidar_link` frames, `cmd_vel_nav`, and an un-namespaced `navigate_to_pose` action. It is a single-stack workflow, not a namespaced multi-robot Nav2 launcher; starting a second stack in the same ROS graph causes name/TF/remap collisions. `nav2_setup.py` also defaults to `/tmp/eai_nav2_<robot>`, so concurrent launches for the same robot overwrite the same generated files.

`send_goal.py` targets that global action. It waits up to ten seconds for the server, but after a goal is accepted it has no result timeout or cancellation path and can wait indefinitely. It logs the returned action status but treats any accepted result as success, and `main()` does not propagate its boolean return. A zero process exit code therefore does not prove that Nav2 reached the pose; inspect the terminal action status, feedback, final pose, and server logs.

### Interface Catalog and Probe Limits

These catalog commands are pure static inspection and do not launch Isaac or require a runtime snapshot:

```bash
python simulator.py interfaces list --json
python simulator.py interfaces scene --env keyboard --json
```

See section 10's mixed-type instance/interface naming limitation before relying on resolved endpoints. `interfaces status` is a separate snapshot-display path: it requires a readable runtime snapshot, reports the stored PID and calculated snapshot age, but does not check that the PID is still live or apply a heartbeat-age liveness threshold. Adding `--probe` runs presence probes for read-only interfaces recorded in that snapshot; ROS topic probes require a sourced ROS CLI environment and working discovery, while plain status display does not.

`interfaces test` runs one read-only probe. An explicit `--endpoint` is used as the probe target and bypasses snapshot endpoint selection. Without it, the command uses the first matching interface ID from a readable snapshot when that snapshot file exists; if the file is absent or contains no match, it falls back to the static catalog endpoint, which may still contain a template such as `/{robot}/...`. Supply `--endpoint` to target a particular robot or to avoid first-match and template ambiguity. Presence, sample, and frequency probes provide progressively stronger runtime evidence, but live-flow acceptance still requires checking the relevant samples, rates, frames, values, and application behavior.

```bash
python simulator.py interfaces status --json
python simulator.py interfaces status --probe --json
python simulator.py interfaces test ros.lidar.odometry \
  --endpoint /carter_1/odometry --mode sample --json
```

Declarations are capability metadata, not activation records. Static scene resolution includes interfaces by model/attachment. At runtime the launcher filters only `ros.cmd_vel` entries to bridges whose setup succeeded; other sensor and manipulator declarations can remain in the snapshot even when their publisher or graph did not start. Known examples are the Orsus `scan` declaration, which actually depends on the external Nav2 conversion pipeline; Orsus and LiDAR declarations that collide on cloud/odometry; Z1 declarations without main-session registration; and UR5 declarations whose active status still depends on successful graph setup.

Presence probes only show that a topic name and type are discoverable. Sample and frequency probes provide stronger read-only evidence but can still miss semantic errors in frames, values, timing, or command application. Input declarations such as cmd_vel and manipulator commands intentionally have no `read_only_test`; the CLI blocks probing them so diagnosis cannot publish commands accidentally.

### Stale and Unsupported ROS Artifacts

Some tracked ROS notes and helpers describe older workflows. `algorithm/ros/tools/ros2_nav2_test.py` is absent, so the tracked `quick_test_nav2.sh` that invokes it is not a valid test entry point. Older summaries and quick references use `/<robot>/odom`; current sensor and Nav2 code uses `/<robot>/odometry`. `algorithm/ros/bridges/ros2_odometry_bridge.py` is a tracked file-IPC subscriber, but neither the current simulator nor unified Nav2 launch consumes its JSON output. Current cmd_vel, odometry, and sensor paths use ROS/OmniGraph directly.

Treat README claims as secondary when they conflict with launch files and source. In particular, do not copy broad `pkill`, forced-kill, or process-name cleanup commands from stale notes. Stop only processes you started, using their owning terminal or recorded PID, and let the simulator/session cleanup close graphs, bridges, and snapshots.

### ROS and Nav2 Verification Tiers

The lightweight tier parses maintained shell, Python, and YAML sources and exercises static interface/configuration paths. It does not require ROS discovery, Isaac, a GPU, sensor assets, or provider access:

```bash
bash -n \
  algorithm/ros/nav2/run_nav2.sh \
  algorithm/ros/tools/quick_test_nav2.sh \
  algorithm/ros/z1/run_z1_ros2_bridge.sh \
  algorithm/ros/z1/send_z1_joint_command.sh
python - <<'PY'
import ast
from pathlib import Path

for path in (
    Path("simulator.py"),
    Path("algorithm/keyboard/keyboard.py"),
    Path("algorithm/ros/nav2/nav2_setup.py"),
    Path("algorithm/ros/nav2/nav2.launch.py"),
    Path("algorithm/ros/nav2/tf_bridge.py"),
    Path("algorithm/ros/nav2/send_goal.py"),
):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
python - <<'PY'
from pathlib import Path
import yaml

paths = [Path("algorithm/ros/nav2/nav2_profiles.yaml")]
paths += sorted(Path("source/EAI/EAI/interface_catalog/interfaces").rglob("*.yaml"))
for path in paths:
    yaml.safe_load(path.read_text(encoding="utf-8"))
    print(path)
PY
python simulator.py interfaces list --json >/dev/null
python simulator.py interfaces scene --env keyboard --json >/dev/null
```

The live tier requires Ubuntu ROS2 Humble, separately prepared Isaac/Isaac Lab and system ROS environments, the ROS bridge, matching discovery settings, a GPU/display mode appropriate to the selected sensors, and any gated assets already available or downloadable. Verify topic type and actual samples/rates for `/clock`, `/<instance>/odometry`, `/<instance>/cloud`, and `/<instance>/scan`; verify TF continuity and timestamps; send cmd_vel and observe command application plus a later zero stop; for UR5, verify command subscribers and changing `joint_states`/`ee_pose`; and for Nav2, verify lifecycle states, action acceptance, feedback, terminal action status, and final pose. Z1 main-session topics remain a known activation gap and cannot pass equivalent formal live verification until the launcher registers them.

## 13. Testing Strategy

### Tests That Do Not Require Isaac Sim

The maintained Python test inventory is the Git index, not filesystem discovery. On 2026-08-11 at commit `adff272b`, `git ls-files` reported 16 tracked Python test modules: one below `source/EAI/test/` and 15 below `source/EAI_assets/test/`. It reported no tracked Python tests below `source/EAI_env_diy/`, `demo/`, or `algorithm/`. These locations and the count are observations, not a selection contract. Two additional tracked Node.js checks are `tools/check_env_diy_runtime.mjs` and `tools/github_oauth_worker/oauth_worker_test.mjs`.

Do not run pytest against an entire test directory or the repository root. `.gitignore` hides broad classes such as `test_*.py`, `*_test.py`, `tests/`, and most of `source/EAI_assets/test/`; a developer's ignored local tests can still be collected by directory discovery and silently change the result. Build the canonical argument list from every path in the index, accepting both maintained basename forms. The NUL-delimited loop preserves spaces and other ordinary shell-special characters in paths:

```bash
EAI_TRACKED_TESTS=()
while IFS= read -r -d '' EAI_TEST; do
  case "${EAI_TEST##*/}" in
    test_*.py|*_test.py) EAI_TRACKED_TESTS+=("$EAI_TEST") ;;
  esac
done < <(git ls-files -z)
test "${#EAI_TRACKED_TESTS[@]}" -gt 0
```

Use two distinct suites. The green lightweight baseline deliberately excludes two committed defect modules and the three installed-pack checksum parameters. Derive its module list from the canonical inventory rather than maintaining a second fixed inventory:

```bash
EAI_TRACKED_TESTS=()
while IFS= read -r -d '' EAI_TEST; do
  case "${EAI_TEST##*/}" in
    test_*.py|*_test.py) EAI_TRACKED_TESTS+=("$EAI_TEST") ;;
  esac
done < <(git ls-files -z)
test "${#EAI_TRACKED_TESTS[@]}" -gt 0

EAI_BASELINE_TESTS=()
for EAI_TEST in "${EAI_TRACKED_TESTS[@]}"; do
  case "$EAI_TEST" in
    source/EAI_assets/test/test_human_action_tools.py|source/EAI_assets/test/test_human_asset_acceptance.py) continue ;;
  esac
  EAI_BASELINE_TESTS+=("$EAI_TEST")
done
test "${#EAI_BASELINE_TESTS[@]}" -gt 0
PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs${PYTHONPATH:+:$PYTHONPATH}" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q "${EAI_BASELINE_TESTS[@]}" \
    --deselect='source/EAI_assets/test/test_human_asset_distribution.py::test_tracked_checksum_manifest_matches_local_human_pack[characters]' \
    --deselect='source/EAI_assets/test/test_human_asset_distribution.py::test_tracked_checksum_manifest_matches_local_human_pack[activities]' \
    --deselect='source/EAI_assets/test/test_human_asset_distribution.py::test_tracked_checksum_manifest_matches_local_human_pack[motions]'
```

The full diagnostic tracked suite keeps all known defects visible for before/after comparison and may be red:

```bash
EAI_TRACKED_TESTS=()
while IFS= read -r -d '' EAI_TEST; do
  case "${EAI_TEST##*/}" in
    test_*.py|*_test.py) EAI_TRACKED_TESTS+=("$EAI_TEST") ;;
  esac
done < <(git ls-files -z)
test "${#EAI_TRACKED_TESTS[@]}" -gt 0

PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs${PYTHONPATH:+:$PYTHONPATH}" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q "${EAI_TRACKED_TESTS[@]}"
```

`--rootdir="$PWD"` makes node IDs stable enough for the precise deselections. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` is intentional. A ROS installation can expose ambient pytest plugins, including launch-testing plugins whose `lark` dependency can be incompatible with the active Conda environment. Those plugins are not part of this repository's tracked unit-test contract. Explicitly requested plugins must be enabled by name rather than restoring uncontrolled global autoload.

Observed research evidence is a diagnostic baseline, not a permanent expected count. On 2026-08-11 in the dirty worktree based at `adff272b`, the full diagnostic suite produced `457 passed, 24 skipped, 1 failed`; the failure was the installed human motion pack no longer matching tracked checksum metadata. The green baseline in that same worktree produced `443 passed, 24 skipped, 3 deselected`. In an isolated clean checkout of `fd767eec` on 2026-08-11, the full diagnostic suite produced `354 passed, 28 skipped, 3 failed`: two failures were in `test_human_action_tools.py`, and one was in `test_human_asset_acceptance.py`. With no external human packs installed, all three checksum parameters skipped rather than failed. The clean green suite produced `346 passed, 24 skipped, 3 deselected`. These dated, environment-specific results expose current coverage and checkout-independence gaps. They do not prove a new change passes, and different installed OpenUSD or human assets legitimately change skip and integrity outcomes.

The tracked tests have four execution tiers:

| Tier | What it exercises | Environment meaning |
| --- | --- | --- |
| Pure Python | Registry rules, selection and requirement data, animation math, path following, authoring plans, package metadata, and mocked subprocess/provider behavior | Runs without Isaac Sim, ROS2, a browser, a GPU, or network access. |
| `pxr` / OpenUSD | Temporary USD stages, skeletons, animation adapters, and authoring validation | Runs when compatible USD Python bindings are importable; otherwise affected tests skip. `pxr` alone is not proof that Isaac Sim started. |
| Installed asset pack | Checks maintained metadata against files present below the active/default human root | May skip when payload is absent and may fail when locally installed content has drifted. This is local integrity evidence, not a unit fixture. |
| Mocked Hugging Face and filesystem | Resolver classification, allow patterns, staging, rollback, and checksum behavior | Tracked tests replace download/provider calls; they do not contact Hugging Face or download real files. |

No tracked pytest module starts `isaacsim`, imports and launches `isaaclab.app.AppLauncher`, creates a real PhysX scene, calls ROS discovery, drives a browser, or makes an LLM request. This remains true for files whose names contain `isaacsim_integration`: they use temporary data, test doubles, `pxr`, or skip gates.

### Package-Level Unit Tests

Use the smallest tracked modules that own the changed behavior. On 2026-08-11, the following pure/runtime subset was verified as lightweight evidence in a guide-writing worktree:

```bash
PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs${PYTHONPATH:+:$PYTHONPATH}" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q \
    source/EAI_assets/test/test_human_action_authoring.py \
    source/EAI_assets/test/test_human_animation_runtime.py \
    source/EAI_assets/test/test_human_path_follower.py \
    source/EAI_assets/test/test_human_spawner.py \
    source/EAI_assets/test/test_human_stage_runtime.py
```

That 2026-08-11 run produced `74 passed, 10 skipped`; the skips were optional `pxr` or unavailable installed-asset paths. This is environment-specific evidence from that run, not a fixed assertion.

For human registry and resolver changes, exclude only the local installed-pack checksum comparison when the purpose is to test code independently of workstation payload state:

```bash
PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs${PYTHONPATH:+:$PYTHONPATH}" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q \
    source/EAI_assets/test/test_human_asset_registry.py \
    source/EAI_assets/test/test_human_asset_distribution.py \
    -k 'not tracked_checksum_manifest_matches_local_human_pack'
```

On 2026-08-11, the guide-writing worktree run produced `201 passed, 1 skipped, 3 deselected`. This result is environment-specific. Run the deselected checksum cases separately only when the exact immutable human pack named by `usd/human/pack-checksums.json` is installed. Do not weaken or delete an integrity test merely to accommodate a different local pack.

Package setup modules can be checked without installing or launching the simulator:

```bash
PYTHONDONTWRITEBYTECODE=1 python - \
  source/EAI/EAI \
  source/EAI_assets/EAI_assets \
  source/EAI_hmrs/EAI_hmrs <<'PY'
import ast
import os
import subprocess
import sys
from pathlib import Path

roots = sys.argv[1:]
result = subprocess.run(
    ["git", "ls-files", "-z", "--", *roots],
    check=True,
    stdout=subprocess.PIPE,
)
files = sorted(
    Path(os.fsdecode(value))
    for value in result.stdout.split(b"\0")
    if value and value.endswith(b".py")
)
assert files, f"No tracked Python files found under: {', '.join(roots)}"
for path in files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print(f"Parsed {len(files)} tracked Python files")
PY
PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs${PYTHONPATH:+:$PYTHONPATH}" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q \
    source/EAI_assets/test/test_human_package_install.py \
    -p no:cacheprovider
```

The AST parse builds its inventory from the Git index, ignores downloaded or untracked Python content, and is a read-only syntax check rather than an import test.

### CLI Smoke Tests

The interface catalog is the normal no-Isaac CLI fast path. These are manual smoke commands, not tracked pytest coverage:

```bash
python simulator.py --help >/dev/null
python simulator.py interfaces --help >/dev/null
python simulator.py interfaces list --json >/dev/null
python simulator.py interfaces scene --env keyboard --json >/dev/null
python -m demo.fire_rescue.main --help >/dev/null
```

`simulator.py --help` reads and may report the host inotify limits before argument parsing exits, but it does not import/start Isaac or construct `AppLauncher`. The interface commands validate CLI dispatch, YAML loading, and saved-environment resolution without constructing `AppLauncher`. The Fire Rescue module help command builds its parser and exits without opening a simulator session; direct file invocation is unsupported because `main.py` uses package-relative imports. `interfaces status` requires a runtime snapshot, and probe modes can require live ROS or socket endpoints; do not include them in an offline smoke test.

For saved JSON lookup and normalization, pass the name without `.json` and exercise the pure storage boundary directly:

```bash
EAI_ENV_NAME=keyboard
PYTHONPATH=source/EAI python - "$EAI_ENV_NAME" <<'PY'
import sys
from EAI.hmrs_env.env_diy.storage import load_task, task_path

name = sys.argv[1]
print(task_path(name))
print(load_task(name)["task_name"])
PY
```

### Isaac Sim Integration Tests

There is no tracked true Isaac Sim integration suite. A unit result, an import of `pxr`, or a file named `test_human_isaacsim_integration.py` must not be reported as a successful simulator launch.

The following are heavy, opt-in manual integration entry points:

```bash
python simulator.py --env robo
python simulator.py --env keyboard --headless
python simulator.py --diy-3d
python -m demo.fire_rescue.main --headless
```

Run only the entry point relevant to the change. Prerequisites include Ubuntu 22.04, Isaac Sim 5.1, Isaac Lab 2.x in `env_isaaclab`, compatible CUDA/GPU and display or headless configuration, editable repository packages, enough memory and disk, and all selected gated assets. ROS-enabled cases add the ROS bridge and ROS2 environment. Animated-human selections intentionally replace requested CUDA physics with CPU PhysX.

Fire Rescue adds optional EMOS/global-planner dependencies such as the OpenAI-compatible Python client, PyYAML, and Pillow. Its default `zhipu-glm4-flash` preset requires `ZHIPU_API_KEY` and can make real network API calls that incur provider cost. Other presets require their named key, such as `OPENAI_API_KEY` or `DEEPSEEK_API_KEY`. Review the selected endpoint, credential environment, budget, and network policy before launch. Individual OpenAI-compatible client calls use a 60-second timeout and the experiment can wait up to 160 seconds before dispatching its local fallback, so a fallback does not make the launch offline or cost-free. Pytest must mock the client and fallback timing; never use a real LLM credential or request in automated tests.

These commands were not run while validating this guide. The default resolver revision `main` follows the latest provider commit and is not immutable release evidence; pin a tag or commit for reproducible validation.

When heavy verification is available, record the exact command, environment versions, selected JSON, asset repository and immutable revision, GPU, display/headless mode, startup/reset result, controller load, representative steps, and clean shutdown. A window opening is not enough: first reset and at least one behavior-relevant step must succeed.

### ROS2 and Nav2 Verification

No tracked test automates a live ROS2 or Nav2 graph. Keep static checks separate from live evidence. Section 12 provides the exact shell, AST, YAML, catalog, and temporary Nav2-generation checks; those are safe without ROS discovery and must be run for affected source.

Live verification uses system Python/ROS2 Humble and a separately running Isaac environment. Use a matrix rather than a single "ROS works" claim:

| Chain | Live evidence required |
| --- | --- |
| Discovery | Matching `ROS_DOMAIN_ID` and middleware, expected topic names and types, fresh `/clock`, and no duplicate publishers. |
| Cmd_vel | `/<instance>/cmd_vel` subscriber exists, a bounded nonzero command changes the robot, and a later zero command stops it. |
| Sensor and odometry | For Orsus, fresh image samples under `camera` and fresh `/<instance>/cloud` plus `/<instance>/odometry` samples under `ros`; scan additionally requires the external conversion pipeline. For aerial robots, fresh camera samples under `camera` and LiDAR plus applicable base-sensor samples under `ros`. |
| TF | Timestamped `odom -> base_link` plus static `base_link -> lidar_link`, and Nav2's `map -> odom` when localization is active. |
| Nav2 | Nodes reach active lifecycle state, `navigate_to_pose` accepts the goal, feedback advances, terminal status is successful, and final pose is plausible. |
| Manipulator | UR5 command subscribers and changing `joint_states`/`ee_pose`; Z1 remains a known main-session activation gap. |

Catalog declarations and `tmp/runtime_interfaces.json` describe capability/runtime intent; except for filtered cmd_vel entries, they do not prove that a publisher, graph, TF chain, or Nav2 action is live.

### UI and Env DIY Verification

Node.js 20 LTS or a newer LTS release is required, with ECMAScript-module support and unqualified Web Crypto plus the built-in `Request`, `Response`, and `fetch` globals. Node 18's global Web Crypto availability was not reliable across its releases, so Node 18 is not a supported baseline for the OAuth worker check. Neither check requires `npm install` or network access:

```bash
node --version
node -e 'const major = Number(process.versions.node.split(".")[0]); if (major < 20 || !process.release.lts) throw new Error("Node.js 20+ LTS is required"); for (const name of ["crypto", "Request", "Response", "fetch"]) if (!(name in globalThis)) throw new Error(`Missing global: ${name}`); if (!crypto.subtle) throw new Error("Missing Web Crypto subtle API");'
node tools/check_env_diy_runtime.mjs all
node tools/github_oauth_worker/oauth_worker_test.mjs
```

The first checker validates required/retired HTML markers, unique IDs, referenced local images, and inline JavaScript syntax. The OAuth worker test uses a fake fetch implementation to validate signed state, origin restrictions, redirects, response escaping, and token non-disclosure. On 2026-08-11, the observed guide-writing run used Node `v22.22.2` and printed `PASS: Env DIY runtime HTML contract` and `GitHub OAuth Worker tests passed`; that is dated environment evidence, not a guarantee for another Node installation.

There is no tracked browser automation and no tracked Kit UI automation. Manually verify pywebview layout, selection/save/run behavior, keyboard and mouse interaction, display scaling, and error presentation for lightweight UI changes. For 3D extension changes, verify extension startup, preview creation/replacement, placement, save/cancel, transition to the formal stage, and cleanup inside the supported Kit runtime. Ignored local UI tests are not maintained evidence and must not be cited as repository coverage.

### Selecting Tests by Change Type

| Change | Minimum lightweight verification | Additional evidence |
| --- | --- | --- |
| Catalog, selection, or saved JSON | No tracked pytest exists; a new tracked module using the `test_*.py` or `*_test.py` basename contract is required for changed behavior. For current lightweight evidence, run `PYTHONPATH=source/EAI python -c 'from EAI.hmrs_env.env_diy.catalog import robot_keys, scene_choices; assert robot_keys() and scene_choices()'`, the section 13 storage command, and `python simulator.py interfaces scene --env keyboard --json >/dev/null`. | Add synchronization assertions for every affected duplicated UI/builder registration. |
| Requirement graph or mapping | No tracked test covers `resolve_selection()`, `resolve_card_requirement()`, or the requirement path maps; changed mapping behavior requires a new tracked test. Current pure evidence is `PYTHONPATH="$PWD/source/EAI:$PWD/source/EAI_assets" python -c 'from EAI_assets.asset_requirements import resolve_card_requirement, resolve_selection; graph=resolve_selection({"scene_key":"plane","robots":[{"type":"carter","attachments":[]}]}); ids={item.id for item in graph.requirements}; assert {"scene:plane","robot:carter","controller:CARTER_DIFF_CFG"} <= ids; assert resolve_card_requirement("robot:carter").id == "robot:carter"'`. | Synchronize catalog, requirement maps, builder mappings, and provider paths; run an immutable provider dry-run when provider-backed paths change. |
| Resolver download, transaction, or integrity | Run `PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --rootdir="$PWD" -q source/EAI_assets/test/test_human_asset_registry.py source/EAI_assets/test/test_human_asset_distribution.py --deselect='source/EAI_assets/test/test_human_asset_distribution.py::test_tracked_checksum_manifest_matches_local_human_pack[characters]' --deselect='source/EAI_assets/test/test_human_asset_distribution.py::test_tracked_checksum_manifest_matches_local_human_pack[activities]' --deselect='source/EAI_assets/test/test_human_asset_distribution.py::test_tracked_checksum_manifest_matches_local_human_pack[motions]'`. | Run the checksum parameters only against the exact released pack; use isolated roots for real provider/install evidence. |
| Human manifest or schema metadata | Run `PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --rootdir="$PWD" -q source/EAI_assets/test/test_human_asset_registry.py`; then run `for EAI_JSON in usd/human/manifest.json usd/human/manifest.schema.json; do python -m json.tool "$EAI_JSON" >/dev/null; done`. | The JSON parser checks syntax only; the registry suite owns manifest/schema semantics. |
| Human audit metadata or migration | Run `PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --rootdir="$PWD" -q source/EAI_assets/test/test_human_activity_migration.py`; then run `python -m json.tool usd/human/audit-summary.json >/dev/null`. | The JSON parser checks syntax only; the migration suite owns repository audit invariants. Record optional `pxr` skips and run source/conversion integration only with reviewed external inputs. |
| Human pack-checksum metadata | Run all parameters of the owning node: `PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --rootdir="$PWD" -q source/EAI_assets/test/test_human_asset_distribution.py::test_tracked_checksum_manifest_matches_local_human_pack`; then run `python -m json.tool usd/human/pack-checksums.json >/dev/null`. | Every parameter asserts checksum-metadata structure before checking its installed payload. An absent pack skips only the later payload comparison; an installed matching pack passes; an installed mismatch fails and must be investigated. JSON parsing alone covers syntax, not these semantics. |
| Human action authoring | Run `PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --rootdir="$PWD" -q source/EAI_assets/test/test_human_action_authoring.py`. | Run the affected `pxr` cases and source/conversion integration only with reviewed external inputs. |
| Human animation runtime | Run `PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --rootdir="$PWD" -q source/EAI_assets/test/test_human_animation_runtime.py source/EAI_assets/test/test_human_path_follower.py source/EAI_assets/test/test_human_spawner.py source/EAI_assets/test/test_human_stage_runtime.py`. | Run the affected `pxr` adapter/stage cases when compatible OpenUSD bindings are available. |
| Env DIY HTML or OAuth worker | Run `node tools/check_env_diy_runtime.mjs all` and `node tools/github_oauth_worker/oauth_worker_test.mjs`. | Manual pywebview or Kit verification according to the changed frontend. |
| Launcher, controller, robot, scene, or attachment | No general tracked pytest exists; a new tracked module using the maintained basename contract is required for changed behavior. Run the read-only parse `python -c 'import ast; from pathlib import Path; files=("simulator.py", "source/EAI/EAI/controllers/base.py", "source/EAI/EAI/hmrs_env/multi_robot_direct_env.py", "source/EAI_hmrs/EAI_hmrs/env_builder.py"); [ast.parse(Path(p).read_text(encoding="utf-8"), filename=p) for p in files]'`. | Targeted Isaac launch through first reset, representative steps, and clean shutdown. |
| ROS2, interface, or Nav2 | No tracked pytest exists; a new tracked module using the maintained basename contract is required for changed behavior. Run the exact section 12 static commands, `python simulator.py --help >/dev/null`, `python simulator.py interfaces --help >/dev/null`, `python simulator.py interfaces list --json >/dev/null`, and `python simulator.py interfaces scene --env keyboard --json >/dev/null`. | Run the applicable discovery, cmd_vel, sensor/odometry, TF, Nav2, or manipulator rows from the live matrix above. |
| City Traffic human bridge | Run `PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --rootdir="$PWD" -q source/EAI/test/test_city_traffic_human_bridge.py`. | Run a targeted City Traffic integration only when its simulator/runtime prerequisites are available. |
| Other algorithm or demo | No tracked pytest exists; changed behavior requires a new tracked module using the maintained basename contract. Run `python -m demo.fire_rescue.main --help >/dev/null` for Fire Rescue CLI changes and the read-only planner parse `python -c 'import ast; from pathlib import Path; files=("algorithm/global_planner/core.py", "algorithm/global_planner/session.py", "algorithm/global_planner/tracking.py"); [ast.parse(Path(p).read_text(encoding="utf-8"), filename=p) for p in files]'` for global-planner changes. | Targeted heavy demo/integration run. Review ports 8766/8767 only for dashboard-enabled, non-headless Fire Rescue launches; follow the LLM prerequisites above for every real run. |

Coverage is absent for many launcher, Env DIY, ROS, algorithm, and demo paths. When behavior changes at an uncovered boundary, add a maintainable tracked test rather than relying on an ignored local file. Because the broad ignore rules can hide it, add a narrow negation after the last matching rule and verify both ignore and index state:

```bash
: "${EAI_NEW_TEST:?Set EAI_NEW_TEST to the new test path}"
test -f "$EAI_NEW_TEST"
if git check-ignore -q --no-index -- "$EAI_NEW_TEST"; then
  echo "New test is still ignored: $EAI_NEW_TEST" >&2
  exit 1
else
  EAI_IGNORE_STATUS=$?
  test "$EAI_IGNORE_STATUS" -eq 1 || exit "$EAI_IGNORE_STATUS"
fi
git ls-files --error-unmatch -- "$EAI_NEW_TEST" >/dev/null

EAI_TRACKED_TESTS=()
while IFS= read -r -d '' EAI_TEST; do
  case "${EAI_TEST##*/}" in
    test_*.py|*_test.py) EAI_TRACKED_TESTS+=("$EAI_TEST") ;;
  esac
done < <(git ls-files -z)
EAI_NEW_TEST_SELECTED=false
for EAI_TEST in "${EAI_TRACKED_TESTS[@]}"; do
  if [[ "$EAI_TEST" == "$EAI_NEW_TEST" ]]; then
    EAI_NEW_TEST_SELECTED=true
    break
  fi
done
"$EAI_NEW_TEST_SELECTED"
```

For an intended unignored test, quiet `git check-ignore -q --no-index` must return 1. Treat any other status as failure. Do not use verbose output as the assertion: `git check-ignore -v --no-index` can print the matching negation rule and return zero even though that rule means the path is unignored. `git ls-files --error-unmatch` confirms the file is staged or committed, while the final loop separately proves that the canonical basename selector includes it. Keep the exception as narrow as the maintained test; do not unignore a whole generated or local-test tree.

## 14. Debugging and Common Failures

Diagnose from the symptom toward the owning boundary. Start with read-only or pure checks, preserve the failing output, and perform heavy confirmation only after prerequisites are known. Do not change multiple environments, assets, and source mappings at once.

### Python, Package, and Saved-Environment Failures

Use this read-only interpreter and package-origin inventory first:

```bash
command -v python
command -v pip
python --version
pip --version
python -m pip --version
conda info --envs
python - <<'PY'
import importlib.util
import sys
from pathlib import Path

print("executable", sys.executable)
for name in ("EAI", "EAI_assets", "EAI_hmrs", "isaaclab", "isaacsim"):
    spec = importlib.util.find_spec(name)
    origin = None if spec is None else spec.origin
    print(name, None if origin is None else Path(origin).resolve())
PY
```

| Symptom | Likely boundary | Lightweight diagnosis | Heavy confirmation |
| --- | --- | --- | --- |
| `ModuleNotFoundError`, package resolves outside the checkout, or bare `pip` disagrees with `python -m pip` | Wrong Conda activation, mixed installers, or stale editable install | Compare every path above; run `python -m pip show EAI EAI_assets EAI_hmrs`; inspect `sys.path` without installing anything | Reactivate `env_isaaclab`, repeat controlled editable installs with that interpreter, then run the smallest affected import/test. |
| Isaac module imports fail, extension APIs differ, or startup crashes early | Unsupported Isaac Sim/Isaac Lab/Python combination | Record `python --version`, package origins, `python -m pip show`, and the Isaac installation's own version output | Start one minimal supported launcher case after matching Ubuntu 22.04, Isaac Sim 5.1, Isaac Lab 2.x, and the intended environment. The launcher has no comprehensive runtime version gate, so absence of a warning is not compatibility proof. |
| Saved environment is not found, `.json` is rejected, or only one case spelling works | `storage.validate_task_name()` and case-sensitive filesystem lookup | Set `EAI_ENV_NAME=keyboard`; run `test -f "source/EAI_hmrs/EAI_hmrs/envs/${EAI_ENV_NAME}.json"` and the section 13 storage command | Run `python simulator.py --env "$EAI_ENV_NAME"` only after asset prerequisites; supply no suffix and preserve exact filename case. |

Do not solve an import mismatch by installing packages into multiple interpreters. Fix the active interpreter boundary, then use its `python -m pip` consistently.

### Asset, Provider, Integrity, and LFS Failures

Keep credentials, gated approval, revision existence, ordinary-file completeness, and human-pack integrity as separate questions. These provider checks are read-only but network-dependent; never print the active token:

```bash
EAI_HF_REPO="${EAI_ASSETS_HF_REPO:-HuangQIjun/eai-simulator-assets}"
EAI_HF_REVISION="${EAI_ASSETS_HF_REVISION:-main}"
hf auth whoami
hf datasets info "$EAI_HF_REPO" --revision "$EAI_HF_REVISION"
hf download "$EAI_HF_REPO" \
  --type dataset \
  --revision "$EAI_HF_REVISION" \
  --include usd/robot/carter/carter.usd \
  --dry-run
```

| Symptom | Likely boundary | Lightweight diagnosis | Heavy confirmation |
| --- | --- | --- | --- |
| 401, invalid/expired token, or `AUTH_REQUIRED` | Authentication | `hf auth whoami`; inspect only whether `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, or the intended `HF_HOME` is configured, never its value | Reauthenticate with `hf auth login` in the simulator user's environment, then repeat the dry-run. |
| 403, gated denial, or `ACCESS_PENDING` | Dataset access approval | Confirm the authenticated account and repository ID | Check approval with the provider owner; a valid token does not grant gated access. |
| `Revision Not Found` | Requested branch/tag/commit is absent | Print only `EAI_HF_REPO` and `EAI_HF_REVISION`; run `hf datasets info` | Use the default `main` branch or select an existing immutable tag/commit. |
| Requested ordinary USD/controller file is still missing after a download | Requirement seed, provider path, allow pattern, or partial ordinary merge | Inspect the exact local path, requirement mapping, collected configuration paths, and provider dry-run | Retry from a deliberately isolated asset root and verify every selected/transitive path. Ordinary bundles have requested-file postchecks but no whole-bundle checksum or rollback guarantee. |
| `AssetIntegrityError`, revision/checksum mismatch, unsafe path, or failed human replacement | Human checksum metadata and staged-pack transaction | Parse `usd/human/pack-checksums.json`, compare its revision with `EAI_HF_REVISION`, and run the resolver subset in section 13 | Install the exact released human packs in an isolated root and run the specific checksum cases. Never bypass validation or hand-merge failed staging output. |

An LFS pointer is different from a resolver-missing production asset. Diagnose a known tracked LFS image without modifying it:

```bash
EAI_LFS_PATH=usd/picture/robot/carter.png
git ls-files --error-unmatch -- "$EAI_LFS_PATH"
git check-attr filter diff merge -- "$EAI_LFS_PATH"
git lfs status
if grep -aqm1 '^version https://git-lfs.github.com/spec/v1$' -- "$EAI_LFS_PATH"; then
  EAI_LFS_POINTER=true
else
  EAI_LFS_POINTER_STATUS=$?
  test "$EAI_LFS_POINTER_STATUS" -eq 1 || exit "$EAI_LFS_POINTER_STATUS"
  EAI_LFS_POINTER=false
fi
printf 'lfs_pointer=%s\n' "$EAI_LFS_POINTER"
```

`lfs_pointer=true` means the tracked object is not hydrated; `false` means the exact pointer marker was not found. A status greater than 1 is a diagnostic error. This check does not print binary content. Confirm Git LFS installation and remote access, then hydrate the exact file in a clean checkout with `git lfs pull --include="$EAI_LFS_PATH"`. Do not apply LFS advice to ignored resolver-managed production USD merely because its extension appears in `.gitattributes`.

### Simulator Resources, Physics, and Host Limits

| Symptom | Likely boundary | Lightweight diagnosis | Heavy confirmation |
| --- | --- | --- | --- |
| CUDA out of memory or allocation failure | Selected scene/robots/controllers/rendering exceed available GPU memory, or another process owns memory | Run `nvidia-smi` and `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv`; identify ownership before acting | Reduce `--num_envs`, selection size, sensors, or rendering in a process you started. Stop only your own process through its terminal or recorded PID; never kill unrelated GPU processes. |
| Log says animated humans use CPU PhysX despite `--device cuda` | Expected Isaac Sim 5.1 safety fallback | Inspect the saved JSON for a `Human` selection and retain the launcher warning in the report | Verify the human case with CPU physics while monitoring remaining GPU rendering/controller load. Do not force GPU PhysX for animated pose writes. |
| inotify warning or file-watcher exhaustion | Host kernel limits below launcher thresholds | Read `/proc/sys/fs/inotify/max_user_watches`, `max_user_instances`, and `max_queued_events`; run `tools/configure_inotify_limits.sh --dry-run` | An administrator may review and run `sudo tools/configure_inotify_limits.sh`; it writes `/etc/sysctl.d/90-eai-isaac-sim-inotify.conf` and changes live sysctls. This is a deliberate system change, not a routine test command. |

The launcher thresholds are 524288 watches, 1024 instances, and 32768 queued events. A warning is preflight guidance, not evidence that the later crash has the same cause.

### Env DIY and Qt Failures

Use the following without opening a window:

```bash
printf 'DISPLAY=%s\nWAYLAND_DISPLAY=%s\nQT_QPA_PLATFORM=%s\n' \
  "${DISPLAY:-}" "${WAYLAND_DISPLAY:-}" "${QT_QPA_PLATFORM:-}"
python - <<'PY'
import importlib.util
for name in ("webview", "PyQt6"):
    spec = importlib.util.find_spec(name)
    print(name, None if spec is None else spec.origin)
PY
dpkg-query -W -f='${Status}\n' libxcb-cursor0 2>/dev/null || true
node tools/check_env_diy_runtime.mjs all
```

| Symptom | Likely boundary | Lightweight diagnosis | Heavy confirmation |
| --- | --- | --- | --- |
| `No module named webview` | pywebview missing from the active Python | Inspect module origin and `python -m pip show pywebview PyQt6` | Install the repository package or reviewed `pywebview[qt]` dependency with the intended interpreter, then reopen the UI. |
| Qt xcb plugin error, missing `libxcb-cursor`, or blank/native-window failure | Qt platform plugin, system library, or display session | Inspect display variables, package status, `QT_PLUGIN_PATH`, and the static Node result | Have an administrator provision `libxcb-cursor0` when absent. For a controlled manual reproduction, set `QT_DEBUG_PLUGINS=1` and launch the lightweight UI from the intended display session; capture plugin diagnostics. |
| HTML checker passes but selection/save interaction fails | Python bridge or browser runtime rather than static markup | Check `webview_app.py`, storage normalization, and output path permissions | Manually exercise save/run/cancel in pywebview; for `--diy-3d`, inspect Kit extension lifecycle and stage replacement separately. |

Do not interpret the Node checker as browser rendering evidence. Do not install Qt system packages automatically during diagnosis on a shared host.

### Controller and Attachment Failures

Controller code resolution and controller resource loading occur at different times. The builder maps a configured name to a Python file; configuration construction can download/import that module. Model weights and other resources are collected after configuration construction, while `ControllerCfg.load()` executes on the environment's first reset.

```bash
EAI_CONTROLLER_CFG=CARTER_DIFF_CFG
EAI_CONTROLLER_BUNDLE=traditional/carter_diff
rg -n --fixed-strings "$EAI_CONTROLLER_CFG" \
  source/EAI_hmrs/EAI_hmrs/env_builder.py \
  source/EAI_assets/EAI_assets/asset_requirements.py
rg -n --fixed-strings "$EAI_CONTROLLER_BUNDLE" \
  source/EAI_hmrs/EAI_hmrs/env_builder.py \
  source/EAI_assets/EAI_assets/asset_requirements.py
find source/EAI_assets/EAI_assets/controller -maxdepth 4 -type f \
  \( -name '*.py' -o -name '*.pt' -o -name '*.onnx' \) \
  2>/dev/null | sort
```

| Symptom | Likely boundary | Lightweight diagnosis | Heavy confirmation |
| --- | --- | --- | --- |
| Named controller is unknown or mapped module cannot import | Catalog/`CONTROLLER_CFG_IMPORTS`, provider path, or transitive controller module | Inspect exact name mapping, requirement seed, local module, and import traceback | Run saved-environment preflight with isolated asset roots after immutable provider coverage exists. |
| Build succeeds but first reset fails loading policy/model | `ControllerCfg.load()`, referenced model path, framework/device compatibility | Inspect collected `model_path`, `nav_model_path`, or `locomotion_model_path` and file type/size; preserve reset traceback | Targeted Isaac launch through first reset on the supported device/framework. |
| Attachment rejected or appears at an invalid mount | Catalog compatibility, one-manipulator rule, or builder mount profile | Exercise the pure contract with concrete values below and inspect catalog/builder mount maps | Targeted 3D preview plus formal Isaac scene; verify transform, collision, controller, and graph activation. |

```bash
EAI_HOST_ROBOT=Carter
EAI_ATTACHMENT=z1
PYTHONPATH=source/EAI python - "$EAI_HOST_ROBOT" "$EAI_ATTACHMENT" <<'PY'
import sys
from EAI.hmrs_env.env_diy.catalog import attachment_supported, validate_attachment_types

host, attachment = sys.argv[1:]
print("supported", attachment_supported(host, attachment))
print("normalized", validate_attachment_types(host, [attachment]))
PY
```

Catalog compatibility does not prove a builder mount exists or that a ROS graph activates. A robot cannot host both UR5 and Z1, and visual-only cards are not runnable attachments.

### ROS2, TF, Odometry, and Nav2 Failures

Start with the no-ROS static tier in section 12. For a running graph, record discovery settings before inspecting data:

```bash
printf 'ROS_DOMAIN_ID=%s\nRMW_IMPLEMENTATION=%s\n' \
  "${ROS_DOMAIN_ID:-}" "${RMW_IMPLEMENTATION:-}"
ros2 topic list -t
ros2 topic info /carter_1/odometry --verbose
ros2 topic info /carter_1/cmd_vel --verbose
ros2 action list -t
ros2 lifecycle nodes
```

| Symptom | Likely boundary | Lightweight diagnosis | Live confirmation |
| --- | --- | --- | --- |
| Catalog lists a topic but `ros2 topic list` does not | Declaration exists, publisher/bridge activation failed, or discovery domains differ | Compare saved selection, runtime snapshot, catalog resolution, `ROS_DOMAIN_ID`, and middleware | Check publisher/subscriber counts and simulator graph logs; declarations alone are not activation. |
| Cmd_vel is discoverable but robot does not move | Wrong instance, bridge inactive, command timeout/shape, or controller application | Confirm `/<instance>/cmd_vel` type/count and selected `ros`/`keyboard` tool or launcher flag | Send a bounded command, observe motion, then send zero and confirm stop. Do not publish during read-only diagnosis. |
| Odometry exists but TF/Nav2 fails | Missing/stale `odom -> base_link`, incorrect timestamps/frames, absent `map -> odom`, or duplicate sensor publishers | Inspect one odometry sample, `/clock`, `/tf`, `/tf_static`, and configured robot/sensor profile | Run `ros2 run tf2_ros tf2_echo odom base_link` and `map base_link`; validate continuity and timestamps. Current topic is `/<instance>/odometry`, not `/<instance>/odom`. |
| `/<instance>/scan` is absent while cloud exists | Orsus declaration describes derived output but pointcloud-to-laserscan pipeline is not running | Inspect `/<instance>/cloud`, Nav2 launch, and `pointcloud_to_laserscan` node | Verify `scan_cloud` and `scan` rates/frames after starting the supported single-stack pipeline. |
| Nav2 nodes exist but goals stall or fail | Lifecycle, map/localization, TF, scan, odometry, planner/controller, or final cmd_vel chain | Inspect lifecycle nodes, action list, generated config, map path, and topic counts | Require active lifecycle, accepted goal, progressing feedback, terminal status, and plausible final pose. A zero `send_goal.py` exit code alone is insufficient. |

ROS2 Humble tools and `rclpy` programs normally use system Python 3.10, while the simulator remains in `env_isaaclab`. Do not fix one side by contaminating the other interpreter. The tracked suite provides no live ROS automation.

### Fire Rescue Port Ownership

The Fire Rescue dashboard binds WebSocket port 8766 and HTTP/data port 8767 only for non-headless launches. Inspect ownership before a dashboard-enabled launch:

```bash
ss -ltnp | rg ':(8766|8767)\b' || true
```

Current non-headless dashboard startup scans these ports and can send `SIGKILL` to other detected holders before binding. Do not casually start the dashboard-enabled demo on a shared host. If either port belongs to another user, service, or experiment, coordinate with its owner or choose an isolated host/session; do not terminate it. `python -m demo.fire_rescue.main --headless` does not start the dashboard or image stream, but it remains a heavy Isaac/EMOS integration launch, not a generic smoke test.

## 15. Generated Files, Caches, and Files Not to Commit

`.gitignore` is broad and order-sensitive. Repository-relative ignored runtime/cache roots include `/log/`, `/logs/`, `/tmp/`, `/.worktrees/`, `/.superpowers/`, `/docs/superpowers/`, `/.agents/`, `/.agentmemory/`, `/memory/`, `/data/`, `/.pretrained_checkpoints/`, `/datasets/`, `/test_results/`, controller downloads below `/source/EAI_assets/EAI_assets/controller/`, and most of `/usd/`. Recursive ignores also cover `__pycache__/`, `.pytest_cache/`, `.cache/huggingface/`, `node_modules/`, `test-results/`, `playwright-report/`, build directories, logs, outputs, videos, experiment tracking, generated documentation, and package metadata.

A clean committed checkout does not ignore `.internal/`. It is still private/internal data and must not be committed. Teams that need a local ignore must configure `.git/info/exclude` or a personal global excludes file, or add an intentional tracked `.gitignore` rule in a separately reviewed change. Never infer the committed ignore contract from a dirty worktree's `.gitignore`.

Runtime-specific repository output includes `tmp/runtime_interfaces.json`, Env DIY result JSON such as `tmp/task_diy_window_result.json`, preflight payloads when directed below `tmp/`, `ros2_cmd_vel.json`, `*.tmp`, local experiment output, downloaded controllers, external human packs, retarget caches, and custom actions. They are state or materialized dependencies, not source declarations.

System temporary output is outside the repository and has a different lifecycle. Nav2 generation uses paths matching `/tmp/eai_nav2_*`, and `run_nav2.sh` uses `/tmp/eai_nav2_sim.log` and `/tmp/eai_nav2_stack.log`. Other tools use uniquely created system temporary directories for staging. Do not confuse these with repository-relative `tmp/`, and do not remove broad `/tmp` content. A tool that owns a uniquely created path should clean only that validated path.

Tracked exceptions are intentional and must be established with the index, not inferred from ignore comments:

- Saved environments below `source/EAI_hmrs/EAI_hmrs/envs/*.json` may be maintained fixtures, but `test*.json` and `n.json` are ignored. Filenames still follow the section 9 contract.
- `usd/picture/**` contains maintained UI images.
- `usd/human/manifest.json`, `usd/human/manifest.schema.json`, `usd/human/audit-summary.json`, and `usd/human/pack-checksums.json` are maintained metadata exceptions.
- The tracked source and 16 tracked Python test modules remain tracked even when a later ignore pattern would match them. Ignore rules affect admission of untracked files, not files already in the index.

The earlier MuSHR exception comments and negations are nullified by later `/usd/*`, controller-root, and test-root ignore rules. They do not make MuSHR production USD, controller code, or tests eligible automatically. Likewise, the generic final `test_*.py` and `*_test.py` rules hide new tests almost anywhere unless a later narrow negation applies. Always inspect the last matching rule.

Secrets are prohibited regardless of ignore state. `.env` is not currently ignored. Never store or commit Hugging Face tokens, API keys, OAuth secrets, private asset URLs, internal notes, credentials, or copied configuration containing them. Do not rely on `.gitignore` to protect sensitive data, and do not print a token while collecting diagnostics.

Before committing, inventory both worktree and index:

```bash
git status --short
git diff --cached --name-status
git diff --cached --check
EAI_CANDIDATE=tmp/runtime_interfaces.json
git check-ignore -v --no-index -- "$EAI_CANDIDATE" || true
git ls-files --error-unmatch -- "$EAI_CANDIDATE" 2>/dev/null || true
```

For every proposed source, fixture, or test, run `git check-ignore -v --no-index -- "$EAI_CANDIDATE"`; for every file claimed to be maintained, require `git ls-files --error-unmatch -- "$EAI_CANDIDATE"`. For an intended unignored file, quiet `git check-ignore -q --no-index -- "$EAI_CANDIDATE"` must return 1. Do not treat verbose status as the assertion because a printed negation rule can still return zero. A clean `git status` does not prove an ignored artifact is safe to publish.

## 16. Git, Branch, Commit, and Documentation Rules

GitHub is the public community and review entry point; maintainers perform canonical development in the internal GitLab repository and may port accepted GitHub pull requests into GitLab merge requests before mirroring results back. Follow `CONTRIBUTING.md` and the linked community workflow rather than assuming the two repositories are independent release authorities.

Allowed primary branches are `main`, `master`, `develop`, and `development`. Topic branches use one of these patterns with a nonempty name: `feature/`, `bugfix/`, `fix/`, `hotfix/`, `release/`, `chore/`, `build/`, `docs/`, `refactor/`, `style/`, `test/`, `ci/`, or `perf/`.

Every regular commit message must start with the related issue identifier:

```text
#123 concise description
```

Maintainers use the internal GitLab IID after promotion; public contributors may use the GitHub issue number. Merge, Revert, `fixup!`, `squash!`, and `amend!` commits are exempt. Conventional prefixes such as `feat:`, `fix:`, or `docs:` are not regular-message exemptions by themselves. `CONTRIBUTING.md` is the normative policy. The current `.githooks/commit-msg` implementation is only a prefix heuristic: it accepts any subject beginning with `Merge`, `Revert`, `fixup!`, `squash!`, or `amend!`, even when the commit is not genuinely Git-generated or a valid fixup. Passing that hook does not prove policy compliance.

`tools/setup-git-hooks.sh` sets `core.hooksPath` to `.githooks` and makes the repository hooks executable. Diagnose the resulting state directly:

```bash
git config --get core.hooksPath
find .githooks -maxdepth 1 -type f -perm -u+x -print | sort
git lfs version
```

These commands diagnose configuration only. Do not infer that repository hooks are inactive from the setup helper's status alone, and do not overwrite `.githooks` with a second hook manager without maintainer agreement. If Git LFS is missing, `.githooks/post-commit` can print an error after the commit has already been created; inspect `git log -1 --oneline` and `git status --short` before retrying so that the same change is not committed twice.

Activating `.githooks` also activates the current `post-checkout` warning. For a nonconforming branch it prints destructive delete/recreate advice and interpolates the branch name into copyable commands without shell quoting. Never copy that advice. After manually reviewing both names, use a quoted rename only:

```bash
EAI_CURRENT_BRANCH=$(git branch --show-current)
EAI_NEW_BRANCH=feature/reviewed-name
printf 'rename %s -> %s\n' "$EAI_CURRENT_BRANCH" "$EAI_NEW_BRANCH"
git check-ref-format --branch "$EAI_NEW_BRANCH"
git branch -m -- "$EAI_CURRENT_BRANCH" "$EAI_NEW_BRANCH"
```

Assume the worktree can already contain someone else's tracked and untracked work. At the start and before every commit:

```bash
git status --short
git diff --cached --name-status
```

Read overlapping changes and work with them. Do not delete, rewrite, stage, restore, or revert unrelated files. Never use destructive Git commands to obtain a clean tree. Enumerate the exact task paths, review every worktree diff, stage only those paths, and audit every staged entry and its cached content before committing.

The following is an executable **AGENTS-only example**, not a generic list of paths:

```bash
git diff -- AGENTS.md
git add -- AGENTS.md
git diff --cached --name-status
git diff --cached -- AGENTS.md
git diff --cached --check

EAI_SECRET_PATTERN="[\"']?(api[_-]?key|token|secret|password|private[_-]?key)[\"']?[[:space:]]*[:=][[:space:]]*[\"']?[^[:space:]\"'\$<{][^[:space:]\"']{7,}"
EAI_SECRET_LOCATIONS=$(
  git grep --cached -n -I -i -E \
    "$EAI_SECRET_PATTERN" \
    -- AGENTS.md |
    awk -F: '{print $1 ":" $2 ": possible secret-like assignment"}'
)
if [[ -n "$EAI_SECRET_LOCATIONS" ]]; then
  printf '%s\n' "$EAI_SECRET_LOCATIONS" >&2
  exit 1
fi
```

The case-insensitive heuristic covers common bare assignments plus quoted JSON and Python-dictionary keys, and emits only staged file and line locations, never the matched value. It still misses encoded, split, indirect, non-keyed, and high-entropy secrets and does not inspect binary content. Zero heuristic findings are not proof that the staged content is secret-free. Investigate every finding, manually review the complete cached patch, and run an approved maintained scanner when the project or host provides one. If no such scanner is available, report that fact and these heuristic limitations instead of claiming clearance. `git diff --cached --check` detects whitespace errors only; it is not a content, scope, or secret audit. For any other task, use the same sequence with the reviewed explicit paths and require `git diff --cached --name-status` to contain exactly those paths before commit.

Keep commits focused on one issue and behavior. Update `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, user documentation, examples, and migration notes in the same change when their owned public behavior, configuration, compatibility, or workflow changes; do not edit generated documentation output as a substitute for its source.

A pull request or merge request must link the issue/discussion, explain user-visible behavior, list exact commands and observed results, identify unrun heavy/network/system checks, call out compatibility and migration concerns, and include screenshots for UI changes. Asset-backed changes additionally name the dataset repository, immutable revision, exact remote paths, hashes/sizes, provenance/license, and clean-root verification. Never claim that an unrun check passed.

## 17. Definition of Done

Use this checklist for every change; omit an item only when it is demonstrably outside scope, and state why in the final report. Staging and index checks apply only when the user requested or authorized staging or a commit. Otherwise preserve the existing index, review `git status --short` plus the exact scoped worktree diff, and report staged/index checks as not applicable.

- [ ] The requested scope is explicit, `git status --short` was reviewed before editing, unrelated tracked/untracked work was preserved, and source/configuration/tests rather than secondary documentation determined behavior.
- [ ] Every affected authority in the source-of-truth map was updated together: catalog, builder/runtime registration, requirement mapping, controller mapping, interface declaration, duplicated UI vocabulary, saved schema, or attachment compatibility as applicable.
- [ ] Public schema, JSON normalization, naming, case, instance endpoints, backward compatibility, and migration behavior were reviewed; documentation and maintained examples changed with public behavior.
- [ ] Tests were selected from the repository-wide canonical `git ls-files -z` inventory, not directory discovery. New tests are maintainable, narrowly unignored after the last matching rule, and present in the canonical basename selection. After staging is authorized, also require quiet `git check-ignore -q --no-index` to return 1 and `git ls-files --error-unmatch` to confirm the intended staged path.
- [ ] The smallest relevant pure tests, JSON/YAML parsers, shell syntax checks, AST/compile checks, Node checks, and CLI smoke commands were run with exact commands and their pass/fail/skip counts recorded.
- [ ] Ambient pytest plugin autoload was controlled where appropriate; skips and installed-asset-dependent failures were investigated rather than hidden.
- [ ] Optional Isaac, GPU, ROS2, Nav2, browser, Kit, network, or system-level checks were run only with their prerequisites and authorization. Exact versions, selection, device, asset state, and results were recorded; every unrun check and resulting limitation is explicit.
- [ ] A simulator change was verified through configuration construction, first reset/controller load, representative behavior, and clean shutdown when heavy integration was available. A `pxr` test or opened window was not substituted for this evidence.
- [ ] ROS/interface changes distinguish catalog declaration, snapshot presence, discovery, samples/rates, TF, command application, lifecycle/action status, and final behavior. Z1 and other known activation gaps are not reported as working.
- [ ] Provider-backed files have maintainer publication evidence: repository ID, immutable tag/commit, exact paths, sizes and hashes, provenance and license, synchronized source maps/checksum metadata/default revision, and verification from clean asset roots. A local file or moving `main` dry-run is not sufficient.
- [ ] Human-pack integrity metadata matches the selected provider payload and revision. For reproducible validation, pin an immutable provider tag or commit and verify that `pack-checksums.json` checksums match it before claiming standard asset-backed first-launch support.
- [ ] `git status --short` and every exact scoped worktree diff were reviewed. When staging/commit was authorized, `git diff --cached --name-status` contains exactly the intended paths, `git diff --cached -- <each exact path>` was inspected, `git diff --cached --check` passed, secret findings were reported only as redacted file/line locations and investigated, and the maintained scanner result or heuristic limitation was recorded. When staging was not authorized, the index was preserved and these cached checks were reported as not applicable. Ignore diagnostics and tracked-file inventory show no `.env`, private/internal notes, downloaded assets, weights, caches, runtime snapshots, generated output, or unrelated staged files.
- [ ] The final report states the exact files changed, behavioral/documentation outcome, commands and results, commit SHA when applicable, remaining work, and all validation limitations without converting observed local baseline counts into project guarantees.

## 18. Quick Command Reference

These commands assume the repository root. Use the detailed workflows and limitations in sections 4, 8, 11, 12, and 13 when a quick command crosses an environment or service boundary.

### Environment and Package Setup

**Local shell mutation; prerequisite: an existing Isaac Lab installation with the `env_isaaclab` environment.** Activation itself does not install or download anything:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
command -v python
command -v pip
pip --version
python -m pip --version
```

**Network and system mutation; prerequisite: review section 4 first.** The helper performs editable package installs and can run `apt-get` through `sudo` to install `libxcb-cursor0`:

```bash
./tools/install_packages.sh
```

**Conda environment mutation and possible package-index network; prerequisites: activate the intended environment and verify that `python -m pip` resolves to it.** When system packages are managed separately, use the controlled editable installs from section 4:

```bash
python -m pip install -e source/EAI
python -m pip install -e source/EAI_assets
python -m pip install -e source/EAI_hmrs
```

### Discover Saved Environments and Selectable Catalog Entries

List and parse only tracked saved-environment JSON, excluding ignored local experiments:

```bash
git ls-files -z 'source/EAI_hmrs/EAI_hmrs/envs/*.json' |
while IFS= read -r -d '' EAI_ENV_PATH; do
  python -m json.tool "$EAI_ENV_PATH" >/dev/null
  EAI_ENV_FILE=${EAI_ENV_PATH##*/}
  printf '%s\n' "${EAI_ENV_FILE%.json}"
done
```

Query the pure shared catalog without importing Isaac Sim or Isaac Lab. These are selectable names and compatibility declarations; runnable wiring still depends on `env_builder.py` and the asset requirement maps:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=source/EAI python - <<'PY'
from EAI.hmrs_env.env_diy import catalog

print("scenes", *(key for key, _label in catalog.scene_choices()))
print("robots", *catalog.robot_keys())
print("controllers", *catalog.controller_cfg_names())
for name, entry in catalog.attachment_catalog().items():
    print("attachment", name, "hosts", *entry.supported_robots)
PY
```

Parse the builder source without importing it to discover the actual scene, robot, and controller registrations. The script requires ordinary Python only; it does not import or start Isaac Sim or Isaac Lab:

```bash
PYTHONDONTWRITEBYTECODE=1 python - <<'PY'
import ast
from pathlib import Path

path = Path("source/EAI_hmrs/EAI_hmrs/env_builder.py")
tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
assignments = {}
for node in tree.body:
    if not isinstance(node, ast.Assign):
        continue
    for target in node.targets:
        if isinstance(target, ast.Name):
            assignments[target.id] = node.value

def option_keys(assignment_name, constructor_name):
    value = assignments[assignment_name]
    assert isinstance(value, (ast.List, ast.Tuple))
    keys = []
    for item in value.elts:
        assert isinstance(item, ast.Call)
        assert isinstance(item.func, ast.Name) and item.func.id == constructor_name
        assert item.args and isinstance(item.args[0], ast.Constant)
        assert isinstance(item.args[0].value, str)
        keys.append(item.args[0].value)
    return keys

controller_map = assignments["CONTROLLER_CFG_IMPORTS"]
assert isinstance(controller_map, ast.Dict)
controller_keys = []
for key in controller_map.keys:
    assert isinstance(key, ast.Constant) and isinstance(key.value, str)
    controller_keys.append(key.value)

print("runtime scenes", *option_keys("SCENE_OPTIONS", "SceneOption"))
print("runtime robots", *option_keys("ROBOT_OPTIONS", "RobotOption"))
print("runtime controllers", *controller_keys)
PY
```

Compare the shared and runtime outputs: a scene or robot declaration is not runnable until it has a matching builder option, and a controller name is not loadable until it has a `CONTROLLER_CFG_IMPORTS` entry plus provider requirements. Attachments remain shared-catalog compatibility declarations whose formal mount and spawn branches must be checked separately.

### Inspect Interface Declarations Without Isaac

These commands load declarations and saved selection data only. Their output describes declared capability, not a live bridge, publisher, subscriber, or OmniGraph:

```bash
PYTHONDONTWRITEBYTECODE=1 python simulator.py interfaces list --json
PYTHONDONTWRITEBYTECODE=1 python simulator.py interfaces search --protocol ros2 --json
PYTHONDONTWRITEBYTECODE=1 python simulator.py interfaces show ros.cmd_vel --json
PYTHONDONTWRITEBYTECODE=1 python simulator.py interfaces scene --env keyboard --json
```

### Run Focused Lightweight Checks

Discover tracked Python test modules with the repository-wide section 13 naming contract:

```bash
git ls-files -z |
while IFS= read -r -d '' EAI_TEST; do
  case "${EAI_TEST##*/}" in
    test_*.py|*_test.py) printf '%s\n' "$EAI_TEST" ;;
  esac
done
```

Run one tracked pure-Python module without ambient pytest plugins or a repository pytest cache:

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs${PYTHONPATH:+:$PYTHONPATH}" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q \
    source/EAI_assets/test/test_human_path_follower.py \
    -p no:cacheprovider
```

Run the two tracked Node.js checks:

```bash
node tools/check_env_diy_runtime.mjs all
node --test tools/github_oauth_worker/oauth_worker_test.mjs
```

Parse representative Python and maintained YAML without generating repository bytecode caches:

```bash
PYTHONDONTWRITEBYTECODE=1 python - <<'PY'
import ast
from pathlib import Path
import yaml

for path in (
    Path("simulator.py"),
    Path("algorithm/keyboard/keyboard.py"),
    Path("algorithm/ros/nav2/nav2_setup.py"),
    Path("algorithm/ros/nav2/nav2.launch.py"),
    Path("demo/fire_rescue/main.py"),
):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

yaml_paths = [Path("algorithm/ros/nav2/nav2_profiles.yaml")]
yaml_paths += sorted(Path("source/EAI/EAI/interface_catalog/interfaces").rglob("*.yaml"))
yaml_paths.append(Path("demo/fire_rescue/assets/factory_map.yaml"))
for path in yaml_paths:
    yaml.safe_load(path.read_text(encoding="utf-8"))
PY
```

### Launch the Simulator and Env DIY

**Heavy Isaac/GPU or CPU-PhysX/provider/network command.** Requires the supported simulator environment, display or headless configuration, sufficient resources, and every selected gated asset at a usable provider revision:

```bash
python simulator.py --env keyboard
```

**Chooser/GUI command with possible heavy side effects.** Requires terminal input or the lightweight Qt/pywebview UI; choosing Run proceeds into the same Isaac/provider workflow:

```bash
python simulator.py
```

**Heavy Kit GUI/GPU/provider/network command.** Requires the Env DIY extension dependencies, a display, Isaac Sim, and selected assets; it opens the in-process 3D authoring workflow:

```bash
python simulator.py --diy-3d
```

### Run Keyboard, ROS2, and Nav2 Workflows

**Heavy Isaac/provider plus live ROS graph command.** Run in `env_isaaclab`; bridge setup and asset availability are required, and the bridge has no stale-command watchdog:

```bash
python simulator.py --env keyboard --enable-cmd-vel-bridge
```

**External ROS2 command.** In a separate Ubuntu ROS2 Humble shell, use system Python 3.10 and a running matching bridge. The publisher sends zero on normal switching and exit; still verify the robot stops:

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/keyboard/keyboard.py --robot carter_1
```

**Heavy Isaac/GPU/provider plus live ROS bridge prerequisite for Nav2.** In `env_isaaclab`, start the tracked `nav2` selection before the separate system ROS2 launch. It selects Factory, `carter_1`, one Orsus, and both the `camera` and `ros` tools: `camera` enables the Orsus image graphs, while `ros` enables its point-cloud/odometry graph and cmd_vel bridge consideration. The launch requires the selected gated assets plus matching ROS discovery settings:

```bash
python simulator.py --env nav2
```

**Live Nav2/ROS2 system command.** In a separate shell after the simulator above is running, source ROS2 Humble and require Nav2 including `pointcloud_to_laserscan`, the simulator's fresh `tmp/runtime_interfaces.json`, matching ROS discovery settings, and the tracked Factory map below. It starts a single global Nav2 stack and writes generated configuration below the system temporary directory:

Run the exact `/tmp/eai_nav2_carter_1` ownership and symlink preflight from section 12 immediately before this launch. If the host is shared or untrusted, use an isolated session or host instead.

```bash
source /opt/ros/humble/setup.bash
ros2 launch algorithm/ros/nav2/nav2.launch.py \
  robot_name:=carter_1 \
  robot_type:=Carter \
  sensor:=orsus \
  scene:=factory \
  map:="$(pwd)/demo/fire_rescue/assets/factory_map.yaml" \
  rviz:=false
```

For `carter_1`, `nav2_setup.py` reuses the predictable system path `/tmp/eai_nav2_carter_1` and overwrites `nav2_params.yaml`, `pointcloud_to_laserscan.yaml`, `view.rviz`, and `meta.txt` there. Do not store unrelated or user data in that directory.

Do not substitute `algorithm/ros/nav2/run_nav2.sh` on a clean checkout: its implicit map is untracked. Do not use `algorithm/ros/tools/quick_test_nav2.sh`: it calls an absent test program. See section 12 for generation, command-stop, TF, lifecycle, and goal-result checks.

### User Documentation

Build the hosted user documentation (Sphinx + `myst_parser` + Furo, Chinese by default) with the `env_isaaclab` environment, which provides `sphinx-build`; the output directory is git-ignored:

```bash
conda activate env_isaaclab
make -C docs clean && make -C docs html
python3 -m http.server 8090 --directory docs/build/html
```

See `Add or Update a User Documentation Page` in section 8 for the sidebar-template and toctree registration rules.

### Run Fire Rescue

The help path is lightweight, uses the supported module entry point, and does not open an Isaac session or make an LLM request:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m demo.fire_rescue.main --help
```

**Heavy Isaac/GPU/provider/network/LLM command.** The default EMOS preset expects a real provider API key and can incur network cost, latency, and timeouts. Review credentials and provider terms without printing secrets. Headless mode avoids the dashboard ports but still launches the simulator and EMOS:

```bash
python -m demo.fire_rescue.main --headless
```

Non-headless Fire Rescue can send `SIGKILL` to existing holders of ports 8766 and 8767 before starting its dashboard. Inspect and coordinate port ownership as described in section 14 rather than using dashboard mode on a shared host.

## 19. Task-to-File Lookup Index

Directories in this table mean the tracked source inventory below that directory. Generated `tmp/runtime_interfaces.json`, downloaded USD/controller payloads, and other runtime material are deliberately not authorities.

| Task | Authoritative path(s) | Guide section(s) and boundary |
| --- | --- | --- |
| Startup / CLI | `simulator.py` | Sections 5, 6, 7, 8, and 13; owns argument dispatch, application/session lifecycle, preflight, loop, and shutdown. |
| Environment JSON | `source/EAI/EAI/hmrs_env/env_diy/storage.py`; `source/EAI/EAI/hmrs_env/env_diy/flow.py`; `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI_hmrs/EAI_hmrs/envs/` | Sections 6, 8, 9, and 10; filenames are stems, JSON is normalized selection data, not Gym registration. |
| Robot | `source/EAI_assets/EAI_assets/robots/`; `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI_assets/EAI_assets/asset_resolver.py`; `source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html`; `source/EAI_env_diy/EAI_env_diy/ui.py`; `source/EAI_env_diy/EAI_env_diy/preview_stage.py`; `usd/picture/processed/robot/`; `source/EAI/EAI/interface_catalog/interfaces/robots/mobile_base.yaml` | Sections 7, 8, 10, and 11; synchronize assets, selection/runtime/requirements, provider resolution, both UIs, preview, image, and interface aliases. |
| Scene | `source/EAI_assets/EAI_assets/scene/`; `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI_assets/EAI_assets/asset_resolver.py`; `source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html`; `source/EAI_env_diy/EAI_env_diy/ui.py`; `source/EAI_env_diy/EAI_env_diy/preview_stage.py`; `usd/picture/scene/` | Sections 7, 8, 10, and 11; synchronize assets, selection/runtime/requirements, provider resolution, both UIs, 3D preview, and image. |
| Controller base | `source/EAI/EAI/controllers/base.py`; `source/EAI/EAI/hmrs_env/multi_robot_direct_env.py`; `source/EAI/EAI/hmrs_env/multi_robot_direct_env_cfg.py` | Sections 5, 7, and 8; owns resource loading, command-to-action callbacks, application, and spaces. |
| Traditional controller | `source/EAI/EAI/controllers/base.py`; `source/EAI/EAI/controllers/differential_drive_controller.py`; `source/EAI/EAI/controllers/ackermann_controller.py`; `source/EAI/EAI/controllers/ik_controller.py`; `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_hmrs/EAI_hmrs/controller_loader.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI_assets/EAI_assets/asset_resolver.py`; `source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html`; `source/EAI_env_diy/EAI_env_diy/ui.py` | Sections 5, 8, 10, and 11; synchronize the callback/adapter contract, selectable name, lazy mapping, requirement seed, provider bundle, and exposed UI names. |
| RL controller | `source/EAI/EAI/controllers/__init__.py`; `source/EAI/EAI/controllers/base.py`; `source/EAI/EAI/controllers/utils.py`; `source/EAI/EAI/controllers/rsl_controller.py`; `source/EAI/EAI/controllers/skrl_controller.py`; `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_hmrs/EAI_hmrs/controller_loader.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI_assets/EAI_assets/asset_resolver.py`; `source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html`; `source/EAI_env_diy/EAI_env_diy/ui.py` | Sections 5, 8, 10, and 11; keep public exports, shared helpers, adapter semantics, selectable/import/requirement mappings, framework configuration, provider weights, and UI names compatible. |
| Sensor | `source/EAI_assets/EAI_assets/sensor/`; `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI_assets/EAI_assets/asset_resolver.py`; `source/EAI_env_diy/EAI_env_diy/preview_stage.py`; `source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html`; `source/EAI_env_diy/EAI_env_diy/ui.py`; `usd/picture/processed/sensor/`; `source/EAI/EAI/interface_catalog/interfaces/` | Sections 7, 8, 10, 11, and 12; synchronize cfg, compatibility, formal/preview mounts, requirements/provider resolution, both UIs, image, declaration, and actual publisher activation. |
| UR5 | `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI_assets/EAI_assets/asset_resolver.py`; `source/EAI_assets/EAI_assets/robots/manipulator_mount.py`; `source/EAI_assets/EAI_assets/robots/ur5_mount.py`; `source/EAI_env_diy/EAI_env_diy/preview_stage.py`; `source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html`; `source/EAI_env_diy/EAI_env_diy/ui.py`; `usd/picture/processed/manipulator/ur5.png`; `source/EAI/EAI/interface_catalog/interfaces/sensors/ur5.yaml`; `source/EAI/EAI/hmrs_ros/manipulator_omnigraph.py`; `source/EAI/EAI/hmrs_ros/ur5_omnigraph.py`; `simulator.py` | Sections 6, 8, 10, 11, and 12; synchronize compatibility, mount/preview assembly, controller requirements/provider resolution, UI/image, declarations, graph, and current main-session activation. |
| Z1 | `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI_assets/EAI_assets/asset_resolver.py`; `source/EAI_assets/EAI_assets/robots/z1.py`; `source/EAI_assets/EAI_assets/robots/manipulator_mount.py`; `source/EAI_assets/EAI_assets/robots/z1_mount.py`; `source/EAI_env_diy/EAI_env_diy/preview_stage.py`; `source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html`; `source/EAI_env_diy/EAI_env_diy/ui.py`; `usd/picture/processed/manipulator/z1.png`; `source/EAI/EAI/interface_catalog/interfaces/sensors/z1.yaml`; `source/EAI/EAI/hmrs_ros/manipulator_omnigraph.py`; `source/EAI/EAI/hmrs_ros/z1_omnigraph.py`; `simulator.py` | Sections 6, 8, 10, 11, and 12; synchronize the direct arm asset/actuator cfg, compatibility, mount/preview assembly, controller requirements/provider resolution, UI/image, declarations, and graph helpers; main-session activation remains absent. |
| Human animation | `source/EAI_assets/EAI_assets/humans/`; `source/EAI_assets/EAI_assets/asset_resolver.py`; `usd/human/README.md`; `usd/human/manifest.json`; `usd/human/manifest.schema.json`; `usd/human/pack-checksums.json`; `usd/human/audit-summary.json`; `tools/human_assets/` | Sections 3, 6, 8, 10, 11, and 13; keep registry/runtime, path and action contracts, maintained metadata, conversion/validation/authoring tools, external payloads, integrity, and publication rights synchronized. |
| Asset download | `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI_assets/EAI_assets/asset_resolver.py` | Sections 6, 7, 8, and 11; requirement seeds, transitive discovery, provider revision, installation, and human integrity are distinct boundaries. |
| Env DIY lightweight UI | `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI/EAI/hmrs_env/env_diy/flow.py`; `source/EAI/EAI/hmrs_env/env_diy/storage.py`; `source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html`; `source/EAI/EAI/hmrs_env/env_diy/webview_app.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI/setup.py`; `usd/picture/` | Sections 5, 6, 8, 9, and 10; keep selection, persistence, embedded vocabulary, bridge payload, builder/requirements, package data, and images synchronized. |
| Env DIY 3D | `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI/EAI/hmrs_env/env_diy/flow.py`; `source/EAI/EAI/hmrs_env/env_diy/storage.py`; `source/EAI_env_diy/EAI_env_diy/`; `source/EAI_env_diy/config/extension.toml`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI/setup.py`; `usd/picture/`; `simulator.py` | Sections 5, 6, 8, 9, and 10; synchronize the portable contract, Kit authoring/preview/download/result lifecycle, builder/requirements, package data, images, and stage transition. |
| ROS2 cmd_vel | `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI/EAI/hmrs_env/env_diy/flow.py`; `source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html`; `source/EAI_env_diy/EAI_env_diy/`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `usd/picture/processed/tool/`; `source/EAI/EAI/interface_catalog/interfaces/robots/mobile_base.yaml`; `source/EAI/EAI/hmrs_ros/cmd_vel_bridge.py`; `source/EAI/EAI/hmrs_ros/twist_subscriber.py`; `algorithm/keyboard/keyboard.py`; `simulator.py` | Sections 6, 8, 10, and 12; synchronize tool compatibility/exposure, builder/requirements, interface declaration, external Twist publication, bridge activation, command application, snapshot filtering, and a later zero stop. |
| Nav2 | `source/EAI_hmrs/EAI_hmrs/envs/nav2.json`; `algorithm/ros/nav2/nav2.launch.py`; `algorithm/ros/nav2/nav2_setup.py`; `algorithm/ros/nav2/nav2_profiles.yaml`; `algorithm/ros/nav2/tf_bridge.py`; `demo/fire_rescue/assets/factory_map.yaml`; `source/EAI/EAI/hmrs_ros/`; `simulator.py` | Sections 6, 12, 13, and 14; use the tracked matching selection and explicit map, and treat the current launcher as one global stack. |
| Interface catalog | `source/EAI/EAI/interface_catalog/interfaces/`; `source/EAI/EAI/interface_catalog/`; `source/EAI/EAI/hmrs_ros/`; `source/EAI/setup.py`; `simulator.py` | Sections 5, 6, 7, 8, 10, and 12; synchronize declarations, package data, real bridge/graph setup, and filtering; generated `tmp/runtime_interfaces.json` is runtime state, not authority. |
| Algorithm | `algorithm/emos/`; `algorithm/global_planner/`; `algorithm/city_traffic/human_bridge.py`; `algorithm/keyboard/keyboard.py`; `algorithm/ros/` | Sections 5, 7, 8, and 12; keep pure algorithm contracts separate from simulator and external-service adapters; city traffic has only the tracked human bridge, not a package API. |
| Fire Rescue demo | `demo/fire_rescue/main.py`; `demo/fire_rescue/config.py`; `demo/fire_rescue/scenario.py`; `demo/fire_rescue/experiment.py`; `demo/fire_rescue/runtime/`; `demo/fire_rescue/dashboard/`; `demo/fire_rescue/assets/`; `simulator.py` | Sections 5, 6, 7, 8, 13, and 14; enter through the reusable session and synchronize CLI/config/scenario/hooks/adapters/dashboard/assets while auditing external services. |
| RealSense D455 | `source/EAI_assets/EAI_assets/sensor/high_sensor/realsense_d455.py`; `source/EAI/EAI/hmrs_ros/realsense_d455_imu.py`; `source/EAI/EAI/hmrs_env/env_diy/catalog.py`; `source/EAI_hmrs/EAI_hmrs/env_builder.py`; `source/EAI_assets/EAI_assets/asset_requirements.py`; `source/EAI/EAI/interface_catalog/interfaces/sensors/realsense_d455.yaml`; `source/EAI_hmrs/EAI_hmrs/envs/mushr_realsense.json`; `algorithm/ros/tools/vis_sensors.py` | Sections 7, 8, 10, 11, and 12; synchronize the payload cfg and its publish graphs, the synthesized IMU manager, catalog/builder gates (`camera` vs `ros`), requirements/provider resolution, declarations, and the visualization tool's depth display. |
| User documentation | `docs/source/` pages; `docs/source/index.rst`; `docs/source/index_en.rst`; `docs/source/conf.py`; `docs/source/_templates/sidebar/navigation.html`; `docs/source/_templates/sidebar/navigation_en.html`; `docs/source/assets/media/` | Sections 8, 16, and 20; keep page content external-facing, keep the toctree and hardcoded sidebar entries synchronized across both languages, and commit media with the page; `docs/build/` is generated output, not authority. |
| Tests | `source/EAI/test/`; `source/EAI_assets/test/`; paths selected from `git ls-files -z` whose basenames match `test_*.py` or `*_test.py`; `tools/check_env_diy_runtime.mjs`; `tools/github_oauth_worker/oauth_worker_test.mjs` | Sections 7, 13, and 17; the tracked inventory and environment-dependent pass/skip counts are dynamic. |

## 20. Maintaining This AGENTS.md

Maintain this file by hand in the same focused change as the behavior, configuration, or workflow it describes. Source code, configuration, and tests remain authoritative; do not make this guide depend on hosted documentation. Re-run the relevant examples and update dated verification evidence as observations, never as guarantees for another checkout or environment.

| Change trigger | Sections to review |
| --- | --- |
| Runtime entry points, CLI arguments, preflight, application/session lifecycle, or shutdown | 5, 6, 7, 8, 13, 14, 17, 18, and 19 |
| Supported Ubuntu, Isaac Sim, Isaac Lab, CUDA/GPU, Conda, Python, ROS2, or Nav2 versions | 3, 4, 12, 13, 14, 17, and 18 |
| Package layout, install metadata, editable-install workflow, or import boundary | 4, 5, 7, 13, 15, 18, and 19 |
| Saved-environment filename, JSON normalization, schema, pose, attachment, or instance naming contract | 6, 8, 9, 10, 13, 17, 18, and 19 |
| Scene, robot, controller, attachment, tool, UI, or compatibility catalog | 7, 8, 9, 10, 13, 17, 18, and 19 |
| Controller cfg mapping, lazy loading, callback/space contract, transitive bundle, or model/config path | 5, 6, 7, 8, 10, 11, 13, 14, 17, 18, and 19 |
| Asset requirement or resolver behavior, provider repository/revision, integrity metadata, publication, or gated access | 3, 4, 6, 7, 8, 11, 14, 15, 17, 18, and 19 |
| ROS bridge, cmd_vel, sensor/manipulator graph, interface declaration/snapshot/probe, Nav2 profile, map, TF, or launch behavior | 5, 6, 7, 8, 10, 12, 13, 14, 17, 18, and 19 |
| Human asset, animation runtime, registry/action behavior, maintained metadata, conversion/migration/validation, cache, or provider payload | 3, 5, 6, 7, 8, 10, 11, 13, 14, 15, 17, 18, and 19 |
| Env DIY lightweight selection/UI/persistence or 3D Kit authoring/preview/download/result/extension behavior | 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 17, 18, and 19 |
| EMOS, global planner, city-traffic bridge, keyboard/ROS client, or another algorithm contract/integration | 5, 7, 8, 12, 13, 14, 15, 17, 18, and 19 |
| Demo or Fire Rescue CLI, config, scenario, runtime adapter/loop, dashboard, asset, simulator session, LLM, or external service | 5, 6, 7, 8, 11, 12, 13, 14, 15, 17, 18, and 19 |
| Test command, pytest isolation, tracked test inventory, Node check, skip gate, or verification tier | 2, 8, 13, 17, 18, and 19 |
| User documentation page, sidebar navigation template, media asset, or docs build | 7, 8, 16, 17, 18, 19, and 20 |
| Repository layout, generated/runtime path, ignore rule, tracked exception, Git LFS rule, hook, branch, or commit policy | 2, 4, 5, 7, 11, 15, 16, 17, 18, and 19 |
