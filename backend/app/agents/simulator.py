"""⑤ 仿真评估 Agent —— 裁判: 候选离散预演+评分选优; 审批后按 5 分钟轮次驱动执行与触发判定。"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict, List

from ..agentkit.base import BaseAgent
from ..domain.store import BOARD
from ..rules import tools as R
from ..rules.knowledge import query_knowledge

RETURN_SOC = R.RETURN_SOC


class SimulatorAgent(BaseAgent):
    agent_id = "simulator"
    name = "仿真评估"
    role = "离散轮次仿真 · 多目标评分 J · 净处置能力 · 触发重规划"
    subgroup = "system"
    color = "#f59e0b"
    emoji = "⚖️"
    tools = {"query_knowledge": query_knowledge, "net_capability": R.net_capability,
             "simulate_round": R.simulate_round}

    # ------------------------------------------------ 评估: 为每个候选打分排名

    async def evaluate(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        fire = state["fire"]
        fleet = {u["uav_id"]: u for u in state["fleet"]}
        inventory = state["inventory"]
        scene = state["environment"]
        base = scene["base"]

        ranked: List[Dict[str, Any]] = []
        for cand in state["candidates"]:
            if not cand["feasible"]:
                continue
            uavs = [fleet[uid] for uid in cand["suppression_uavs"]]
            sim = R.fast_simulate_candidate(uavs, fire["total_flp"], fire["growth_flp_per_hour"],
                                            cand["module"], fire["fire_type"], fire["wind_speed"],
                                            inventory, state["fire_center"], base)
            score = R.score_plan(sim["control_minutes"], sim["residual_flp"], fire["total_flp"],
                                 sim["energy_total"], len(uavs), sim["material_used"],
                                 sim["swaps"] + sim["refills"])
            uav_count = len(uavs)
            net = R.net_capability(cand["per_sortie_flp"] * uav_count * 12, fire["growth_flp_per_hour"])
            # 规则: C_net>0 且 库存/电量可持续 才算 can_control; 预演未控制(药剂/电池耗尽)即输出缺口
            if sim["controlled"]:
                verdict = net["verdict"]
            elif net["verdict"] == "can_control":
                verdict = "cannot_control"
                net = {"net_flp_per_hour": net["net_flp_per_hour"], "verdict": verdict}
            else:
                verdict = net["verdict"]
            cand = dict(cand)
            cand.update({"sim": sim, "score": score, "feasibility": verdict,
                         "time_interval": self._interval(sim["control_minutes"]),
                         "gap": self._gap(sim, verdict, uav_count)})
            ranked.append(cand)

        if not ranked:
            # 全部被硬约束否决: 输出最小资源缺口而不是虚假方案
            best = {"candidate_id": "none", "feasible": False, "feasibility": "cannot_control",
                    "gap": {"message": "当前资源无法满足硬约束(返航SOC/载荷/兼容性)", "need": "更多可用灭火机或电池组"},
                    "time_interval": "无法给出", "sim": {}, "score": {"score": 1.0}}
            self.say(task_id, "SIM_RESULT", "commander", "所有候选未通过硬约束,输出资源缺口,不承诺完成时间。")
        else:
            controllable = [c for c in ranked if c["feasibility"] == "can_control"]
            best = min(controllable or ranked, key=lambda c: c["score"]["score"])
        BOARD.update(task_id, candidates=ranked, best_candidate=best)
        summary = (f"离散仿真完成:{len(ranked)} 个可行候选。最优 {best['candidate_id']}"
                   f"(灭火机 {len(best.get('suppression_uavs', []))} 架,评分 J={best['score']['score']},"
                   f"预计 {best.get('time_interval', '无法给出')},可行性 {best['feasibility']})。") if ranked else \
                  "离散仿真完成:无可行候选,判定 cannot_control。"
        self.say(task_id, "SIM_RESULT", "commander", summary,
                 {"ranked": [{ "id": c["candidate_id"], "J": c["score"]["score"], "t": c["time_interval"],
                              "feasibility": c["feasibility"]} for c in ranked]})
        if ranked:
            ranking_brief = "; ".join(f"{c['candidate_id']}(灭火机{len(c.get('suppression_uavs', []))}架,"
                                      f"J={c['score']['score']},{c['time_interval']},{c['feasibility']})" for c in ranked)
            best_id = best.get("candidate_id")
            self.think_bg(task_id, "SIM_RESULT", "commander",
                          "解读候选排序: 为什么最优是它(时间/残余/耗电/物资/变更的权衡), 排序由规则引擎评分确定不可推翻",
                          f"B={fire['total_flp']} FLP,增长率 {fire['growth_flp_per_hour']} FLP/h,风 {fire['wind_speed']} m/s;"
                          f"候选(J 越小越优): {ranking_brief};规则引擎判定最优={best_id}")
        return {"candidates": ranked, "best_candidate": best}

    @staticmethod
    def _interval(control_minutes: float | None) -> str:
        if control_minutes is None:
            return "无法给出"
        if control_minutes <= 0.01:
            return "0 分钟(残余火情为 0)"
        return f"{round(control_minutes * 0.9)}–{round(control_minutes * 1.15)} 分钟"

    @staticmethod
    def _gap(sim: Dict[str, Any], verdict: str, uav_count: int) -> Dict[str, Any]:
        if verdict == "can_control":
            return {}
        stalled = sim.get("stalled")
        reason = {"agent_insufficient": "水剂库存耗尽,架次无法持续", "soc_below_return": "返航 SOC 硬约束触发且备用电池耗尽",
                  "no_uav": "无可派遣灭火机", None: "净处置能力不足(C_net≤0)"}[stalled] if stalled in {"agent_insufficient", "soc_below_return", "no_uav", None} else stalled
        return {"message": f"{verdict}: {reason}", "need": "增加灭火机架次 / 备用电池 / 水剂库存",
                "current_uavs": uav_count}

    # ------------------------------------------------ 执行: 每次调用推进一个 5 分钟轮次

    async def execute_round(self, state: Dict[str, Any]) -> Dict[str, Any]:
        cfg = R.sim_config()
        demo = cfg["demo"]
        task_id = state["task_id"]
        plan = state["plan"]
        cand = plan["candidate"]
        fleet = state["fleet"]
        inventory = state["inventory"]
        fire = state["fire"]
        center = state["fire_center"]
        base = state["environment"]["base"]
        module = cand["module"]
        spray = cfg["spray"][module]
        quantity = spray["quantity"]
        round_minutes = float(cfg["time"]["round_minutes"])
        round_index = state.get("round_index", 0) + 1
        round_events: List[str] = []
        suppression_flp = 0.0

        by_id = {u["uav_id"]: u for u in fleet}
        cap = R.suppression_capability(module, fire["fire_type"], fire["wind_speed"])
        eff = cap["effective_flp"]

        # --- 全局充电恢复: 充电中的无人机(含方案外, 如 E4)按 100%/h 补能, ≥75% 恢复可用
        for uav in fleet:
            if uav["status"] == "charging":
                uav["soc"] = round(min(100.0, uav["soc"] + R.charge_soc(0, round_minutes)), 2)
                if uav["soc"] >= 75:
                    uav["status"] = "available"
                    round_events.append(f"{uav['uav_id']} 充电至 {uav['soc']:.0f}%,恢复待命")

        # --- 灭火子群: 返航周转 / 补给 / 换电 / 架次
        for uid in cand["suppression_uavs"]:
            uav = by_id[uid]
            if uav["status"] == "fault":
                continue
            uav["target"] = center
            if uav["status"] in {"returning", "charging"}:
                # 返航周转: 优先用补给点/基地电池包换电, 无包则基地充电(100%/h)
                if self._take_pack(inventory):
                    uav["soc"] = cfg["charging"]["battery_swap_soc"]
                    uav["swaps"] += 1
                    uav["status"] = "available"
                    uav["position"] = dict(base)
                    round_events.append(f"{uid} 返航周转换电,SOC→95%,恢复待命")
                else:
                    uav["soc"] = min(100.0, uav["soc"] + R.charge_soc(0, round_minutes))
                    uav["status"] = "charging"
                    uav["position"] = dict(base)
                    if uav["soc"] >= 75:
                        uav["status"] = "available"
                        round_events.append(f"{uid} 基地充电至 {uav['soc']:.0f}%,恢复待命")
                    else:
                        round_events.append(f"{uid} 基地充电中({uav['soc']:.0f}%)")
                continue
            if uav["agent_remaining"] < quantity:
                if module == "water_20l" and inventory["water_liters"] >= quantity and inventory["water_modules_w20"] >= 1:
                    inventory["water_liters"] -= quantity
                    inventory["water_modules_w20"] -= 1
                    uav["agent_remaining"] = quantity
                    uav["refills"] += 1
                    uav["status"] = "servicing"
                    round_events.append(f"{uid} 基地补水 {quantity}L(4 min)")
                elif module == "co2_6kg" and inventory["co2_modules_c6"] >= 1:
                    inventory["co2_modules_c6"] -= 1
                    uav["agent_remaining"] = quantity
                    uav["refills"] += 1
                    uav["status"] = "servicing"
                    round_events.append(f"{uid} 更换 CO₂ 模块(5 min)")
                else:
                    uav["status"] = "fault"
                    round_events.append(f"{uid} 药剂库存耗尽,停止出动")
                    continue
            sortie_soc = self._sortie_soc(uav, spray, center)
            if uav["soc"] - sortie_soc < RETURN_SOC:
                if self._take_pack(inventory):
                    uav["soc"] = cfg["charging"]["battery_swap_soc"]
                    uav["swaps"] += 1
                    round_events.append(f"{uid} 换电(5 min,SOC→95%)")
                else:
                    uav["status"] = "returning"
                    round_events.append(f"{uid} SOC 不足且无备用电池,返航")
                    continue
            # 执行架次
            uav["soc"] = round(uav["soc"] - sortie_soc, 2)
            uav["soc_cost_total"] = round(uav["soc_cost_total"] + sortie_soc, 2)
            uav["agent_remaining"] = round(uav["agent_remaining"] - quantity, 2)
            uav["sorties"] += 1
            uav["status"] = "working"
            uav["position"] = dict(center)
            suppression_flp += eff
            round_events.append(f"{uid} 第 {uav['sorties']} 架次喷洒,SOC→{uav['soc']}%")

        # --- 侦察子群: 悬停监测耗电
        for assignment in state.get("support_plan", {}).get("recon", []):
            uav = by_id.get(assignment["uav_id"])
            if uav and uav["status"] not in {"fault"}:
                rate = R.uav_mode_rate(uav, loaded=False, hover=True)
                uav["soc"] = round(max(0, uav["soc"] - R.delta_soc(rate, round_minutes)), 2)
                uav["status"] = "working"
                uav["position"] = {"x": center["x"] + 60, "y": center["y"] - 60, "z": assignment.get("alt_m", 60)}
                uav["target"] = center

        # --- 支援子群: 有人分支通信悬停 / 无人分支物流运送
        for assignment in state.get("support_plan", {}).get("support", []):
            uav = by_id.get(assignment["uav_id"])
            if not uav or uav["status"] == "fault":
                continue
            if assignment.get("mode") == "comms_hover":
                rate = R.uav_mode_rate(uav, loaded=False, hover=True)
                uav["soc"] = round(max(0, uav["soc"] - R.delta_soc(rate, round_minutes)), 2)
                uav["status"] = "working"
                uav["position"] = {"x": center["x"] - 120, "y": center["y"] + 120, "z": 80}
                uav["target"] = uav["position"]
            elif assignment.get("mode") == "logistics":
                fsp = state["environment"]["forward_supply_point"]
                # 库存富余才前送(基地保留至少 6 组), 且每 2 轮送一次, 避免掏空基地
                if round_index % 2 == 1 and inventory.get("battery_packs", 0) >= 8:
                    loaded_rate = R.uav_mode_rate(uav, loaded=True)
                    empty_rate = R.uav_mode_rate(uav, loaded=False)
                    dist = R.distance_m(base, fsp)
                    trip_min = R.flight_minutes(dist, uav["speed_mps"]) * 2 + 2
                    cost = R.delta_soc(loaded_rate, trip_min / 2) + R.delta_soc(empty_rate, trip_min / 2)
                    uav["soc"] = round(max(0, uav["soc"] - cost), 2)
                    uav["status"] = "working"
                    uav["position"] = dict(fsp)
                    uav["target"] = fsp
                    packs = min(2, int(inventory.get("battery_packs", 0)) - 6)
                    if packs > 0:
                        inventory["battery_packs"] -= packs
                        for point in inventory.get("forward_supply_points", []):
                            if point["id"] == fsp.get("id", "fsp-1"):
                                point["battery_packs"] = point.get("battery_packs", 0) + packs
                        round_events.append(f"{uav['uav_id']} 交付 {packs} 组电池至前向补给点")
                else:
                    uav["status"] = "available"
                    uav["position"] = dict(base)

        # --- 场景脚本: 风速突变
        wind_speed = fire["wind_speed"]
        shift = state["scenario_cfg"].get("wind_shift")
        if shift and round_index == shift["round"]:
            wind_speed = shift["to_mps"]
            round_events.append(f"实测风速升至 {wind_speed} m/s(跳档)")
            for cell in fire["cells"]:
                cell["flp"] = round(cell["flp"] * (R.resolve_wind_band(wind_speed)["k_wind"] / R.resolve_wind_band(fire["wind_speed"])["k_wind"]), 2)
            band = R.resolve_wind_band(wind_speed)
            fire["wind_speed"] = wind_speed
            fire["wind_band"] = band["band"]
            fire["wind_band_label"] = band["label"]

        # --- 火情轮次演化: B_(t+dt) = max(0, B + G*dt/60 - ΣS)
        before = fire["total_flp"]
        cell_growth = fire["growth_flp_per_hour"] * round_minutes / 60 / max(len(fire["cells"]), 1)
        for cell in fire["cells"]:
            cell["flp"] = round(cell["flp"] + cell_growth, 3)
        fire["total_flp"] = round(sum(c["flp"] for c in fire["cells"]), 2)
        result = R.simulate_round(before, fire["growth_flp_per_hour"], suppression_flp, round_minutes)
        for cell in fire["cells"]:
            share = (cell["flp"] / fire["total_flp"]) if fire["total_flp"] else 0
            cell["flp"] = round(max(0, cell["flp"] - suppression_flp * share), 3)
        fire["total_flp"] = round(sum(c["flp"] for c in fire["cells"]), 2)

        # --- 触发判定(规则 10: 关键事件)
        trigger = None
        if shift and round_index == shift["round"]:
            trigger = {"type": "wind_band_change", "detail": f"风速 {fire['wind_speed']} m/s 进入 {fire['wind_band_label']}",
                       "rule": "风速进入更高档位 → 强制重规划"}
        elif before > 0 and fire["total_flp"] > before * (1 + R.sim_config()["triggers"]["flp_growth_ratio"]):
            trigger = {"type": "flp_growth", "detail": f"火情负荷上升超过 20%", "rule": "FLP↑>20% → 强制重规划"}

        record = {
            "round_index": round_index, "sim_minutes": round_index * round_minutes,
            "before_flp": before, "growth_flp": result["growth_flp"],
            "suppression_flp": round(suppression_flp, 2), "after_flp": fire["total_flp"],
            "wind_speed": wind_speed,
            "uavs": [{"uav_id": u["uav_id"], "status": u["status"], "position": dict(u["position"]), "soc": u["soc"],
                      "agent_remaining": u["agent_remaining"], "sorties": u["sorties"], "swaps": u["swaps"],
                      "refills": u["refills"]} for u in fleet],
            # 深拷贝: forward_supply_points 等嵌套结构会被后续轮次原地修改, 浅拷贝会污染历史
            "inventory": copy.deepcopy(inventory), "events": round_events,
        }
        rounds = state.get("rounds", []) + [record]
        BOARD.update(task_id, fire=fire, fleet=fleet, inventory=inventory, rounds=rounds, round_index=round_index)
        events_text = "; ".join(round_events) if round_events else "常规轮次"
        self.say(task_id, "ROUND", "blackboard",
                 f"第 {round_index} 轮(t+{round_index * round_minutes:.0f} min):B {before}→{fire['total_flp']} FLP,"
                 f"本轮压制 {round(suppression_flp, 1)};{events_text}",
                 {"round": record})

        # 卡滞计数: 所有灭火机连续多轮返航/充电且无人作业 → 资源周转缺口
        active = [by_id[uid] for uid in cand["suppression_uavs"] if by_id[uid]["status"] not in {"fault"}]
        stalled_states = {"returning", "charging"}
        all_stalled = all(u["status"] in stalled_states for u in active) if active else True
        any_working = any(u["status"] in {"working", "servicing", "available", "assigned"} for u in active)
        stall_rounds = (state.get("stall_rounds", 0) + 1) if (all_stalled and not any_working) else 0

        route, conclusion = "next_round", None
        if fire["total_flp"] <= 0.01:
            # 火情已归零: 无论本轮是否同时出现风变等触发, 优先结束, 不做无意义重规划
            route, conclusion = "done", "火情负荷归零,首轮控制目标达成。"
        elif trigger:
            route = "replan"
            replans = state.get("replans", 0) + 1
            BOARD.update(task_id, phase="replanning", replans=replans)
            self.say(task_id, "REPLAN_TRIGGER", "commander",
                     f"触发重规划:{trigger['detail']}({trigger['rule']})。指挥官请重新组织研判与方案生成。", trigger)
            self.think_bg(task_id, "REPLAN_TRIGGER", "commander",
                          "分析突变影响: 触发事件对火势/药剂效率/方案的影响, 以及重规划应重点调整什么",
                          f"第{round_index}轮, 触发: {trigger['detail']};当前 B={fire['total_flp']} FLP,"
                          f"风 {fire['wind_speed']} m/s({fire['wind_band_label']}),本轮压制 {round(suppression_flp, 1)} FLP")
        elif round_index >= cfg["time"]["max_rounds"]:
            route, conclusion = "done", f"达到最大轮次({cfg['time']['max_rounds']}),剩余 FLP {fire['total_flp']}。"
        elif active and stall_rounds >= 4:
            route, conclusion = "done", f"灭火机持续无法出动(电池/电量周转不足),剩余 FLP {fire['total_flp']},输出资源缺口。"

        await asyncio.sleep(demo["round_interval_ms"] / 1000)  # 演示节奏
        out = {"round_index": round_index, "rounds": rounds, "fleet": fleet, "inventory": inventory,
               "fire": fire, "route": route, "stall_rounds": stall_rounds}
        if conclusion:
            out["conclusion"] = conclusion
        return out

    @staticmethod
    def _take_pack(inventory: Dict[str, Any]) -> bool:
        """取一组满电电池: 优先前向补给点(S2 预置), 再扣主库存。"""
        for point in inventory.get("forward_supply_points", []):
            if point.get("id") == "fsp-1" and point.get("battery_packs", 0) >= 1:
                point["battery_packs"] -= 1
                return True
        if inventory.get("battery_packs", 0) >= 1:
            inventory["battery_packs"] -= 1
            return True
        return False

    @staticmethod
    def _sortie_soc(uav: Dict[str, Any], spray: Dict[str, Any], center: Dict[str, float]) -> float:
        dist = R.distance_m(uav["position"], center)
        out_min = R.flight_minutes(dist, uav["speed_mps"])
        rate_out = R.uav_mode_rate(uav, loaded=True)
        rate_back = R.uav_mode_rate(uav, loaded=False)
        hover = R.sim_config()["energy"]["suppression"]["hover_spray"]
        return R.delta_soc(rate_out, out_min) + R.delta_soc(hover, spray["minutes"]) + R.delta_soc(rate_back, out_min)
