"""LangGraph 任务图 —— 多 Agent 协作编排。

结构(与《多Agent架构设计》一致):
START → 指挥官接警 → 侦察研判 → (灭火调度 ∥ 支援保障) → 仿真评估
      → 交互审批(interrupt 审批门) → 指挥官仲裁
          ├─ approve  → 轮次执行 ──┬─ next_round → 轮次执行(环)
          │                        ├─ replan → 侦察研判(重规划环, 再次审批)
          │                        └─ done → 报告归档 → END
          ├─ adjust → 侦察研判(带用户约束)
          └─ reject → 报告归档 → END
"""
from __future__ import annotations

from typing import Any, Dict, TypedDict

from langgraph.graph import END, START, StateGraph

try:  # 兼容不同 LangGraph 版本的检查点命名
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:  # pragma: no cover
    from langgraph.checkpoint.base import InMemorySaver as MemorySaver

from .approver import ApproverAgent
from .commander import CommanderAgent
from .recon import ReconAgent
from .simulator import SimulatorAgent
from .suppression import SuppressionAgent
from .support import SupportAgent


class MissionState(TypedDict, total=False):
    # 输入
    task_id: str
    scene_id: str
    image_name: str
    scenario: str
    scenario_cfg: Dict[str, Any]
    # 黑板镜像
    environment: Dict[str, Any]
    fleet: list
    inventory: Dict[str, Any]
    fire: Dict[str, Any]
    fire_center: Dict[str, Any]
    vision: Dict[str, Any]
    # 方案
    candidates: list
    module: str
    capability: Dict[str, Any]
    support_plan: Dict[str, Any]
    best_candidate: Dict[str, Any]
    # 审批
    approval: Dict[str, Any]
    approval_request: Dict[str, Any]
    # 执行
    plan: Dict[str, Any]
    rounds: list
    round_index: int
    route: str
    trigger: Dict[str, Any]
    replans: int
    max_drones: int
    people_override: str
    conclusion: str
    report: Dict[str, Any]
    phase: str


def _route_decide(state: MissionState) -> str:
    return state.get("route", "rejected")


def _route_round(state: MissionState) -> str:
    return state.get("route", "done")


def build_mission_graph():
    graph = StateGraph(MissionState)
    graph.add_node("commander_intake", COMMANDER.handle)
    graph.add_node("recon", RECON.handle)
    graph.add_node("suppression", SUPPRESSION.handle)
    graph.add_node("support", SUPPORT.handle)
    graph.add_node("simulator", SIMULATOR.evaluate)
    graph.add_node("approver", APPROVER.prepare)
    graph.add_node("commander_decide", COMMANDER.decide)
    graph.add_node("execute_round", SIMULATOR.execute_round)
    graph.add_node("report", APPROVER.report)

    graph.add_edge(START, "commander_intake")
    graph.add_edge("commander_intake", "recon")
    graph.add_edge("recon", "suppression")   # 并行分支 1
    graph.add_edge("recon", "support")       # 并行分支 2
    graph.add_edge("suppression", "simulator")
    graph.add_edge("support", "simulator")   # 汇合后统一评估
    graph.add_edge("simulator", "approver")
    graph.add_edge("approver", "commander_decide")
    graph.add_conditional_edges("commander_decide", _route_decide,
                                {"next_round": "execute_round", "adjust": "recon", "rejected": "report"})
    graph.add_conditional_edges("execute_round", _route_round,
                                {"next_round": "execute_round", "replan": "recon", "done": "report"})
    graph.add_edge("report", END)
    return graph.compile(checkpointer=MemorySaver())


# 全局 6 Agent 实例: 任务图与 API 协作档案共用(Agent 无内部状态, 全部经 task_id 读写黑板)
COMMANDER = CommanderAgent()
RECON = ReconAgent()
SUPPRESSION = SuppressionAgent()
SUPPORT = SupportAgent()
SIMULATOR = SimulatorAgent()
APPROVER = ApproverAgent()
AGENTS = [COMMANDER, RECON, SUPPRESSION, SUPPORT, SIMULATOR, APPROVER]
