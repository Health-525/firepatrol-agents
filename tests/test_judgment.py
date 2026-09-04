"""自主研判模块单测: 协议解析 / 规范化 / 保守降级 / 经验库检索(全部离线, 不依赖 GLM)。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["FIREOPS_LLM_API_KEY"] = ""  # 离线: judge() 必须走保守降级且不抛异常
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.agents import judgment as J  # noqa: E402


# ---------------------------------------------------------------- 协议解析

def test_parse_judgment_plain_json():
    text = '{"situation": "ok", "severity": "info", "decision": "continue"}'
    parsed = J.parse_judgment(text)
    assert parsed is not None and parsed["decision"] == "continue"


def test_parse_judgment_fenced_and_prose():
    text = '研判如下:\n```json\n{"situation": "风变", "severity": "urgent", "decision": "replan"}\n```\n以上。'
    parsed = J.parse_judgment(text)
    assert parsed is not None and parsed["severity"] == "urgent"


def test_parse_judgment_invalid_returns_none():
    assert J.parse_judgment("没有 JSON 的自由文本") is None
    assert J.parse_judgment("") is None
    assert J.parse_judgment("{broken json") is None


# ---------------------------------------------------------------- 规范化

def test_normalize_coerces_decision_from_severity():
    out = J.normalize_judgment({"situation": "x", "severity": "urgent"}, "glm")
    assert out is not None and out["decision"] == "replan" and out["source"] == "glm"
    out = J.normalize_judgment({"situation": "x", "severity": "watch"}, "glm")
    assert out["decision"] == "continue"


def test_normalize_rejects_bad_severity():
    assert J.normalize_judgment({"severity": "灾难级"}, "glm") is None
    assert J.normalize_judgment({}, "glm") is None


# ---------------------------------------------------------------- 快照与降级研判

def _snapshot(**over):
    base = {
        "round_index": 4, "sim_minutes": 20.0, "replans": 0, "stall_rounds": 0,
        "rounds_since_replan": 99,
        "plan": {"plan_id": "plan-0001-v1", "module": "water_20l", "feasibility": "can_control",
                 "time_interval": "35–45 分钟", "tasked_uavs": ["E1", "E2"]},
        "fire": {"total_flp": 80.0, "last_round_before_flp": 95.0, "last_round_growth_flp": 0.5,
                 "last_round_suppression_flp": 15.5, "growth_flp_per_hour": 6.0,
                 "wind_speed": 5.2, "wind_band_label": "4–6 m/s",
                 "wind_band_jump_this_round": False, "net_rising_rounds": 0},
        "tasked_fleet": [{"uav_id": "E1", "status": "working", "soc": 60.0, "agent_remaining": 0.0, "sorties": 2},
                         {"uav_id": "E2", "status": "working", "soc": 65.0, "agent_remaining": 20.0, "sorties": 2}],
        "spare_fleet": [{"uav_id": "E3", "status": "available", "soc": 88.0},
                        {"uav_id": "E4", "status": "charging", "soc": 46.0}],
        "inventory": {"water_liters": 500, "water_modules_w20": 20, "co2_modules_c6": 4,
                      "battery_packs": 20, "fsp_battery_packs": 2},
        "people": {"status": "confirmed", "evac_progress_cells": 2.0, "evacuated": False, "trapped": False},
    }
    base.update(over)
    return base


def test_fallback_healthy_round_continues():
    verdict = J.conservative_judgment(_snapshot())
    assert verdict["decision"] == "continue" and verdict["source"] == "conservative-fallback"
    assert verdict["severity"] == "info"


def test_fallback_wind_band_jump_replans():
    fire = dict(_snapshot()["fire"], wind_band_jump_this_round=True, wind_speed=6.8,
                wind_band_label="6–8 m/s")
    verdict = J.conservative_judgment(_snapshot(fire=fire))
    assert verdict["decision"] == "replan" and verdict["severity"] == "urgent"
    assert any("风档跳变" in e for e in verdict["evidence"])


def test_fallback_rising_fire_trend_replans():
    fire = dict(_snapshot()["fire"], net_rising_rounds=3)
    verdict = J.conservative_judgment(_snapshot(fire=fire))
    assert verdict["decision"] == "replan"
    assert any("净增长" in e for e in verdict["evidence"])


def test_fallback_tasked_fault_with_spare_replans():
    fleet = [dict(u, status="fault") for u in _snapshot()["tasked_fleet"]]
    verdict = J.conservative_judgment(_snapshot(tasked_fleet=fleet))
    assert verdict["decision"] == "replan"  # 还有备用机 → 重规划补位, 不等火涨


def test_fallback_tasked_fault_no_spare_terminates():
    fleet = [dict(u, status="fault") for u in _snapshot()["tasked_fleet"]]
    verdict = J.conservative_judgment(_snapshot(tasked_fleet=fleet, spare_fleet=[]))
    assert verdict["decision"] == "terminate" and "无备用机可补" in verdict["conclusion"]


def test_fallback_stall_terminates_with_gap():
    verdict = J.conservative_judgment(_snapshot(stall_rounds=4))
    assert verdict["decision"] == "terminate"
    assert "资源缺口" in verdict["conclusion"] and "剩余 FLP" in verdict["conclusion"]


def test_fallback_supply_out_replans():
    verdict = J.conservative_judgment(_snapshot(inventory={"water_liters": 0, "water_modules_w20": 0,
                                                           "co2_modules_c6": 4, "battery_packs": 5,
                                                           "fsp_battery_packs": 0}))
    assert verdict["decision"] == "replan"


def test_fallback_cooldown_blocks_urgent_but_not_critical():
    fire = dict(_snapshot()["fire"], wind_band_jump_this_round=True, wind_speed=6.8)
    urgent = J.conservative_judgment(_snapshot(fire=fire, rounds_since_replan=1))
    assert urgent["decision"] == "continue"  # 冷却期内非 critical 不再重规划
    critical = J.conservative_judgment(_snapshot(fire=fire, rounds_since_replan=1,
                                                 people={"status": "confirmed", "evac_progress_cells": 0,
                                                         "evacuated": False, "trapped": True}))
    assert critical["decision"] == "replan" and critical["severity"] == "critical" and critical["escalate"]


def test_consecutive_rising_ignores_turnaround_rounds():
    flying_rise = {"suppression_flp": 11}
    rounds = [{"before_flp": 100, "after_flp": 110, "suppression_flp": 10},   # 早前: 计数被后面打断
              {"before_flp": 110, "after_flp": 111, "suppression_flp": 0},   # 周转轮回升: 不计
              {"before_flp": 111, "after_flp": 118, "suppression_flp": 12},  # 压制中上涨: 计
              dict(flying_rise, before_flp=118, after_flp=122)]              # 压制中上涨: 计
    assert J._consecutive_rising(rounds) == 2
    turnaround_only = [{"before_flp": 100, "after_flp": 101, "suppression_flp": 0}] * 3
    assert J._consecutive_rising(turnaround_only) == 0


def test_fallback_trapped_under_guidance_continues_but_escalates():
    people = {"status": "confirmed", "support_branch": "people", "evac_progress_cells": 0,
              "evacuated": False, "trapped": True}
    verdict = J.conservative_judgment(_snapshot(people=people))
    assert verdict["decision"] == "continue" and verdict["escalate"]  # S1 引导中: 上报人工, 不打断处置
    assert "S1 引导避险中" in verdict["situation"]


def test_fallback_trapped_without_guidance_replans():
    people = {"status": "confirmed", "support_branch": "logistics", "evac_progress_cells": 0,
              "evacuated": False, "trapped": True}
    verdict = J.conservative_judgment(_snapshot(people=people))
    assert verdict["decision"] == "replan" and verdict["severity"] == "critical"


def test_judge_offline_never_raises_and_degrades():
    import asyncio
    verdict, trace = asyncio.run(J.judge(_snapshot()))
    assert verdict["source"] == "conservative-fallback" and trace == []


# ---------------------------------------------------------------- 经验库检索

def test_knowledge_includes_experience_source():
    from backend.app.rules.knowledge import knowledge_stats, query_knowledge
    assert "experience" in knowledge_stats()["chunks_by_source"]
    result = query_knowledge("未知险情 单机异常 处置", top_k=3)
    assert result["ok"] and any(r["source"] == "experience" for r in result["results"])
