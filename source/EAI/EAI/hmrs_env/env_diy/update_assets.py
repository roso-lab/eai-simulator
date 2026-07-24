from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .paths import REPO_ROOT
from .prepare_assets import DEFAULT_OUTPUT_ROOT, DEFAULT_SOURCE_ROOT, GROUPS, MANIPULATOR_NAMES, process_tree


@dataclass(frozen=True)
class AssetUpdate:
    group: str
    source: Path
    target: Path
    reason: str


@dataclass(frozen=True)
class UpdateResult:
    updated_assets: tuple[AssetUpdate, ...]
    remaining_updates: tuple[AssetUpdate, ...]
    processed_sensors: tuple[str, ...]


def find_assets_needing_update(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    *,
    groups: tuple[str, ...] = GROUPS,
) -> list[AssetUpdate]:
    updates: list[AssetUpdate] = []
    for group in groups:
        source_dir = source_root / group
        if not source_dir.exists():
            continue
        for source in sorted(source_dir.glob("*.png")):
            if group == "sensor" and source.stem in MANIPULATOR_NAMES:
                if (source_root / "manipulator" / source.name).exists():
                    continue
                target_group = "manipulator"
            else:
                target_group = group
            target = output_root / target_group / source.name
            if not target.exists():
                updates.append(AssetUpdate(target_group, source, target, "missing"))
                continue
            if source.stat().st_mtime_ns > target.stat().st_mtime_ns:
                updates.append(AssetUpdate(target_group, source, target, "stale"))
    return updates


def processed_sensor_names(output_root: Path = DEFAULT_OUTPUT_ROOT) -> tuple[str, ...]:
    sensor_dir = output_root / "sensor"
    if not sensor_dir.exists():
        return ()
    return tuple(path.stem for path in sorted(sensor_dir.glob("*.png")) if path.stem not in MANIPULATOR_NAMES)


def processed_manipulator_names(output_root: Path = DEFAULT_OUTPUT_ROOT) -> tuple[str, ...]:
    manipulator_dir = output_root / "manipulator"
    if not manipulator_dir.exists():
        return ()
    return tuple(path.stem for path in sorted(manipulator_dir.glob("*.png")))


def run_update(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> UpdateResult:
    pending = tuple(find_assets_needing_update(source_root, output_root))
    if pending:
        process_tree(source_root, output_root)
    remaining = tuple(find_assets_needing_update(source_root, output_root))
    return UpdateResult(
        updated_assets=pending,
        remaining_updates=remaining,
        processed_sensors=processed_sensor_names(output_root),
    )


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Env DIY processed image assets.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    pending = find_assets_needing_update(args.source_root, args.output_root)
    if not pending:
        print("[EnvDIY] processed robot/payload/tool assets are already up to date.")
    else:
        print(f"[EnvDIY] found {len(pending)} asset(s) to update:")
        for item in pending:
            print(f"  - {item.reason}: {_relative(item.source)} -> {_relative(item.target)}")

    result = run_update(args.source_root, args.output_root)
    if result.remaining_updates:
        print("[EnvDIY] update failed; these assets still need processing:")
        for item in result.remaining_updates:
            print(f"  - {item.reason}: {_relative(item.source)} -> {_relative(item.target)}")
        return 1

    if result.updated_assets:
        print(f"[EnvDIY] updated {len(result.updated_assets)} asset(s).")
    sensors = ", ".join(result.processed_sensors) if result.processed_sensors else "(none)"
    manipulators = ", ".join(processed_manipulator_names(args.output_root)) or "(none)"
    print(f"[EnvDIY] DIY payload assets: manipulators={manipulators}; sensors={sensors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
