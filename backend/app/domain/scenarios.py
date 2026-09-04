"""演示场景预设: 初始火情网格/增长率/人员状态/风况脚本。"""
from __future__ import annotations

from typing import Any, Dict

SCENARIOS: Dict[str, Dict[str, Any]] = {
    # 标准场景: 5 格火情 B0=108 FLP, 有人(东侧露营区) → 支援走有人分支
    "standard": {
        "label": "标准火情 · 有人区域",
        "fire_cells": [{"cx": 14, "cy": 9, "intensity": 2}, {"cx": 15, "cy": 9, "intensity": 2},
                        {"cx": 14, "cy": 10, "intensity": 2}, {"cx": 15, "cy": 10, "intensity": 1},
                        {"cx": 13, "cy": 9, "intensity": 2}],
        "growth_flp_per_hour": 6.0,
        "people_status": "confirmed",
        "wind_speed": 5.2,
        "fire_type": "vegetation",
        "wind_shift": None,
    },
    # 风速突变: 第 3 轮风起 6.8 m/s → 风档跳档 + FLP 抬升 → 触发重规划演示
    "wind_shift": {
        "label": "风速突变 · 重规划演示",
        "fire_cells": [{"cx": 14, "cy": 9, "intensity": 3}, {"cx": 15, "cy": 9, "intensity": 3},
                        {"cx": 14, "cy": 10, "intensity": 3}, {"cx": 15, "cy": 10, "intensity": 3},
                        {"cx": 13, "cy": 9, "intensity": 3}, {"cx": 13, "cy": 10, "intensity": 3}],
        "growth_flp_per_hour": 8.0,
        "people_status": "confirmed",
        "wind_speed": 5.2,
        "fire_type": "vegetation",
        "wind_shift": {"round": 3, "to_mps": 6.8},
    },
    # 无人场景: 支援双机全走物流分支
    "no_people": {
        "label": "无人林区 · 物流支援",
        "fire_cells": [{"cx": 12, "cy": 8, "intensity": 2}, {"cx": 13, "cy": 8, "intensity": 2},
                        {"cx": 12, "cy": 9, "intensity": 1}],
        "growth_flp_per_hour": 5.0,
        "people_status": "absent",
        "wind_speed": 4.2,
        "fire_type": "vegetation",
        "wind_shift": None,
    },
    # 资源不足: 高强度蔓延, 净处置能力为负 → 输出资源缺口, 不给虚假完成时间
    "overwhelmed": {
        "label": "重大火情 · 资源缺口",
        "fire_cells": [{"cx": 13, "cy": 8, "intensity": 4}, {"cx": 14, "cy": 8, "intensity": 4},
                        {"cx": 15, "cy": 8, "intensity": 4}, {"cx": 13, "cy": 9, "intensity": 4},
                        {"cx": 14, "cy": 9, "intensity": 4}, {"cx": 15, "cy": 9, "intensity": 4},
                        {"cx": 13, "cy": 10, "intensity": 4}, {"cx": 14, "cy": 10, "intensity": 4},
                        {"cx": 15, "cy": 10, "intensity": 3}, {"cx": 12, "cy": 9, "intensity": 3}],
        "growth_flp_per_hour": 90.0,
        "people_status": "unknown",
        "wind_speed": 7.2,
        "fire_type": "vegetation",
        "wind_shift": None,
    },
}
