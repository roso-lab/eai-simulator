"""Canonical entrypoint for the Warehouse paired-carrier native probe."""

from .paired_native_probe import main


if __name__ == "__main__":
    raise SystemExit(main())
