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
    for band_no, band in enumerate(sim_config()["wind_bands"]):
        if wind_speed < band["max_mps"]:
            return {"band": band_no, "label": band["label"],
                    "k_wind": band["k_wind"], "weather_efficiency": band["weather_efficiency"], "wind_speed": wind_speed}
    over = sim_config()
    return {"band": len(over["wind_bands"]), "label": ">8 m/s",
            "k_wind": over["k_wind_over"], "weather_efficiency": over["weather_efficiency_over"],
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


def loaded_cruise_rate(uav: Dict[str, Any], module_mass_kg: float) -> float:
    """载荷差异巡航速率(规则 4.1 载荷修正): r = r_empty x (1 + 0.45 x m/M)。

    表内 cruise_loaded 是满载(m=M)标定值; 水剂模块 22kg 与 CO₂ 模块 14kg 满载程度不同,
    去程巡航按实际模块质量修正, 不再共用同一个"满载"速率(E 机 M=25kg:
    水 22kg -> 258.3%/h, CO₂ 14kg -> 231.7%/h, 满载 25kg -> 268.3%/h)。
    """
    base = float(sim_config()["energy"][uav["subgroup"]]["cruise_empty"])
    return round(energy_rate(base, task_mass=module_mass_kg, capacity_mass=uav["payload_capacity_kg"]), 1)


def distance_m(a: Dict[str, float], b: Dict[str, float]) -> float:
    return math.hypot(b["x"] - a["x"], b["y"] - a["y"])


def flight_minutes(dist_m: float, speed_mps: float) -> float:
    return dist_m / max(speed_mps, 0.1) / 60.0


def climb_adjusted_rate(base_rate: float, from_x: float, from_y: float, to_x: float, to_y: float) -> float:
    """爬升耗电(规则 4.1 f_climb): 航段爬升超过 30m 时该段速率 +10%。地形数据缺失则不修正。"""
    try:
        from .terrain import elevation_at
        if elevation_at(to_x, to_y) - elevation_at(from_x, from_y) > 30:
            return base_rate * (1 + sim_config()["energy_correction"]["climb"])
    except Exception:
        pass
    return base_rate


def charge_soc(soc: float, minutes: float, mode: str = "base") -> float:
    rate = sim_config()["charging"]["forward_soc_per_hour"] if mode == "forward" else sim_config()["charging"]["base_soc_per_hour"]
    return round(min(100.0, soc + rate * minutes / 60.0), 2)


def battery_swap() -> Dict[str, Any]:
    c = sim_config()["charging"]
    return {"minutes": c["battery_swap_minutes"], "soc_after": c["battery_swap_soc"], "requires_battery_pack": True}


# ---------------------------------------------------------------- 就地取水(规则 5.2 / 5.3)

def segment_crosses_cells(a: Dict[str, float], b: Dict[str, float], fire_cells: List[Dict[str, Any]],
                          cell_m: float = 100.0, step_m: float = 25.0, flp_threshold: float = 5.0) -> bool:
    """直线航段是否穿越高风险火情网格(水源取水路线先决条件之一)。

    起点格(无人机所在的火场格)与终点格(水源格)不计: 离开火场必然经过当前着火格。
    """
    blocked = {(c["cx"], c["cy"]) for c in (fire_cells or []) if c.get("flp", 0) >= flp_threshold}
    if not blocked:
        return False
    start_cell = (int(a["x"] // cell_m), int(a["y"] // cell_m))
    end_cell = (int(b["x"] // cell_m), int(b["y"] // cell_m))
    dist = distance_m(a, b)
    steps = max(1, int(dist / step_m))
    for i in range(steps + 1):
        t = i / steps
        x, y = a["x"] + (b["x"] - a["x"]) * t, a["y"] + (b["y"] - a["y"]) * t
        cell = (int(x // cell_m), int(y // cell_m))
        if cell in blocked and cell not in {start_cell, end_cell}:
            return True
    return False


def plan_water_source(module: str, fire_pos: Dict[str, float], base: Dict[str, float],
                      water_sources: List[Dict[str, Any]], uav: Optional[Dict[str, Any]],
                      fire_cells: Optional[List[Dict[str, Any]]] = None,
                      saving_threshold_min: float = 5.0) -> Dict[str, Any]:
    """就地取水决策(规则 5.3): 对每个水源做 前置条件 / 往返SOC / 与基地补给的省时对比。

    周期口径: 无人机喷洒完毕位于火场, 补给周期 = 火→补给点 + 灌装 + 补给点→火。
    全部条件满足(可用/安全到达/容量≥20L/路线不穿高风险格/往返后 SOC≥25%/省时≥5min)
    才选水源; 否则基地补水, 并保留合格水源作为基地断供时的回退。
    """
    cfg = sim_config()
    quantity = cfg["spray"][module]["quantity"]
    if module != "water_20l":
        return {"module": module, "mode": "base", "source": None, "options": [], "fallback_sources": [],
                "note": "CO₂ 模块只能在基地整模块更换, 无就地灌装条件",
                "reason": "药剂类型不支持就地取水"}
    if not uav:
        return {"module": module, "mode": "base", "source": None, "options": [], "fallback_sources": [],
                "note": "无可用灭火机, 不评估水源", "reason": "无机"}

    fill_base = float(cfg["refill_minutes"]["base"])
    fill_src = float(cfg["refill_minutes"]["water_source"])
    d_base = distance_m(fire_pos, base)
    t_base = flight_minutes(d_base, uav["speed_mps"])
    rate_empty = uav_mode_rate(uav, loaded=False)
    rate_loaded = loaded_cruise_rate(uav, cfg["spray"][module]["module_mass_kg"])
    soc_base_cycle = round(delta_soc(rate_empty, t_base) + delta_soc(rate_loaded, t_base), 2)
    base_option = {"kind": "base", "id": "base", "name": base.get("name", "基地"), "x": base["x"], "y": base["y"],
                   "distance_m": round(d_base, 0), "fill_minutes": fill_base,
                   "cycle_minutes": round(2 * t_base + fill_base, 1), "soc_after_cycle": round(float(uav["soc"]) - soc_base_cycle, 1),
                   "eligible": True, "vetoes": []}

    options = [base_option]
    viable: List[Dict[str, Any]] = []
    fallback: List[Dict[str, Any]] = []
    for src in water_sources or []:
        vetoes: List[str] = []
        if not src.get("available", False):
            vetoes.append("水源不可用")
        if not src.get("safe_access", False):
            vetoes.append("水源接近不安全")
        capacity = float(src.get("capacity_liters", 0))
        if capacity < quantity:
            vetoes.append(f"剩余水量 {capacity:.0f}L < {quantity:.0f}L")
        pos = {"x": src["x"], "y": src["y"]}
        d_src = distance_m(fire_pos, pos)
        t_src = flight_minutes(d_src, uav["speed_mps"])
        if segment_crosses_cells(fire_pos, pos, fire_cells or []):
            vetoes.append("取水路线穿越高风险网格")
        soc_src = round(float(uav["soc"]) - delta_soc(rate_empty, t_src) - delta_soc(rate_loaded, t_src), 1)
        if soc_src < RETURN_SOC:
            vetoes.append(f"往返后 SOC {soc_src:.0f}% < {RETURN_SOC:.0f}%")
        cycle_src = round(2 * t_src + fill_src, 1)
        saving = round(base_option["cycle_minutes"] - cycle_src, 1)
        # 硬性先决(可用/安全/容量/路线/SOC)与省时门槛分层: 前者不满足永远排除,
        # 后者只决定"平时是否值得绕行", 基地断供时仍可作为回退(规则 5.3 补救链: 更换水源)
        hard_ok = not vetoes
        saving_ok = saving >= saving_threshold_min
        option = {"kind": "source", "id": src["id"], "name": src.get("name", src["id"]), "x": src["x"], "y": src["y"],
                  "distance_m": round(d_src, 0), "fill_minutes": fill_src, "cycle_minutes": cycle_src,
                  "saving_minutes": saving, "soc_after_cycle": soc_src,
                  "capacity_remaining": capacity, "eligible": hard_ok and saving_ok, "vetoes": vetoes,
                  "saving_note": "" if saving_ok else f"省时 {saving:.1f} min 未达 {saving_threshold_min:.0f} min 门槛, 仅作断供回退"}
        options.append(option)
        if hard_ok and saving_ok:
            viable.append(option)
        elif hard_ok:
            fallback.append(option)

    plan: Dict[str, Any] = {"module": module, "quantity_per_refill": quantity, "options": options}
    if viable:
        chosen = min(viable, key=lambda o: o["cycle_minutes"])
        plan.update({"mode": "water_source", "source": chosen,
                     "note": f"就地取水:{chosen['name']}(省时 {chosen['saving_minutes']} min/周期, 往返 SOC {chosen['soc_after_cycle']}%)",
                     "reason": f"{chosen['name']} 周期 {chosen['cycle_minutes']} min, 比基地 {base_option['cycle_minutes']} min 省 {chosen['saving_minutes']} min(≥{saving_threshold_min:.0f} min), 全部先决条件通过"})
        fallback = [o for o in fallback if o["id"] != chosen["id"]]
    else:
        src_notes = "; ".join(f"{o['name']}: {'、'.join(o['vetoes']) or o['saving_note']}" for o in options if o["kind"] == "source")
        plan.update({"mode": "base", "source": None,
                     "note": "基地补水(水源省时不足 5 min 或先决条件不满足, 合格水源保留为断供回退)",
                     "reason": src_notes or "场景无水源"})
    # 回退水源: 基地水剂断供时按周期从短到长启用(容量随取水实时扣减)
    plan["fallback_sources"] = sorted(fallback, key=lambda o: o["cycle_minutes"])
    return plan


def refill_providers(water_source_plan: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """有序补给来源链: 选定水源(若 mode=water_source) -> 基地库存 -> 回退水源。

    预演与执行器共用本顺序, 保证评分口径与实际轮次一致; base 的水剂/模块库存由调用方校验。
    """
    plan = water_source_plan or {}
    providers: List[Dict[str, Any]] = []
    if plan.get("mode") == "water_source" and plan.get("source"):
        providers.append(plan["source"])
    providers.append({"kind": "base", "id": "base"})
    providers.extend(plan.get("fallback_sources") or [])
    return providers


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
                            round_minutes: float = 5, max_rounds: int = 24,
                            water_source_plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对单个灭火机组合做 5 分钟离散预演: 补给(基地/就地取水)、换电、返航硬约束, 输出控制时间与消耗供评分。"""
    cfg = sim_config()
    spray = cfg["spray"][module]
    module_mass = float(spray["module_mass_kg"])
    quantity, spray_minutes = spray["quantity"], float(spray["minutes"])
    refill = cfg["refill_minutes"]["base"]
    swap = battery_swap()
    cap = suppression_capability(module, fire_type, wind_speed)
    eff = cap["effective_flp"]
    # 补给来源链: 就地取水决策 + 基地库存 + 回退水源(与执行器同序)
    base_water_left = (min(inventory.get("water_liters", 0) / quantity, inventory.get("water_modules_w20", 0))
                       if module == "water_20l" else inventory.get("co2_modules_c6", 0))
    providers = refill_providers(water_source_plan) if module == "water_20l" else [{"kind": "base"}]
    src_stock = {o["id"]: float(o.get("capacity_remaining", 0)) / quantity for o in providers if o.get("kind") == "source"}
    refill_cycle = ((water_source_plan or {}).get("source") or {}).get("cycle_minutes", refill) \
        if (water_source_plan or {}).get("mode") == "water_source" else refill
    packs_left = float(inventory.get("battery_packs", 0)) + sum(
        p.get("battery_packs", 0) for p in inventory.get("forward_supply_points", []) if p.get("id") == "fsp-1")
    growth_per_round = growth_per_hour * round_minutes / 60.0

    drones = []
    for uav in uavs:
        dist = distance_m(uav["position"], fire_pos)
        out_min = flight_minutes(dist, uav["speed_mps"])
        # 去程按实际模块质量做载荷修正(水22kg/CO₂14kg 速率不同); 爬升超 30m 再按 f_climb 上浮
        rate_out = climb_adjusted_rate(loaded_cruise_rate(uav, module_mass),
                                       uav["position"]["x"], uav["position"]["y"], fire_pos["x"], fire_pos["y"])
        rate_back = uav_mode_rate(uav, loaded=False)
        soc_out = delta_soc(rate_out, out_min)
        soc_task = delta_soc(cfg["energy"]["suppression"]["hover_spray"], spray_minutes)
        soc_back = delta_soc(rate_back, out_min)
        drones.append({"uav_id": uav["uav_id"], "soc": float(uav["soc"]), "agent": min(quantity, float(uav.get("agent_remaining", quantity))),
                       "sortie_soc": round(soc_out + soc_task + soc_back, 2), "out_minutes": out_min,
                       "sortie_minutes": round(2 * out_min + spray_minutes, 1),
                       "sorties": 0, "swaps": 0, "refills": 0, "state": "ready", "soc_cost_total": 0.0})
    if not drones:
        return {"controlled": False, "control_minutes": None, "rounds_used": 0, "residual_flp": round(fire_flp, 2),
                "material_used": 0, "swaps": 0, "refills": 0, "stalled": "no_uav", "energy_total": 0.0, "per_uav": []}

    load = max(0.0, float(fire_flp))
    rounds_used, material_used, energy_total = 0, 0.0, 0.0
    stalled = None
    while load > 0.01 and rounds_used < max_rounds:
        rounds_used += 1
        suppression = 0.0
        for d in drones:
            if d["state"] != "ready" or load <= 0:
                continue
            # 时间一致性: 一轮一事 —— 补给轮/换电轮不出动, 与执行器口径一致
            if d["agent"] < quantity:
                refilled = False
                for provider in providers:
                    if provider.get("kind") == "source":
                        if src_stock.get(provider["id"], 0) >= 1:
                            src_stock[provider["id"]] -= 1
                            refilled = True
                            break
                    elif base_water_left >= 1:
                        base_water_left -= 1
                        refilled = True
                        break
                if refilled:
                    d["agent"] = quantity; d["refills"] += 1
                else:
                    d["state"] = "out_of_agent"; stalled = stalled or "agent_insufficient"
                continue
            if d["soc"] - d["sortie_soc"] < RETURN_SOC:
                if packs_left >= 1:
                    packs_left -= 1; d["soc"] = swap["soc_after"]; d["swaps"] += 1
                else:
                    d["state"] = "out_of_energy"; stalled = stalled or "soc_below_return"; continue
                continue
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
    controlled = load <= 0.01
    # 墙钟估计: 多机并行, 取最慢无人机 —— 首架次 11 分钟量级, 后续每周期(补给+换电+架次)约 20 分钟(规则 11.3 口径)
    cycle = round(refill_cycle + swap["minutes"] + (drones[0]["sortie_minutes"] if drones else 11), 1)
    slowest_cycles = max(d["sorties"] for d in drones) if drones else 0
    control_minutes = round((drones[0]["sortie_minutes"] if drones else 0) + max(0, slowest_cycles - 1) * cycle, 1) if controlled else None
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

from .knowledge import knowledge_stats, query_knowledge  # noqa: E402

TOOLS: Dict[str, Any] = {
    "resolve_wind_band": resolve_wind_band, "resolve_slope_factor": resolve_slope_factor,
    "cell_flp": cell_flp, "build_fire_grid": build_fire_grid, "agent_kappa": agent_kappa,
    "suppression_capability": suppression_capability, "energy_rate": energy_rate, "delta_soc": delta_soc,
    "soc_need": soc_need, "uav_mode_rate": uav_mode_rate, "loaded_cruise_rate": loaded_cruise_rate,
    "distance_m": distance_m,
    "flight_minutes": flight_minutes, "charge_soc": charge_soc, "battery_swap": battery_swap,
    "plan_water_source": plan_water_source, "segment_crosses_cells": segment_crosses_cells,
    "refill_providers": refill_providers,
    "check_hard_constraints": check_hard_constraints, "simulate_round": simulate_round,
    "net_capability": net_capability, "score_plan": score_plan,
    "fast_simulate_candidate": fast_simulate_candidate, "plan_evacuation_route": plan_evacuation_route,
    "query_knowledge": query_knowledge, "knowledge_stats": knowledge_stats,
}
