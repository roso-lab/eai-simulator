"""Launch this application through the shared simulator session boundary."""

from demo.benchmark.application import run_demo
from . import config


def main(argv=None, *, participant_adapter=None):
    return run_demo(config, argv, participant_adapter=participant_adapter)


if __name__ == "__main__":
    raise SystemExit(main())
