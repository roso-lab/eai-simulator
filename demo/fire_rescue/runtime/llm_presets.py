"""Shared EMOS discussion LLM presets (model + OpenAI-compatible endpoint + API key env name)."""

from __future__ import annotations

import os
from typing import Any, Dict, List


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# id -> preset metadata — never expose api keys to HTTP clients.
EMOS_LLM_PRESETS: Dict[str, Dict[str, Any]] = {
    "deepseek": {
        "label": "DeepSeek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "openai-gpt4o": {
        "label": "OpenAI GPT-4o",
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
    },
    # 智谱 GLM：OpenAI 兼容接口；密钥在开放平台创建后写入环境变量 ZHIPU_API_KEY（勿写入代码）
    # GLM-4-Flash 速度更快（RTT 约 2-5s，远低于 GLM-4 的 10-45s 长尾），输出结构相同，是 EMOS 默认。
    "zhipu-glm4-flash": {
        "label": "智谱 GLM-4-Flash（默认）",
        "model": "glm-4-flash",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "api_key_env": "ZHIPU_API_KEY",
    },
    # 保留 GLM-4 作为高质量但更慢的可选项；仅在需要更强推理时通过网页/CLI 切换
    "zhipu-glm4": {
        "label": "智谱 GLM-4",
        "model": "glm-4",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "api_key_env": "ZHIPU_API_KEY",
    },
}

DEFAULT_EMOS_LLM_PRESET = "zhipu-glm4-flash"


def preset_to_group_discussion_kwargs(preset_id: str) -> Dict[str, Any]:
    """Map preset id to ``group_discussion`` keyword arguments."""
    p = EMOS_LLM_PRESETS.get(preset_id) or EMOS_LLM_PRESETS[DEFAULT_EMOS_LLM_PRESET]
    kwargs: Dict[str, Any] = {
        "llm_model": os.environ.get(p.get("model_env", ""), p["model"]).strip() or p["model"],
        "llm_base_url": os.environ.get(p.get("base_url_env", ""), p["base_url"]).strip() or p["base_url"],
        "llm_api_key_env": p.get("api_key_env", "DEEPSEEK_API_KEY"),
        # 供 EMOS 聊天/日志展示（不传给 group_discussion）
        "llm_display_label": p["label"],
    }
    if "api_key_default" in p:
        kwargs["llm_api_key_default"] = p["api_key_default"]
    if "stream" in p:
        kwargs["llm_stream"] = _env_bool(str(p.get("stream_env", "")), bool(p["stream"]))
    if "temperature" in p:
        kwargs["llm_temperature"] = _env_float(str(p.get("temperature_env", "")), float(p["temperature"]))
    if "top_p" in p:
        kwargs["llm_top_p"] = _env_float(str(p.get("top_p_env", "")), float(p["top_p"]))
    if "temperature_param" in p:
        kwargs["llm_temperature_param"] = p["temperature_param"]
    if "output_profile" in p:
        kwargs["llm_output_profile"] = p["output_profile"]
    if "preserve_raw_assignments" in p:
        kwargs["llm_preserve_raw_assignments"] = bool(p["preserve_raw_assignments"])
    if "should_agent_reflection" in p:
        kwargs["llm_should_agent_reflection"] = bool(p["should_agent_reflection"])
    if "leader_self_reflection" in p:
        kwargs["llm_leader_self_reflection"] = bool(p["leader_self_reflection"])
    if "max_tokens" in p:
        kwargs["llm_max_tokens"] = _env_int(str(p.get("max_tokens_env", "")), int(p["max_tokens"]))
    return kwargs


def list_presets_for_api() -> List[Dict[str, str]]:
    """Public entries for GET /api/llm_presets (no secrets)."""
    return [{"id": k, "label": v["label"]} for k, v in EMOS_LLM_PRESETS.items()]
