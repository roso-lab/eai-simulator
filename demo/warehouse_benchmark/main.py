"""Launch this application through the shared simulator session boundary."""

from demo.benchmark.application import run_demo
from . import config
from .domain_adapter import build_warehouse_domain_evidence_provider


def main(argv=None, *, domain_evidence_provider=None, domain_adapter_id=None,
         lifecycle_callbacks=None, communication_contract=None,
         communication_schedule=None):
    if domain_adapter_id is None and domain_evidence_provider is not None:
        domain_adapter_id = getattr(domain_evidence_provider, "adapter_id", None)
    return run_demo(config, argv, domain_evidence_provider=domain_evidence_provider,
                    domain_adapter_id=domain_adapter_id,
                    lifecycle_callbacks=lifecycle_callbacks,
                    communication_contract=communication_contract,
                    communication_schedule=communication_schedule)


def build_domain_evidence_provider(public_task, **backends):
    """Build the Warehouse-owned live evidence provider for ``main``.

    The returned object can be passed as ``domain_evidence_provider`` to
    :func:`main`.  Missing backends remain pending and cannot produce claims.
    """
    return build_warehouse_domain_evidence_provider(public_task, **backends)


if __name__ == "__main__":
    raise SystemExit(main())
