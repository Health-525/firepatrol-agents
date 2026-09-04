"""自主研判模块 —— 「要不要改变计划」由 Agent 大脑判断, 不再是代码里的固定触发表。

原则(fire-agent-autonomy skill):
- 测量归工具: 快照里的数字全部来自规则引擎与黑板, Agent 只引用、不编造、不外推;
- 判断归 Agent: 每轮执行后, GLM 大脑对全量快照走「观察-定向-决策」研判, 产出结构化判断;
- 降级保底: GLM 不可用/超时/输出非法时, 回落保守降级研判(异常观测集 + 重规划冷却),
  离线测试与演示永不中断, 且降级来源被明确标注(source=conservative-fallback)。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..agentkit.brain import AgentBrain
from ..agentkit.llm import SAFETY_RULE
from ..rules import tools as R
from ..rules.knowledge import query_knowledge

SEVERITIES = ("info", "watch", "urgent", "critical")
DECISIONS = ("continue", "replan", "terminate")

JUDGE_TOOLS = {"query_knowledge": query_knowledge, "net_capability": R.net_capability}

JUDGMENT_PROTOCOL = (
    '{"situation": "一句话态势", "severity": "info|watch|urgent|critical", '
    '"evidence": ["引用快照数字的证据"], '
    '"options": [{"action": "...", "pros": "...", "cons": "...", "risk": "低|中|高"}], '
    '"chosen": "选中的动作", "rationale": "为什么选它", "expected": "预期下轮效果", '
    '"fallback": "预期不成立时怎么办", "decision": "continue|replan|terminate", '
    '"escalate": false, "conclusion": "仅 terminate 时填: 结案结论(剩余FLP与资源缺口口径)"}'
)

JUDGMENT_PROMPT = f"""{SAFETY_RULE}
你是「仿真评估」Agent 的研判大脑: 每个 5 分钟执行轮次结束后, 自主研判当前方案还要不要继续。
没有预设触发表——风变、火势超预期、单机失能、补给断供、人员被困、以及快照里任何与方案预期
不一致的信号, 都由你自己发现、自己定级、自己决定。

研判顺序(每轮):
1. 观察: 通读快照, 找出与方案预期/上轮结果的偏差;
2. 定向: 判断偏差是噪声还是趋势, 是否打破方案前提(风档/增长/压制能力/人员);
3. 决策: 给出 2-4 个选项(继续/局部调整/重规划/收缩/终止), 权衡代价后选一个。

决策语义:
- continue: 态势在预期内或仅需盯防, 继续当前方案;
- replan: 方案前提被打破, 需重新生成方案并再次审批(执行会暂停等人确认);
- terminate: 目标已不可达或继续只会耗尽资源——结案结论写明剩余 FLP 与资源缺口, 不给虚假完成时间。

判断尺度: 优先级为 人员安全 > 通信连续 > 火势不扩大 > 资源节约; 拿不准时往高一级处理(宁可过防)。
刚重规划不到 2 轮处于冷却期, 非critical不再触发replan(见快照 rounds_since_replan)。
处置经验(征象/处置/禁忌)可查知识库: query_knowledge, 关键词如 风况突变/火势/单机异常/
新发现人员/多火点/通信/库存/疏散/传感器矛盾/未知险情。净处置能力可用 net_capability 核实。

只输出一个 JSON 对象(不要 markdown 代码块、不要多余文字), 结构:
{JUDGMENT_PROTOCOL}"""

_brain = AgentBrain(JUDGMENT_PROMPT, JUDGE_TOOLS)


# ------------------------------------------------ 快照: 研判的全部输入(纯测量数据)

def build_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    plan = state.get("plan") or {}
    cand = plan.get("candidate") or {}
    rounds = state.get("rounds") or []
    last = rounds[-1] if rounds else {}
    fleet = state.get("fleet") or []
    tasked_ids = set(cand.get("suppression_uavs") or [])
    fire = state.get("fire") or {}
    inventory = state.get("inventory") or {}
    evac = ((state.get("support_plan") or {}).get("evacuation")) or {}
    round_index = state.get("round_index", 0)
    last_replan = state.get("last_replan_round")
    return {
        "round_index": round_index,
        "sim_minutes": state.get("sim_minutes", 0.0),
        "plan": {"plan_id": plan.get("plan_id"), "module": cand.get("module"),
                 "feasibility": cand.get("feasibility"), "time_interval": cand.get("time_interval"),
                 "tasked_uavs": sorted(tasked_ids)},
        "fire": {"total_flp": fire.get("total_flp"),
                 "last_round_before_flp": last.get("before_flp"),
                 "last_round_growth_flp": last.get("growth_flp"),
                 "last_round_suppression_flp": last.get("suppression_flp"),
                 "growth_flp_per_hour": fire.get("growth_flp_per_hour"),
                 "wind_speed": fire.get("wind_speed"), "wind_band_label": fire.get("wind_band_label"),
                 "wind_band_jump_this_round": bool(last.get("band_jump")),
                 "net_rising_rounds": _consecutive_rising(rounds)},
        "tasked_fleet": [{"uav_id": u["uav_id"], "status": u["status"], "soc": u["soc"],
                          "agent_remaining": u.get("agent_remaining"), "sorties": u.get("sorties", 0)}
                         for u in fleet if u["uav_id"] in tasked_ids],
        "spare_fleet": [{"uav_id": u["uav_id"], "status": u["status"], "soc": u["soc"]}
                        for u in fleet
                        if u.get("subgroup") == "suppression" and u["uav_id"] not in tasked_ids],
        "inventory": {"water_liters": inventory.get("water_liters"),
                      "water_modules_w20": inventory.get("water_modules_w20"),
                      "co2_modules_c6": inventory.get("co2_modules_c6"),
                      "battery_packs": inventory.get("battery_packs"),
                      "fsp_battery_packs": sum(p.get("battery_packs", 0)
                                               for p in inventory.get("forward_supply_points", [])
                                               if p.get("id") == "fsp-1")},
        "people": {"status": fire.get("people_status"),
                   "support_branch": (state.get("support_plan") or {}).get("branch"),
                   "evac_progress_cells": evac.get("progress_cells"),
                   "evacuated": bool(evac.get("evacuated")), "trapped": bool(evac.get("trapped"))},
        "stall_rounds": state.get("stall_rounds", 0),
        "replans": state.get("replans", 0),
        "rounds_since_replan": (round_index - last_replan) if isinstance(last_replan, int) else 99,
    }


def _consecutive_rising(rounds: List[Dict[str, Any]]) -> int:
    """尾部连续「带压制仍净增长」轮数——真实的能力不足信号。

    周转轮(补给/换电, 本轮无架次)的火情回升属预期物理, 不计入; 只有压制在进行
    而火仍在涨, 才说明净处置能力被打破。用趋势而不是单轮阈值说话。
    """
    count = 0
    for record in reversed(rounds):
        if ((record.get("after_flp") or 0) > (record.get("before_flp") or 0) + 1e-9
                and (record.get("suppression_flp") or 0) > 0):
            count += 1
        else:
            break
    return count


# ------------------------------------------------ 协议解析与规范化

def parse_judgment(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中提取 JSON 对象(容忍代码块/前后缀文风); 提不出返回 None。"""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def normalize_judgment(raw: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    """校验并补全研判结构; severity 非法视为不可用(调用方走降级), decision 缺失按 severity 推导。"""
    severity = str(raw.get("severity", "")).strip().lower()
    if severity not in SEVERITIES:
        return None
    decision = str(raw.get("decision", "")).strip().lower()
    if decision not in DECISIONS:
        decision = "replan" if severity in {"urgent", "critical"} else "continue"
    options = []
    for opt in (raw.get("options") or [])[:4]:
        if isinstance(opt, dict) and opt.get("action"):
            options.append({"action": str(opt["action"])[:120], "pros": str(opt.get("pros", ""))[:80],
                            "cons": str(opt.get("cons", ""))[:80], "risk": str(opt.get("risk", ""))[:10]})
    return {
        "situation": str(raw.get("situation") or "态势研判")[:200],
        "severity": severity,
        "evidence": [str(e)[:140] for e in (raw.get("evidence") or [])[:6]],
        "options": options,
        "chosen": str(raw.get("chosen") or decision)[:120],
        "rationale": str(raw.get("rationale") or "")[:300],
        "expected": str(raw.get("expected") or "")[:160],
        "fallback": str(raw.get("fallback") or "")[:160],
        "decision": decision,
        "escalate": bool(raw.get("escalate")),
        "conclusion": str(raw.get("conclusion") or "")[:200],
        "source": source,
    }


# ------------------------------------------------ 保守降级: GLM 不可用时的研判

def conservative_judgment(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """降级研判: 以异常观测集 + 冷却代替固定触发表, 输出与其他来源同构的判断。

    与旧版两触发器(wind_band / FLP↑20%)的区别: 覆盖单机失能、库存断供、周转卡滞、
    人群被困; 火势判断用连续趋势(净增长轮数)而不是单轮百分比阈值; 重规划带冷却防环。
    """
    fire, plan, inv = snapshot["fire"], snapshot["plan"], snapshot["inventory"]
    anomalies: List[Dict[str, str]] = []
    if fire["wind_band_jump_this_round"]:
        anomalies.append({"name": "wind_band_jump", "severity": "urgent",
                          "detail": f"本轮观测到风档跳变, 当前 {fire['wind_speed']} m/s({fire['wind_band_label']})"})
    if fire["net_rising_rounds"] >= 2 and plan.get("feasibility") != "cannot_control":
        anomalies.append({"name": "flp_rising", "severity": "urgent",
                          "detail": f"连续 {fire['net_rising_rounds']} 轮压制中仍净增长, 净处置能力被打破"})
    faults = [u["uav_id"] for u in snapshot["tasked_fleet"] if u["status"] == "fault"]
    if faults:
        anomalies.append({"name": "tasked_uav_fault", "severity": "urgent",
                          "detail": f"方案内灭火机 {', '.join(faults)} 失能, 压制能力受损"})
    spares_usable = [u for u in snapshot.get("spare_fleet") or [] if u["status"] != "fault"]
    if snapshot["stall_rounds"] >= 2:
        anomalies.append({"name": "turnaround_stall", "severity": "watch",
                          "detail": f"灭火机连续 {snapshot['stall_rounds']} 轮处于返航/充电周转"})
    if plan.get("module") == "water_20l" and (inv["water_modules_w20"] or 0) < 1:
        anomalies.append({"name": "water_supply_out", "severity": "urgent", "detail": "W20 水剂模块库存耗尽"})
    if plan.get("module") == "co2_6kg" and (inv["co2_modules_c6"] or 0) < 1:
        anomalies.append({"name": "co2_supply_out", "severity": "urgent", "detail": "C6 CO₂ 模块库存耗尽"})
    trapped_under_guidance = False
    if snapshot["people"]["trapped"] and not snapshot["people"]["evacuated"]:
        if snapshot["people"].get("support_branch") == "people":
            # 「火围人但 S1 已引导避险」是应对进行中的状态: 上报人工, 继续压制火势为人群开路,
            # 重规划不会让路线立刻打开, 反而打断既定处置
            trapped_under_guidance = True
        else:
            anomalies.append({"name": "people_trapped", "severity": "critical",
                              "detail": "疏散人群被困且当前方案无人员引导分支"})

    # 全部失能且无机可补才终止; 还有备用机 → 重规划补位(经验: 立即补位, 不等火涨)
    hard_stop = snapshot["stall_rounds"] >= 4 or (
        snapshot["tasked_fleet"] and all(u["status"] == "fault" for u in snapshot["tasked_fleet"])
        and not spares_usable)
    top = max((a["severity"] for a in anomalies), key=lambda s: SEVERITIES.index(s), default="info")
    cooled = snapshot["rounds_since_replan"] < 2
    if hard_stop:
        decision = "terminate"
    elif anomalies and (top == "critical" or not cooled):
        decision = "replan"
    else:
        decision = "continue"

    if decision == "terminate":
        conclusion = (f"灭火机持续无法出动(电池/电量周转不足),剩余 FLP {fire['total_flp']},输出资源缺口。"
                      if snapshot["stall_rounds"] >= 4 else
                      f"方案内灭火机全部失能且无备用机可补,剩余 FLP {fire['total_flp']},输出资源缺口。")
        situation = anomalies[-1]["detail"] if anomalies else "任务无法推进"
    else:
        conclusion = ""
        situation = "; ".join(a["detail"] for a in anomalies[:2]) or \
            f"第 {snapshot['round_index']} 轮: B={fire['total_flp']} FLP, 本轮压制 {fire['last_round_suppression_flp']}, 方案在预期轨道上"
    if trapped_under_guidance:
        situation += "; 人群被困但 S1 引导避险中, 继续压制开路"
    if decision == "replan":
        chosen, rationale = "暂停执行, 基于实时状态重新生成方案", (
            f"保守降级研判: 命中异常观测 [{', '.join(a['name'] for a in anomalies)}]"
            + ("; 冷却期内降级为盯防" if cooled and top != "critical" else ""))
    elif decision == "terminate":
        chosen, rationale = "终止任务并输出资源缺口", "继续执行只会耗尽资源, 不给虚假完成时间"
    else:
        chosen = "继续当前方案"
        rationale = ("无异常观测" if not anomalies else
                     f"存在观测 {[' '.join(a['name'] for a in anomalies)]} 但未达调整门槛或处于冷却期") \
                   + ", 下轮继续研判"
    return {
        "situation": situation, "severity": top,
        "evidence": [a["detail"] for a in anomalies] or
                    [f"B {fire['last_round_before_flp']}→{fire['total_flp']} FLP",
                     f"风速 {fire['wind_speed']} m/s({fire['wind_band_label']})"],
        "options": [{"action": "继续当前方案", "pros": "保持节奏", "cons": "若偏差是趋势则损失窗口", "risk": "低"},
                    {"action": "重规划", "pros": "吸收突变", "cons": "周转与审批耗时", "risk": "低"}],
        "chosen": chosen, "rationale": rationale,
        "expected": "下一轮继续研判" if decision == "continue" else "重规划/结案后按新状态推进",
        "fallback": "若下轮同类异常持续或升级, 提高级别处置",
        "decision": decision, "escalate": top == "critical" or trapped_under_guidance,
        "conclusion": conclusion, "source": "conservative-fallback",
    }


# ------------------------------------------------ 研判入口: LLM 优先, 降级保底

async def judge(snapshot: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """执行一轮自主研判。返回 (结构化判断, 工具调用轨迹); 永不抛异常、永不返回 None。"""
    brief = json.dumps(snapshot, ensure_ascii=False)
    try:
        text, trace = await _brain.run(
            "对本轮快照做自主研判, 按协议只输出 JSON。", brief,
            max_tool_rounds=2, max_tokens=800, temperature=0.2, timeout=15.0)
    except Exception:  # noqa: BLE001  # 大脑任何异常都不许打断任务图
        text, trace = None, []
    parsed = parse_judgment(text)
    normalized = normalize_judgment(parsed, "glm") if parsed else None
    if normalized is not None:
        return normalized, trace
    return conservative_judgment(snapshot), []
