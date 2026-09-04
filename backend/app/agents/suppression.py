"""③ 灭火调度 Agent —— 候选生成(1–4 架 E 子群组合)+ 硬约束过滤(与仿真评分解耦)。"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, List

from ..agentkit.base import BaseAgent
from ..domain.store import BOARD
from ..rules import tools as R

RETURN_SOC = R.RETURN_SOC


class SuppressionAgent(BaseAgent):
    agent_id = "suppression"
    name = "灭火调度"
    role = "候选生成 · 硬约束过滤 · 药剂选择 · 航线校验"
    subgroup = "suppression"
    color = "#ef4444"
    emoji = "🚒"

    def handle(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        fire = state["fire"]
        fleet = state["fleet"]
        center = state["fire_center"]
        module = "water_20l" if fire["fire_type"] == "vegetation" else "co2_6kg"
        cap = R.suppression_capability(module, fire["fire_type"], fire["wind_speed"])
        max_drones = state.get("max_drones")

        pool = [u for u in fleet if u["subgroup"] == "suppression" and u["status"] not in {"fault", "charging"}]
        pool.sort(key=lambda u: -u["soc"])
        limit = min(4, len(pool), max_drones or 4)

        candidates: List[Dict[str, Any]] = []
        for size in range(1, limit + 1):
            combo = pool[:size]
            checks = []
            feasible = True
            for uav in combo:
                check = self._check(uav, module, fire, center, battery_packs=int(state.get("inventory", {}).get("battery_packs", 0)))
                checks.append(check)
                feasible = feasible and check["feasible"]
            candidates.append({
                "candidate_id": f"C{size}", "module": module,
                "suppression_uavs": [u["uav_id"] for u in combo],
                "checks": checks, "feasible": feasible,
                "per_sortie_flp": cap["effective_flp"],
            })
        feasible_ids = [c["candidate_id"] for c in candidates if c["feasible"]]
        BOARD.update(task_id, candidates=candidates)
        veto = ""
        if not feasible_ids:
            worst = candidates[-1]["checks"] if candidates else []
            veto = f"全部组合未过硬约束({'; '.join({r for chk in worst for r in chk['reasons']})})," \
                   f"系统将输出资源缺口而不是虚假方案。"
        self.say(task_id, "PLAN_PROPOSAL", "commander",
                 f"候选方案生成完毕:药剂 {module}(kappa={cap['kappa']},单架次有效能力 {cap['effective_flp']} FLP),"
                 f"枚举 {len(candidates)} 个组合,过硬约束 {len(feasible_ids)} 个:{', '.join(feasible_ids) or '无'}。{veto}",
                 {"candidates": candidates, "capability": cap})
        return {"candidates": candidates, "module": module, "capability": cap}

    @staticmethod
    def _check(uav: Dict[str, Any], module: str, fire: Dict[str, Any], center: Dict[str, float],
               battery_packs: int = 0) -> Dict[str, Any]:
        spray = R.sim_config()["spray"][module]
        dist = R.distance_m(uav["position"], center)
        out_min = R.flight_minutes(dist, uav["speed_mps"])
        loaded = spray["module_mass_kg"] > 0
        rate_out = R.uav_mode_rate(uav, loaded=loaded)
        rate_back = R.uav_mode_rate(uav, loaded=False)
        soc_out = R.delta_soc(rate_out, out_min)
        soc_task = R.delta_soc(R.sim_config()["energy"]["suppression"]["hover_spray"], spray["minutes"])
        soc_back = R.delta_soc(rate_back, out_min)
        return R.check_hard_constraints(uav, module, fire["fire_type"], soc_out, soc_task, soc_back,
                                        battery_packs=battery_packs) | {
            "soc_out": round(soc_out, 2), "soc_task": round(soc_task, 2), "soc_back": round(soc_back, 2),
            "distance_m": round(dist, 0), "out_minutes": round(out_min, 2)}
