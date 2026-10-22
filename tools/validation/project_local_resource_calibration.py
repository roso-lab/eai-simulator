"""Run one preregistered physical command/feedback measurement preflight.

Produces raw traffic and physical time, not formal calibration reference scales.
The frozen measurement plan is persisted before the native simulator launches.
"""
import argparse
from pathlib import Path
from tools.validation.project_local_node_route import run


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    raise SystemExit(run(parser.parse_args().output, metered=True))
