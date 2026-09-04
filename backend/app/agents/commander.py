"""① 指挥官 Agent —— 中央协调者: 接警建案、分发任务、审批后仲裁与重规划决策。"""
from __future__ import annotations

from typing import Any, Dict

from ..agentkit.base import BaseAgent
from ..domain import scenarios as scen
from ..domain.store import BOARD
from ..rules import tools as R
from ..rules.knowledge import knowledge_stats, query_knowledge


class CommanderAgent(BaseAgent):
    agent_id = "commander"
    name = "指挥官"
    role = "任务分解 · 分发 · 汇总仲裁 · 重规划触发"
    subgroup = "system"
    color = "#8b5cf6"
    emoji = "🧭"
    tools = {"query_knowledge": query_knowledge, "knowledge_stats": knowledge_stats}

    async def handle(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        if state.get("scenario") == "random":
            scenario = scen.build_random_scenario(state["task_id"])  # 任务 ID 做种子, 可复现
        else:
            scenario = scen.SCENARIOS.get(state.get("scenario", "standard"), scen.SCENARIOS["standard"])
        scene = R.load_json("data/scene.json")
        fleet = R.load_json("data/fleet.json")["uavs"]
        for uav in fleet:  # 执行期统计字段统一初始化
            uav.setdefault("sorties", 0)
            uav.setdefault("swaps", 0)
            uav.setdefault("refills", 0)
            uav.setdefault("soc_cost_total", 0.0)
            uav.setdefault("target", None)
        inventory = R.load_json("data/inventory.json")
        # 场景库存覆盖(如就地取水演示: 基地水剂受限, 迫使补给链走向水源)
        if scenario.get("inventory_override"):
            inventory.update(scenario["inventory_override"])
        # 真值隐藏: 火从 t=0 已存在, 但系统"看不见"—— 先派 R 机巡航搜索
        ignition_cell = scenario["fire_cells"][0]
        truth = {"cx": ignition_cell["cx"], "cy": ignition_cell["cy"],
                 "x": ignition_cell["cx"] * 100 + 50, "y": ignition_cell["cy"] * 100 + 50,
                 "intensity": ignition_cell["intensity"], "detected": False}
        BOARD.update(task_id, phase="searching", environment=scene, fleet=fleet, inventory=inventory,
                     fire=None, search={"legs": 0, "coverage": 0}, truth=truth)
        order = (f"接警建案 {task_id}:场景「{scenario['label']}」。侦察研判 Agent,"
                 "请执行火情感知、环境研判与 FLP 评估。")
        self.say(task_id, "TASK_ASSIGN", "recon", order)
        # LLM 指令后台补发, 不阻塞任务图(数字先行)
        self.think_bg(task_id, "TASK_ASSIGN", "recon",
                      "下达任务指令: 向侦察研判 Agent 明确本次研判重点(不超过80字, 像指挥官下命令)",
                      f"场景「{scenario['label']}」,初始风 {scenario['wind_speed']} m/s,人员 {scenario['people_status']},"
                      f"火型 {scenario['fire_type']}", max_tokens=140)
        return {
            "scenario_cfg": scenario, "environment": scene, "fleet": fleet, "inventory": inventory,
            "replans": 0, "round_index": 0, "rounds": [], "truth": truth,
            "search_legs_done": 0, "search_detected": False,
        }

    async def decide(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """审批中断恢复后的仲裁: approve 锁资源进入执行(人员状态有变先重生成支援分支); reject 归档终止; adjust 带约束回到方案生成。"""
        task_id = state["task_id"]
        approval = state.get("approval") or {}
        decision = approval.get("decision", "reject")
        feedback = approval.get("feedback", "")
        people_override = approval.get("people_status")

        if decision == "approve":
            best = state.get("best_candidate") or {}
            if not best.get("suppression_uavs"):
                # 资源缺口方案被确认: 直接归档结论, 不进入执行
                BOARD.update(task_id, phase="completed")
                self.say(task_id, "INFO", "approver", "方案无可派遣灭火机(资源缺口),任务按缺口结论归档。")
                return {"route": "done", "conclusion": "当前资源无法控制火情,已输出资源缺口,未执行灭火。"}
            # 审批确认的人员状态优先于侦察初判: 变化时必须重生成支援分支再锁资源,
            # 否则"待复核"分支会带着旧方案一路执行到底(缺陷修复)
            fire = state.get("fire") or {}
            extra: Dict[str, Any] = {}
            if people_override in {"confirmed", "absent"} and fire.get("people_status") != people_override:
                fire = dict(fire, people_status=people_override)
                BOARD.update(task_id, fire=fire)
                from .graph import SUPPORT  # 运行时导入, 避免模块级循环依赖
                result = await SUPPORT.handle({**state, "fire": fire})
                support_plan = result["support_plan"]
                state["support_plan"] = support_plan
                # 人工确认写入状态: 后续 adjust/重规划走重建路径时不再回退到场景初判
                extra = {"fire": fire, "support_plan": support_plan, "people_override": people_override}
                branch_label = {"people": "有人(通信中继+疏散指引)", "logistics": "无人(物流补给)",
                                "verify": "待复核"}.get(support_plan.get("branch"), support_plan.get("branch"))
                self.say(task_id, "TASK_ASSIGN", "support",
                         f"人员状态经审批确认更新为 {people_override},支援保障 Agent 已重新分支 → {branch_label}。"
                         f"灭火方案不变, 支援编成按新分支锁定。")
            plan = self._lock_plan(state)
            BOARD.update(task_id, phase="executing", plan=plan, fleet=state["fleet"])
            self.say(task_id, "INFO", "simulator",
                     f"方案 {plan['plan_id']} 已批准并锁定资源。仿真评估 Agent,开始按 5 分钟轮次驱动执行。")
            return {"plan": plan, "route": "next_round", "fleet": state["fleet"], **extra}

        if decision == "adjust":
            max_drones = None
            for token in feedback.replace("架", " ").replace("最多", " ").split():
                if token.isdigit():
                    max_drones = int(token)
                    break
            BOARD.update(task_id, phase="analyzing")
            self.say(task_id, "TASK_ASSIGN", "suppression",
                     f"用户调整意见:「{feedback}」。灭火调度 Agent,请按新约束重新生成候选方案"
                     + (f"(灭火机不超过 {max_drones} 架)。" if max_drones else "。"))
            if people_override:
                self.say(task_id, "TASK_ASSIGN", "support", f"人员状态更新为 {people_override},支援保障 Agent 请重新分支。")
            return {"route": "adjust", "max_drones": max_drones,
                    "people_override": people_override, "plan": None}

        BOARD.update(task_id, phase="rejected")
        self.say(task_id, "INFO", "approver", "用户拒绝方案,任务终止,资源锁释放。")
        return {"route": "rejected", "conclusion": "用户拒绝方案,任务已终止。"}

    def _lock_plan(self, state: Dict[str, Any]) -> Dict[str, Any]:
        candidate = state["best_candidate"]
        fleet = state["fleet"]
        support_plan = state.get("support_plan") or {}
        tasked_ids = set(candidate["suppression_uavs"])
        tasked_ids |= {a["uav_id"] for a in support_plan.get("recon", [])}
        tasked_ids |= {a["uav_id"] for a in support_plan.get("support", [])}
        for uav in fleet:
            if uav["uav_id"] in tasked_ids:
                uav["status"] = "assigned"
                uav["assigned_task"] = state["task_id"]
        version = (state.get("plan") or {}).get("version", 0) + 1 if state.get("plan") else state.get("replans", 0) + 1
        plan = {
            "plan_id": f"plan-{state['task_id'][-4:]}-v{version}", "version": version,
            "candidate": candidate, "battery_plan": candidate.get("sim", {}).get("battery_plan", {}),
            "water_source_plan": candidate.get("water_source_plan", {}),
            "people_branch": state.get("support_plan", {}),
            "estimated_control_time": candidate.get("time_interval", ""),
            "feasibility": candidate.get("feasibility", "can_control"),
            "resource_gap": candidate.get("gap", {}),
        }
        return plan
