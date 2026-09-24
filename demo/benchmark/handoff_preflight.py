"""One-command offline preflight for handing the benchmark to another team.

The preflight creates fresh authoring and handoff outputs, reads both back,
checks the exact public release inventory, and runs one public-only
``GoalAlgorithmHost`` turn for each scene. It never starts Isaac, ROS, an
external scheduler, or a participant process.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .goal_algorithm_adapter import GoalAlgorithmHost
from .goal_suite import verify_goal_suite, write_goal_suite
from .handoff_manifest import FIVE_SCENE_KEYS, verify_goal_handoff, write_goal_handoff


SCENES = FIVE_SCENE_KEYS
PUBLIC_FILES = {"public_task.json", "participant_api.json", "baseline_manifest.json"}


class _PublicSelectionSmoke:
    def __init__(self) -> None:
        self.robot_id: str | None = None

    def manifest(self) -> Mapping[str, Any]:
        return {
            "kind": "participant", "id": "handoff-preflight", "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1, "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task: Mapping[str, Any], participant_api: Mapping[str, Any]) -> None:
        del participant_api
        self.robot_id = public_task["candidate_robot_pool"][0]["id"]

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not any(event["type"] == "episode_started" for event in events):
            return []
        if self.robot_id is None:
            raise RuntimeError("preflight adapter was not initialized")
        return [{
            "schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": "handoff-preflight-select", "submitted_at_s": events[-1]["time_s"],
            "type": "select_team", "payload": {"agent_ids": [self.robot_id]},
        }]

    def close(self) -> None:
        pass


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_handoff_preflight(
        output_root: str | Path, *, scene_keys=SCENES) -> dict[str, Any]:
    """Create and verify fresh artifacts below ``output_root``.

    ``output_root`` must not exist, which prevents a preflight from silently
    mixing results from different source revisions.
    """
    keys = tuple(scene_keys)
    if (not keys or len(keys) != len(set(keys))
            or any(key not in SCENES for key in keys)):
        raise ValueError("preflight scene_keys must be distinct registered scenes")
    root = Path(output_root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("preflight output cannot traverse symlinks")
    if root.exists() or root.is_symlink():
        raise FileExistsError(f"preflight output already exists: {root}")
    root.mkdir(parents=True)
    try:
        write_goal_suite(root / "suite", scene_keys=list(keys), verify_sources=True)
        suite_readback = verify_goal_suite(root / "suite", verify_sources=True)
        write_goal_handoff(root / "handoff", scene_keys=keys)
        handoff_readback = verify_goal_handoff(root / "handoff", scene_keys=keys)

        public_smoke = {}
        for scene in keys:
            release = root / "handoff" / scene / "release"
            files = {path.name for path in release.iterdir() if path.is_file()}
            if files != PUBLIC_FILES:
                raise ValueError(f"{scene}: public release inventory drifted: {sorted(files)}")
            public = _read(release / "public_task.json")
            api = _read(release / "participant_api.json")
            adapter = _PublicSelectionSmoke()
            with GoalAlgorithmHost(public, api, adapter) as host:
                results = host.request_actions()
                if len(results) != 1 or results[0].get("accepted") is not True:
                    raise ValueError(f"{scene}: public adapter smoke was not accepted")
            public_smoke[scene] = {"accepted": True, "task_id": public["task_id"]}

        report = {
            "schema": "eai.benchmark.handoff_preflight", "version": 2,
            "scene_keys": list(keys), "suite": suite_readback,
            "handoff": {"scene_count": len(handoff_readback["scenes"]),
                        "all_handoff_ready": handoff_readback["all_handoff_ready"]},
            "public_release_inventory": sorted(PUBLIC_FILES),
            "public_adapter_smoke": public_smoke,
            "isaac_started": False, "ros_started": False,
            "external_scheduler_started": False,
            "execution_validated": False,
            "business_outcomes_validated": False,
            "scoreable": False,
        }
        (root / "preflight.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return report
    except Exception:
        # Preserve no misleading partial handoff. The caller can inspect the
        # exception and rerun with a fresh directory.
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True,
                        help="new directory for suite, handoff and preflight.json")
    report = run_handoff_preflight(parser.parse_args(argv).output_root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


__all__ = ["run_handoff_preflight", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
