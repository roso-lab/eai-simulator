"""Dependency-light helpers for environment-indexed runtime state."""

from __future__ import annotations

from typing import Dict

import torch


def zero_reset_rows(
    values: Dict[str, torch.Tensor], env_ids: torch.Tensor, num_envs: int,
) -> Dict[str, torch.Tensor]:
    """Reset selected batch rows without mutating or dropping sibling rows."""

    result: Dict[str, torch.Tensor] = {}
    for name, value in values.items():
        if (
            not isinstance(value, torch.Tensor)
            or value.ndim < 1
            or value.shape[0] != num_envs
        ):
            raise RuntimeError(
                f"environment-indexed state {name!r} lacks the num_envs batch axis"
            )
        copied = value.clone()
        copied.index_fill_(0, env_ids.to(device=copied.device), 0)
        result[name] = copied
    return result


__all__ = ["zero_reset_rows"]
