"""环境观测流 —— 把"风速变化"从场景剧本变成外生观测。

原则: 执行器每轮读取观测值(带确定性抖动的传感器模拟), 与当前火情风档比较;
观测到跳档才触发重规划。没有"第 N 轮必须变风"的剧本。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .tools import resolve_wind_band


def observe_wind(cfg: Dict[str, Any], round_index: int, current_wind: float) -> Tuple[float, bool]:
    """返回 (观测风速, 是否跳档)。确定性抖动(可复现), 跳档 = 观测风档 > 当前风档。"""
    series = cfg.get("wind_series") or {}
    base = series.get(str(round_index), series.get(round_index))
    if base is None:
        # 无剧本场景: 当前风 + 确定性微抖(±0.2 m/s), 模拟传感器噪声
        jitter = ((round_index * 37) % 5 - 2) * 0.1
        observed = round(max(0.4, current_wind + jitter), 1)
    else:
        observed = float(base)
    band_jump = resolve_wind_band(observed)["band"] > resolve_wind_band(current_wind)["band"]
    return observed, band_jump


def observation_event(cfg: Dict[str, Any], observed: float) -> Optional[str]:
    return f"观测到风速 {observed} m/s" if observed is not None else None
