"""人员疏散链路测试: A* 寻路(地形+火格) / 人群推进 / 改道。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["FIREOPS_LLM_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.rules.evacuation import advance_people, plan_evacuation  # noqa: E402

SCENE = json.loads((Path(__file__).resolve().parents[1] / "data" / "scene.json").read_text(encoding="utf-8"))
FIRE = [{"cx": 14, "cy": 9, "flp": 30}, {"cx": 15, "cy": 9, "flp": 24}, {"cx": 14, "cy": 10, "flp": 24}]


def test_evacuation_finds_route_around_fire():
    evac = plan_evacuation(SCENE, FIRE)
    assert evac["found"] is True
    assert evac["exit"] == "东侧集结点"
    assert evac["walk_minutes"] > 0
    # 路径不得穿越高强度火格
    for pt in evac["path"]:
        assert not any(c["cx"] == pt["cx"] and c["cy"] == pt["cy"] and c["flp"] > 5 for c in FIRE)
    # 起点是人员区
    assert evac["path"][0]["cx"] == 15 and evac["path"][0]["cy"] == 10


def test_evacuation_prefers_slope_aware_exit():
    # 全堵东侧出口通道 → 应改走北门(证明出口择优生效)
    blockers = [{"cx": c, "cy": 12, "flp": 40} for c in range(10, 20)]
    blockers += [{"cx": c, "cy": 11, "flp": 40} for c in range(10, 20)]
    evac = plan_evacuation(SCENE, FIRE + blockers)
    assert evac["found"] is True
    assert evac["exit"] == "北门出口"


def test_evacuation_blocked_reports_no_path():
    # 火海围死人员区
    ring = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if (dx, dy) != (0, 0):
                ring.append({"cx": 15 + dx, "cy": 10 + dy, "flp": 40})
    evac = plan_evacuation(SCONE := SCENE, FIRE + ring)
    assert evac["found"] is False
    assert "封锁" in evac["note"] or "引导" in evac["note"]


def test_people_advance_and_arrive():
    evac = plan_evacuation(SCENE, FIRE)
    evac["progress_cells"] = 0
    done_rounds = 0
    for _ in range(10):
        progress, done, here = advance_people(evac, 5)
        evac["progress_cells"] = progress
        done_rounds += 1
        assert here is not None
        if done:
            break
    assert done and done_rounds <= 6  # ~10.4 分钟 ≈ 2-3 轮, 留裕量
