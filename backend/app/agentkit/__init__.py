"""AgentKit —— 基于 LangGraph 的多 Agent 协作组件。

职责划分:
- LangGraph 负责: 状态图编排、审批中断(interrupt)、检查点持久化。
- AgentKit 负责: Agent 身份/角色、黑板消息协作流、可选 LLM 解释层。

安全原则: Agent 只调用 rules.tools 并解释结果, 不产生任何安全关键数字。
"""
from .base import BaseAgent
from .llm import llm_explain

__all__ = ["BaseAgent", "llm_explain"]
