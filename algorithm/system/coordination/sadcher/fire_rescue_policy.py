"""Fail-closed compatibility entry point for the incomplete legacy request.

AssignmentRequest does not provide the explicit durations, complete DAG state,
and start/end environment contract needed by the unchanged SADCHER simulator.
Use NativeSadcherAdapter with EaiTaskPackage instead. No approximate allocation
or partially loaded checkpoint is permitted through this entry point.
"""

from algorithm.common_coordination import ContractError


def assign(request):
    raise ContractError(
        "legacy SADCHER AssignmentRequest adapter is disabled: it cannot express "
        "native durations/status/sentinels. Use NativeSadcherAdapter with an "
        "EaiTaskPackage and a strictly compatible official checkpoint."
    )


__all__ = ["assign"]
