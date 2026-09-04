"""⑥ 交互审批 Agent —— 面向用户: 方案解释(数字来源标注)、审批中断、报告归档。"""
from __future__ import annotations

from typing import Any, Dict

from langgraph.types import interrupt

from ..agentkit.base import BaseAgent
from ..domain.store import BOARD
from ..rules import tools as R
from ..rules.knowledge import query_knowledge


class ApproverAgent(BaseAgent):
    agent_id = "approver"
    name = "交互审批"
    role = "方案解释 · 审批门(human-in-the-loop) · 报告归档"
    subgroup = "system"
    color = "#ec4899"
    emoji = "📝"
    tools = {"query_knowledge": query_knowledge, "score_plan": R.score_plan}

    async def prepare(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        best = state["best_candidate"]
        candidates = state["candidates"]
        fire = state["fire"]
        support = state.get("support_plan", {})
        ranked = sorted([c for c in candidates if c.get("feasible")], key=lambda c: c["score"]["score"])
        alternative = ranked[1] if len(ranked) > 1 else None

        feasibility_label = {"can_control": "当前资源能够控制", "maintain_only": "只能维持,难以缩小火情",
                             "cannot_control": "当前资源无法控制"}.get(best.get("feasibility", "cannot_control"))
        request = {
            "plan_summary": {
                "plan_id": best["candidate_id"],
                "suppression_uavs": best.get("suppression_uavs", []),
                "module": best.get("module"),
                "per_sortie_flp": best.get("per_sortie_flp"),
                "time_interval": best.get("time_interval"),
                "feasibility": best.get("feasibility"), "feasibility_label": feasibility_label,
                "score": best.get("score", {}).get("score"),
                "resource_gap": best.get("gap", {}),
                "support_branch": support.get("branch"), "support": support.get("support", []),
                "recon": support.get("recon", []),
            },
            "alternative": {"plan_id": alternative["candidate_id"], "score": alternative["score"]["score"],
                            "time_interval": alternative.get("time_interval"),
                            "suppression_uavs": alternative.get("suppression_uavs")} if alternative else None,
            "key_numbers": [
                {"name": "总火情负荷 B", "value": f"{fire['total_flp']} FLP", "source": "规则引擎 build_fire_grid"},
                {"name": "单架次有效能力 S", "value": f"{best.get('per_sortie_flp')} FLP", "source": "规则引擎 suppression_capability"},
                {"name": "预计完成时间", "value": best.get("time_interval", "无法给出"), "source": "离散轮次仿真"},
                {"name": "评分 J(越小越优)", "value": best.get("score", {}).get("score"), "source": "规则引擎 score_plan"},
            ],
            "people_note": ("人员状态尚未确认:批准前请在审批操作中确认是否有人,或直接批准按「待复核」分支执行。"
                            if fire["people_status"] == "unknown" else None),
        }
        # 审批中断恢复时节点会重跑: 已发布的同号请求不重复发送(含 GLM 调用)
        existing = BOARD.require(task_id).get("approval_request")
        if not existing or existing.get("plan_summary", {}).get("plan_id") != best["candidate_id"]:
            BOARD.update(task_id, phase="awaiting_approval", approval_request=request)
            self.say(task_id, "APPROVAL_REQ", "human",
                     f"方案已生成,等待审批。最优 {best['candidate_id']}:灭火机 {len(best.get('suppression_uavs', []))} 架 + "
                     f"{best.get('module')};{feasibility_label};预计 {best.get('time_interval', '无法给出')}。"
                     f"生成方案 ≠ 执行,确认后才会锁定资源。", request)
            numbers = "; ".join(f"{k['name']}={k['value']}" for k in request["key_numbers"])
            advice, trace = await self.think(
                "给出审批建议: 推荐批准/调整/拒绝中的哪个, 最关键的理由与风险提示(数字必须来自下方数据)",
                f"最优方案 {best['candidate_id']}({','.join(best.get('suppression_uavs', []))},"
                f"评分J={best.get('score', {}).get('score')},{best.get('time_interval', '无法给出')},"
                f"{feasibility_label});支援分支 {support.get('branch')};关键数字: {numbers};"
                f"备选: {request['alternative']};资源缺口: {best.get('gap', {}).get('message', '无')}", max_tokens=260)
            if advice:
                self.say_llm(task_id, "APPROVAL_REQ", "human", f"审批建议:{advice}", trace)

        decision = interrupt(request)  # ---- LangGraph 审批中断, 等待 human ----

        BOARD.update(task_id, phase="analyzing", approval_request=None)  # 立即离开待审批态, 避免读到旧请求
        self.say(task_id, "APPROVAL_DECISION", "commander",
                 f"用户决策:{decision.get('decision')}" + (f"(意见:{decision.get('feedback')})" if decision.get("feedback") else ""),
                 decision)
        return {"approval": decision}

    # ------------------------------------------------ 报告归档

    async def report(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        rounds = state.get("rounds", [])
        fleet = state.get("fleet", [])
        first = rounds[0] if rounds else {}
        last = rounds[-1] if rounds else {}
        flp0 = first.get("before_flp", 0)
        flp1 = last.get("after_flp", 0)
        material = sum(u.get("sorties", 0) for u in fleet if u["subgroup"] == "suppression") * R.sim_config()["spray"]["water_20l"]["quantity"]
        conclusion = state.get("conclusion") or ("任务完成。" if flp1 <= 0.01 else "任务终止。")
        report = {
            "report_id": f"RPT-{task_id[-6:].upper()}", "task_id": task_id, "conclusion": conclusion,
            "rounds_total": len(rounds), "flp_initial": flp0, "flp_final": flp1,
            "material_used": round(material, 0),
            "swaps": sum(u.get("swaps", 0) for u in fleet), "refills": sum(u.get("refills", 0) for u in fleet),
            "replans": state.get("replans", 0),
            "timeline": [{"round": r["round_index"], "t_min": r["sim_minutes"], "flp": r["after_flp"],
                          "events": r["events"]} for r in rounds],
        }
        final_phase = "rejected" if state.get("route") == "rejected" else "completed"
        BOARD.update(task_id, phase=final_phase, report=report)
        self.say(task_id, "REPORT_READY", "human",
                 f"任务归档:{conclusion} 轮次 {len(rounds)},FLP {flp0}→{flp1},"
                 f"换电 {report['swaps']} 次、补水 {report['refills']} 次、重规划 {report['replans']} 次。", report)
        closing, trace = await self.think(
            "结案复盘: 任务结果评价、资源使用效率、下次可改进点(只引用给定数字)",
            f"结论: {conclusion};轮次 {len(rounds)};FLP {flp0}→{flp1};换电 {report['swaps']},"
            f"补水 {report['refills']},重规划 {report['replans']}", max_tokens=220)
        if closing:
            self.say_llm(task_id, "REPORT_READY", "human", f"结案研判:{closing}", trace)
        return {"report": report, "phase": final_phase}
