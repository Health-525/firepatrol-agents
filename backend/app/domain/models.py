"""领域模型: 黑板上的结构化契约。数值字段一律由规则引擎产出。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Position(BaseModel):
    x: float = 0
    y: float = 0
    z: float = 0


class FireCell(BaseModel):
    cx: int
    cy: int
    intensity: float = 1
    flp: float = 0


class FireState(BaseModel):
    fire_id: str = "fire_01"
    fire_type: str = "vegetation"
    cells: List[FireCell] = []
    total_flp: float = 0
    growth_flp_per_hour: float = 0
    wind_speed: float = 0
    wind_direction_deg: float = 315
    wind_band: int = 0
    wind_band_label: str = ""
    slope_deg: float = 0
    fuel_type: str = "general_forest"
    intensity_level: int = 2
    people_status: str = "unknown"  # unknown | confirmed | absent
    detected_classes: List[str] = []
    confidence: float = 0.0
    source: str = "rules"


class UAVState(BaseModel):
    uav_id: str
    subgroup: str
    status: str = "available"
    position: Position = Position()
    soc: float = 100
    payload_capacity_kg: float = 0
    payload_module: str = "none"
    agent_remaining: float = 0
    agent_unit: str = "L"
    speed_mps: float = 10
    signal: float = 90
    health: float = 100
    assigned_task: Optional[str] = None
    target: Optional[Position] = None
    route: List[Position] = []
    sorties: int = 0
    swaps: int = 0
    refills: int = 0
    soc_cost_total: float = 0


class CandidateUAV(BaseModel):
    uav_id: str
    role: str
    task: str
    module: str = "none"
    soc_end_estimate: float = 0


class PlanCandidate(BaseModel):
    candidate_id: str
    module: str = "water_20l"
    suppression_uavs: List[str] = []
    recon_uavs: List[str] = []
    support_assignments: List[Dict[str, Any]] = []
    sim: Dict[str, Any] = {}
    score: Dict[str, Any] = {}
    feasible: bool = False
    explanation: str = ""


class MissionPlan(BaseModel):
    plan_id: str
    version: int = 1
    candidate: PlanCandidate
    battery_plan: Dict[str, Any] = {}
    water_source_plan: Dict[str, Any] = {}
    people_branch: Dict[str, Any] = {}
    estimated_control_time: str = ""
    feasibility: str = "can_control"
    resource_gap: Dict[str, Any] = {}
    replan_trigger: Dict[str, Any] = {}


class RoundUAV(BaseModel):
    uav_id: str
    status: str
    position: Position
    soc: float
    agent_remaining: float
    sorties: int
    swaps: int
    refills: int
    event: str = ""


class RoundRecord(BaseModel):
    round_index: int
    sim_minutes: float
    before_flp: float
    growth_flp: float
    suppression_flp: float
    after_flp: float
    uavs: List[RoundUAV] = []
    inventory: Dict[str, Any] = {}
    events: List[str] = []
    wind_speed: float = 0


class AgentMessage(BaseModel):
    seq: int = 0
    t: float = 0
    task_id: str = ""
    msg_type: str = "INFO"  # TASK_ASSIGN | FINDING | PLAN_PROPOSAL | SIM_RESULT | APPROVAL_REQ | APPROVAL_DECISION | ROUND | REPLAN_TRIGGER | REPORT | INFO | ERROR
    frm: str = ""
    to: str = ""
    content: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)


class MissionReport(BaseModel):
    report_id: str
    task_id: str
    conclusion: str = ""
    rounds_total: int = 0
    flp_initial: float = 0
    flp_final: float = 0
    material_used: float = 0
    swaps: int = 0
    refills: int = 0
    replans: int = 0
    timeline: List[Dict[str, Any]] = []
