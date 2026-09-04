"""缺陷修复契约测试:

1. 载荷差异耗电: 水剂 22kg 与 CO₂ 14kg 去程巡航速率按载荷修正公式区分;
2. 就地取水: 水源选择算法(先决条件/路线风险/往返 SOC/省时对比) + 预演与执行的补给链;
3. 人员状态确认生效: 批准时确认有人/无人必须重生成支援分支; 调整时人员状态随意见回传;
4. 网格面积口径: 100m×100m = 10000 m²。

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


# ---------------------------------------------------------------- 缺陷3: 载荷差异耗电

def test_loaded_cruise_rate_differentiates_payload():
    e1 = {"uav_id": "E1", "subgroup": "suppression", "payload_capacity_kg": 25}
    water = R.loaded_cruise_rate(e1, 22)   # 水剂模块 22kg
    co2 = R.loaded_cruise_rate(e1, 14)     # CO₂ 模块 14kg
    full = R.loaded_cruise_rate(e1, 25)    # 满载 25kg
    empty = R.sim_config()["energy"]["suppression"]["cruise_empty"]
    # r = r_empty x (1 + 0.45 x m/M): 水更重 -> 耗电更高, 两者不再共用 270%/h 满载速率
    assert empty < co2 < water < full
    assert abs(water - (empty * (1 + 0.45 * 22 / 25))) < 0.1
    assert abs(co2 - (empty * (1 + 0.45 * 14 / 25))) < 0.1
    assert water != R.sim_config()["energy"]["suppression"]["cruise_loaded"]  # 不再是表内一刀切满载值


def test_sortie_check_uses_payload_rate():
    """硬约束检查输出去程速率: 同一架机水剂与 CO₂ 的 soc_out 必须不同。"""
    from backend.app.agents.suppression import SuppressionAgent

    uav = {"uav_id": "E1", "subgroup": "suppression", "status": "available", "soc": 100, "health": 100,
           "payload_capacity_kg": 25, "speed_mps": 8, "position": {"x": 260, "y": 240}}
    fire = {"fire_type": "vegetation"}
    center = {"x": 1390, "y": 990}
    water_check = SuppressionAgent._check(uav, "water_20l", fire, center)
    co2_check = SuppressionAgent._check(uav, "co2_6kg", fire, center)
    assert water_check["rate_out_loaded"] > co2_check["rate_out_loaded"]
    assert water_check["soc_out"] > co2_check["soc_out"]
    assert water_check["payload_mass_kg"] == 22 and co2_check["payload_mass_kg"] == 14


# ---------------------------------------------------------------- 缺陷2: 就地取水

def test_plan_water_source_selects_viable_source():
    """合成几何: 近水源被火格拦路 -> 否决; 绕行水源省时≥5min -> 选中。"""
    uav = {"uav_id": "E1", "subgroup": "suppression", "payload_capacity_kg": 25,
           "speed_mps": 8, "soc": 100}
    fire_pos = {"x": 3000, "y": 0}
    base = {"x": 0, "y": 0, "name": "基地"}
    fire_cells = [{"cx": 27, "cy": 0, "flp": 24}]  # 拦在近水源路径中段
    sources = [
        {"id": "ws-near", "name": "近水源", "x": 2600, "y": 0, "available": True, "safe_access": True,
         "capacity_liters": 800, "fill_minutes": 8},
        {"id": "ws-detour", "name": "绕行水源", "x": 2800, "y": 700, "available": True, "safe_access": True,
         "capacity_liters": 800, "fill_minutes": 8},
    ]
    plan = R.plan_water_source("water_20l", fire_pos, base, sources, uav, fire_cells)
    assert plan["mode"] == "water_source"
    assert plan["source"]["id"] == "ws-detour"
    assert plan["source"]["saving_minutes"] >= 5.0
    near = next(o for o in plan["options"] if o["id"] == "ws-near")
    assert any("路线" in v for v in near["vetoes"])
    base_opt = next(o for o in plan["options"] if o["kind"] == "base")
    assert base_opt["cycle_minutes"] == round(2 * R.flight_minutes(3000, 8) + 4, 1)


def test_plan_water_source_default_scene_keeps_base_with_fallback():
    """默认场景几何: 水源省时不足 5min -> 基地补水, 但合格水源保留为断供回退。"""
    scene = R.load_json("data/scene.json")
    fleet = R.load_json("data/fleet.json")
    e1 = next(u for u in fleet["uavs"] if u["uav_id"] == "E1")
    fire_cells = [{"cx": 14, "cy": 9, "flp": 24}, {"cx": 15, "cy": 9, "flp": 24},
                  {"cx": 14, "cy": 10, "flp": 24}, {"cx": 15, "cy": 10, "flp": 12}, {"cx": 13, "cy": 9, "flp": 24}]
    plan = R.plan_water_source("water_20l", {"x": 1390, "y": 990}, scene["base"],
                               scene["water_sources"], e1, fire_cells)
    assert plan["mode"] == "base"  # 默认几何下水源不省时
    assert plan["fallback_sources"], "合格水源应保留为断供回退"
    stream = next(o for o in plan["options"] if o["id"] == "ws-stream")
    assert any("路线" in v for v in stream["vetoes"])  # 东麓溪流在火场上方, 穿火线不可达


def test_plan_water_source_co2_rejects():
    uav = {"uav_id": "E1", "subgroup": "suppression", "payload_capacity_kg": 25, "speed_mps": 8, "soc": 100}
    plan = R.plan_water_source("co2_6kg", {"x": 1000, "y": 500}, {"x": 0, "y": 0}, [], uav, [])
    assert plan["mode"] == "base" and not plan["fallback_sources"]


def test_fast_sim_water_source_extends_endurance():
    """基地水剂耗尽时, 水源补给链让预演继续给出可行完成时间(而不是虚假缺口)。"""
    uav = [{"uav_id": "E1", "subgroup": "suppression", "position": {"x": 1450, "y": 950},
            "soc": 100, "payload_capacity_kg": 25, "speed_mps": 8, "agent_remaining": 0}]
    inventory = {"water_liters": 0, "water_modules_w20": 0, "battery_packs": 10, "forward_supply_points": []}
    fire_pos = {"x": 1450, "y": 950}
    base = {"x": 260, "y": 240}
    without = R.fast_simulate_candidate(uav, 60, 0, "water_20l", "vegetation", 5.2, inventory, fire_pos, base)
    assert without["controlled"] is False and without["stalled"] == "agent_insufficient"
    ws_plan = {"module": "water_20l", "mode": "water_source",
               "source": {"kind": "source", "id": "ws-test", "name": "测试水源", "x": 1450, "y": 750,
                          "capacity_remaining": 200, "cycle_minutes": 9.0},
               "options": [], "fallback_sources": []}
    with_src = R.fast_simulate_candidate(uav, 60, 0, "water_20l", "vegetation", 5.2, inventory,
                                         fire_pos, base, water_source_plan=ws_plan)
    assert with_src["controlled"] is True
    assert with_src["refills"] >= 3
    assert with_src["control_minutes"] is not None


async def _wait_phase(task_id: str, phases: set[str], timeout: float = 60.0) -> dict:
    loop = asyncio.get_event_loop()
    await asyncio.sleep(0.5)
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        snapshot = BOARD.snapshot(task_id)
        if snapshot["phase"] in phases:
            return snapshot
        await asyncio.sleep(0.15)
    raise AssertionError(f"等待阶段 {phases} 超时, 当前 {BOARD.snapshot(task_id)['phase']}, 消息尾部: "
                         f"{[m['content'][:60] for m in BOARD.snapshot(task_id)['messages'][-3:]]}")


def test_mission_water_source_scenario_executes_local_refill():
    """就地取水闭环: 基地水剂受限场景下, 执行轮真实出现「基地补水→就地取水」补给链且火被扑灭。"""
    async def run():
        task_id = await SERVICE.start("water_source")
        snap = await _wait_phase(task_id, {"awaiting_approval"})
        assert snap["inventory"]["water_liters"] == 40  # 场景库存覆盖生效
        summary = snap["approval_request"]["plan_summary"]
        assert summary["water_source_note"], "审批卡应展示水剂补给策略"
        await SERVICE.approve(task_id, "approve")
        done = None
        for _ in range(4):  # 中途研判可能触发重规划再审批, 逐次放行
            snap = await _wait_phase(task_id, {"awaiting_approval", "completed", "rejected"}, timeout=150)
            if snap["phase"] != "awaiting_approval":
                done = snap
                break
            await SERVICE.approve(task_id, "approve")
        assert done and done["phase"] == "completed", f"任务未完成: {done and done['phase']}"
        assert done["report"]["flp_final"] <= 0.01
        events = [e for r in done["rounds"] for e in r["events"]]
        assert any("基地补水" in e for e in events), "应先用完受限的基地水剂"
        assert any("就地取水" in e for e in events), "基地断供后应回退到水源就地取水"
        ws = done["plan"]["water_source_plan"]
        drawn = next((o for o in [ws.get("source")] + ws.get("fallback_sources", [])
                                if o and o.get("id") == "ws-stream"), None)
        assert drawn and drawn["capacity_remaining"] < 800, "取水量必须实时扣减水源容量"
    asyncio.run(run())


# ---------------------------------------------------------------- 缺陷1: 人员状态确认生效

def test_mission_people_status_override_on_approve():
    """批准时把「无人」改为「确认有人」: 支援分支必须重生成(有人分支+疏散广播), 不再沿用物流分支。"""
    async def run():
        task_id = await SERVICE.start("no_people")
        snap = await _wait_phase(task_id, {"awaiting_approval"})
        assert snap["support_plan"]["branch"] == "logistics"  # 初判无人 -> 物流分支
        await SERVICE.approve(task_id, "approve", people_status="confirmed")
        snap2 = await _wait_phase(task_id, {"executing"})
        assert snap2["fire"]["people_status"] == "confirmed"
        assert snap2["support_plan"]["branch"] == "people", "批准确认有人后支援分支必须切换"
        assert "evacuation" in snap2["support_plan"], "有人分支必须生成疏散方案"
        types = {m["msg_type"] for m in snap2["messages"]}
        assert "EVAC_BROADCAST" in types
        # 锁定的方案使用新分支编成(通信中继/照明复核), 而不是旧的物流任务
        tasks = {a.get("task") for a in snap2["support_plan"]["support"]}
        assert any("通信" in t or "广播" in t for t in tasks)
        done = await _wait_phase(task_id, {"completed"}, timeout=150)
        assert done["report"]["flp_final"] <= 0.01
    asyncio.run(run())


def test_mission_people_status_via_adjust():
    """调整路径: 人员状态随调整意见回传, 重规划后的方案与支援分支按新状态生成。"""
    async def run():
        task_id = await SERVICE.start("no_people")
        await _wait_phase(task_id, {"awaiting_approval"})
        await SERVICE.approve(task_id, "adjust", feedback="最多出动 2 架", people_status="confirmed")
        snap = await _wait_phase(task_id, {"awaiting_approval"})
        assert snap["fire"]["people_status"] == "confirmed"
        assert snap["support_plan"]["branch"] == "people"
        assert len(snap["approval_request"]["plan_summary"]["suppression_uavs"]) <= 2
        assert snap["approval_request"]["people_note"] is None  # 已确认, 不再提示待复核
        await SERVICE.approve(task_id, "approve")
        done = await _wait_phase(task_id, {"completed"}, timeout=150)
        assert done["support_plan"]["branch"] == "people"
        assert done["report"]["flp_final"] <= 0.01
    asyncio.run(run())


# ---------------------------------------------------------------- 缺陷4: 网格面积口径

def test_grid_cell_area_unit():
    cfg = R.sim_config()
    assert cfg["grid"]["cell_m"] == 100
    assert cfg["grid"]["cell_m2"] == cfg["grid"]["cell_m"] ** 2  # 100m x 100m = 10000 m²
    assert cfg["grid"]["cell_m2"] == 10000
