"""补位决策 —— 单机失能后「派谁顶替」由 Agent 大脑决定(行动权, 不是旁白)。

与重规划的分界: 补位是方案内的一次资源替换——同药剂、同目标、同编成规模, 只换机,
不经过审批门; 重规划是方案前提被打破后的重新组织。失能 → 补位 → 每轮研判三层递进:
补位解决"接下来谁飞", 研判裁决"方案整体还成不成立"。

测量归工具: 候选备用机的 SOC / 药剂 / 出动门槛全部由规则引擎算好再交给大脑
(ready_now = SOC 够一趟架次+返航储备; ready_after_service = 基地换电/补给一轮后可出动),
大脑只做选择与给理由。GLM 不可用/输出非法 → 确定性降级: 优先 ready_now 中 SOC 最高,
否则 ready_after_service 中 SOC 最高; 两类皆空 → none, 交由每轮研判重规划或降级。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from ..agentkit.brain import AgentBrain
from ..agentkit.llm import SAFETY_RULE
from .judgment import parse_judgment

BACKFILL_PROMPT = f"""{SAFETY_RULE}
你是「灭火调度」Agent 的战术大脑: 一架执行中的灭火机机电失能, 立刻决定派谁顶替。

候选分两类(门槛已由规则引擎算好, 不要自行计算):
- ready_now: SOC 够「一趟架次+返航储备」, 下一轮即可出动;
- ready_after_service: 需先在基地换电/补药剂(一轮周转, 库存已确认足够), 随后可出动。

选择依据(按序): 压制不中断(ready_now 优先) > SOC 余量 > 药剂余量。
若补位只会掏空库存、或两类候选都为空, 选 none——宁缺毋滥, 交给每轮自主研判去重规划/降级。

只输出一个 JSON 对象(不要代码块、不要多余文字):
{{"choice": "备用机ID 或 none", "rationale": "60字内理由"}}"""

_brain = AgentBrain(BACKFILL_PROMPT, {})


def rule_fallback(candidates: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """确定性降级: ready_now 取 SOC 最高; 其次 ready_after_service; 皆空 → none。"""
    ready = candidates.get("ready_now") or []
    service = candidates.get("ready_after_service") or []
    if ready:
        best = max(ready, key=lambda c: c["soc"])
        return {"choice": best["uav_id"], "source": "rule-fallback",
                "rationale": f"确定性降级: 立即可用候选中 {best['uav_id']} SOC 最高"
                             f"({best['soc']:.0f}%), 压制不中断"}
    if service:
        best = max(service, key=lambda c: c["soc"])
        return {"choice": best["uav_id"], "source": "rule-fallback",
                "rationale": f"确定性降级: 无立即可用候选, {best['uav_id']}(SOC {best['soc']:.0f}%)"
                             "一轮换电/补给后顶替"}
    return {"choice": "none", "source": "rule-fallback",
            "rationale": "无满足出动门槛的备用机, 交由自主研判决定重规划或降级目标"}


async def decide(faulted: List[str], candidates: Dict[str, List[Dict[str, Any]]],
                 context: Dict[str, Any]) -> Dict[str, Any]:
    """补位决策入口: GLM 选择 + 理由; 任何失败回落确定性规则。永不抛异常。"""
    decision = rule_fallback(candidates)
    if decision["choice"] == "none":
        return decision
    valid = {c["uav_id"] for group in candidates.values() for c in group}
    brief = json.dumps({"faulted": faulted, "candidates": candidates, "context": context},
                       ensure_ascii=False)
    try:
        text, _trace = await _brain.run(
            f"灭火机 {'/'.join(faulted)} 失能, 决定补位机, 按协议只输出 JSON。", brief,
            max_tool_rounds=0, max_tokens=120, temperature=0.2, timeout=10.0)
        parsed = parse_judgment(text)
        if parsed:
            choice = str(parsed.get("choice", "")).strip()
            if choice in valid | {"none"}:
                return {"choice": choice, "source": "glm",
                        "rationale": str(parsed.get("rationale") or "")[:120]}
    except Exception:  # noqa: BLE001  # 补位大脑任何异常都不许打断执行轮
        pass
    return decision
