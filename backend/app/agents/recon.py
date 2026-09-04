"""② 侦察研判 Agent —— 巡航搜索发现火情(PWM-Net)、环境研判、FLP 网格评估、人员状态。"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from ..agentkit.base import BaseAgent
from ..domain.store import BOARD
from ..rules import search as S
from ..rules import tools as R
from ..rules.knowledge import query_knowledge


def cell_center(cx: int, cy: int) -> Dict[str, float]:
    return {"x": cx * 100 + 50, "y": cy * 100 + 50}


class ReconAgent(BaseAgent):
    agent_id = "recon"
    name = "侦察研判"
    role = "巡航搜索发现火情 · 环境研判 · FLP 评估 · 有人/无人判断"
    subgroup = "reconnaissance"
    color = "#3b82f6"
    emoji = "🔭"

    # ------------------------------------------------ 搜索阶段: 每次调用飞一条巡航航线

    async def search_round(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """R1/R2 各飞一条巡逻航线并扫描; 起火点进入检测半径才算发现。

        未发现前: 火对系统不可见(board.fire 为空), 地图上不存在火情;
        发现后: 回传指挥中心, 进入研判。
        """
        cfg = R.sim_config()
        task_id = state["task_id"]
        truth = state["truth"]
        ignition = {"cx": truth["cx"], "cy": truth["cy"], "x": truth["x"], "y": truth["y"]}
        intensity = truth["intensity"]
        legs_done = state.get("search_legs_done", 0)
        fleet = state["fleet"]
        by_id = {u["uav_id"]: u for u in fleet}
        detected_by: Optional[str] = None

        for uav_id, legs in S.PATTERN.items():
            leg_index = min(legs_done, len(legs) - 1)
            leg = legs[leg_index]
            hit, dist = S.scan_leg(leg, ignition, intensity)
            uav = by_id.get(uav_id)
            if uav and uav["status"] != "fault":
                uav["position"] = {"x": leg["x1"], "y": leg["y"], "z": 60}
                uav["status"] = "working"
                uav["soc"] = round(max(0, uav["soc"] - R.delta_soc(
                    R.uav_mode_rate(uav, loaded=False), cfg["time"]["round_minutes"])), 2)
            if hit and detected_by is None:
                detected_by = uav_id

        legs_done += 1
        coverage_pct = S.coverage(legs_done)
        BOARD.update(task_id, phase="searching", fleet=fleet, search={"legs": legs_done, "coverage": coverage_pct})

        if detected_by:
            truth["detected"] = True
            truth["detected_by"] = detected_by
            BOARD.update(task_id, truth=truth)
            self.say(task_id, "FINDING", "commander",
                     f"🔭 {detected_by} 巡航至 ({ignition['cx']},{ignition['cy']}) 附近,机载检测(PWM-Net)发现明火与烟柱,"
                     f"置信度 0.93,已回传指挥中心!搜索耗时 {legs_done} 个巡航段,地图覆盖率 {coverage_pct}%。",
                     {"detected": True, "ignition": ignition, "coverage": coverage_pct})
            return {"search_detected": True, "search_legs_done": legs_done, "truth": truth}

        self.say(task_id, "INFO", "commander",
                 f"搜索巡航第 {legs_done} 段:R1/R2 已扫描 {coverage_pct}% 区域,暂未发现明火。")
        if legs_done >= 6:  # 兜底: 全图两遍必发现(烟柱扩散)
            truth["detected"] = True
            truth["detected_by"] = "R1"
            BOARD.update(task_id, truth=truth)
            self.say(task_id, "FINDING", "commander",
                     f"🔭 R1 复扫发现烟柱扩散,确认火点 ({ignition['cx']},{ignition['cy']}),已回传!")
            return {"search_detected": True, "search_legs_done": legs_done, "truth": truth}
        await asyncio.sleep(cfg["demo"]["round_interval_ms"] / 1000)
        return {"search_detected": False, "search_legs_done": legs_done}

    # ------------------------------------------------ 研判

    async def handle(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        cfg = state["scenario_cfg"]
        scene = state["environment"]
        BOARD.update(task_id, phase="analyzing")
        mid_mission = state.get("round_index", 0) > 0 and state.get("fire")

        if mid_mission:
            # 重规划路径: 复用执行中的实时火情(网格FLP/风速已由仿真更新), 不重置
            fire = dict(state["fire"])
            if state.get("people_override"):
                fire["people_status"] = state["people_override"]
            band = R.resolve_wind_band(fire["wind_speed"])
            fire["wind_band"], fire["wind_band_label"] = band["band"], band["label"]
            BOARD.update(task_id, fire=fire)
            center = self._fire_center(fire)
            self.say(task_id, "FINDING", "commander",
                     f"重规划研判:当前 B={fire['total_flp']} FLP,风速 {fire['wind_speed']} m/s({band['label']},"
                     f"K_wind={band['k_wind']}),人员 {fire['people_status']}。基于实时状态重新生成方案。",
                     {"fire": fire, "center": center})
            return {"fire": fire, "fire_center": center}

        # 1) 感知: PWM-Net fixture(真实适配器可替换 detect_fire)
        observation = R.load_json("data/vision_observations.json")["default"]
        # 2) 环境: 风档/坡度/燃料
        wind = cfg["wind_speed"]
        band = R.resolve_wind_band(wind)
        # 3) FLP 网格: B_i = 10 x I x K_fuel x K_wind x K_slope (规则引擎唯一来源)
        grid = R.build_fire_grid(cfg["fire_cells"], scene["fuel_type"], wind, scene["slope_deg"])
        total_flp = grid["total_flp"]
        intensity_level = min(4, max(1, round(sum(c["intensity"] for c in cfg["fire_cells"]) / len(cfg["fire_cells"]))))

        fire = {
            "fire_id": "fire_01", "fire_type": cfg["fire_type"],
            "cells": [{**c, "x": cell_center(c["cx"], c["cy"])["x"], "y": cell_center(c["cx"], c["cy"])["y"]} for c in grid["cells"]],
            "total_flp": total_flp, "growth_flp_per_hour": cfg["growth_flp_per_hour"],
            "wind_speed": wind, "wind_direction_deg": scene["wind_direction_deg"],
            "wind_band": band["band"], "wind_band_label": band["label"],
            "slope_deg": scene["slope_deg"], "fuel_type": scene["fuel_type"],
            "intensity_level": intensity_level, "people_status": state.get("people_override", cfg["people_status"]),
            "detected_classes": [d["class_name"] for d in observation["detections"]],
            "confidence": max(d["confidence"] for d in observation["detections"]),
            "source": "rules+vision-fixture",
        }
        BOARD.update(task_id, fire=fire)
        center = self._fire_center(fire)
        self.say(task_id, "FINDING", "commander",
                 f"火情研判完成:{len(fire['cells'])} 个网格,总火情负荷 B={total_flp} FLP,增长率 {cfg['growth_flp_per_hour']} FLP/h,"
                 f"火势等级 {intensity_level}。风速 {wind} m/s({band['label']}),K_wind={band['k_wind']}。"
                 f"人员状态:{fire['people_status']}。", {"fire": fire, "center": center})
        self.think_bg(task_id, "FINDING", "commander",
                      "给出火情研判意见: 火势态势、蔓延风险(结合风档/坡度/燃料)、人员分支建议",
                      f"网格数 {len(fire['cells'])},FLP={total_flp},增长 {cfg['growth_flp_per_hour']} FLP/h,"
                      f"风 {wind} m/s({band['label']}),坡度 {scene['slope_deg']}°,燃料 {scene['fuel_type']},"
                      f"人员 {fire['people_status']},检测置信度 {fire['confidence']}")
        return {"fire": fire, "fire_center": center, "vision": observation}

    @staticmethod
    def _fire_center(fire: Dict[str, Any]) -> Dict[str, float]:
        cells = fire["cells"]
        return {"x": round(sum(c["x"] for c in cells) / len(cells), 1), "y": round(sum(c["y"] for c in cells) / len(cells), 1)}
