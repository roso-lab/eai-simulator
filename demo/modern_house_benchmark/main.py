"""Launch this application through the shared simulator session boundary."""

from demo.benchmark.application import run_demo
from . import config
from .domain_adapter import build_modern_house_domain_evidence_provider


def main(argv=None, *, domain_evidence_provider=None, domain_adapter_id=None,
         lifecycle_callbacks=None, communication_contract=None,
         communication_schedule=None):
    return run_demo(config, argv, domain_evidence_provider=domain_evidence_provider,
                    domain_adapter_id=domain_adapter_id,
                    lifecycle_callbacks=lifecycle_callbacks,
                    communication_contract=communication_contract,
                    communication_schedule=communication_schedule)


def build_domain_evidence_provider(public_task, **backends):
    """Build the Modern House-owned live evidence provider for ``main``.

    The returned object can be passed as ``domain_evidence_provider`` to
    :func:`main`. Missing detector or path backends remain pending.
    """
    return build_modern_house_domain_evidence_provider(public_task, **backends)


if __name__ == "__main__":
    raise SystemExit(main())
