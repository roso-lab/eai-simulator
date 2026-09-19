"""Prepare the factory wrapper metadata without starting Isaac Sim."""

from demo.benchmark.prepare import main as prepare_main
from . import config


def main(argv=None):
    return prepare_main(config, argv)


if __name__ == "__main__":
    raise SystemExit(main())
