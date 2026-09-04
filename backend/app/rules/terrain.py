"""三维地形建模 —— 从 SRTM 高程数据(N32E118.hgt, 紫金山地区)提取演示林区的真实地形网格。

HGT 格式: 3601x3601 大端 int16, 行 0 = 北边缘(纬度 33.0N), 列 0 = 西边缘(经度 118.0E), 1 弧秒/像素。
演示场景(2000m x 1400m)映射到紫金山北麓一块真实山体, 重采样为 100x70 网格(20m/格)。
数据缺失时回落确定性合成地形, 演示永不中断。
"""
from __future__ import annotations

import array
import math
import sys
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[3]
HGT_PATH = ROOT / "data" / "terrain" / "N32E118.hgt"

GRID = 3601
LAT0, LON0 = 32.0, 118.0            # 瓦片左上角
CENTER_LAT, CENTER_LON = 32.0757, 118.8444  # 实测峰体(438m)东北侧, 场景覆盖主峰山脊
SCENE_W, SCENE_H = 2000.0, 1400.0   # 与 scene.json 地图一致(米)
NX, NY = 100, 70                    # 重采样网格(20m/格)
EXAGGERATION = 2.2                  # 前端建议垂直夸张系数


def _sample_hgt() -> List[List[float]] | None:
    """读取 HGT 中覆盖演示场景的矩形窗并双线性重采样为 NXxNY。"""
    if not HGT_PATH.exists():
        return None
    arcsec_lat = SCENE_H / 30.92          # 1 弧秒纬度 ≈ 30.92 m
    arcsec_lon = SCENE_W / (30.92 * math.cos(math.radians(CENTER_LAT)))
    row_center = (LAT0 + 1.0 - CENTER_LAT) * 3600  # 瓦片北边缘为 LAT0+1, 行号向南递增
    col_center = (CENTER_LON - LON0) * 3600
    row0 = max(0, int(row_center - arcsec_lat / 2))
    row1 = min(GRID - 1, int(row_center + arcsec_lat / 2) + 1)
    col0 = max(0, int(col_center - arcsec_lon / 2))
    col1 = min(GRID - 1, int(col_center + arcsec_lon / 2) + 1)
    try:
        window: List[List[float]] = []
        with HGT_PATH.open("rb") as handle:
            for row in range(row0, row1):
                handle.seek(row * GRID * 2 + col0 * 2)
                raw = handle.read((col1 - col0) * 2)
                line = array.array("h")
                line.frombytes(raw)
                if sys.byteorder == "little":  # HGT 为大端 int16
                    line.byteswap()
                # SRTM 空洞值 -32768 视为无效
                window.append([v if v != -32768 else 0.0 for v in line])
    except OSError:
        return None
    return _resample(window, NX, NY)


def _resample(window: List[List[float]], nx: int, ny: int) -> List[List[float]]:
    h, w = len(window), len(window[0])
    out: List[List[float]] = []
    for j in range(ny):
        gy = j / (ny - 1) * (h - 1)
        y0, y1 = int(gy), min(int(gy) + 1, h - 1)
        fy = gy - y0
        row = []
        for i in range(nx):
            gx = i / (nx - 1) * (w - 1)
            x0, x1 = int(gx), min(int(gx) + 1, w - 1)
            fx = gx - x0
            value = (window[y0][x0] * (1 - fx) * (1 - fy) + window[y0][x1] * fx * (1 - fy)
                     + window[y1][x0] * (1 - fx) * fy + window[y1][x1] * fx * fy)
            row.append(round(float(value), 1))
        out.append(row)
    return out


def _synthetic() -> List[List[float]]:
    """确定性合成地形(数据缺失时的演示回退)。"""
    out = []
    for j in range(NY):
        row = []
        for i in range(NX):
            x, y = i / NX, j / NY
            ridge = 180 * math.exp(-((x - 0.62) ** 2 / 0.05 + (y - 0.38) ** 2 / 0.06))
            hills = 60 * math.sin(x * 9.1) * math.cos(y * 7.3) + 30 * math.sin(x * 17 + y * 5)
            row.append(round(40 + ridge + max(hills, 0), 1))
        out.append(row)
    return out


@lru_cache(maxsize=1)
def terrain_model() -> Dict:
    grid = _sample_hgt()
    source = "srtm-n32e118(紫金山实测)" if grid else "synthetic-fallback(合成地形)"
    if grid is None:
        grid = _synthetic()
    flat = [v for row in grid for v in row]
    return {
        "nx": NX, "ny": NY, "cell_m": 20.0,
        "scene_w": SCENE_W, "scene_h": SCENE_H,
        "min_elev": min(flat), "max_elev": max(flat),
        "exaggeration": EXAGGERATION,
        "source": source,
        "elevations": grid,
    }


def elevation_at(x_m: float, y_m: float) -> float:
    """场景坐标(米) -> 高程(米), 供航线/悬停高度计算。"""
    model = terrain_model()
    gx = min(NX - 1, max(0, int(x_m / SCENE_W * (NX - 1))))
    gy = min(NY - 1, max(0, int(y_m / SCENE_H * (NY - 1))))
    return model["elevations"][gy][gx]
