"""Fail-closed scenario execution boundary for the current project.

The historical scenario executor lived in ``demo.benchmark``.  That package
is intentionally absent from the current HeteroCoord source tree, and the
generic :mod:`benchmark.runtime` is a different tick-runtime contract.  This
entry point therefore refuses execution explicitly instead of importing an
external/legacy backend or silently treating a logical runtime as Isaac
scenario evidence.
"""

from __future__ import annotations

import argparse


BACKEND_ERROR = (
    "scenario execution backend is unavailable in the current project: "
    "the legacy demo.benchmark executor is not part of HeteroCoord; "
    "use the project-local runtime probes or add a versioned scenario backend"
)


def execution_contract(data, *, transport_mode: str = "none"):
    """Reject scenario rollout before any simulator process is started."""

    raise ValueError(BACKEND_ERROR)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?")
    parser.add_argument("--report")
    parser.parse_args(argv)
    raise SystemExit(BACKEND_ERROR)


if __name__ == "__main__":
    main()
