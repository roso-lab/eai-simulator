"""Run a declared local macro holdout episode through native HAPPO/TaskRunner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .native_adapter import CONTRACT, NativeHAPPOAdapter


def infer(manifest_path, *, source_root=None, seed=20031):
    from .macro_environment import EaiMacroEnvironment, SymbolicMacroExecutor
    from task.contracts import TaskRunRequest
    from task.runner import TaskRunner

    adapter = NativeHAPPOAdapter(manifest_path, source_root=source_root)
    env = EaiMacroEnvironment(seed, split="inference-holdout")
    executor = SymbolicMacroExecutor()
    runner = TaskRunner(executor=executor)
    rounds = []
    for _ in range(CONTRACT["horizon"]):
        result = runner.run(TaskRunRequest(env.package), adapter)
        raw = result.decision.provenance["actor_outputs"]
        _, _, _, done, _, _ = env.step([[a["action"]] for a in raw])
        adapter.update_feedback(result.feedback, terminal=bool(done.all()))
        rounds.append({**result.to_dict(), "native_provenance": result.decision.provenance})
        if done.all():
            break
    return {"condition": "locally_trained_macro_smoke", "seed": seed, "rounds": rounds,
            "symbolic_completed_tasks": sorted(env.completed), "simulation": "symbolic_macro_only",
            "physical": "N/A", "benchmark_performance": "not_evaluable"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--source-root")
    parser.add_argument("--seed", type=int, default=20031)
    parser.add_argument("--output")
    args = parser.parse_args()
    text = json.dumps(infer(args.manifest, source_root=args.source_root, seed=args.seed), indent=2)+"\n"
    if args.output:
        Path(args.output).write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
