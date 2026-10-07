"""Safe OpenAI-compatible provenance and optional dongli API client.

The released COHERENT implementation uses an external LLM rather than a
learned checkpoint.  This module keeps that dependency outside the typed PEFA
contract.  It reads only environment variables, never serializes a key, and
does not make a network request unless ``chat_completion`` is called.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class ProvenanceError(ValueError):
    """Raised for missing or unsafe runtime LLM configuration."""


@dataclass(frozen=True)
class DongliConfig:
    base_url: str
    model: str
    api_key_env: str = "DONGLI_API_KEY"
    has_api_key: bool = False

    @classmethod
    def from_env(cls, *, model: str | None = None) -> "DongliConfig":
        base_url = os.environ.get("DONGLI_BASE_URL", "").strip().rstrip("/")
        if not base_url.startswith(("https://", "http://")):
            raise ProvenanceError("DONGLI_BASE_URL must be an http(s) URL")
        selected_model = (model or os.environ.get("DONGLI_MODEL", "gpt-6-luna")).strip()
        if not selected_model:
            raise ProvenanceError("a non-empty dongli model is required")
        key_env = os.environ.get("DONGLI_API_KEY_ENV", "DONGLI_API_KEY").strip()
        if not key_env or not key_env.replace("_", "").isalnum():
            raise ProvenanceError("DONGLI_API_KEY_ENV must be a simple environment name")
        return cls(base_url, selected_model, key_env, bool(os.environ.get(key_env, "").strip()))

    def manifest(self) -> dict[str, Any]:
        """Return publishable metadata without credentials."""

        return {
            "provider": "dongli-openai-compatible",
            "base_url": self.base_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "api_key_present": self.has_api_key,
            "checkpoint": "not_applicable_llm_prompt_method",
        }


def chat_completion(
    messages: Sequence[Mapping[str, str]],
    *,
    config: DongliConfig,
    temperature: float = 0.0,
    max_tokens: int = 256,
    timeout: float = 60.0,
) -> tuple[str, Mapping[str, Any]]:
    """Call ``/chat/completions`` and return text plus usage metadata.

    The key is read at call time and is never included in an exception, return
    value, manifest, or log.  This function is deliberately small so a caller
    can wrap it with the benchmark's retry, ledger, and cost accounting rules.
    """

    if not config.has_api_key:
        raise ProvenanceError(f"missing environment variable {config.api_key_env}")
    try:
        temperature_value = float(temperature)
        token_limit = int(max_tokens)
    except (TypeError, ValueError):
        raise ProvenanceError("temperature and max_tokens must be numeric") from None
    if not 0.0 <= temperature_value <= 2.0:
        raise ProvenanceError("temperature must be in [0, 2]")
    if token_limit <= 0:
        raise ProvenanceError("max_tokens must be positive")
    key = os.environ.get(config.api_key_env, "").strip()
    body = json.dumps(
        {
            "model": config.model,
            "messages": [dict(item) for item in messages],
            "temperature": temperature_value,
            "max_tokens": token_limit,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        config.base_url + "/chat/completions",
        data=body,
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ProvenanceError(f"dongli API HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProvenanceError(f"dongli API transport failure: {type(exc).__name__}") from None
    try:
        text = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage", {})
    except (KeyError, IndexError, TypeError) as exc:
        raise ProvenanceError("dongli API returned malformed chat completion") from exc
    if not isinstance(text, str):
        raise ProvenanceError("dongli API content is not text")
    return text, usage if isinstance(usage, Mapping) else {}
