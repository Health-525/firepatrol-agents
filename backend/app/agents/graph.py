"""LangGraph 任务图 —— 多 Agent 协作编排。

结构(与《多Agent架构设计》一致):
START → 指挥官接警 → 侦察研判 → (灭火调度 ∥ 支援保障) → 仿真评估
      → 交互审批(interrupt 审批门) → 指挥官仲裁
          ├─ approve  → 轮次执行 → 自主研判 ─┬─ next_round → 轮次执行(环)
          │                                ├─ replan → 侦察研判(重规划环, 再次审批)
          │                                └─ done → 回收返航 → 报告归档 → END
          ├─ adjust → 侦察研判(带用户约束)
          └─ reject → 回收返航 → 报告归档 → END

自主研判节点(judge_round): 每轮执行后由仿真评估 Agent 的 GLM 大脑对全量快照判断
继续/重规划/终止, 无固定触发表; GLM 不可用时保守降级(见 agents/judgment.py)。
回收节点(recover_round): 结案后机队分两拍全员返航基地, 归位后才归档——任务结束
不是"原地悬停", 而是把机队安全带回家。
执行中单机机电失能 → 补位决策(agents/backfill.py): 方案内换机立即生效, 不过审批门。
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
    # 搜索阶段(真值隐藏: 发现前系统不可见火情)
    truth: Dict[str, Any]
    search_legs_done: int
    search_detected: bool
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
    # 自主研判
    last_replan_round: int
    last_judgment: Dict[str, Any]
    # 单机失能 / 结案回收
    failure_applied: bool
    recovery_phase: str
    recovery_archive: str


def _route_decide(state: MissionState) -> str:
    return state.get("route", "rejected")


def _route_round(state: MissionState) -> str:
    return state.get("route", "done")


def build_mission_graph():
    graph = StateGraph(MissionState)
    graph.add_node("commander_intake", COMMANDER.handle)
    graph.add_node("search_round", RECON.search_round)
    graph.add_node("recon", RECON.handle)
    graph.add_node("suppression", SUPPRESSION.handle)
    graph.add_node("support", SUPPORT.handle)
    graph.add_node("simulator", SIMULATOR.evaluate)
    graph.add_node("approver", APPROVER.prepare)
    graph.add_node("commander_decide", COMMANDER.decide)
    graph.add_node("execute_round", SIMULATOR.execute_round)
    graph.add_node("judge_round", SIMULATOR.judge_round)
    graph.add_node("recover", SIMULATOR.recover_round)
    graph.add_node("report", APPROVER.report)

    graph.add_edge(START, "commander_intake")
    graph.add_edge("commander_intake", "search_round")
    graph.add_conditional_edges("search_round", lambda state: "found" if state.get("search_detected") else "searching",
                                {"found": "recon", "searching": "search_round"})
    graph.add_edge("recon", "suppression")   # 并行分支 1
    graph.add_edge("recon", "support")       # 并行分支 2
    graph.add_edge("suppression", "simulator")
    graph.add_edge("support", "simulator")   # 汇合后统一评估
    graph.add_edge("simulator", "approver")
    graph.add_edge("approver", "commander_decide")
    # commander_decide 的四个出口: 执行 / 带约束重规划 / 拒绝回收 / 资源缺口归档(直接 done, 机队未出动也要走回收归位)
    graph.add_conditional_edges("commander_decide", _route_decide,
                                {"next_round": "execute_round", "adjust": "recon",
                                 "rejected": "recover", "done": "recover"})
    graph.add_conditional_edges("execute_round", _route_round,
                                {"judge": "judge_round", "done": "recover"})
    graph.add_conditional_edges("judge_round", _route_round,
                                {"next_round": "execute_round", "replan": "recon", "done": "recover"})
    graph.add_conditional_edges("recover", _route_round,
                                {"recovering": "recover", "done": "report", "rejected": "report"})
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
