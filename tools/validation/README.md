# Validation Tools

[Chinese](README.zh-CN.md)

These commands provide focused repository checks that run without a full simulator launch. Run them from the repository root. A passing result covers only the contract named below; it is not evidence that Isaac Sim, a GPU, live ROS2, network access, or downloaded assets work.

## Check inventory

| Command | What it checks | Runtime |
| --- | --- | --- |
| `python tools/validation/check_asset_download_errors.py` | Asset-preflight network/access error normalization, parent reports, worker payloads, visible diagnostics, and nonzero exit behavior | Python; uses mocks instead of downloading assets |
| `python tools/validation/check_documentation_consistency.py` | Release revision text, algorithm README inventory, public README image references, and hosted-documentation references when the hosted tree exists | Python; local files only |
| `python tools/validation/check_env_diy_exclusivity.py` | Shared Env DIY attachment validation, Orsus/LiDAR exclusivity, authoring-model validation, simulator validation, and Navigation I/O gates | Python; imports pure modules and test doubles, not Isaac |
| `node tools/validation/check_env_diy_runtime.mjs all` | Env DIY HTML structure, unique element IDs, local resource references, inline JavaScript syntax, and related runtime contracts | Node.js 20 LTS or newer |
| `python tools/validation/check_release_links.py` | Public release/download links and release revision consistency | Python; local files only |
| `python tools/validation/check_ros_distro_config.py` | Humble/Jazzy validation, resolution precedence, persisted selection, and invalid-value behavior | Python and Bash; does not import Isaac or ROS2 |
| `python tools/validation/check_scene_map_assets.py` | Seven canonical scene-map pairs, requirement expansion, normal-preflight path merging, and removal of algorithm/demo map copies | Python; local files and pure modules only |

## Recommended sequence

```bash
python tools/validation/check_asset_download_errors.py
python tools/validation/check_documentation_consistency.py
python tools/validation/check_env_diy_exclusivity.py
python tools/validation/check_release_links.py
python tools/validation/check_ros_distro_config.py
python tools/validation/check_scene_map_assets.py
node tools/validation/check_env_diy_runtime.mjs all
```

Each command exits nonzero when a check fails and prints the failed assertion or diagnostic. Run commands individually when isolating a failure; do not hide their exit codes in an unconditional shell pipeline.

## When to run them

- Run the documentation and release-link checks after changing README files, release references, algorithm documentation, or public image links.
- Run the Env DIY checks after changing its catalog, selection format, compatibility rules, HTML application, authoring model, or launcher gates.
- Run the asset-download error check after changing preflight workers, provider error translation, or parent/child reports.
- Run the scene-resource check after changing selectable scenes, resource declarations, external asset requests, preflight path collection, or occupancy-map ownership.
- Run the ROS distribution check after changing setup scripts, distribution precedence, or persisted configuration.

## Limitations

These scripts intentionally avoid expensive dependencies. Follow them with the relevant unit tests and, when behavior crosses a runtime boundary, a prepared integration check using the exact Isaac Sim, ROS2, assets, and hardware configuration affected by the change.
