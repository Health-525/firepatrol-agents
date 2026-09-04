"""迭代改进的单元测试: 数字审计 / 爬升耗电 / LLM 战略解析 / 环境观测流 / 一轮一事口径。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["FIREOPS_LLM_API_KEY"] = ""  # 测试禁用 LLM
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.agentkit.llm import audit_numbers  # noqa: E402
from backend.app.agents.suppression import SuppressionAgent  # noqa: E402
from backend.app.domain import scenarios as scen  # noqa: E402
from backend.app.rules import tools as R  # noqa: E402
from backend.app.rules.environment import observe_wind  # noqa: E402


def test_audit_numbers():
    brief = "B=108.0 FLP, 风 5.2 m/s, 3 架"
    ok = audit_numbers("当前 B=108.0,建议出动 3 架,风速 5.2", brief)
    bad = audit_numbers("当前 B=1080,预计 9 分钟完成,风速 5.2", brief)
    assert ok == []
    assert "1080" in bad and "9" in bad


def test_climb_adjusted_rate():
    # 基地(260,240)≈106m → 火场(1450,950)≈195m, 高差>30m → 速率上浮 10%
    assert R.climb_adjusted_rate(270.0, 260, 240, 1450, 950) == 297.0
    # 平地航段不修正
    assert R.climb_adjusted_rate(270.0, 260, 240, 300, 260) == 270.0


def test_parse_sizes():
    parse = SuppressionAgent._parse_sizes
    assert parse("2-3", 4) == [2, 3]
    assert parse("出动规模 3 架", 4) == [3]
    assert parse("1~2", 4) == [1, 2]
    assert parse("4-2", 4) == [2, 3, 4]
    assert parse("9", 4) == [4]      # 钳制到上限
    assert parse("无法决定", 4) == []  # 回退全枚举


def test_observe_wind_series_and_jitter():
    cfg = scen.SCENARIOS["wind_shift"]
    observed, jump = observe_wind(cfg, 3, 5.2)
    assert observed == 6.8 and jump is True          # 观测序列第 3 轮跳档
    observed, jump = observe_wind(cfg, 4, 6.8)
    assert observed == 7.0 and jump is False         # 6–8 同档不重复触发
    # 无剧本场景: 只有确定性微抖, 不跳档
    plain = {"wind_speed": 5.2}
    for round_index in range(1, 10):
        observed, jump = observe_wind(plain, round_index, 5.2)
        assert jump is False and 4.9 <= observed <= 5.6


def test_fast_sim_one_action_per_round():
    """一轮一事口径: 单机串行下限 = 其(架次+服务)次数(补给/换电轮不架次), 且墙钟按 ~20min 周期估计。"""
    fleet = {u["uav_id"]: u for u in R.load_json("data/fleet.json")["uavs"]}
    inv = R.load_json("data/inventory.json")
    sim = R.fast_simulate_candidate([fleet["E1"], fleet["E2"]], 108.0, 6.0, "water_20l", "vegetation", 5.2,
                                    inv, {"x": 1450, "y": 950}, {"x": 260, "y": 240})
    assert sim["controlled"] is True
    per_uav_serial = max(u["sorties"] + u["refills"] + u["swaps"] for u in sim["per_uav"])
    max_sorties = max(u["sorties"] for u in sim["per_uav"])
    assert sim["rounds_used"] >= per_uav_serial        # 多机并行, 但单机自身必须一轮一事
    assert sim["control_minutes"] > max_sorties * 11   # 墙钟含补给/换电周转, 大于纯飞行喷洒时间
