"""Standalone LLM selector for sudden obstacle-rescue tasks."""

from __future__ import annotations

import contextlib
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


def build_rescue_llm_prompt(
    *,
    obstacle_pos: Tuple[float, float, float],
    candidates: Sequence[Dict[str, Any]],
    blocked_robot: str = "carter_1",
) -> str:
    lines = [
        "突发清障救援任务：通道被障碍物阻挡，需要从候选机器人中选择一个机器人前往清障。",
        f"被困机器人：{blocked_robot}",
        f"障碍物位置：({obstacle_pos[0]:.2f}, {obstacle_pos[1]:.2f}, {obstacle_pos[2]:.2f})",
        "候选机器人：",
    ]
    for c in candidates:
        note = str(c.get("advise_note") or "").strip()
        lines.append(
            "- "
            f"robot_id={c.get('name')}; "
            f"类型={c.get('type')}; "
            f"机械臂={c.get('arm')}; "
            f"是否有机械臂={bool(c.get('has_arm'))}; "
            f"地形能力={c.get('terrain')}; "
            f"距离障碍物={c.get('distance')}m; "
            f"当前是否已有任务={bool(c.get('has_task'))}; "
            f"系统候选标记={bool(c.get('advised'))}; "
            f"备注={note}"
        )
    allowed = "、".join(str(c.get("name")) for c in candidates if c.get("name"))
    lines.extend(
        [
            "请根据候选机器人的能力、距离和当前任务状态选择一个救援机器人。",
            "可以选择系统候选标记为 false 的机器人，但要基于你自己的判断。",
            "只输出一个救援机器人，格式为 {robot_id||简短原因}。",
            f"只能使用这些 robot_id：{allowed}。",
            "不要输出 JSON、Markdown、编号、解释段落或多个机器人。",
        ]
    )
    return "\n".join(lines)


def parse_rescue_robot_choice(text: str, allowed_robot_ids: Sequence[str]) -> Optional[str]:
    allowed = [str(r) for r in allowed_robot_ids]
    allowed_set = set(allowed)
    raw = text or ""

    match = re.search(r"\{\s*([A-Za-z][A-Za-z0-9_-]*_\d+)\s*\|\|.*?\}", raw, flags=re.S)
    if match and match.group(1) in allowed_set:
        return match.group(1)

    with contextlib.suppress(Exception):
        data = json.loads(raw)
        if isinstance(data, dict):
            for key in ("robot", "robot_id", "rescue_robot", "name"):
                value = str(data.get(key, "")).strip()
                if value in allowed_set:
                    return value

    for robot_id in allowed:
        if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(robot_id)}(?![A-Za-z0-9_-])", raw):
            return robot_id
    return None


def _collect_stream_text(stream_response: Any) -> str:
    parts: List[str] = []
    for chunk in stream_response:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None)
        if content:
            parts.append(content)
            current = "".join(parts)
            if re.search(r"\{\s*[A-Za-z][A-Za-z0-9_-]*_\d+\s*\|\|.*?\}", current, flags=re.S):
                close = getattr(stream_response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()
                break
    return "".join(parts)


def _chat_completion_text(prompt: str, llm_kwargs: Dict[str, Any]) -> str:
    try:
        import openai
    except ImportError as exc:
        raise RuntimeError("缺少 openai 依赖，无法调用救援 LLM") from exc

    api_key_env = str(llm_kwargs.get("llm_api_key_env") or "DEEPSEEK_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"缺少环境变量 {api_key_env}")

    proxy_vars = ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]
    saved_proxies = {v: os.environ.pop(v) for v in proxy_vars if v in os.environ}
    try:
        base_url = str(llm_kwargs.get("llm_base_url") or "https://api.deepseek.com/v1")
        client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60.0,
        )
        request_kwargs: Dict[str, Any] = {
            "model": str(llm_kwargs.get("llm_model") or "deepseek-chat"),
            "messages": [
                {"role": "system", "content": "你是 EMOS 突发清障救援机器人选择器。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": int(llm_kwargs.get("llm_rescue_max_tokens") or 160),
        }
        temp = float(llm_kwargs.get("llm_temperature", 0.0))
        top_p = llm_kwargs.get("llm_top_p")
        temp_param = str(llm_kwargs.get("llm_temperature_param", "temperature") or "temperature")
        if temp_param == "temperature":
            request_kwargs["temperature"] = temp
            if top_p is not None:
                request_kwargs["top_p"] = float(top_p)
        else:
            extra_body = {temp_param: temp}
            if top_p is not None:
                extra_body["top_p"] = float(top_p)
            request_kwargs["extra_body"] = extra_body

        if bool(llm_kwargs.get("llm_stream", False)):
            return _collect_stream_text(client.chat.completions.create(**request_kwargs, stream=True))
        resp = client.chat.completions.create(**request_kwargs)
        return resp.choices[0].message.content or ""
    finally:
        os.environ.update(saved_proxies)


def choose_rescue_robot_with_llm(
    *,
    obstacle_pos: Tuple[float, float, float],
    candidates: Sequence[Dict[str, Any]],
    llm_kwargs: Dict[str, Any],
    fallback_fn: Callable[[List[Dict[str, Any]]], Optional[str]],
    blocked_robot: str = "carter_1",
) -> Tuple[Optional[str], str, bool]:
    candidate_list = list(candidates)
    allowed = [str(c.get("name")) for c in candidate_list if c.get("name")]
    if not allowed:
        return None, "", True

    prompt = build_rescue_llm_prompt(
        obstacle_pos=obstacle_pos,
        candidates=candidate_list,
        blocked_robot=blocked_robot,
    )
    try:
        raw = _chat_completion_text(prompt, llm_kwargs)
        choice = parse_rescue_robot_choice(raw, allowed)
        if choice:
            return choice, raw, False
        return fallback_fn(candidate_list), raw, True
    except Exception as exc:
        return fallback_fn(candidate_list), f"[LLM_ERROR] {exc}", True
