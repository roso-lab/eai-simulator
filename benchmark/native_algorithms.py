"""Paper algorithm entry point using frozen original implementations.

This module performs no allocation, repair, ranking or action replacement.
Each provider translates EAI inputs and outputs around its upstream algorithm.
"""
from __future__ import annotations

from dataclasses import replace
import importlib
import json
from pathlib import Path
from typing import Any

from algorithm.system.coordination.native_runtime import sha256_file, verify_source
from task.contracts import AlgorithmDecision


REGISTER = Path(__file__).resolve().parents[1] / "algorithm/heterocoord/paper_algorithms.json"
PAPER_ALGORITHMS = ("fmc_ata", "bt_capabilities", "sadcher", "harl_happo", "teamweaver", "emos")
_PROVIDERS = {
    "fmc_ata": ("fmc_ata", "NativeFMCATAAdapter"),
    "bt_capabilities": ("bt_capabilities", "NativeBTCapabilitiesAdapter"),
    "sadcher": ("sadcher", "NativeSadcherAdapter"),
    "harl_happo": ("harl", "NativeHAPPOAdapter"),
    "teamweaver": ("TeamWeaver", "NativeTeamWeaverAdapter"),
    "emos": ("emos", "EmosNativeAdapter"),
}


def source_spec(algorithm: str) -> dict[str, Any]:
    if algorithm not in PAPER_ALGORITHMS:
        raise ValueError(f"not a registered paper result column: {algorithm!r}")
    return dict(json.loads(REGISTER.read_text())["algorithms"][algorithm])


class VerifiedNativeAdapter:
    """Integrity envelope; delegates decisions unchanged, including failures."""

    def __init__(self, algorithm: str, provider: Any, source_root: str | Path):
        self.algorithm = algorithm
        self.spec = source_spec(algorithm)
        self.provider = provider
        self.source_root = Path(source_root).expanduser().resolve()
        self.source_receipt = verify_source(self.source_root, self.spec["commit"])
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        # Only lifecycle operations actually implemented by the native adapter
        # exist; this envelope never synthesizes a feedback/replan policy.
        value = getattr(self.provider, name)
        if name == "replan":
            return lambda *args: self._run(value, *args)
        return value

    def _run(self, callback, *args):
        before = verify_source(self.source_root, self.spec["commit"])
        module = importlib.import_module(type(self.provider).__module__)
        adapter_hash = sha256_file(module.__file__)
        register_hash = sha256_file(REGISTER)
        try:
            decision = callback(*args)
        except Exception as exc:
            # Rejected native output is evidence too. Preserve the original
            # exception and decision while recording the exact source boundary.
            exc.native_source_provenance = {
                "paper_algorithm": self.algorithm,
                "source_integrity": before.to_dict(),
                "adapter_source_sha256": adapter_hash,
                "source_register_sha256": register_hash,
                "decision_is_physical_evidence": False,
            }
            raise
        finally:
            after = verify_source(self.source_root, self.spec["commit"])
            if before != after:
                raise RuntimeError("upstream changed during native algorithm call")
        if not isinstance(decision, AlgorithmDecision):
            raise TypeError("native adapter must return AlgorithmDecision")
        self.calls += 1
        provenance = {
            **decision.provenance,
            "paper_algorithm": self.algorithm,
            "source_integrity": after.to_dict(),
            "adapter_source_sha256": adapter_hash,
            "source_register_sha256": register_hash,
            "native_adapter_call": self.calls,
            "decision_is_physical_evidence": False,
        }
        return replace(decision, provenance=provenance)

    def __call__(self, task, robots) -> AlgorithmDecision:
        return self._run(self.provider, task, robots)

def build_native_adapter(algorithm: str, *, source_root: str | Path | None = None,
                         **configuration: Any) -> VerifiedNativeAdapter:
    """Construct only an explicitly registered source-backed adapter.

    There is deliberately no fallback to the older Fire Rescue facade.
    Model/actor/action manifests are algorithm-specific required configuration.
    """
    spec = source_spec(algorithm)
    json_config = configuration.pop("json_config", None)
    config_base_dir = Path(configuration.pop("config_base_dir", ".")).resolve()
    if json_config is not None:
        if algorithm != "emos" or configuration:
            raise ValueError("json_config is an explicit EMOS configuration; do not mix constructor kwargs")
        if source_root is None and json_config.get("source_root"):
            configured = Path(json_config["source_root"]).expanduser()
            source_root = configured if configured.is_absolute() else config_base_dir / configured
    root = Path(source_root or Path.home() / ".cache/heterocoord/upstream" / spec["cache_name"])
    verify_source(root, spec["commit"])
    if json_config is not None:
        from tools.algorithm_adapters.emos_config import build_emos_from_config
        provider = build_emos_from_config(json_config, source_root=root, base_dir=config_base_dir)
    else:
        package, class_name = _PROVIDERS[algorithm]
        module = importlib.import_module(f"algorithm.system.coordination.{package}.native_adapter")
        provider = getattr(module, class_name)(source_root=root, **configuration)
    return VerifiedNativeAdapter(algorithm, provider, root)
