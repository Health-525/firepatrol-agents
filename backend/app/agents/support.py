"""④ 支援保障 Agent —— 子群3分支决策: 有人(通信中继+广播指引) / 无人(电池药剂物流) / 待复核。"""
from __future__ import annotations

from typing import Any, Dict

from ..agentkit.base import BaseAgent
from ..domain.store import BOARD
from ..rules import tools as R


class SupportAgent(BaseAgent):
    agent_id = "support"
    name = "支援保障"
    role = "有人分支:通信/广播/指引 · 无人分支:电池与药剂物流 · 后备监测"
    subgroup = "support"
    color = "#22c55e"
    emoji = "🛟"

    def handle(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        fire = state["fire"]
        people = fire["people_status"]
        center = state["fire_center"]
        scene = state["environment"]
        base = scene["base"]
        fsp = scene["forward_supply_point"]

        recon = [u for u in state["fleet"] if u["subgroup"] == "reconnaissance" and u["status"] not in {"fault", "charging"}]
        support = [u for u in state["fleet"] if u["subgroup"] == "support" and u["status"] not in {"fault", "charging"}]

        # 侦察保留: 至少 1 架 R 持续监测(规则 8.3)
        recon_assignments = []
        if recon:
            recon_assignments.append({"uav_id": recon[0]["uav_id"], "task": "持续监测", "alt_m": 60, "pos": center})
            if people == "unknown" and len(recon) > 1:
                recon_assignments.append({"uav_id": recon[1]["uav_id"], "task": "近距复核人员", "alt_m": 30, "pos": center})

        branch, assignments, desc = self._branch(people, support, center, base, fsp, state)
        plan = {"branch": branch, "recon": recon_assignments, "support": assignments, "people_status": people}
        BOARD.update(task_id, support_plan=plan)
        self.say(task_id, "PLAN_PROPOSAL", "commander", f"支援分支决策:{desc}", plan)
        return {"support_plan": plan}

    def _branch(self, people: str, support: list, center: dict, base: dict, fsp: dict, state: dict) -> tuple:
        inventory = state.get("inventory", {})
        logistic_desc = None
        if people == "confirmed":
            if support:
                s1, s2 = support[0], support[-1]
                assignments = [
                    {"uav_id": s1["uav_id"], "task": "通信中继+广播疏散指引", "mode": "comms_hover", "pos": center},
                    {"uav_id": s2["uav_id"], "task": "运送备用电池至前向补给点", "mode": "logistics", "pos": fsp,
                     "packs": 2, "minutes": round(R.flight_minutes(R.distance_m(base, fsp), s2["speed_mps"]) * 2 + 2, 1)},
                ]
                logistic_desc = f"{s2['uav_id']} 送 {assignments[1]['packs']} 组备用电池到 {fsp['name']}"
            else:
                assignments = []
            return ("people", assignments,
                    f"检测到人员且置信度高 → 有人分支:{support[0]['uav_id'] if support else 'S1'} 通信中继悬停+广播指引,"
                    f"{logistic_desc or '无支援机可用'};按规则保留灭火力量保护疏散通道。")
        if people == "absent":
            assignments = []
            for idx, uav in enumerate(support):
                target = fsp if idx == 0 else base
                assignments.append({"uav_id": uav["uav_id"], "task": "运送电池/水剂模块", "mode": "logistics",
                                    "pos": target, "packs": 2,
                                    "minutes": round(R.flight_minutes(R.distance_m(base, target), uav["speed_mps"]) * 2 + 2, 1)})
            return ("logistics", assignments,
                    f"确认无人 → 无人分支:{len(assignments)} 架支援机全部转物流,运送备用电池与水剂模块至前向补给点;"
                    f"当前库存 电池组 {inventory.get('battery_packs', 0)} / W20模块 {inventory.get('water_modules_w20', 0)}。")
        # unknown: 暂不投入全部资源, 待复核确认
        assignments = []
        if support:
            assignments.append({"uav_id": support[0]["uav_id"], "task": "待命+通信预备", "mode": "standby", "pos": base})
        return ("verify", assignments,
                "人员信息不确定 → 不把全部资源投入远端火点:支援机待命,侦察机近距复核,等待确认后再切换分支。")
