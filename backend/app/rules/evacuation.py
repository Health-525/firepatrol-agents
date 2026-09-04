"""人员疏散路径规划 —— 基于地图网格 + 真实地形 + 实时火情的 A* 寻路。

要素:
- 网格: 与场景地图一致(20x14, 100m/格);
- 通行代价: 基础 1 + 坡度代价(相邻格高差, 陡坡更费力) + 热烟代价(低强度火格);
- 封锁: 高强度火格(FLP>5)与 restricted_cells 完全不可通行;
- 目标: 从人员区到多个出口中总代价最小者;
- 输出: 逐格路径(世界坐标)、步行时间(1.2 m/s)、所选出口、是否被火逼停改道。
"""
from __future__ import annotations

import heapq
from typing import Any, Dict, List, Optional, Tuple

from .terrain import terrain_model

WALK_MPS = 1.2
CELL_M = 100.0


def _grid(scene: Dict[str, Any]) -> Tuple[int, int]:
    m = scene.get("map") or {}
    width = int(m.get("width_m", 2000)); height = int(m.get("height_m", 1400))
    return max(1, width // 100), max(1, height // 100)


def _elev_grid(cols: int, rows: int) -> List[List[float]]:
    model = terrain_model()
    out = []
    for r in range(rows):
        row = []
        for c in range(cols):
            x = c * 100 + 50
            y = r * 100 + 50
            gx = min(model["nx"] - 1, max(0, round(x / model["scene_w"] * (model["nx"] - 1))))
            gy = min(model["ny"] - 1, max(0, round(y / model["scene_h"] * (model["ny"] - 1))))
            row.append(model["elevations"][gy][gx])
        out.append(row)
    return out


def plan_evacuation(scene: Dict[str, Any], fire_cells: List[Dict[str, Any]],
                    people_zone: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cols, rows = _grid(scene)
    elev = _elev_grid(cols, rows)
    fire_map: Dict[Tuple[int, int], float] = {}
    for cell in fire_cells or []:
        fire_map[(int(cell["cx"]), int(cell["cy"]))] = float(cell.get("flp", 0))
    restricted = {(int(c["cx"]), int(c["cy"])) for c in scene.get("restricted_cells", [])}
    zone = people_zone or (scene.get("people_zones") or [{}])[0]
    start = (int(zone.get("cx", 0)), int(zone.get("cy", 0)))
    exits = scene.get("exits") or []
    exit_cells = [(e.get("name", "出口"), int(e["x"]) // 100, int(e["y"]) // 100) for e in exits] or [("出口", cols - 1, rows - 1)]

    def passable(c: int, r: int) -> bool:
        return 0 <= c < cols and 0 <= r < rows and (c, r) not in restricted and fire_map.get((c, r), 0) <= 5.0

    def step_cost(a: Tuple[int, int], b: Tuple[int, int]) -> float:
        slope = abs(elev[b[1]][b[0]] - elev[a[1]][a[0]]) / 12.0   # 坡度代价: 每米高差
        heat = fire_map.get(b, 0) * 1.2                             # 热烟代价
        return 1.0 + slope + heat

    def heuristic(c: int, r: int, goal: Tuple[int, int]) -> float:
        return (abs(c - goal[0]) + abs(r - goal[1])) * 1.0

    best: Optional[Dict[str, Any]] = None
    for exit_name, ec, er in exit_cells:
        goal = (min(ec, cols - 1), min(er, rows - 1))
        if not passable(*goal):
            continue
        # 起点特例: 人员区即使已被火覆盖也必须作为起点(身陷火中才更要逃生)
        if not (0 <= start[0] < cols and 0 <= start[1] < rows and start not in restricted):
            continue
        open_set = [(heuristic(*start, goal), 0.0, start, [start])]
        seen = {start: 0.0}
        found: Optional[List[Tuple[int, int]]] = None
        while open_set:
            _, cost, node, path = heapq.heappop(open_set)
            if node == goal:
                found = path
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (node[0] + dx, node[1] + dy)
                if not passable(*nxt):
                    continue
                ncost = cost + step_cost(node, nxt)
                if ncost < seen.get(nxt, 1e9):
                    seen[nxt] = ncost
                    heapq.heappush(open_set, (ncost + heuristic(*nxt, goal), ncost, nxt, path + [nxt]))
        if found:
            world = [{"cx": c, "cy": r, "x": c * 100 + 50, "y": r * 100 + 50} for c, r in found]
            length_m = (len(found) - 1) * CELL_M
            climb = sum(max(0, elev[found[i + 1][1]][found[i + 1][0]] - elev[found[i][1]][found[i][0]]) for i in range(len(found) - 1))
            minutes = round(length_m / WALK_MPS / 60 + climb / 60, 1)  # 爬升按 60m/min 折算
            candidate = {"found": True, "exit": exit_name, "path": world,
                         "walk_minutes": minutes, "length_m": length_m, "climb_m": round(climb, 0),
                         "cells": len(found), "cost": round(seen[goal], 1)}
            if best is None or candidate["cost"] < best["cost"]:
                best = candidate
    if best:
        return best
    return {"found": False, "exit": None, "path": [], "walk_minutes": None,
            "length_m": 0, "climb_m": 0, "cells": 0, "cost": None,
            "note": "火情或地形已封锁全部出口路径,建议呼叫空中引导至安全集结区"}


def advance_people(evac: Dict[str, Any], round_minutes: float) -> Tuple[int, bool, Optional[Dict[str, Any]]]:
    """推进人群沿路径移动。返回 (新的进度格数, 是否抵达, 当前所在格)。"""
    path = evac.get("path") or []
    if not path:
        return 0, False, None
    progress = float(evac.get("progress_cells", 0))
    progress += WALK_MPS * round_minutes * 60 / CELL_M  # 每轮步行距离换算格数
    done = progress >= len(path) - 1
    progress = min(progress, len(path) - 1)
    here = path[int(progress)]
    return round(progress, 2), done, here
