"""搜索阶段 —— R 机巡逻航线、扫描检测与覆盖度。

真实链路: 火从 t=0 就存在并在增长, 但系统"看不见"——
侦察机沿巡逻航线(牛耕式往返)飞行, 机载检测(PWM-Net)半径覆盖起火点才算发现;
发现后回传指挥中心, 才进入研判/调度。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

# 巡逻航线: 牛耕式往返(boustrophedon), 700m 扫描带覆盖 1400m 纵深
# R1 扫北半区(y=250, 950), R2 扫南半区(y=650, 1350), 方向交替
PATTERN: Dict[str, List[Dict[str, Any]]] = {
    "R1": [
        {"x0": 120, "x1": 1880, "y": 250, "dir": 1},
        {"x0": 1880, "x1": 120, "y": 950, "dir": -1},
    ],
    "R2": [
        {"x0": 1880, "x1": 120, "y": 650, "dir": -1},
        {"x0": 120, "x1": 1880, "y": 1350, "dir": 1},
    ],
}


def detect_radius(intensity: float) -> float:
    """检测半径: 火越强烟柱越大, 越远可测(基线 380m + 每级 60m)。"""
    return 380 + 60 * max(1, min(4, intensity))


def point_to_segment(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
    dx, dy = x1 - x0, y1 - y0
    length2 = dx * dx + dy * dy or 1.0
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def scan_leg(leg: Dict[str, Any], ignition: Dict[str, Any], intensity: float) -> Tuple[bool, float]:
    """飞完一条航线: 返回 (是否探测到, 起火点到航线的最近距离)。"""
    dist = point_to_segment(ignition["x"], ignition["y"], leg["x0"], leg["y"], leg["x1"], leg["y"])
    return dist <= detect_radius(intensity), dist


def coverage(legs_done: int) -> int:
    """已飞航线数 -> 地图覆盖率%(4 条航线全覆盖)。"""
    return min(100, round(legs_done / 4 * 100))


def leg_position(leg: Dict[str, Any], fraction: float) -> Dict[str, float]:
    return {"x": leg["x0"] + (leg["x1"] - leg["x0"]) * fraction, "y": leg["y"]}
