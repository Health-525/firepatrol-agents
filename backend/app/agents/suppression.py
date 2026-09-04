"""③ 灭火调度 Agent —— 候选生成(1–4 架 E 子群组合)+ 硬约束过滤(与仿真评分解耦)。"""
from __future__ import annotations

import json
from itertools import combinations
from typing import Any, Dict, List, Tuple

from ..agentkit.base import BaseAgent
from ..domain.store import BOARD
from ..rules import tools as R
from ..rules.knowledge import query_knowledge

RETURN_SOC = R.RETURN_SOC


class SuppressionAgent(BaseAgent):
    agent_id = "suppression"
    name = "灭火调度"
    role = "候选生成 · 硬约束过滤 · 药剂选择 · 航线校验"
    subgroup = "suppression"
    color = "#ef4444"
    emoji = "🚒"
    tools = {"query_knowledge": query_knowledge, "suppression_capability": R.suppression_capability,
             "agent_kappa": R.agent_kappa}

    async def handle(self, state: Dict[str, Any]) -> Dict[str, Any]:
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

        # ---- LLM 战术决策(真决策点): 出动规模策略, 输出改变候选生成; 失败回退全枚举 ----
        sizes = list(range(1, limit + 1))
        strategy_note = "全枚举(1–%d 架)" % limit if limit > 1 else "1 架"
        if limit > 1:
            inventory = state.get("inventory") or {}
            strategy, trace = await self.think(
                "你是灭火调度战术决策者。只回答出动规模策略, 格式必须是 'N-M'(如 2-3)或单个数字 'N'(1 到 %d 之间),"
                "不要任何其他文字。判断依据: 火情紧迫度、资源节约、库存与换电周转。" % limit,
                f"B={fire['total_flp']} FLP, 增长 {fire['growth_flp_per_hour']} FLP/h, 风 {fire['wind_speed']} m/s"
                f"({fire['wind_band_label']}), 单架次有效能力 {cap['effective_flp']} FLP, 可用灭火机 {limit} 架,"
                f"水剂库存 {inventory.get('water_liters', 0)}L/{inventory.get('water_modules_w20', 0)}模块,"
                f"电池 {inventory.get('battery_packs', 0)} 组", max_tokens=40)
            parsed = self._parse_sizes(strategy or "", limit)
            if parsed:
                sizes = parsed
                # 全枚举对照自检: LLM 决策是约束性的, 但若全枚举最优档位不在其选择内, 自动纳入并公示
                advisory, best_size = self._full_enum_advisory(pool, fire, module, center,
                                                               state.get("inventory") or {}, limit)
                covers = best_size in sizes
                if not covers:
                    sizes = sorted(set(sizes) | {best_size})
                strategy_note = (f"LLM 决策:出动 {strategy.strip()} 架(候选 {'/'.join('C%d' % s for s in sizes)})"
                                 + ("" if covers else f";全枚举最优 C{best_size} 未被覆盖, 已自动纳入对照"))
                self.say(task_id, "PLAN_PROPOSAL", "commander",
                         f"🧠 战术决策:{strategy_note}。依据 B={fire['total_flp']}、风 {fire['wind_speed']} m/s、"
                         f"单架次 {cap['effective_flp']} FLP 与库存周转。硬约束仍由规则引擎把关。",
                         {"llm_decision": True, "sizes": sizes,
                          "covers_full_enum_optimum": covers, "full_enum_advisory": advisory,
                          "tools": [t['tool'] for t in trace] if trace else []})
            else:
                strategy_note = "LLM 不可用,回退全枚举(1–%d 架)" % limit

        candidates: List[Dict[str, Any]] = []
        for size in sizes:
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

        # ---- 就地取水决策(规则 5.3): 水源选择 + 与基地补给的省时对比 + 往返 SOC 校验
        scene = state["environment"]
        rep = pool[0] if pool else None
        ws_plan = R.plan_water_source(module, center, scene["base"],
                                      scene.get("water_sources") or [], rep, fire.get("cells", []))
        # 重规划结转: 已从水源取走的水量不能因重新评估而"恢复"
        prev_ws = ((state.get("plan") or {}).get("water_source_plan") or {})
        prev_all = [prev_ws.get("source")] + (prev_ws.get("fallback_sources") or [])
        for opt in [ws_plan.get("source")] + (ws_plan.get("fallback_sources") or []):
            if not opt:
                continue
            prev = next((o for o in prev_all if o and o.get("id") == opt["id"]), None)
            if prev is not None:
                opt["capacity_remaining"] = float(prev.get("capacity_remaining", opt["capacity_remaining"]))
        for cand in candidates:
            cand["water_source_plan"] = ws_plan

        feasible_ids = [c["candidate_id"] for c in candidates if c["feasible"]]
        veto_reasons = sorted({r for c in candidates for chk in c["checks"] for r in chk["reasons"]})
        BOARD.update(task_id, candidates=candidates)
        veto = ""
        if not feasible_ids:
            veto = f"全部组合未过硬约束({'; '.join(veto_reasons)})," \
                   f"系统将输出资源缺口而不是虚假方案。"
        self.say(task_id, "PLAN_PROPOSAL", "commander",
                 f"候选方案生成完毕({strategy_note}):药剂 {module}(kappa={cap['kappa']},"
                 f"单架次有效能力 {cap['effective_flp']} FLP),"
                 f"枚举 {len(candidates)} 个组合,过硬约束 {len(feasible_ids)} 个:{', '.join(feasible_ids) or '无'}。"
                 f"补给策略:{ws_plan['note']}。{veto}",
                 {"candidates": candidates, "capability": cap, "water_source_plan": ws_plan})
        self.think_bg(task_id, "PLAN_PROPOSAL", "commander",
                      "解释候选方案设计: 药剂选择依据、组合从少到多的取舍逻辑、硬约束淘汰原因",
                      f"火型 {fire['fire_type']},药剂 {module},kappa={cap['kappa']},单架次有效能力 {cap['effective_flp']} FLP;"
                      f"过硬约束组合: {feasible_ids or '无'};淘汰原因: {veto_reasons or '无'};"
                      f"硬约束明细: {json.dumps(candidates[-1]['checks'], ensure_ascii=False)[:400] if candidates else '[]'}")
        return {"candidates": candidates, "module": module, "capability": cap}

    @staticmethod
    def _full_enum_advisory(pool: List[Dict[str, Any]], fire: Dict[str, Any], module: str,
                            center: Dict[str, float], inventory: Dict[str, Any],
                            limit: int) -> Tuple[List[Dict[str, Any]], int]:
        """全枚举对照: 对 1..limit 每档做同一套离散预演+评分, 返回(逐档结果, 最优档)。

        数字全部来自规则引擎(与仿真评估同一口径), 用于公示 LLM 决策与全枚举最优的差距。
        """
        scene_base = R.load_json("data/scene.json")["base"]
        advisory: List[Dict[str, Any]] = []
        for size in range(1, limit + 1):
            uavs = pool[:size]
            sim = R.fast_simulate_candidate(uavs, fire["total_flp"], fire["growth_flp_per_hour"],
                                            module, fire["fire_type"], fire["wind_speed"],
                                            inventory, center, scene_base)
            score = R.score_plan(sim["control_minutes"], sim["residual_flp"], fire["total_flp"],
                                 sim["energy_total"], size, sim["material_used"],
                                 sim["swaps"] + sim["refills"])
            advisory.append({"size": size, "J": round(score["score"], 3),
                             "controlled": sim["controlled"],
                             "control_minutes": sim["control_minutes"]})
        best_size = min(advisory, key=lambda a: a["J"])["size"] if advisory else 1
        return advisory, best_size

    @staticmethod
    def _parse_sizes(text: str, limit: int) -> List[int]:
        """解析 LLM 战略输出 'N-M' 或 'N' 为候选规模列表; 非法输入返回空(调用方回退)。"""
        import re
        text = text.strip()
        match = re.search(r"(\d)\s*[-–~到至]\s*(\d)", text)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
        else:
            single = re.search(r"(\d)", text)
            if not single:
                return []
            low = high = int(single.group(1))
        low, high = max(1, min(low, limit)), max(1, min(high, limit))
        if low > high:
            low, high = high, low
        return list(range(low, high + 1))

    @staticmethod
    def _check(uav: Dict[str, Any], module: str, fire: Dict[str, Any], center: Dict[str, float],
               battery_packs: int = 0) -> Dict[str, Any]:
        spray = R.sim_config()["spray"][module]
        dist = R.distance_m(uav["position"], center)
        out_min = R.flight_minutes(dist, uav["speed_mps"])
        # 去程巡航按实际模块质量做载荷修正(水 22kg 与 CO₂ 14kg 速率不同), 再叠加爬升修正
        rate_out = R.climb_adjusted_rate(R.loaded_cruise_rate(uav, spray["module_mass_kg"]),
                                         uav["position"]["x"], uav["position"]["y"], center["x"], center["y"])
        rate_back = R.uav_mode_rate(uav, loaded=False)
        soc_out = R.delta_soc(rate_out, out_min)
        soc_task = R.delta_soc(R.sim_config()["energy"]["suppression"]["hover_spray"], spray["minutes"])
        soc_back = R.delta_soc(rate_back, out_min)
        return R.check_hard_constraints(uav, module, fire["fire_type"], soc_out, soc_task, soc_back,
                                        battery_packs=battery_packs) | {
            "soc_out": round(soc_out, 2), "soc_task": round(soc_task, 2), "soc_back": round(soc_back, 2),
            "distance_m": round(dist, 0), "out_minutes": round(out_min, 2),
            "rate_out_loaded": round(rate_out, 1),
            "payload_mass_kg": spray["module_mass_kg"]}
