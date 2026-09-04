"""场景: 随机火情生成(默认) + 演示剧本预设。

随机火情: 起火点/强度/蔓延形状/增长率/风况/人员状态全部随机(种子可复现),
随后由 Agent 链自主研判 —— 系统不预知答案。
"""
from __future__ import annotations

import hashlib
import random
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
    # 风速突变: 外生观测序列(第 3 轮起观测到 6.8+ m/s) → 跳档检测 → 触发重规划
    "wind_shift": {
        "label": "风速突变 · 重规划演示",
        "fire_cells": [{"cx": 14, "cy": 9, "intensity": 3}, {"cx": 15, "cy": 9, "intensity": 3},
                        {"cx": 14, "cy": 10, "intensity": 3}, {"cx": 15, "cy": 10, "intensity": 3},
                        {"cx": 13, "cy": 9, "intensity": 3}, {"cx": 13, "cy": 10, "intensity": 3}],
        "growth_flp_per_hour": 8.0,
        "people_status": "confirmed",
        "wind_speed": 5.2,
        "fire_type": "vegetation",
        "wind_series": {"3": 6.8, "4": 7.0, "5": 7.1},
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
    # 就地取水: 远端火情 + 基地水剂受限 -> 基地补水耗尽后按规则 5.3 回退到东麓溪流就地取水
    "water_source": {
        "label": "远端火情 · 就地取水",
        "fire_cells": [{"cx": 16, "cy": 4, "intensity": 3}, {"cx": 17, "cy": 4, "intensity": 3},
                        {"cx": 16, "cy": 5, "intensity": 2}],
        "growth_flp_per_hour": 6.0,
        "people_status": "absent",
        "wind_speed": 4.2,
        "fire_type": "vegetation",
        "wind_shift": None,
        "inventory_override": {"water_liters": 40, "water_modules_w20": 2},
    },
    # 单机失能: 执行中外生机电故障 → 补位决策(方案内换机) + 每轮研判裁决
    # B=216(9格x24), 增长 12/h: 第 3 轮必然仍在燃烧, 失能落在压制中段(非剧本点名单, 受害机按方案实时选定)
    "equip_failure": {
        "label": "单机失能 · 自主补位",
        "fire_cells": [{"cx": 13, "cy": 8, "intensity": 2}, {"cx": 14, "cy": 8, "intensity": 2},
                        {"cx": 15, "cy": 8, "intensity": 2}, {"cx": 13, "cy": 9, "intensity": 2},
                        {"cx": 14, "cy": 9, "intensity": 2}, {"cx": 15, "cy": 9, "intensity": 2},
                        {"cx": 13, "cy": 10, "intensity": 2}, {"cx": 14, "cy": 10, "intensity": 2},
                        {"cx": 15, "cy": 10, "intensity": 2}],
        "growth_flp_per_hour": 12.0,
        "people_status": "confirmed",
        "wind_speed": 4.2,
        "fire_type": "vegetation",
        "wind_shift": None,
        "uav_failure": {"round": 3},
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


def build_random_scenario(seed: str) -> Dict[str, Any]:
    """从任务种子生成随机火情: 随机起火点 + 下风向簇状蔓延 + 随机强度/增长/风况/人员。"""
    rng = random.Random(f"fire-{seed}")
    # 起火点: 避开基地一角(左下)与地图边缘
    cx = rng.randint(5, 18)
    cy = rng.randint(4, 12)
    # 初始强度(加权): 小火多、大火少
    intensity = rng.choices([1, 2, 3, 4], weights=[25, 35, 28, 12])[0]
    # 蔓延: 主导风向 315°(西北风) -> 火向东南扩散
    spread_dirs = [(1, 1), (1, 0), (0, 1), (1, -1)]
    cells = [{"cx": cx, "cy": cy, "intensity": intensity}]
    target_cells = 2 + intensity  # 3~6 格
    guard = 0
    while len(cells) < target_cells and guard < 40:
        guard += 1
        base = rng.choice(cells)
        dx, dy = rng.choice(spread_dirs)
        nxt = {"cx": base["cx"] + dx, "cy": base["cy"] + dy}
        if not (1 <= nxt["cx"] <= 18 and 1 <= nxt["cy"] <= 12):
            continue
        if any(c["cx"] == nxt["cx"] and c["cy"] == nxt["cy"] for c in cells):
            continue
        nxt["intensity"] = max(1, min(4, intensity + rng.choice([-1, 0, 0, 1])))
        cells.append(nxt)
    growth = round((1.5 + rng.random() * 1.5) * intensity, 1)          # 1.5~3 x 强度 FLP/h
    wind = round(2.5 + rng.random() * 4.5, 1)                          # 2.5~7 m/s
    people = rng.choices(["confirmed", "absent", "unknown"], weights=[50, 30, 20])[0]
    wind_series = {}
    if rng.random() < 0.45:                                            # 45% 概率发生风变
        shift_round = rng.randint(2, 5)
        wind_series = {str(shift_round): round(min(9.0, wind + 1.5 + rng.random() * 1.8), 1)}
    # 35% 概率出现执行中单机机电失能(轮次随机; 受害机由当前方案实时选定, 场景不点名单)
    uav_failure = {"round": rng.randint(2, 5)} if rng.random() < 0.35 else None
    label = f"随机火情 · {intensity} 级 · 风 {wind} m/s · {'有' if people == 'confirmed' else ('无' if people == 'absent' else '待确认')}人"
    return {"label": label, "fire_cells": cells, "growth_flp_per_hour": growth,
            "people_status": people, "wind_speed": wind, "fire_type": "vegetation",
            "wind_series": wind_series, "uav_failure": uav_failure, "random": True}
