"""端到端契约测试: 规则引擎数值 + 多 Agent 任务闭环(审批/重规划/资源缺口)。

测试强制关闭 GLM(离线确定性), 保证不依赖网络也能全绿。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ["FIREOPS_LLM_API_KEY"] = ""  # 测试环境禁用 LLM, 保持确定性
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.domain.store import BOARD  # noqa: E402
from backend.app.rules import tools as R  # noqa: E402
from backend.app.services.mission import SERVICE  # noqa: E402


# ---------------------------------------------------------------- 规则引擎

def test_flp_grid_math():
    # 4 格 I=2 + 1 格 I=1, fuel=1.0, wind 5.2(band1 k=1.2), slope 12(k=1.0)
    cells = [{"cx": 14, "cy": 9, "intensity": 2}, {"cx": 15, "cy": 9, "intensity": 2},
             {"cx": 14, "cy": 10, "intensity": 2}, {"cx": 15, "cy": 10, "intensity": 1},
             {"cx": 13, "cy": 9, "intensity": 2}]
    grid = R.build_fire_grid(cells, "general_forest", 5.2, 12)
    assert grid["total_flp"] == 108.0  # 4*24 + 12
    assert grid["wind_band"]["band"] == 1


def test_kappa_compatibility():
    assert R.agent_kappa("water_20l", "vegetation") == (1.0, True)
    assert R.agent_kappa("co2_6kg", "electrical") == (1.5, True)
    assert R.agent_kappa("water_20l", "electrical") == (0.0, False)  # 电气火禁水


def test_soc_model():
    assert R.delta_soc(270, 5) == 22.5          # 满载悬停喷洒 5 min
    assert R.soc_need(10, 22.5, 10, 25) == 67.5  # 返航储备计入
    assert R.flight_minutes(1500, 8) == 3.125     # 1.5 km @ 8 m/s


def test_score_lower_is_better():
    fast = R.score_plan(20, 0, 40, 40, 2, 20, 1)      # (时间, 剩余, B总, 耗电, 架数, 药剂, 变更)
    slow = R.score_plan(90, 30, 40, 80, 4, 120, 3)
    assert fast["score"] < slow["score"]


# ---------------------------------------------------------------- 任务闭环

async def _wait_phase(task_id: str, phases: set[str], timeout: float = 30.0) -> dict:
    loop = asyncio.get_event_loop()
    await asyncio.sleep(0.5)  # 让后台 runner 先推进, 避免读到上一个阶段的残留快照
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        snapshot = BOARD.snapshot(task_id)
        if snapshot["phase"] in phases:
            return snapshot
        await asyncio.sleep(0.15)
    raise AssertionError(f"等待阶段 {phases} 超时, 当前 {BOARD.snapshot(task_id)['phase']}, 消息尾部: "
                         f"{[m['content'][:60] for m in BOARD.snapshot(task_id)['messages'][-3:]]}")


def test_mission_standard_closed_loop():
    async def run():
        task_id = await SERVICE.start("standard")
        snap = await _wait_phase(task_id, {"awaiting_approval"})
        assert snap["approval_request"] is not None
        assert snap["fire"]["total_flp"] == 108.0
        request = snap["approval_request"]["plan_summary"]
        assert request["feasibility"] == "can_control"
        assert "灭火" in str(request) or request["suppression_uavs"]
        await SERVICE.approve(task_id, "approve")
        done = await _wait_phase(task_id, {"completed"}, timeout=90)
        assert done["report"]["flp_final"] <= 0.01
        assert len(done["rounds"]) >= 2
        assert done["report"]["rounds_total"] == len(done["rounds"])
    asyncio.run(run())


def test_mission_wind_shift_triggers_replan():
    async def run():
        task_id = await SERVICE.start("wind_shift")
        await _wait_phase(task_id, {"awaiting_approval"})
        await SERVICE.approve(task_id, "approve")
        # 第 3 轮风档跳变 → 重规划 → 再次等待审批
        snap = await _wait_phase(task_id, {"awaiting_approval", "completed"}, timeout=90)
        assert snap["replans"] == 1, f"期望触发一次重规划, 实际 {snap['replans']}"
        assert snap["phase"] == "awaiting_approval"
        await SERVICE.approve(task_id, "approve")
        done = await _wait_phase(task_id, {"completed"}, timeout=90)
        assert done["report"]["flp_final"] <= 0.01
        # Agent 协作流完整: 派单/研判/方案/仿真/审批/轮次/重规划/报告
        types = {m["msg_type"] for m in done["messages"]}
        assert {"TASK_ASSIGN", "FINDING", "PLAN_PROPOSAL", "SIM_RESULT", "APPROVAL_REQ",
                "APPROVAL_DECISION", "ROUND", "REPLAN_TRIGGER", "REPORT_READY"} <= types
    asyncio.run(run())


def test_mission_overwhelmed_reports_gap():
    async def run():
        task_id = await SERVICE.start("overwhelmed")
        snap = await _wait_phase(task_id, {"awaiting_approval"})
        verdicts = [c.get("feasibility") for c in snap["candidates"]]
        assert "can_control" not in verdicts  # 重大火情: 不给虚假完成承诺
        await SERVICE.approve(task_id, "reject")
        done = await _wait_phase(task_id, {"completed", "rejected"})
        assert done["phase"] == "rejected"
    asyncio.run(run())


def test_mission_adjust_with_constraint():
    async def run():
        task_id = await SERVICE.start("standard")
        await _wait_phase(task_id, {"awaiting_approval"})
        await SERVICE.approve(task_id, "adjust", feedback="最多出动 1 架")
        snap = await _wait_phase(task_id, {"awaiting_approval"})
        assert len(snap["approval_request"]["plan_summary"]["suppression_uavs"]) == 1
        await SERVICE.approve(task_id, "approve")
        done = await _wait_phase(task_id, {"completed"}, timeout=120)
        assert done["report"]["flp_final"] <= 0.01
    asyncio.run(run())


# ---------------------------------------------------------------- 结案回收 & 自主补位

def test_mission_end_fleet_returns_home():
    """结案后机队必须全员返航归位: 位置=基地, 无"原地悬停"。"""
    async def run():
        task_id = await SERVICE.start("standard")
        await _wait_phase(task_id, {"awaiting_approval"})
        await SERVICE.approve(task_id, "approve")
        done = await _wait_phase(task_id, {"completed"}, timeout=120)
        base = done["environment"]["base"]
        away = [u["uav_id"] for u in done["fleet"]
                if abs(u["position"]["x"] - base["x"]) + abs(u["position"]["y"] - base["y"]) > 1.0]
        assert not away, f"结案后仍在外的机: {away}"
        assert {m["msg_type"] for m in done["messages"]} >= {"RECOVERY"}
        assert all(u["status"] in {"available", "charging", "fault"} for u in done["fleet"])
    asyncio.run(run())


def test_rejected_mission_also_recovers_and_stays_rejected():
    """拒绝结案的机队同样返航, 且终局 phase 仍是 rejected(不因回收改写)。"""
    async def run():
        task_id = await SERVICE.start("overwhelmed")
        await _wait_phase(task_id, {"awaiting_approval"})
        await SERVICE.approve(task_id, "reject")
        done = await _wait_phase(task_id, {"rejected"}, timeout=60)
        base = done["environment"]["base"]
        away = [u["uav_id"] for u in done["fleet"]
                if abs(u["position"]["x"] - base["x"]) + abs(u["position"]["y"] - base["y"]) > 1.0]
        assert not away, f"拒绝结案后仍在外的机: {away}"
    asyncio.run(run())


def test_backfill_rule_fallback():
    """GLM 离线时补位降级: ready_now 取 SOC 最高; 两类皆空 → none。"""
    from backend.app.agents import backfill

    candidates = {
        "ready_now": [{"uav_id": "E3", "soc": 88, "sortie_soc": 46.2},
                      {"uav_id": "E2", "soc": 100, "sortie_soc": 46.2}],
        "ready_after_service": [{"uav_id": "E4", "soc": 71, "sortie_soc": 46.2}],
    }
    decision = asyncio.run(backfill.decide(["E1"], candidates, {"fire_total_flp": 100}))
    assert decision["choice"] == "E2" and decision["source"] == "rule-fallback"
    only_service = {"ready_now": [], "ready_after_service": candidates["ready_after_service"]}
    decision = asyncio.run(backfill.decide(["E1"], only_service, {}))
    assert decision["choice"] == "E4"
    empty = asyncio.run(backfill.decide(["E1"], {"ready_now": [], "ready_after_service": []}, {}))
    assert empty["choice"] == "none"


def test_mission_equip_failure_backfills_and_completes():
    """单机失能闭环: 第 3 轮失能 → 补位决策(方案内换机) → 不重新审批 → 火仍被扑灭 → 全员返航。"""
    async def run():
        task_id = await SERVICE.start("equip_failure")
        snap = await _wait_phase(task_id, {"awaiting_approval"})
        assert snap["fire"]["total_flp"] == 216.0  # 9 格 x 24 FLP
        await SERVICE.approve(task_id, "approve")
        # 失能→补位后不应再次进入审批门(方案内换机不过审批)
        done = await _wait_phase(task_id, {"completed"}, timeout=180)
        types = {m["msg_type"] for m in done["messages"]}
        assert {"UAV_FAULT", "BACKFILL", "RECOVERY"} <= types
        backfills = [m for m in done["messages"]
                     if m["msg_type"] == "BACKFILL" and m["data"].get("choice") not in (None, "none")]
        assert backfills, "离线降级也应产生确定性补位"
        plan_ids = set(done["plan"]["candidate"]["suppression_uavs"])
        assert backfills[0]["data"]["choice"] in plan_ids
        assert done["report"]["flp_final"] <= 0.01
        base = done["environment"]["base"]
        away = [u["uav_id"] for u in done["fleet"]
                if abs(u["position"]["x"] - base["x"]) + abs(u["position"]["y"] - base["y"]) > 1.0]
        assert not away
    asyncio.run(run())
