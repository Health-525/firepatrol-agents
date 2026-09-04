"""BaseAgent: 6 个业务 Agent 的公共基类。

每个 Agent = LangGraph 节点函数 + 黑板协作身份 + GLM 大脑(专属提示词+白名单工具+有界工具调用)。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from ..domain.store import BOARD
from .brain import AgentBrain
from .llm import llm_status
from .prompts import AGENT_PROMPTS


class BaseAgent:
    agent_id: str = ""           # e.g. "commander"
    name: str = ""               # e.g. "指挥官"
    role: str = ""               # 职责描述
    subgroup: str = "system"     # 关联无人机子群: reconnaissance/suppression/support/system
    color: str = "#8b5cf6"       # 前端头像颜色
    emoji: str = "🧭"
    tools: Dict[str, Callable] = {}   # 该 Agent 可调用的白名单只读工具

    def __init__(self) -> None:
        self.prompt: str = AGENT_PROMPTS.get(self.agent_id, "")
        self.brain = AgentBrain(self.prompt, self.tools)

    # ---------------- 黑板协作 ----------------

    def say(self, task_id: str, msg_type: str, to: str, content: str, data: Dict[str, Any] | None = None) -> None:
        """向黑板发布一条协作消息(前端 Agent 协作面板直接渲染)。"""
        BOARD.post_message(task_id, msg_type, self.agent_id, to, content, data)

    # ---------------- GLM 大脑 ----------------

    async def think(self, task: str, context: str, max_tokens: int = 300) -> Tuple[str, List[Dict[str, Any]]]:
        """有界工具调用推理; GLM 不可用时返回空串(调用方走确定性文案)。"""
        text, trace = await self.brain.run(task, context, max_tokens=max_tokens)
        return text or "", trace

    def say_llm(self, task_id: str, msg_type: str, to: str, text: str,
                trace: List[Dict[str, Any]] | None = None) -> None:
        """发布 GLM 研判消息, 附带模型与工具轨迹(前端渲染 🔧 调用链)。"""
        data: Dict[str, Any] = {"llm": llm_status()["model"]}
        if trace:
            data["tools"] = [t["tool"] for t in trace]
            data["tool_detail"] = trace
        self.say(task_id, msg_type, to, f"💡 {text}", data)

    def profile(self) -> Dict[str, Any]:
        return {"agent_id": self.agent_id, "name": self.name, "role": self.role,
                "subgroup": self.subgroup, "color": self.color, "emoji": self.emoji,
                "model": llm_status()["model"], "tools": list(self.tools)}
