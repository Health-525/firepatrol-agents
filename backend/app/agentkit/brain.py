"""AgentBrain —— 每个 Agent 的 GLM 大脑: 专属提示词 + 白名单工具 + 有界工具调用循环。

模式: 有界 ReAct
  system(人设+安全铁律+工具说明) + user(任务+实时数据)
  → GLM 决定是否调用白名单内的只读规则工具(最多 max_tool_rounds 轮)
  → 带着工具结果给出最终研判文本。

约束:
- 工具只读(规则计算/知识检索), 不改变任务状态;
- 每轮截断工具结果(≤800字), 防上下文膨胀;
- GLM 不可用/失败 → 返回 None, 调用方回落确定性模板, 演示永不中断。
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from .llm import _cfg, _post_chat

_TYPE_MAP = {int: "integer", float: "number", bool: "boolean", str: "string"}


def _schema(name: str, fn: Callable) -> dict:
    """从 Python 函数签名 + docstring 自动生成 OpenAI tools schema。"""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {"type": "function", "function": {"name": name, "description": name,
                "parameters": {"type": "object", "properties": {}, "required": []}}}
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for pname, param in sig.parameters.items():
        json_type = _TYPE_MAP.get(param.annotation, "string")
        if isinstance(param.default, (int, float, str, bool)):
            properties[pname] = {"type": json_type, "default": param.default}
        else:
            properties[pname] = {"type": json_type}
            required.append(pname)
    doc = (inspect.getdoc(fn) or "").strip()
    return {"type": "function", "function": {
        "name": name, "description": doc.splitlines()[0][:120] if doc else name,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


class AgentBrain:
    def __init__(self, prompt: str, tools: Dict[str, Callable]):
        self.prompt = prompt
        self.tools = tools
        self.schemas = [_schema(name, fn) for name, fn in tools.items()]

    async def run(self, task: str, context: str, max_tool_rounds: int = 2,
                  max_tokens: int = 300, temperature: float = 0.3,
                  timeout: float = 12.0) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """返回 (最终文本 | None, 工具调用轨迹)。"""
        tool_note = ("你可以调用以下只读工具核实数据后再作答(最多 "
                     f"{max_tool_rounds} 轮): {', '.join(self.tools)}。不需要就不用调。") if self.tools else ""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.prompt + "\n" + tool_note},
            {"role": "user", "content": f"任务: {task}\n\n当前实时数据(规则引擎产出, 不可修改):\n{context}"},
        ]
        trace: List[Dict[str, Any]] = []
        for index in range(max_tool_rounds + 1):
            final_round = index == max_tool_rounds
            # 最后一轮禁用工具: 模型必须基于已获取的工具结果直出结论,
            # 否则勤勉的模型会把全部轮次花在调工具上, 永远不给答案
            payload = await _post_chat({"model": _cfg()[2], "messages": messages, "tools": self.schemas,
                                        "tool_choice": "none" if final_round else "auto",
                                        "temperature": temperature,
                                        "max_tokens": max_tokens}, timeout)
            if not payload:
                return None, trace
            try:
                message = payload["choices"][0]["message"]
            except (KeyError, IndexError):
                return None, trace
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                text = (message.get("content") or "").strip()
                return (text or None), trace
            if final_round:
                return None, trace  # 强制直出轮仍请求工具: 放弃, 调用方走降级
            messages.append({"role": "assistant", "content": message.get("content") or "",
                             "tool_calls": tool_calls})
            for call in tool_calls[:4]:  # 单轮最多执行 4 个工具, 防滥用
                fn_name = (call.get("function") or {}).get("name", "")
                raw_args = (call.get("function") or {}).get("arguments") or (call.get("function") or {}).get("args") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                handler = self.tools.get(fn_name)
                if handler is None:
                    result: Any = {"error": f"未知工具 {fn_name}, 可用: {list(self.tools)}"}
                else:
                    try:
                        result = handler(**args)
                        if inspect.iscoroutine(result):
                            result = await result
                    except Exception as error:  # noqa: BLE001
                        result = {"error": f"{type(error).__name__}: {error}"}
                trace.append({"tool": fn_name, "args": args})
                messages.append({"role": "tool", "tool_call_id": call.get("id", fn_name),
                                 "content": json.dumps(result, ensure_ascii=False, default=str)[:800]})
        return None, trace
