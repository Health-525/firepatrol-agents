"""⑤ 仿真评估 Agent —— 裁判: 候选离散预演+评分选优; 审批后按 5 分钟轮次驱动执行, 每轮自主研判。"""
from __future__ import annotations

import asyncio
import copy
import random
from typing import Any, Dict, List

from ..agentkit.base import BaseAgent
from ..domain.store import BOARD
from ..rules import tools as R
from ..rules.environment import observe_wind
from ..rules.knowledge import query_knowledge
from . import backfill, judgment

RETURN_SOC = R.RETURN_SOC


class SimulatorAgent(BaseAgent):
    agent_id = "simulator"
    name = "仿真评估"
    role = "离散轮次仿真 · 多目标评分 J · 净处置能力 · 每轮自主研判"
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
        action_minutes = 5.0  # 本轮真实时长: 并行取各机动作的最大耗时, 下限 5 分钟

        by_id = {u["uav_id"]: u for u in fleet}
        cap = R.suppression_capability(module, fire["fire_type"], fire["wind_speed"])
        eff = cap["effective_flp"]

        # --- 外生单机失能事件: 场景只约定"第几轮可能失能", 具体哪架由当前方案实时选定(非剧本点名单)
        failed_ids: List[str] = []
        failure_cfg = (state.get("scenario_cfg") or {}).get("uav_failure")
        if failure_cfg and not state.get("failure_applied") and round_index >= int(failure_cfg.get("round", 0)):
            flying = [by_id[uid] for uid in cand["suppression_uavs"] if by_id[uid]["status"] == "working"]
            pool_fail = flying or [by_id[uid] for uid in cand["suppression_uavs"]
                                   if by_id[uid]["status"] != "fault"]
            if pool_fail:
                victim = random.Random(f"fail-{task_id}").choice(pool_fail)
                victim["status"] = "fault"
                victim["failure"] = True
                failed_ids.append(victim["uav_id"])
                round_events.append(f"{victim['uav_id']} 遥测丢失且电量骤降, 判定机电故障失能")
                self.say(task_id, "UAV_FAULT", "commander",
                         f"⚠️ {victim['uav_id']} 失联:遥测中断、电量骤降, 按机电故障处置, 立即评估补位。",
                         {"faulted": victim["uav_id"], "round": round_index})

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
                    action_minutes = max(action_minutes, cfg["charging"]["battery_swap_minutes"])
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
                    uav["position"] = dict(base)
                    round_events.append(f"{uid} 基地补水 {quantity}L(本轮回合为补给轮)")
                    action_minutes = max(action_minutes, cfg["refill_minutes"]["base"])
                    continue  # 时间一致性: 一轮一事, 补给轮不出动
                elif module == "co2_6kg" and inventory["co2_modules_c6"] >= 1:
                    inventory["co2_modules_c6"] -= 1
                    uav["agent_remaining"] = quantity
                    uav["refills"] += 1
                    uav["status"] = "servicing"
                    uav["position"] = dict(base)
                    round_events.append(f"{uid} 更换 CO₂ 模块(本轮回合为补给轮)")
                    action_minutes = max(action_minutes, cfg["module_swap_minutes"])
                    continue
                else:
                    uav["status"] = "fault"
                    round_events.append(f"{uid} 药剂库存耗尽,停止出动")
                    continue
            sortie_soc = self._sortie_soc(uav, spray, center)
            if uav["soc"] - sortie_soc < RETURN_SOC:
                if self._take_pack(inventory):
                    uav["soc"] = cfg["charging"]["battery_swap_soc"]
                    uav["swaps"] += 1
                    round_events.append(f"{uid} 换电(本轮回合为换电轮)")
                    action_minutes = max(action_minutes, cfg["charging"]["battery_swap_minutes"])
                    continue  # 一轮一事: 换电轮不出动
                else:
                    uav["status"] = "returning"
                    round_events.append(f"{uid} SOC 不足且无备用电池,返航")
                    continue
            # 执行架次(时长=往返飞行+喷洒, 按起飞前位置计算)
            dist_now = R.distance_m(uav["position"], center)
            action_minutes = max(action_minutes, 2 * R.flight_minutes(dist_now, uav["speed_mps"]) + spray["minutes"])
            uav["soc"] = round(uav["soc"] - sortie_soc, 2)
            uav["soc_cost_total"] = round(uav["soc_cost_total"] + sortie_soc, 2)
            uav["agent_remaining"] = round(uav["agent_remaining"] - quantity, 2)
            uav["sorties"] += 1
            uav["status"] = "working"
            uav["position"] = dict(center)
            suppression_flp += eff
            round_events.append(f"{uid} 第 {uav['sorties']} 架次喷洒,SOC→{uav['soc']}%")

        # --- 补位决策(行动权): 失能即刻定替换, 不等火涨、不过审批门——方案内换机, 目标与编成不变
        if failed_ids:
            await self._backfill(state, cand, by_id, spray, center, failed_ids, round_events)

        # --- 侦察子群: 悬停监测耗电
        for assignment in state.get("support_plan", {}).get("recon", []):
            uav = by_id.get(assignment["uav_id"])
            if uav and uav["status"] not in {"fault"}:
                rate = R.uav_mode_rate(uav, loaded=False, hover=True)
                uav["soc"] = round(max(0, uav["soc"] - R.delta_soc(rate, round_minutes)), 2)
                uav["status"] = "working"
                uav["position"] = {"x": center["x"] + 60, "y": center["y"] - 60, "z": assignment.get("alt_m", 60)}
                uav["target"] = center

        # --- 支援子群: 有人分支(语音引导+人群沿疏散路线移动) / 无人分支物流运送
        support_plan = state.get("support_plan") or {}
        evac = dict(support_plan.get("evacuation") or {})
        for assignment in support_plan.get("support", []):
            uav = by_id.get(assignment["uav_id"])
            if not uav or uav["status"] == "fault":
                continue
            if assignment.get("mode") == "comms_hover":
                rate = R.uav_mode_rate(uav, loaded=False, hover=True)
                uav["soc"] = round(max(0, uav["soc"] - R.delta_soc(rate, round_minutes)), 2)
                uav["status"] = "working"
                if evac.get("path"):
                    # S1 悬停在人群上空, 广播跟随引导
                    here = evac["path"][min(int(float(evac.get("progress_cells", 0))), len(evac["path"]) - 1)]
                    uav["position"] = {"x": here["x"] + 80, "y": here["y"] - 80, "z": 80}
                else:
                    uav["position"] = {"x": center["x"] - 120, "y": center["y"] + 120, "z": 80}
                uav["target"] = uav["position"]
                if round_index == 1:
                    round_events.append(f"{uav['uav_id']} 升空至人员区上空,开启语音广播引导疏散")
            elif assignment.get("mode") == "route_cover":
                rate = R.uav_mode_rate(uav, loaded=False, hover=True)
                uav["soc"] = round(max(0, uav["soc"] - R.delta_soc(rate, round_minutes)), 2)
                uav["status"] = "working"
                mid = (evac.get("path") or [center])[len(evac.get("path", [center])) // 2]
                uav["position"] = {"x": mid["x"], "y": mid["y"], "z": 60}
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

        # --- 环境观测流: 每轮读取风速观测(非剧本), 观测到跳档才变更火情风况
        wind_speed = fire["wind_speed"]
        observed_wind, band_jump = observe_wind(state["scenario_cfg"], round_index, fire["wind_speed"])
        if observed_wind != fire["wind_speed"]:
            round_events.append(f"风速观测值 {observed_wind} m/s(当前 {fire['wind_speed']})")
        if band_jump:
            wind_speed = observed_wind
            k_new = R.resolve_wind_band(observed_wind)["k_wind"]
            k_old = R.resolve_wind_band(fire["wind_speed"])["k_wind"]
            for cell in fire["cells"]:
                cell["flp"] = round(cell["flp"] * (k_new / k_old), 2)
            band = R.resolve_wind_band(observed_wind)
            fire["wind_speed"] = observed_wind
            fire["wind_band"] = band["band"]
            fire["wind_band_label"] = band["label"]

        # --- 疏散推进: 火人交互(贴火加速/贴身被困) + 从人群当前位置改道
        if evac.get("path") and not evac.get("evacuated"):
            from ..rules.evacuation import PANIC_MPS, WALK_MPS, advance_people, fire_adjacent, plan_evacuation as plan_evac
            adjacent, on_fire = fire_adjacent(evac["path"], float(evac.get("progress_cells", 0)), fire["cells"])
            if on_fire and not evac.get("trapped"):
                evac["trapped"] = True
                round_events.append("火焰已蔓延至人群所在格!人员被困,S1 就近引导向背火开阔地避险")
                self.say(task_id, "EVAC_BROADCAST", "human",
                         "🔊 紧急!火势已逼近队伍,请立即离开现有路线,向背火方向的开阔地快速转移,S1 在上空引导!")
            if not evac.get("trapped"):
                blocked = [c for c in fire["cells"] if c["flp"] > 5]
                still_safe = all(not any(c["cx"] == p["cx"] and c["cy"] == p["cy"] for c in blocked) for p in evac["path"])
                if not still_safe:
                    here_idx = min(int(float(evac.get("progress_cells", 0))), len(evac["path"]) - 1)
                    here_cell = evac["path"][here_idx]
                    reroute = plan_evac(state["environment"], fire["cells"],
                                        (state["environment"].get("people_zones") or [{}])[0],
                                        start_cell=(here_cell["cx"], here_cell["cy"]))  # 从人群当前位置改道, 不传送回营地
                    if reroute["found"] and reroute.get("path") != evac["path"]:
                        evac.update({"exit": reroute["exit"], "path": reroute["path"],
                                     "walk_minutes": reroute["walk_minutes"], "climb_m": reroute["climb_m"],
                                     "progress_cells": 0})
                        round_events.append(f"火情封锁原路线,人群已从当前位置改道至 {reroute['exit']}(约 {reroute['walk_minutes']} 分钟)")
                        self.say(task_id, "EVAC_BROADCAST", "human",
                                 f"🔊 注意:原路线已被火情封锁!请立即改道,沿新路线向 {reroute['exit']} 撤离,"
                                 f"约 {reroute['walk_minutes']} 分钟,S1 继续在上空引导。",
                                 {"evacuation": {"exit": reroute["exit"], "path": reroute["path"], "reroute": True}})
                    elif not reroute["found"]:
                        evac["trapped"] = True
                        round_events.append("疏散路线全部被封锁,人员被困,S1 引导原地避险等待增援")
                        self.say(task_id, "EVAC_BROADCAST", "human", "🔊 各出口通道均被火情封锁!请就近寻找开阔地与背火坡避险,S1 持续在空中引导!")
                speed = PANIC_MPS if adjacent else WALK_MPS
                if adjacent:
                    round_events.append("火线临近,人群转入恐慌步速(1.6 m/s)")
                progress, done, here = advance_people(evac, action_minutes, speed)
                evac["progress_cells"] = progress
                if done and not evac.get("evacuated"):
                    evac["evacuated"] = True
                    round_events.append(f"人员已全部抵达 {evac['exit']},疏散完成")
                    self.say(task_id, "EVAC_BROADCAST", "human",
                             f"🔊 疏散完成:{evac.get('people', '全体')}名人员已安全抵达 {evac['exit']}。",
                             {"evacuation": {"evacuated": True}})
                elif here and round_index % 2 == 0:
                    round_events.append(f"疏散进行中:人群位于 ({here['cx']},{here['cy']}),距出口约 "
                                        f"{max(0, len(evac['path']) - 1 - int(progress))} 格")
            support_plan["evacuation"] = evac

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

        sim_minutes = state.get("sim_minutes", 0.0) + action_minutes
        record = {
            "round_index": round_index, "sim_minutes": round(sim_minutes, 1),
            "duration_min": round(action_minutes, 1),
            "before_flp": before, "growth_flp": result["growth_flp"],
            "suppression_flp": round(suppression_flp, 2), "after_flp": fire["total_flp"],
            "wind_speed": wind_speed, "band_jump": bool(band_jump),
            "uavs": [{"uav_id": u["uav_id"], "status": u["status"], "position": dict(u["position"]), "soc": u["soc"],
                      "agent_remaining": u["agent_remaining"], "sorties": u["sorties"], "swaps": u["swaps"],
                      "refills": u["refills"]} for u in fleet],
            # 深拷贝: forward_supply_points 等嵌套结构会被后续轮次原地修改, 浅拷贝会污染历史
            "inventory": copy.deepcopy(inventory), "events": round_events,
        }
        rounds = state.get("rounds", []) + [record]
        BOARD.update(task_id, fire=fire, fleet=fleet, inventory=inventory, rounds=rounds, round_index=round_index, support_plan=support_plan)
        events_text = "; ".join(round_events) if round_events else "常规轮次"
        self.say(task_id, "ROUND", "blackboard",
                 f"第 {round_index} 轮(t+{round_index * round_minutes:.0f} min):B {before}→{fire['total_flp']} FLP,"
                 f"本轮压制 {round(suppression_flp, 1)};{events_text}",
                 {"round": record})

        # 卡滞计数: 所有灭火机连续多轮返航/充电且无人作业(作为研判输入, 不再直接决定终止)
        active = [by_id[uid] for uid in cand["suppression_uavs"] if by_id[uid]["status"] not in {"fault"}]
        stalled_states = {"returning", "charging"}
        all_stalled = all(u["status"] in stalled_states for u in active) if active else True
        any_working = any(u["status"] in {"working", "servicing", "available", "assigned"} for u in active)
        stall_rounds = (state.get("stall_rounds", 0) + 1) if (all_stalled and not any_working) else 0

        # 只有物理事实(测量)直接终止: 火灭 / 仿真视界; 其余一律交给自主研判
        route, conclusion = "judge", None
        if fire["total_flp"] <= 0.01:
            route, conclusion = "done", "火情负荷归零,首轮控制目标达成。"
        elif round_index >= cfg["time"]["max_rounds"]:
            route, conclusion = "done", f"达到最大轮次({cfg['time']['max_rounds']}),剩余 FLP {fire['total_flp']}。"

        await asyncio.sleep(demo["round_interval_ms"] / 1000)  # 演示节奏
        out = {"round_index": round_index, "rounds": rounds, "fleet": fleet, "inventory": inventory,
               "fire": fire, "route": route, "stall_rounds": stall_rounds, "sim_minutes": round(sim_minutes, 1),
               "support_plan": support_plan,
               "failure_applied": bool(state.get("failure_applied")) or bool(failed_ids)}
        if conclusion:
            out["conclusion"] = conclusion
        return out

    # ------------------------------------------------ 补位: 失能机的替换由 Agent 决定并立即生效

    async def _backfill(self, state: Dict[str, Any], cand: Dict[str, Any], by_id: Dict[str, Dict[str, Any]],
                        spray: Dict[str, Any], center: Dict[str, float],
                        failed_ids: List[str], round_events: List[str]) -> None:
        """评估备用机两档门槛(立即出动 / 一轮周转后出动), 交大脑选择, 选定即改方案。

        这里是 Agent 的行动权: 选择结果直接改写 plan.candidate 的编成并落黑板,
        下一轮新机即进入架次循环——不是旁白, 失败降级为确定性规则。
        """
        task_id = state["task_id"]
        inventory = state["inventory"]
        module = cand["module"]
        quantity = spray["quantity"]
        tasked = set(cand["suppression_uavs"])
        packs = int(inventory.get("battery_packs", 0)) + sum(
            p.get("battery_packs", 0) for p in inventory.get("forward_supply_points", [])
            if p.get("id") == "fsp-1")
        refillable = ((module == "water_20l" and inventory.get("water_modules_w20", 0) >= 1
                       and inventory.get("water_liters", 0) >= quantity)
                      or (module == "co2_6kg" and inventory.get("co2_modules_c6", 0) >= 1))

        ready_now: List[Dict[str, Any]] = []
        ready_after: List[Dict[str, Any]] = []
        for uav in state["fleet"]:
            if uav.get("subgroup") != "suppression" or uav["uav_id"] in tasked or uav.get("failure"):
                continue
            if uav["status"] not in {"available", "assigned", "charging"}:
                continue
            entry = {"uav_id": uav["uav_id"], "soc": uav["soc"], "status": uav["status"],
                     "agent_remaining": uav.get("agent_remaining", 0),
                     "sortie_soc": round(self._sortie_soc(uav, spray, center), 1)}
            if uav["soc"] - entry["sortie_soc"] >= RETURN_SOC:
                ready_now.append(entry)
            elif packs >= 1 and (uav.get("agent_remaining", 0) >= quantity or refillable):
                entry["service"] = "基地换电/补给一轮后可出动"
                ready_after.append(entry)
        candidates = {"ready_now": sorted(ready_now, key=lambda c: -c["soc"]),
                      "ready_after_service": sorted(ready_after, key=lambda c: -c["soc"])}

        context = {"fire_total_flp": state["fire"]["total_flp"],
                   "growth_flp_per_hour": state["fire"]["growth_flp_per_hour"],
                   "module": module, "battery_packs": packs, "faulted": failed_ids,
                   "round_index": state.get("round_index", 0) + 1}
        decision = await backfill.decide(failed_ids, candidates, context)
        choice = decision["choice"]
        if choice in {c["uav_id"] for group in candidates.values() for c in group}:
            spare = by_id[choice]
            spare["status"] = "assigned"
            spare["assigned_task"] = task_id
            spare["target"] = center
            cand["suppression_uavs"] = [choice if uid in failed_ids else uid
                                        for uid in cand["suppression_uavs"]]
            BOARD.update(task_id, plan=state["plan"], fleet=state["fleet"])
            round_events.append(f"{choice} 受命补位, 接替 {'/'.join(failed_ids)} 的压制架次")
            self.say(task_id, "BACKFILL", "commander",
                     f"🔁 补位决策({decision['source']}):{'/'.join(failed_ids)} 失能 → {choice} 顶替"
                     f"(方案内换机, 不改目标与规模, 无需重新审批)。{decision['rationale']}",
                     {"choice": choice, "faulted": failed_ids, "source": decision["source"],
                      "rationale": decision["rationale"], "candidates": candidates})
        else:
            round_events.append("无可补位备用机, 交由本轮自主研判决定重规划或降级")
            self.say(task_id, "BACKFILL", "commander",
                     f"🔁 补位决策({decision['source']}):无满足出动门槛的备用机;"
                     f"{decision['rationale']}。移交本轮自主研判。",
                     {"choice": "none", "faulted": failed_ids, "source": decision["source"]})

    # ------------------------------------------------ 回收: 任务结束全员返航归位

    async def recover_round(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """结案回收轮: 在外机分两拍返航(先转身、后降落), 全部归位后进入归档。

        不推进火情轮次(rounds 时间线不变), 只更新机队与协作消息——目标已结案,
        这里是"把机队安全带回家": 航程时间与耗电按规则引擎计, 机电故障机带回但停机检修。
        """
        cfg = R.sim_config()
        demo = cfg["demo"]
        task_id = state["task_id"]
        base = state["environment"]["base"]
        fleet = state["fleet"]
        home = {"x": base["x"], "y": base["y"], "z": 0}
        phase = state.get("recovery_phase")
        # 归档路由透传: rejected 结案仍按 rejected 归档; 自循环两拍间经 recovery_archive 传递
        archive = state.get("recovery_archive")
        if archive not in {"done", "rejected"}:
            archive = state.get("route") if state.get("route") in {"done", "rejected"} else "done"

        airborne = [u for u in fleet if R.distance_m(u["position"], base) > 1.0]
        if not airborne:
            if phase != "landed":
                self.say(task_id, "RECOVERY", "human", "任务结束,机队均在基地待命,无需返航。")
            return {"route": archive, "recovery_phase": "landed", "recovery_archive": archive}

        if phase != "landing":
            for uav in airborne:
                uav["status"] = "returning"
                uav["target"] = dict(base)
            BOARD.update(task_id, phase="recovering", fleet=fleet)
            self.say(task_id, "RECOVERY", "human",
                     f"任务结束,下令全员返航:{', '.join(u['uav_id'] for u in airborne)} "
                     f"共 {len(airborne)} 架正返回基地。",
                     {"returning": [u["uav_id"] for u in airborne]})
            await asyncio.sleep(demo["round_interval_ms"] / 1000)
            return {"route": "recovering", "recovery_phase": "landing", "recovery_archive": archive}

        events = []
        longest = 0.0
        for uav in airborne:
            dist = R.distance_m(uav["position"], base)
            minutes = R.flight_minutes(dist, uav["speed_mps"])
            cost = R.delta_soc(R.uav_mode_rate(uav, loaded=False), minutes)
            uav["soc"] = round(max(0.0, uav["soc"] - cost), 2)
            uav["soc_cost_total"] = round(uav.get("soc_cost_total", 0.0) + cost, 2)
            uav["position"] = dict(home)
            uav["target"] = None
            uav["status"] = "fault" if uav.get("failure") else "available"
            longest = max(longest, minutes)
            note = "机电故障,降落即停机检修" if uav.get("failure") else "降落归位待命"
            events.append(f"{uav['uav_id']} 返航 {minutes:.1f} 分钟(SOC {uav['soc']:.0f}%),{note}")
        BOARD.update(task_id, fleet=fleet)
        self.say(task_id, "RECOVERY", "human",
                 f"全员返航完成:{len(airborne)} 架降落基地,最长航程 {longest:.1f} 分钟。" + ";".join(events),
                 {"landed": [u["uav_id"] for u in airborne], "longest_minutes": round(longest, 1)})
        await asyncio.sleep(demo["round_interval_ms"] / 1000)
        return {"route": archive, "recovery_phase": "landed", "recovery_archive": archive}

    # ------------------------------------------------ 自主研判: 要不要继续, 由 Agent 大脑判断

    async def judge_round(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """执行轮次后的研判节点: 无固定触发表, GLM 大脑对全量快照自主定级与决策; 降级保底见 judgment 模块。"""
        task_id = state["task_id"]
        snapshot = judgment.build_snapshot(state)
        verdict, trace = await judgment.judge(snapshot)
        round_index = snapshot["round_index"]

        data = {"judgment": verdict, "source": verdict["source"],
                "tools": [t["tool"] for t in trace]}
        self.say(task_id, "JUDGMENT", "commander",
                 f"⚖️ 第 {round_index} 轮自主研判({verdict['source']}):[{verdict['severity']}] "
                 f"{verdict['situation']} → {verdict['decision']}。{verdict['rationale']}", data)
        if verdict["escalate"]:
            self.say(task_id, "JUDGMENT", "human",
                     f"⚠️ 第 {round_index} 轮研判请求人工关注:{verdict['situation']}。{verdict['expected']}", data)

        BOARD.update(task_id, last_judgment=verdict)
        if verdict["decision"] == "replan":
            replans = state.get("replans", 0) + 1
            BOARD.update(task_id, phase="replanning", replans=replans)
            self.say(task_id, "REPLAN_TRIGGER", "commander",
                     f"触发重规划:{verdict['situation']}(severity={verdict['severity']},"
                     f"研判来源={verdict['source']})。指挥官请基于实时状态重新组织研判与方案生成。", verdict)
            return {"route": "replan", "replans": replans, "last_replan_round": round_index,
                    "last_judgment": verdict}
        if verdict["decision"] == "terminate":
            fire = state.get("fire") or {}
            conclusion = verdict.get("conclusion") or \
                f"研判终止:{verdict['situation']}(剩余 FLP {fire.get('total_flp')})。"
            return {"route": "done", "conclusion": conclusion, "last_judgment": verdict}
        return {"route": "next_round", "last_judgment": verdict}

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
        # 去程爬升耗电(f_climb): 高差 >30m 的航段速率上浮(地形数据接入能耗)
        rate_out = R.climb_adjusted_rate(R.uav_mode_rate(uav, loaded=True),
                                         uav["position"]["x"], uav["position"]["y"], center["x"], center["y"])
        rate_back = R.uav_mode_rate(uav, loaded=False)
        hover = R.sim_config()["energy"]["suppression"]["hover_spray"]
        return R.delta_soc(rate_out, out_min) + R.delta_soc(hover, spray["minutes"]) + R.delta_soc(rate_back, out_min)
