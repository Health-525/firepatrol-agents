"""BaseAgent: 6 个业务 Agent 的公共基类。

每个 Agent = LangGraph 中的一个节点函数 + 黑板上的协作身份。
"""
from __future__ import annotations

from typing import Any, Dict

from ..domain.store import BOARD


class BaseAgent:
    agent_id: str = ""           # e.g. "commander"
    name: str = ""               # e.g. "指挥官"
    role: str = ""               # 职责描述
    subgroup: str = "system"     # 关联无人机子群: reconnaissance/suppression/support/system
    color: str = "#8b5cf6"       # 前端头像颜色
    emoji: str = "🧭"

    def say(self, task_id: str, msg_type: str, to: str, content: str, data: Dict[str, Any] | None = None) -> None:
        """向黑板发布一条协作消息(前端 Agent 协作面板直接渲染)。"""
        BOARD.post_message(task_id, msg_type, self.agent_id, to, content, data)

    def handle(self, state: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def profile(self) -> Dict[str, str]:
        return {"agent_id": self.agent_id, "name": self.name, "role": self.role,
                "subgroup": self.subgroup, "color": self.color, "emoji": self.emoji}
