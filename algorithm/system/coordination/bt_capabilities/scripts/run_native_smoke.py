#!/usr/bin/env python3
"""Run contract/native tests against an already running isolated ROS1 endpoint."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args()
    folder = Path(__file__).resolve().parents[1]
    repo = folder.parents[3]
    evidence = Path(args.evidence_dir).resolve()
    if evidence.exists() and any(evidence.iterdir()):
        parser.error("evidence directory must be new or empty")
    evidence.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(repo))
    from algorithm.system.coordination.native_runtime import verify_source
    before = verify_source(args.source_root, "5438f6e8d7599e39ec12104c0a9f0a8f1543838d")
    env = dict(os.environ, HETEROCOORD_BT_SOURCE_ROOT=str(Path(args.source_root).resolve()),
               HETEROCOORD_BT_NATIVE_ENDPOINT=args.endpoint,
               HETEROCOORD_BT_EVIDENCE_DIR=str(evidence), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-m", "pytest", "-q", str(folder / "tests/test_native_adapter.py"),
               str(folder / "tests/test_dag_native.py"), str(folder / "tests/test_isaac_host_contract.py"),
               "-p", "no:cacheprovider", "--tb=short", "--junitxml=" + str(evidence / "pytest.xml")]
    run = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, timeout=180)
    (evidence / "pytest.log").write_text(run.stdout + run.stderr)
    receipt = verify_source(args.source_root, "5438f6e8d7599e39ec12104c0a9f0a8f1543838d")
    cases = list(ET.parse(evidence / "pytest.xml").getroot().iter("testcase"))
    def counts(items):
        return {"collected":len(items), "passed":sum(all(case.find(tag) is None for tag in ("failure", "error", "skipped")) for case in items),
                "skipped":sum(case.find("skipped") is not None for case in items),
                "failed":sum(case.find("failure") is not None or case.find("error") is not None for case in items)}
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "command": command, "exit_code": run.returncode, "source": receipt.to_dict(),
              "source_unchanged":before == receipt,
              "contract_tests":counts([c for c in cases if not c.attrib["name"].startswith("test_live_")]),
              "native_runtime_tests":counts([c for c in cases if c.attrib["name"].startswith("test_live_")]),
              "simulation_evidence": "not_evaluated", "physical_evidence": "not_evaluated"}
    (evidence / "run.json").write_text(json.dumps(report, indent=2) + "\n")
    print(run.stdout + run.stderr)
    raise SystemExit(run.returncode if before == receipt else 1)


if __name__ == "__main__":
    main()
