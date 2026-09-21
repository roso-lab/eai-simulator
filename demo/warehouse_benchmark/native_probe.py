"""Canonical entrypoint for the Warehouse single-carrier native witness probe.

The implementation and legacy ``native_witness_probe`` CLI remain compatible.
"""

from .native_witness_probe import main


if __name__ == "__main__":
    raise SystemExit(main())
