# Human Asset Tools

[Chinese](README.zh-CN.md)

This directory contains the demo, action-authoring, conversion, migration, cache-building, and validation entry points for the `usd/human/` registry. Run every command from the repository root. See [`usd/human/README.md`](../../usd/human/README.md) for the full asset inventory and motion contracts.

All characters, textures, motions, and caches used by the public runtime live below `usd/human/`. Manifests store paths relative to the human root. Conversion and migration commands may read approved external inputs, but generated runtime content must not depend on a developer home directory or another absolute path outside the repository.

## Install the complete runtime assets

Request access to the gated [`rosolab/eai-simulator-assets`](https://huggingface.co/datasets/rosolab/eai-simulator-assets) dataset, then download the complete Human asset tree from the Git repository root:

```bash
hf auth login
hf download rosolab/eai-simulator-assets \
  --type dataset \
  --revision v0.1.0-beta.1 \
  --include "usd/human/**" \
  --local-dir .
```

This command fills `usd/human/` as one unit. The repository does not provide separate public downloads by character, motion, or pack.

## File responsibilities

| File | Runtime | Input | Output |
| --- | --- | --- | --- |
| `run_demo.py` | Isaac Sim 5.1 / `env_isaaclab` | Manifest, all 44 characters, 12 standard motions, and caches | All-character GUI or complete headless capability validation |
| `scene.py` | Imported by the demo in Isaac Sim | Stage, grid, routes, and character positions | Ground, routes, selection ring, bounds, and camera |
| `motion_controls.py` | Pure Python, imported by the demo | Q, number keys, Enter, X, and Esc | Character selection, motion/movement, and restore requests |
| `edit_action.py` | Pure Python | Action ID, duration, FPS, and profile | JSON keyframe draft |
| `import_action.py` | Isaac Sim 5.1 | One animated GLTF/GLB clip | Custom action USD and overlay manifest |
| `convert_gltf_assets.py` | Pure Python in plan mode; Isaac Sim in conversion mode | Approved urban-sim source root | Allowlist plan, USD files, and conversion report |
| `migrate_assets.py` | Pure Python; requires a validated conversion report | Approved source root and target human root | Manifest and audit summary |
| `build_motion_cache.py` | `pxr` / Isaac Sim | Manifest, optional overlay, character USD, and motion USD | Retarget cache JSON and report |
| `validate_assets.py` | Pure Python | Manifest and installed files | Deterministic JSON structure report |

## Common commands

### GUI validation

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
python -u tools/human_assets/run_demo.py
```

The demo sorts assets by stable ID and loads all 44 characters together. Press `Q` to restore the current character to its pre-action position, select the next character, move the selection ring, and focus the camera. For each of the 39 skeletal characters, enter `1-12` with the top number row and press `Enter` to play a standard motion. The four movable rigid activity characters accept only `1` and perform one outward-and-return movement. The static rider is selectable but rejects numbered actions. `Backspace` edits input, `X` stops and restores the current character, and `Esc` exits.

Every movable character starts still. The demo restores a character after a non-looping motion, after a looping motion is cancelled or replaced, when `Q` changes the selected character, and when a rigid movement ends. A motion such as `phone_call` with `path_policy=pause` stays in place; a motion with `path_policy=continue` follows the short path while playing.

Characters are grounded after spawn and motion transitions. Skinned characters use visible mesh points deformed by the current UsdSkel pose to determine ground height. Non-skinned meshes, or content whose skinning cannot be evaluated, fall back to USD bounds.

### Headless validation

Headless mode uses the same backend and control state machine. It validates all 39 by 12 skeletal motion combinations, four rigid round trips, and one static character:

```bash
conda run --no-capture-output -n env_isaaclab \
  python -u tools/human_assets/run_demo.py --headless
```

A successful run ends with `Verified unified human matrix: 39x12 + 4 + 1`.

### Create a JSON action draft

```bash
python tools/human_assets/edit_action.py init \
  --action-id wave --duration 2.0 usd/human/custom-actions/wave.json
```

### Import one GLTF/GLB clip

`usd/human/motions/sources/bow.gltf` is a self-contained relative-path example. Its buffer and images do not depend on files outside the repository:

```bash
python tools/human_assets/import_action.py usd/human/motions/sources/bow.gltf \
  --action-id bow-example --profile smplx_70 --human-root usd/human
```

The sample demonstrates the single-clip import flow only. `run_demo.py` does not convert or publish GLTF at startup and does not modify the overlay manifest or retarget cache.

### Plan or perform a source conversion

Generate a conversion plan without starting Isaac Sim:

```bash
python tools/human_assets/convert_gltf_assets.py \
  --source-root path/to/approved/urban-sim \
  --target-root usd/human --plan-only
```

Remove `--plan-only` to convert, and explicitly set `--result-json usd/human/conversion-report.json`. Conversion starts Isaac Sim. Review the allowlist, input licenses, and output paths before running it.

### Migrate converted assets

Run migration in dry-run mode first:

```bash
python tools/human_assets/migrate_assets.py \
  --source-root path/to/approved/urban-sim \
  --target-root usd/human --dry-run
```

After review, remove `--dry-run` to write `manifest.json` and `audit-summary.json`. This tool does not generate `pack-checksums.json`.

### Build a motion cache

Rebuild one motion cache for one character:

```bash
python tools/human_assets/build_motion_cache.py \
  --manifest usd/human/manifest.json \
  --overlay usd/human/custom-actions/manifest.json \
  --asset-id synbody-0000001 --motion-id bow
```

A cross-profile cache records rest rotations for the source and target skeletons. `UsdSkelAnimation` rotation samples are absolute local transforms. Playback first accumulates skeleton-space rotations through each hierarchy, applies the source skeleton-space rest-relative pose to the target rest pose, and then solves target local rotations. Cross-profile arm chains also transfer wrist position in the character body frame, use the source elbow as a bend-direction hint, and solve shoulder/elbow rotation from the target upper-arm and forearm lengths; wrist world orientation keeps the previous retargeting result. Strict same-profile caches do not add these fields and must preserve their original format after rebuilding.

### Validate installed assets

```bash
python tools/human_assets/validate_assets.py \
  --manifest usd/human/manifest.json \
  --output /tmp/eai-human-validation.json
```

`scene.py` and `motion_controls.py` are internal modules without standalone CLIs. `test_human_demo_controls.py` and `test_human_motion_number_controls.py` cover their behavior.

This version supports single-clip GLTF/GLB import and JSON keyframe action publication. It does not provide a general compound-motion authoring tool.

## Provider publication requirements

The Human provider must include `characters/`, `activities/`, and `motions/`. The self-contained `usd/human/motions/sources/bow.gltf` sample belongs to the motions pack but is not required to start the demo. Before publication, complete the unified Isaac GUI/headless validation, provenance and license approval, and exact pack hash generation.

After uploading to the Hugging Face provider, create an immutable tag, update `usd/human/pack-checksums.json`, and verify that pinned revision from a clean repository root. Source mappings, provider files, the tag, and checksum metadata must remain version-aligned.
