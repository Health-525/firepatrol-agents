"""② 侦察研判 Agent —— 火情感知(PWM-Net fixture)、环境研判、FLP 网格评估、人员状态。"""
from __future__ import annotations

from typing import Any, Dict

from ..agentkit.base import BaseAgent
from ..agentkit.llm import agent_analysis, llm_status
from ..domain.store import BOARD
from ..rules import tools as R


def cell_center(cx: int, cy: int) -> Dict[str, float]:
    return {"x": cx * 100 + 50, "y": cy * 100 + 50}


class ReconAgent(BaseAgent):
    agent_id = "recon"
    name = "侦察研判"
    role = "火情感知 · 环境研判 · FLP 评估 · 有人/无人判断"
    subgroup = "reconnaissance"
    color = "#3b82f6"
    emoji = "🔭"

    async def handle(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        cfg = state["scenario_cfg"]
        scene = state["environment"]
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
        analysis = await agent_analysis(self.name, self.role,
                                        f"网格数 {len(fire['cells'])},FLP={total_flp},增长 {cfg['growth_flp_per_hour']} FLP/h,"
                                        f"风 {wind} m/s({band['label']}),坡度 {scene['slope_deg']}°,燃料 {scene['fuel_type']},"
                                        f"人员 {fire['people_status']},置信度 {fire['confidence']}",
                                        topic=f"火情研判 风速 蔓延 {fire['people_status']}")
        if analysis:
            self.say(task_id, "FINDING", "commander", f"💡 GLM 研判:{analysis}",
                     {"llm": llm_status()["model"], "grounded": "knowledge-base"})
        return {"fire": fire, "fire_center": center, "vision": observation}

    @staticmethod
    def _fire_center(fire: Dict[str, Any]) -> Dict[str, float]:
        cells = fire["cells"]
        return {"x": round(sum(c["x"] for c in cells) / len(cells), 1), "y": round(sum(c["y"] for c in cells) / len(cells), 1)}
