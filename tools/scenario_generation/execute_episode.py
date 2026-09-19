"""Compatibility entry point; benchmark execution is owned by demo.benchmark."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demo.benchmark import runtime

if __name__ == "__main__":
    raise SystemExit(runtime.main())
else:
    sys.modules[__name__] = runtime
