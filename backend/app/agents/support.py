"""④ 支援保障 Agent —— 子群3分支决策: 有人(通信中继+广播指引) / 无人(电池药剂物流) / 待复核。"""
from __future__ import annotations

from typing import Any, Dict

from ..agentkit.base import BaseAgent
from ..domain.store import BOARD
from ..rules import tools as R
from ..rules.evacuation import plan_evacuation
from ..rules.knowledge import query_knowledge


class SupportAgent(BaseAgent):
    agent_id = "support"
    name = "支援保障"
    role = "有人分支:通信/广播/指引 · 无人分支:电池与药剂物流 · 后备监测"
    subgroup = "support"
    color = "#22c55e"
    emoji = "🛟"
    tools = {"query_knowledge": query_knowledge, "battery_swap": R.battery_swap,
             "soc_need": R.soc_need}

    async def handle(self, state: Dict[str, Any]) -> Dict[str, Any]:
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

        # 有人分支: 依地图+地形+火情做 A* 疏散寻路, 生成语音广播指令
        if people == "confirmed" and branch == "people":
            zone = (scene.get("people_zones") or [{}])[0]
            evac = plan_evacuation(scene, fire.get("cells", []), zone)
            existing = ((state.get("support_plan") or {}).get("evacuation") or {})
            if evac["found"]:
                text = (f"{zone.get('name', '人员区域')}的{zone.get('people', '各位')}名人员请注意:"
                        f"当前火情负荷 {fire['total_flp']} FLP,风速 {fire['wind_speed']} 米每秒,"
                        f"请立即沿{'、'.join(self._route_brief(evac))}方向向{evac['exit']}撤离,"
                        f"全程约 {evac['walk_minutes']} 分钟,累计爬升 {evac['climb_m']} 米,"
                        f"请勿穿越火场东侧浓烟区,S1 号机将在上空持续引导。")
                # 重规划时继承人群进度(路线若因火情改变, 进度按比例折算)
                carried = existing.get("progress_cells", 0)
                if existing.get("path") and len(existing["path"]) > 1 and existing["path"][-1] == evac["path"][-1]:
                    carried = carried * (len(evac["path"]) - 1) / max(len(existing["path"]) - 1, 1)
                evac.update({"text": text, "progress_cells": round(carried, 2),
                             "evacuated": bool(existing.get("evacuated", False)), "people": zone.get("people", 0)})
                plan["evacuation"] = evac
                self.say(task_id, "EVAC_BROADCAST", "human",
                         f"🔊 S1 已开始空中语音广播:{text}",
                         {"evacuation": {k: evac[k] for k in ("exit", "walk_minutes", "climb_m", "path", "people")}})
            else:
                plan["evacuation"] = evac
                self.say(task_id, "EVAC_BROADCAST", "human",
                         f"🔊 {evac.get('note', '疏散路径被封锁')}。S1 已升空引导人员向安全集结区转移。", {"evacuation": evac})

        BOARD.update(task_id, support_plan=plan)
        self.say(task_id, "PLAN_PROPOSAL", "commander", f"支援分支决策:{desc}", plan)
        self.think_bg(task_id, "PLAN_PROPOSAL", "commander",
                      "解释支援分支决策依据: 为什么选该分支、S1/S2 分工逻辑、对灭火资源的影响",
                      f"人员状态 {people},分支 {branch};支援分配 {assignments};侦察保留 {len(recon_assignments)} 架;"
                      f"库存 电池 {(state.get('inventory') or {}).get('battery_packs', 0)} 组 / "
                      f"W20模块 {(state.get('inventory') or {}).get('water_modules_w20', 0)}")
        return {"support_plan": plan}

    def _branch(self, people: str, support: list, center: dict, base: dict, fsp: dict, state: dict) -> tuple:
        inventory = state.get("inventory", {})
        logistic_desc = None
        if people == "confirmed":
            if support:
                s1, s2 = support[0], support[-1]
                assignments = [
                    {"uav_id": s1["uav_id"], "task": "通信中继+语音广播疏散指引", "mode": "comms_hover", "pos": center},
                    {"uav_id": s2["uav_id"], "task": "疏散路线照明与复核", "mode": "route_cover", "pos": fsp,
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

    @staticmethod
    def _route_brief(evac: Dict[str, Any]) -> list:
        """把路径方向压缩为两三个方位词(广播口播用)。"""
        path = evac.get("path") or []
        if len(path) < 3:
            return ["就近安全通道"]
        words = []
        mid = path[len(path) // 2]
        start = path[0]
        dx, dy = mid["cx"] - start["cx"], mid["cy"] - start["cy"]
        if abs(dx) >= abs(dy):
            words.append("向东" if dx > 0 else "向西")
        else:
            words.append("向南" if dy > 0 else "向北")
        end = path[-1]
        dx2, dy2 = end["cx"] - mid["cx"], end["cy"] - mid["cy"]
        if abs(dx2) >= abs(dy2):
            second = "向东" if dx2 > 0 else "向西"
        else:
            second = "向南" if dy2 > 0 else "向北"
        if second not in words:
            words.append(second)
        return words[:2]
