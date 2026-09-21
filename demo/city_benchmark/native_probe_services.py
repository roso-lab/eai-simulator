"""Canonical entrypoint for City service component measurements (C-SERVICE-01)."""

from .service_probe import main


if __name__ == "__main__":
    raise SystemExit(main())
