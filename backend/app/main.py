"""FastAPI 入口: 任务闭环 API + SSE 事件流 + 前端静态托管。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agents import AGENTS
from .domain import scenarios
from .domain.store import BOARD
from .rules import tools as R
from .services.mission import SERVICE

app = FastAPI(title="火巡智策 · 多智能体无人机调度仿真", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class MissionCreate(BaseModel):
    scenario: str = "standard"
    image_name: str = "default"


class ApprovalDecision(BaseModel):
    decision: str  # approve | reject | adjust
    feedback: str = ""
    people_status: str | None = None


@app.get("/api/health")
async def health():
    return {"status": "ok", "system": "firepatrol-agents", "framework": "LangGraph", "agents": len(AGENTS)}


@app.get("/api/agents")
async def agent_profiles():
    return {"agents": [a.profile() for a in AGENTS],
            "architecture": "commander(协调) + recon/suppression/support(三子群) + simulator(裁判) + approver(审批)",
            "framework": "LangGraph StateGraph + interrupt 审批门 + MemorySaver"}


@app.get("/api/scene")
async def scene():
    return R.load_json("data/scene.json")


@app.get("/api/scenarios")
async def scenario_list():
    return {"scenarios": [{"id": key, **value} for key, value in scenarios.SCENARIOS.items()]}


@app.get("/api/fleet")
async def fleet():
    return R.load_json("data/fleet.json")


@app.get("/api/inventory")
async def inventory():
    return R.load_json("data/inventory.json")


@app.post("/api/missions")
async def create_mission(payload: MissionCreate):
    if payload.scenario not in scenarios.SCENARIOS:
        raise HTTPException(400, f"未知场景 {payload.scenario}, 可选: {list(scenarios.SCENARIOS)}")
    task_id = await SERVICE.start(payload.scenario, payload.image_name)
    await asyncio.sleep(0.2)
    return {"task_id": task_id, "snapshot": BOARD.snapshot(task_id)}


@app.get("/api/missions")
async def list_missions():
    return {"missions": [{"task_id": m["task_id"], "phase": m["phase"], "scenario": m["scenario"],
                          "created_at": m["created_at"]} for m in BOARD.missions.values()]}


@app.get("/api/missions/{task_id}")
async def mission_snapshot(task_id: str):
    try:
        return BOARD.snapshot(task_id)
    except KeyError:
        raise HTTPException(404, "mission not found")


@app.post("/api/missions/{task_id}/approval")
async def approve(task_id: str, payload: ApprovalDecision):
    if payload.decision not in {"approve", "reject", "adjust"}:
        raise HTTPException(400, "decision 必须是 approve/reject/adjust")
    try:
        BOARD.require(task_id)
    except KeyError:
        raise HTTPException(404, "mission not found")
    if BOARD.require(task_id).get("phase") != "awaiting_approval":
        raise HTTPException(409, f"当前阶段 {BOARD.require(task_id)['phase']} 不可审批")
    await SERVICE.approve(task_id, payload.decision, payload.feedback, payload.people_status)
    await asyncio.sleep(0.2)
    return {"task_id": task_id, "snapshot": BOARD.snapshot(task_id)}


@app.get("/api/missions/{task_id}/events")
async def mission_events(task_id: str, last_seq: int = 0):
    try:
        BOARD.require(task_id)
    except KeyError:
        raise HTTPException(404, "mission not found")

    async def stream():
        seq = last_seq
        keepalive = 0.0
        while True:
            mission = BOARD.require(task_id)
            fresh = [m for m in mission["messages"] if m["seq"] > seq]
            for message in fresh:
                seq = message["seq"]
                yield f"event: agent_message\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"
            snapshot = BOARD.snapshot(task_id)
            yield f"event: snapshot\ndata: {json.dumps({'phase': snapshot['phase'], 'rev': snapshot['rev'], 'round_index': len(snapshot['rounds'])}, ensure_ascii=False)}\n\n"
            if fresh:
                keepalive = 0.0
            else:
                keepalive += 0.4
                if keepalive > 15:
                    yield ": keepalive\n\n"
                    keepalive = 0.0
            if snapshot["phase"] in {"completed", "rejected", "error"} and not fresh:
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# 生产模式: 托管前端构建产物(开发模式走 vite 5173)
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
