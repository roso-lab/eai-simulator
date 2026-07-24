"""OpenAI-compatible chat client (DeepSeek、智谱 GLM 等统一经 base_url + api_key_env 配置)."""
import contextlib
import json
import os
from typing import Callable, List, Tuple, Union, Optional
from .crab_core import Action
from .llm_request_gate import radxa_local_request_gate
from .python_interpreter import SubprocessInterpreter
import openai
from pydantic import BaseModel

class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, BaseModel):
            return dict(obj)
        return super().default(obj)


def _message_to_dict(message) -> dict:
    """Convert ChatCompletionMessage to a JSON-serializable dict."""
    # If already a dict, return as-is
    if isinstance(message, dict):
        return message
    
    # Convert Pydantic model / ChatCompletionMessage to dict
    result = {
        "role": message.role,
        "content": message.content,
    }
    
    # Handle tool_calls if present
    if hasattr(message, 'tool_calls') and message.tool_calls:
        result["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                }
            }
            for tc in message.tool_calls
        ]
    
    return result


# Robot action functions that should NOT be called in numerical check code
_ROBOT_ACTION_FUNCTIONS = [
    'nav_to_position', 'nav_to_obj', 'nav_to_robot',
    'pick', 'place', 'reset_arm',
    'wait', 'send_request',
]


def _sanitize_code_for_execution(code: str) -> str:
    """
    Remove lines containing robot action function calls from code.
    These are actions to be executed by the robot, not Python functions.
    """
    lines = code.split('\n')
    sanitized_lines = []
    removed_lines = []
    
    for line in lines:
        # Check if line contains any robot action function call
        contains_action = False
        for action in _ROBOT_ACTION_FUNCTIONS:
            # Match function call pattern: action_name(
            if f'{action}(' in line:
                contains_action = True
                removed_lines.append(line.strip())
                break
        
        if not contains_action:
            sanitized_lines.append(line)
    
    if removed_lines:
        print(f"[WARNING] Removed invalid robot action calls from code: {removed_lines}")
    
    return '\n'.join(sanitized_lines)

        
class DeepSeekModel:

    def __init__(
        self,
        system_prompt: str,
        action_space: List[Action],
        model: str = "deepseek-chat",
        window_size: Optional[int] = None,
        discussion_stage: bool = False,
        code_execution: bool = False,
        enable_logging: bool = False,
        logging_file: str = "",
        save_on_each_chat: bool = True,
        agent_name: str = "unknown",
        base_url: Optional[str] = None,
        api_key_env: Optional[str] = None,
        api_key_default: Optional[str] = None,
        stream: bool = False,
        temperature: float = 0.0,
        top_p: Optional[float] = None,
        temperature_param: str = "temperature",
        max_tokens: Optional[int] = None,
        stream_stop_checker: Optional[Callable[[str], bool]] = None,
    ) -> None:
        # 先初始化 logging 相关属性，避免 __del__ 中出错
        self.enable_logging = enable_logging
        self.logging_file = logging_file
        self.save_on_each_chat = save_on_each_chat
        self.agent_name = agent_name
        # 须在可能 raise 之前设置，避免 __del__/save_chat_history 访问缺失属性
        self.token_usage = 0

        self.system_message = {
            "role": "system",
            "content": system_prompt,
        }
        self._convert_action_to_schema(action_space)
        self.action_map = {action.name: action for action in action_space}
        self.chat_history = []
        self.window_size = window_size
        self.model = model
        self.stream = bool(stream)
        self.temperature = float(temperature)
        self.top_p = top_p
        self.temperature_param = str(temperature_param or "temperature")
        self.max_tokens = max_tokens
        self.stream_stop_checker = stream_stop_checker

        _base_url = base_url if base_url is not None else "https://api.deepseek.com/v1"
        self.base_url = _base_url
        _key_env = api_key_env if api_key_env is not None else "DEEPSEEK_API_KEY"
        api_key = os.getenv(_key_env) or api_key_default
        if not api_key:
            raise ValueError(
                f"请设置环境变量 {_key_env}（当前 API 端点: {_base_url}）。\n"
                f"在终端执行 export {_key_env}=... 后再启动仿真；勿将密钥写入仓库代码。"
            )

        # 清除代理环境变量，避免 socks 代理不兼容问题
        proxy_vars = ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY']
        self._saved_proxies = {var: os.environ.pop(var) for var in proxy_vars if var in os.environ}

        self.client = openai.OpenAI(
            base_url=_base_url,
            api_key=api_key,
            timeout=60.0,
        )
        
        self.planning_stage = discussion_stage
        self.code_execution = code_execution
        if self.code_execution:
            self.interpreter = SubprocessInterpreter()
            
        self.openai_tools = (
            [{"type": "function", "function": action} for action in self.actions]
            if action_space
            else None
        )
        self.tool_calls_enable = bool(action_space)

    def _chat_completion_create(self, **kwargs):
        if self.max_tokens is not None:
            kwargs["max_tokens"] = int(self.max_tokens)
        if self.temperature_param == "temperature":
            kwargs["temperature"] = self.temperature
            if self.top_p is not None:
                kwargs["top_p"] = self.top_p
        else:
            extra_body = dict(kwargs.pop("extra_body", {}) or {})
            extra_body[self.temperature_param] = self.temperature
            if self.top_p is not None:
                extra_body["top_p"] = self.top_p
            kwargs["extra_body"] = extra_body
        if self.stream:
            kwargs["stream"] = True
            with radxa_local_request_gate(self.base_url):
                return self._collect_streaming_completion(
                    self.client.chat.completions.create(**kwargs)
                )
        with radxa_local_request_gate(self.base_url):
            return self.client.chat.completions.create(**kwargs)

    def _collect_streaming_completion(self, stream_response):
        parts = []
        for chunk in stream_response:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None)
            if content:
                parts.append(content)
                current = "".join(parts)
                if self.stream_stop_checker is not None and self.stream_stop_checker(current):
                    close = getattr(stream_response, "close", None)
                    if callable(close):
                        with contextlib.suppress(Exception):
                            close()
                    break
        message = type("ChatMessage", (), {"role": "assistant", "content": "".join(parts)})()
        choice = type("ChatChoice", (), {"message": message})()
        return type("ChatResponse", (), {"choices": [choice], "usage": None})()

    def _add_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        total = getattr(usage, "total_tokens", None)
        if total:
            self.token_usage += total

    def __del__(self):
        """Save chat history to a file if logging is enabled"""
        # 使用 hasattr 检查，避免构造函数中途失败时出错
        if hasattr(self, 'enable_logging') and self.enable_logging and hasattr(self, 'logging_file') and self.logging_file:
            self.save_chat_history(self.logging_file)
        
        # 恢复代理环境变量
        if hasattr(self, '_saved_proxies'):
            for var, value in self._saved_proxies.items():
                os.environ[var] = value
           
    def save_chat_history(self, file_path: str):
        with open(file_path, "w") as f:
            full_history = [self.system_message] + [
                msg for turn in self.chat_history for msg in turn
            ]
            json.dump(full_history, f, indent=2, cls=CustomJSONEncoder)
        
        episode_path = os.path.dirname(file_path)
        token_path = os.path.join(episode_path, "token_usage.json")
        os.makedirs(episode_path, exist_ok=True)
        
        if not os.path.exists(token_path):
            with open(token_path, 'w') as f:
                json.dump({}, f)

        with open(token_path, 'r') as f:
            data = json.load(f)
        data[self.agent_name] = getattr(self, "token_usage", 0)

        with open(token_path, 'w') as f:
            json.dump(data, f, indent=4)

    def set_system_message(self, system_message: str):
        self.system_message = {"role": "system", "content": system_message}

    def execute_action(self, action_name: str, parameters: dict) -> str:
        print(f"🚀 执行动作: {action_name}({parameters})")
        try:
            result = self.action_map[action_name].run(**parameters)
            return str(result)
        except Exception as e:
            error_msg = f"动作执行失败: {e}"
            print(f"❌ {error_msg}")
            return error_msg

    def chat(self, content: str, crab_planning: bool = False) -> Union[str, Tuple[str, dict]]:
        new_message = {"role": "user", "content": content}
        self.chat_history.append([new_message])

        # 构建请求消息
        request = [self.system_message]
        if self.window_size is None:
            for turn in self.chat_history[:-1]:
                request.extend(turn)
        elif self.window_size > 0:
            for turn in self.chat_history[-(self.window_size + 1) : -1]:
                request.extend(turn)
        request.append(new_message)

        if self.planning_stage:
            while True:
                # 发送请求
                if self.tool_calls_enable:
                    response = self._chat_completion_create(
                        messages=request,
                        model=self.model,
                        tools=self.openai_tools,
                    )
                else:
                    response = self._chat_completion_create(
                        messages=request,
                        model=self.model,
                    )
                self._add_usage(response)

                response_message = response.choices[0].message
                response_message_dict = _message_to_dict(response_message)
                self.chat_history[-1].append(response_message_dict)
                request.append(response_message_dict)

                tool_calls = getattr(response_message, 'tool_calls', None)
                codes = _extract_code(response_message.content)

                # 处理工具调用
                if self.tool_calls_enable and tool_calls:
                    for tool_call in tool_calls:
                        try:
                            func_args = json.loads(tool_call.function.arguments)
                        except json.JSONDecodeError:
                            func_args = {}
                        tool_result = {
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": tool_call.function.name,
                            "content": self.execute_action(tool_call.function.name, func_args),
                        }
                        self.chat_history[-1].append(tool_result)
                        request.append(tool_result)

                # 处理代码执行
                elif self.code_execution and codes:
                    merged_code = "\n".join([
                        code_block for code_block, code_type in codes
                        if code_type in getattr(self.interpreter, '_CODE_TYPE_MAPPING', {})
                    ])
                    # Sanitize code: remove robot action function calls
                    merged_code = _sanitize_code_for_execution(merged_code)
                    if merged_code.strip():
                        print("============合并代码============")
                        print(merged_code)
                        result_content = self.interpreter.run(merged_code, "python")
                        print("============执行结果============")
                        print(result_content)
                        print("==============================")
                        result_message = {"role": "user", "content": result_content}
                        self.chat_history[-1].append(result_message)
                        request.append(result_message)
                    else:
                        break
                else:
                    if self.save_on_each_chat and self.logging_file:
                        self.save_chat_history(self.logging_file)
                    return response_message.content or ""

        elif crab_planning:
            print(f"[DEBUG] Entering crab_planning branch")
            response = self._chat_completion_create(
                messages=request,
                model=self.model,
            )
            self._add_usage(response)
            response_message = response.choices[0].message
            print(f"[DEBUG] response_message type: {type(response_message)}")
            print(f"[DEBUG] response_message.content: {repr(response_message.content)}")
            self.chat_history[-1].append(_message_to_dict(response_message))
            if self.save_on_each_chat and self.logging_file:
                self.save_chat_history(self.logging_file)
            result = response_message.content or ""
            print(f"[DEBUG] Returning result type: {type(result)}, value: {repr(result)}")
            return result

        else:
            # 强制要求工具调用（原逻辑）
            response = self._chat_completion_create(
                messages=request,
                model=self.model,
                tools=self.openai_tools,
                tool_choice="required",
            )
            self._add_usage(response)
            response_message = response.choices[0].message
            self.chat_history[-1].append(_message_to_dict(response_message))
            tool_calls = getattr(response_message, 'tool_calls', [])
            
            if not tool_calls:
                raise RuntimeError("模型未返回任何工具调用")

            # 只执行第一个工具调用（与原逻辑一致）
            call = tool_calls[0]
            try:
                parameters = json.loads(call.function.arguments)
            except json.JSONDecodeError:
                parameters = {}
            
            for idx, tool_call in enumerate(tool_calls):
                self.chat_history[-1].append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": tool_call.function.name,
                    "content": "Success" if idx == 0 else "Skipped"
                })

            if self.save_on_each_chat and self.logging_file:
                self.save_chat_history(self.logging_file)
            
            return (call.function.name, parameters)

    def _convert_action_to_schema(self, action_space):
        self.actions = []
        for action in action_space:
            new_action = action.to_openai_json_schema()
            self.actions.append(new_action)


def _extract_code(content: Optional[str]) -> List[Tuple[str, str]]:
    if not content:
        return []
    codes = []
    lines = content.split("\n")
    idx = 0
    start_idx = 0
    while idx < len(lines):
        while idx < len(lines) and not lines[idx].lstrip().startswith("```"):
            idx += 1
        if idx >= len(lines):
            break
        code_type = lines[idx].strip()[3:].strip() or "python"
        idx += 1
        start_idx = idx
        while idx < len(lines) and not lines[idx].lstrip().startswith("```"):
            idx += 1
        code = "\n".join(lines[start_idx:idx]).strip()
        if code:
            codes.append((code, code_type))
        idx += 1
    return codes
