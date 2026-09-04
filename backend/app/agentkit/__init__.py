"""AgentKit —— 基于 LangGraph 的多 Agent 协作组件。

最佳实践落地(LangGraph supervisor/specialist + 有界 ReAct):
- LangGraph: 状态图编排、审批中断(interrupt)、检查点持久化;
- 每个 Agent = 专属系统提示词(prompts) + 白名单只读工具 + AgentBrain 有界工具调用循环;
- 黑板消息: Agent 间结构化协作(可审计、前端可視化);
- 安全铁律: 一切安全关键数字由规则引擎产出, LLM 只调用工具核实并解释。
"""
from .base import BaseAgent
from .brain import AgentBrain
from .llm import SAFETY_RULE, glm_chat, llm_available, llm_status
from .prompts import AGENT_PROMPTS

__all__ = ["BaseAgent", "AgentBrain", "SAFETY_RULE", "glm_chat", "llm_available", "llm_status", "AGENT_PROMPTS"]
