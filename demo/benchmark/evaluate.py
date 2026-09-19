"""Evaluate a harness report using a compiled private challenge bundle.

Usage: python -m demo.benchmark.evaluate --bundle-dir tmp/release_candidate
       --report tmp/episode.json --output tmp/score.json
"""

import argparse
import json
from pathlib import Path

from .challenge_contract import validate_baseline_manifest, validate_participant_api
from .scene_challenge import evaluate_challenge_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = args.bundle_dir
    files = {"public_task": root / "release/public_task.json",
             "participant_api": root / "release/participant_api.json",
             "baseline_manifest": root / "release/baseline_manifest.json",
             "hidden_evaluator": root / "evaluator/hidden_evaluator.json"}
    bundle = {key: json.loads(path.read_text(encoding="utf-8")) for key, path in files.items()}
    validate_participant_api(bundle["participant_api"], task_spec=bundle["public_task"])
    validate_baseline_manifest(bundle["baseline_manifest"], task_spec=bundle["public_task"])
    result = evaluate_challenge_report(bundle, json.loads(args.report.read_text(encoding="utf-8")))
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    else:
        print(encoded, end="")
    return 0 if result["task_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
