"""确定性规则引擎 Tool 层 —— 所有安全关键数字的唯一来源。

对应《无人机子群与参数规则 V1》:
- FLP 网格火情负荷: B_i = 10 x I x K_fuel x K_wind x K_slope
- 电量: r = r_mode x (1 + 0.45rho + 0.20f_wind + 0.10f_climb) + r_aux, 按分钟折算
- 药剂兼容: 植被火 water_20l kappa=1.0, CO2=0.25; 电气热点 CO2=1.5, water=0
- 离散轮次仿真: B_(t+dt) = max(0, B_t + G*dt/60 - sum S)
- 评分: J = 0.40T + 0.30B + 0.15E + 0.10M + 0.05N (越小越优)

Agent 层只允许调用本模块并解释结果, 不得覆盖任何计算值。
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[3]


class ToolError(ValueError):
    """规则输入非法。"""


def _config() -> Dict[str, Any]:
    return json.loads((ROOT / "configs" / "simulation.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def sim_config() -> Dict[str, Any]:
    return _config()


def load_json(rel: str) -> Any:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def save_json(rel: str, data: Any) -> None:
    (ROOT / rel).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 风档 / 坡度 / 燃料

def resolve_wind_band(wind_speed: float) -> Dict[str, Any]:
    for band in sim_config()["wind_bands"]:
        if wind_speed < band["max_mps"]:
            return {"band": sim_config()["wind_bands"].index(band), "label": band["label"],
                    "k_wind": band["k_wind"], "weather_efficiency": band["weather_efficiency"], "wind_speed": wind_speed}
    return {"band": len(sim_config()["wind_bands"]), "label": ">8 m/s",
            "k_wind": sim_config()["k_wind_over"], "weather_efficiency": sim_config()["weather_efficiency_over"],
            "wind_speed": wind_speed}


def resolve_slope_factor(slope_deg: float) -> Dict[str, Any]:
    for band in sim_config()["slope_bands"]:
        if slope_deg < band["max_deg"]:
            return {"k_slope": band["k_slope"], "slope_deg": slope_deg}
    return {"k_slope": sim_config()["slope_bands"][-1]["k_slope"], "slope_deg": slope_deg}


def fuel_factor(fuel_type: str) -> float:
    return float(sim_config()["fuel_factors"].get(fuel_type, 1.0))


# ---------------------------------------------------------------- FLP 火情负荷

def cell_flp(intensity: float, fuel: str, wind_speed: float, slope_deg: float) -> float:
    b = 10 * intensity * fuel_factor(fuel) * resolve_wind_band(wind_speed)["k_wind"] * resolve_slope_factor(slope_deg)["k_slope"]
    return round(b, 2)


def build_fire_grid(cells: List[Dict[str, Any]], fuel: str, wind_speed: float, slope_deg: float) -> Dict[str, Any]:
    """cells: [{cx, cy, intensity}] -> 每格 B_i 与总 B_total。"""
    detail = []
    total = 0.0
    for cell in cells:
        b = cell_flp(cell["intensity"], fuel, wind_speed, slope_deg)
        detail.append({**cell, "flp": b})
        total += b
    return {"cells": detail, "total_flp": round(total, 2), "wind_band": resolve_wind_band(wind_speed),
            "k_fuel": fuel_factor(fuel), "k_slope": resolve_slope_factor(slope_deg)["k_slope"]}


def agent_kappa(module: str, fire_type: str) -> Tuple[float, bool]:
    kappa = float(sim_config()["kappa"].get(fire_type, {}).get(module, 0.0))
    return kappa, kappa > 0


def suppression_capability(module: str, fire_type: str, wind_speed: float, drop_quality: str = "clear") -> Dict[str, Any]:
    """单架次有效处置能力 S = Q x kappa x eta_drop x eta_weather。"""
    kappa, compatible = agent_kappa(module, fire_type)
    band = resolve_wind_band(wind_speed)
    eta = sim_config()["drop_efficiency"].get(drop_quality, 0.75) * band["weather_efficiency"]
    quantity = sim_config()["spray"][module]["quantity"]
    return {"module": module, "quantity": quantity, "kappa": kappa, "compatible": compatible,
            "eta": round(eta, 4), "effective_flp": round(quantity * kappa * eta, 2)}


# ---------------------------------------------------------------- 电量模型

def energy_rate(mode_rate: float, task_mass: float = 0, capacity_mass: float = 1,
                wind_factor: float = 0, climb_factor: float = 0, aux_rate: float = 0) -> float:
    """r = r_mode x (1 + 0.45rho + 0.20f_wind + 0.10f_climb) + r_aux (若直接采用表内满载率则传 rho=0)。"""
    load = min(1.0, task_mass / max(capacity_mass, 1e-6))
    corr = sim_config()["energy_correction"]
    return mode_rate * (1 + corr["load"] * load + corr["wind"] * wind_factor + corr["climb"] * climb_factor) + aux_rate


def delta_soc(rate_per_hour: float, minutes: float) -> float:
    return rate_per_hour * minutes / 60.0


def soc_need(soc_outbound: float, soc_task: float, soc_return: float, soc_reserve: float = 25) -> float:
    return soc_outbound + soc_task + soc_return + soc_reserve


def uav_mode_rate(uav: Dict[str, Any], loaded: bool, hover: bool = False) -> float:
    table = sim_config()["energy"][uav["subgroup"]]
    if uav["subgroup"] == "reconnaissance":
        return (table["hover"] if hover else table["cruise"]) + (table["sense_aux"] if not hover else 0)
    if uav["subgroup"] == "suppression":
        if hover:
            return table["hover_spray"]
        return table["cruise_loaded"] if loaded else table["cruise_empty"]
    if hover:
        return table["comms_hover"]
    return table["cruise_loaded"] if loaded else table["cruise_empty"]


def distance_m(a: Dict[str, float], b: Dict[str, float]) -> float:
    return math.hypot(b["x"] - a["x"], b["y"] - a["y"])


def flight_minutes(dist_m: float, speed_mps: float) -> float:
    return dist_m / max(speed_mps, 0.1) / 60.0


def charge_soc(soc: float, minutes: float, mode: str = "base") -> float:
    rate = sim_config()["charging"]["forward_soc_per_hour"] if mode == "forward" else sim_config()["charging"]["base_soc_per_hour"]
    return round(min(100.0, soc + rate * minutes / 60.0), 2)


def battery_swap() -> Dict[str, Any]:
    c = sim_config()["charging"]
    return {"minutes": c["battery_swap_minutes"], "soc_after": c["battery_swap_soc"], "requires_battery_pack": True}


# ---------------------------------------------------------------- 硬约束

RETURN_SOC = 25.0


def check_hard_constraints(uav: Dict[str, Any], module: str, fire_type: str,
                           soc_out: float, soc_task: float, soc_back: float,
                           battery_packs: int = 0) -> Dict[str, Any]:
    reasons: List[str] = []
    notes: List[str] = []
    if uav.get("status") in {"fault", "charging"}:
        reasons.append(f"状态 {uav.get('status')} 不可派遣")
    if uav.get("health", 100) < 60:
        reasons.append("健康度不足")
    module_cfg = sim_config()["spray"][module]
    if module_cfg["module_mass_kg"] > uav["payload_capacity_kg"]:
        reasons.append("载荷超限")
    if not agent_kappa(module, fire_type)[1]:
        reasons.append("药剂与火情类型不兼容")
    # SOC_need = 去程 + 任务 + 返程 + 25% 储备; SOC ≥ SOC_need 即可执行(储备只计一次)
    need = soc_need(soc_out, soc_task, soc_back)
    soc = float(uav["soc"])
    if soc < need:
        if battery_packs >= 1 and 95.0 >= need:
            soc = 95.0
            notes.append("当前 SOC 不足,先换电(SOC→95%)再出动")
        else:
            reasons.append(f"SOC {uav['soc']:.0f}% 低于需求 {need:.0f}%(含 25% 返航储备)")
    return {"uav_id": uav["uav_id"], "feasible": not reasons, "reasons": reasons, "notes": notes,
            "soc_need": round(need, 2), "soc_end": round(soc - (need - 25), 2)}


# ---------------------------------------------------------------- 轮次仿真(执行期)

def simulate_round(fire_total_flp: float, growth_flp_per_hour: float, suppression_flp: float,
                   round_minutes: float) -> Dict[str, Any]:
    growth = growth_flp_per_hour * round_minutes / 60.0
    after = max(0.0, fire_total_flp + growth - suppression_flp)
    return {"before_flp": round(fire_total_flp, 2), "growth_flp": round(growth, 3),
            "suppression_flp": round(suppression_flp, 2), "after_flp": round(after, 2)}


def net_capability(suppression_per_hour: float, growth_per_hour: float) -> Dict[str, Any]:
    net = suppression_per_hour - growth_per_hour
    if net > 0:
        verdict = "can_control"
    elif abs(net) < 1e-6:
        verdict = "maintain_only"
    else:
        verdict = "cannot_control"
    return {"net_flp_per_hour": round(net, 2), "verdict": verdict}


# ---------------------------------------------------------------- 方案评分

def score_plan(control_minutes: Optional[float], residual_flp: float, fire_flp: float,
               energy_total: float, uav_count: int, material_used: float, changes: int) -> Dict[str, Any]:
    w = sim_config()["scoring_weights"]
    refs = sim_config()["scoring_refs"]
    time_norm = min(max((control_minutes or refs["time_ref_minutes"] * 2) / refs["time_ref_minutes"], 0), 1) if control_minutes else 1.0
    residual_norm = min(max(residual_flp / max(fire_flp, 1), 0), 1)
    energy_norm = min(max(energy_total / max(uav_count * refs["energy_ref_per_uav"], 1), 0), 1)
    material_norm = min(max(material_used / refs["material_ref_liters"], 0), 1)
    change_norm = min(max(changes / refs["change_ref_rounds"], 0), 1)
    score = (w["time"] * time_norm + w["residual"] * residual_norm + w["energy"] * energy_norm
             + w["material"] * material_norm + w["change"] * change_norm)
    return {"score": round(score, 4), "lower_is_better": True,
            "parts": {"time": round(time_norm, 3), "residual": round(residual_norm, 3),
                      "energy": round(energy_norm, 3), "material": round(material_norm, 3),
                      "change": round(change_norm, 3)}}


# ---------------------------------------------------------------- 候选方案预演(选优用快速仿真)

def fast_simulate_candidate(uavs: List[Dict[str, Any]], fire_flp: float, growth_per_hour: float,
                            module: str, fire_type: str, wind_speed: float, inventory: Dict[str, Any],
                            fire_pos: Dict[str, float], base_pos: Dict[str, float],
                            round_minutes: float = 5, max_rounds: int = 24) -> Dict[str, Any]:
    """对单个灭火机组合做 5 分钟离散预演: 补给、换电、返航硬约束, 输出控制时间与消耗供评分。"""
    cfg = sim_config()
    spray = cfg["spray"][module]
    quantity, spray_minutes = spray["quantity"], float(spray["minutes"])
    refill = cfg["refill_minutes"]["base"]
    swap = battery_swap()
    cap = suppression_capability(module, fire_type, wind_speed)
    eff = cap["effective_flp"]
    water_left = min(inventory.get("water_liters", 0) / quantity, inventory.get("water_modules_w20", 0)) if module == "water_20l" else inventory.get("co2_modules_c6", 0)
    packs_left = float(inventory.get("battery_packs", 0)) + sum(
        p.get("battery_packs", 0) for p in inventory.get("forward_supply_points", []) if p.get("id") == "fsp-1")
    growth_per_round = growth_per_hour * round_minutes / 60.0

    drones = []
    for uav in uavs:
        dist = distance_m(uav["position"], fire_pos)
        out_min = flight_minutes(dist, uav["speed_mps"])
        back_min = flight_minutes(dist, uav["speed_mps"])
        loaded_mass = spray["module_mass_kg"]
        rate_out = uav_mode_rate(uav, loaded=True)
        rate_back = uav_mode_rate(uav, loaded=False)
        soc_out = delta_soc(rate_out, out_min)
        soc_task = delta_soc(cfg["energy"]["suppression"]["hover_spray"], spray_minutes)
        soc_back = delta_soc(rate_back, back_min)
        drones.append({"uav_id": uav["uav_id"], "soc": float(uav["soc"]), "agent": min(quantity, float(uav.get("agent_remaining", quantity))),
                       "sortie_soc": round(soc_out + soc_task + soc_back, 2), "out_minutes": out_min,
                       "sorties": 0, "swaps": 0, "refills": 0, "state": "ready", "soc_cost_total": 0.0})
    if not drones:
        return {"controlled": False, "control_minutes": None, "rounds_used": 0, "residual_flp": round(fire_flp, 2),
                "material_used": 0, "swaps": 0, "refills": 0, "stalled": "no_uav", "energy_total": 0.0, "per_uav": []}

    load = max(0.0, float(fire_flp))
    rounds_used, material_used, extra_min, energy_total = 0, 0.0, 0.0, 0.0
    stalled = None
    while load > 0.01 and rounds_used < max_rounds:
        rounds_used += 1
        suppression = 0.0
        for d in drones:
            if d["state"] != "ready" or load <= 0:
                continue
            if d["agent"] < quantity:
                if water_left >= 1:
                    water_left -= 1; d["agent"] = quantity; d["refills"] += 1; extra_min += refill
                else:
                    d["state"] = "out_of_agent"; stalled = stalled or "agent_insufficient"; continue
            if d["soc"] - d["sortie_soc"] < RETURN_SOC:
                if packs_left >= 1:
                    packs_left -= 1; d["soc"] = swap["soc_after"]; d["swaps"] += 1; extra_min += swap["minutes"]
                else:
                    d["state"] = "out_of_energy"; stalled = stalled or "soc_below_return"; continue
            d["soc"] = round(d["soc"] - d["sortie_soc"], 2)
            d["soc_cost_total"] += d["sortie_soc"]
            energy_total += d["sortie_soc"]
            d["agent"] = round(d["agent"] - quantity, 2)
            d["sorties"] += 1
            material_used += quantity
            suppression += eff
        load = max(0.0, load + growth_per_round - suppression)
        if load > 0 and all(d["state"] != "ready" for d in drones):
            break
    overhead = 2 * max((d["out_minutes"] for d in drones), default=0.0)
    controlled = load <= 0.01
    control_minutes = round(rounds_used * round_minutes + overhead + extra_min, 1) if controlled else None
    return {"controlled": controlled, "control_minutes": control_minutes, "rounds_used": rounds_used,
            "residual_flp": round(load, 2), "suppression_per_sortie": eff, "material_used": round(material_used, 1),
            "swaps": sum(d["swaps"] for d in drones), "refills": sum(d["refills"] for d in drones),
            "energy_total": round(energy_total, 1), "stalled": stalled,
            "per_uav": [{"uav_id": d["uav_id"], "sorties": d["sorties"], "swaps": d["swaps"], "refills": d["refills"],
                         "soc_end": d["soc"], "state": d["state"]} for d in drones]}


# ---------------------------------------------------------------- 疏散 BFS

from collections import deque


def plan_evacuation_route(start: Tuple[int, int], goal: Tuple[int, int], blocked: set,
                          cols: int, rows: int, cell_m: float = 100.0, walk_mps: float = 1.2) -> Dict[str, Any]:
    queue = deque([(start, [start])])
    visited = {start}
    while queue:
        (x, y), path = queue.popleft()
        if (x, y) == goal:
            return {"found": True, "path": [list(p) for p in path], "steps": len(path) - 1,
                    "estimated_minutes": round((len(path) - 1) * cell_m / walk_mps / 60.0, 1)}
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if 0 <= n[0] < cols and 0 <= n[1] < rows and n not in blocked and n not in visited:
                visited.add(n)
                queue.append((n, path + [n]))
    return {"found": False, "path": [], "estimated_minutes": None}


# ---------------------------------------------------------------- Tool 注册表

TOOLS: Dict[str, Any] = {
    "resolve_wind_band": resolve_wind_band, "resolve_slope_factor": resolve_slope_factor,
    "cell_flp": cell_flp, "build_fire_grid": build_fire_grid, "agent_kappa": agent_kappa,
    "suppression_capability": suppression_capability, "energy_rate": energy_rate, "delta_soc": delta_soc,
    "soc_need": soc_need, "uav_mode_rate": uav_mode_rate, "distance_m": distance_m,
    "flight_minutes": flight_minutes, "charge_soc": charge_soc, "battery_swap": battery_swap,
    "check_hard_constraints": check_hard_constraints, "simulate_round": simulate_round,
    "net_capability": net_capability, "score_plan": score_plan,
    "fast_simulate_candidate": fast_simulate_candidate, "plan_evacuation_route": plan_evacuation_route,
}
