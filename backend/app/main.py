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

from . import config  # noqa: F401  # 加载 .env(GLM 密钥, 不入库), 必须先于其他模块
from .agents import AGENTS
from .agentkit.llm import SAFETY_RULE, audit_numbers, glm_chat, llm_status
from .domain import scenarios
from .domain.store import BOARD
from .rules import knowledge
from .rules import terrain
from .rules import tools as R
from .services.mission import SERVICE

app = FastAPI(title="火巡智策 · 多智能体无人机调度仿真", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class MissionCreate(BaseModel):
    scenario: str = "standard"
    image_name: str = "default"


class ChatQuestion(BaseModel):
    question: str


class ApprovalDecision(BaseModel):
    decision: str  # approve | reject | adjust
    feedback: str = ""
    people_status: str | None = None


@app.get("/api/terrain")
async def terrain_endpoint():
    """三维地形模型: 紫金山 SRTM 高程重采样为演示场景网格(100x70, 20m/格)。"""
    return terrain.terrain_model()


@app.get("/api/llm-status")
async def llm_status_endpoint():
    return llm_status()


@app.get("/api/knowledge")
async def knowledge_endpoint(query: str = "", top_k: int = 3):
    """知识库 Tool 的 HTTP 暴露: 三份内置文档(规则/思路/论文)的分块检索。"""
    if query:
        return knowledge.query_knowledge(query, top_k=top_k)
    return knowledge.knowledge_stats()


def _mission_brief(task_id: str) -> str:
    snap = BOARD.snapshot(task_id)
    fire = snap.get("fire") or {}
    plan = snap.get("plan") or {}
    cand = plan.get("candidate") or {}
    rounds = snap.get("rounds") or []
    last = rounds[-1] if rounds else {}
    fleet_line = "; ".join(f"{u['uav_id']}({u['status']},SOC {u['soc']:.0f}%,架次{u.get('sorties', 0)})"
                           for u in (snap.get("fleet") or []))
    inv = snap.get("inventory") or {}
    return (f"阶段: {snap['phase']}; 重规划 {snap['replans']} 次\n"
            f"火情: B={fire.get('total_flp', '—')} FLP, 增长 {fire.get('growth_flp_per_hour', '—')} FLP/h, "
            f"风 {fire.get('wind_speed', '—')} m/s({fire.get('wind_band_label', '—')}), 人员 {fire.get('people_status', '—')}\n"
            f"方案: {plan.get('plan_id', '—')} 灭火机 {cand.get('suppression_uavs', [])} "
            f"{cand.get('module', '')}, 预计 {plan.get('estimated_control_time', '—')}\n"
            f"轮次: {len(rounds)}, 最新 B={last.get('after_flp', '—')}, 事件 {last.get('events', [])[:3]}\n"
            f"机群: {fleet_line}\n"
            f"库存: 水 {inv.get('water_liters', '—')}L, W20模块 {inv.get('water_modules_w20', '—')}, "
            f"电池 {inv.get('battery_packs', '—')}组")


@app.post("/api/missions/{task_id}/chat")
async def mission_chat(task_id: str, payload: ChatQuestion):
    """指挥员问答: GLM 基于黑板实时数据 + 内置知识库回答; 数字仍以规则引擎为准。"""
    try:
        BOARD.require(task_id)
    except KeyError:
        raise HTTPException(404, "mission not found")
    question = payload.question.strip()
    if not question:
        raise HTTPException(400, "question 不能为空")
    BOARD.post_message(task_id, "HUMAN_ASK", "human", "commander", question)
    brief = _mission_brief(task_id)
    refs = knowledge.query_knowledge(question, top_k=2)
    knowledge_text = "\n".join(f"[{r['source_name']}·{r['section']}] {r['text'][:200]}" for r in refs["results"])
    answer = await glm_chat(
        f"{SAFETY_RULE}你是「火巡智策」指挥中心的智能参谋。回答指挥员关于当前任务的问题: "
        f"优先依据任务实时数据, 其次引用知识库(团队规则/思路/PWM-Net论文), 没有的信息就明说没有。不超过160字。",
        f"任务实时数据:\n{brief}\n\n知识库检索:\n{knowledge_text or '无相关片段'}\n\n指挥员提问: {question}",
        max_tokens=260)
    if not answer:
        answer = (f"(GLM 未接入, 确定性回答)当前阶段 {BOARD.snapshot(task_id)['phase']}。"
                  + brief.replace("\n", "; "))
    # 数字事后审计: GLM 回答中出现但黑板数据里不存在的数字 → 标注提醒
    unknown = audit_numbers(answer, brief + knowledge_text)
    if unknown:
        answer = f"{answer}\n⚠ 数字审计:{', '.join(unknown)} 未见于实时数据,请以面板数字为准。"
    message = BOARD.post_message(task_id, "AGENT_REPLY", "commander", "human", answer,
                                 {"llm": llm_status()["model"] if answer else None, "grounded": "blackboard+knowledge",
                                  "audit_flag": unknown or None})
    return {"answer": answer, "message": message.model_dump(), "llm": llm_status(), "audit": unknown}


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
    presets = [{"id": key, **value} for key, value in scenarios.SCENARIOS.items()]
    return {"scenarios": [{"id": "random", "label": "🎲 随机火情 · 每局不同(Agent 自主研判)"}] + presets}


@app.get("/api/fleet")
async def fleet():
    return R.load_json("data/fleet.json")


@app.get("/api/inventory")
async def inventory():
    return R.load_json("data/inventory.json")


@app.post("/api/missions")
async def create_mission(payload: MissionCreate):
    if payload.scenario != "random" and payload.scenario not in scenarios.SCENARIOS:
        raise HTTPException(400, f"未知场景 {payload.scenario}, 可选: random 或 {list(scenarios.SCENARIOS)}")
    task_id = await SERVICE.start(payload.scenario, payload.image_name)
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
    try:
        await SERVICE.approve(task_id, payload.decision, payload.feedback, payload.people_status)
    except ValueError as error:
        raise HTTPException(409, str(error))
    return {"task_id": task_id, "snapshot": BOARD.snapshot(task_id)}


@app.get("/api/missions/{task_id}/events")
async def mission_events(task_id: str, last_seq: int = 0):
    try:
        BOARD.require(task_id)
    except KeyError:
        raise HTTPException(404, "mission not found")

    async def stream():
        seq = last_seq
        last_rev: int | None = None
        keepalive = 0.0
        while True:
            mission = BOARD.require(task_id)
            fresh = [m for m in mission["messages"] if m["seq"] > seq]
            for message in fresh:
                seq = message["seq"]
                yield f"event: agent_message\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"
            snapshot = BOARD.snapshot(task_id)
            # 快照事件仅在 rev 变化时发送(空闲连接不再每 0.4s 重复推送)
            if snapshot["rev"] != last_rev:
                last_rev = snapshot["rev"]
                payload = json.dumps({"phase": snapshot["phase"], "rev": snapshot["rev"],
                                      "round_index": len(snapshot["rounds"])}, ensure_ascii=False)
                yield f"event: snapshot\ndata: {payload}\n\n"
            if fresh:
                keepalive = 0.0
                await asyncio.sleep(0.4)  # 活跃期高频跟进
            else:
                keepalive += 1.0
                if keepalive > 15:
                    yield ": keepalive\n\n"
                    keepalive = 0.0
                await asyncio.sleep(1.0)  # 空闲期降频(等待审批/长仿真时)
            if snapshot["phase"] in {"completed", "rejected", "error"} and not fresh:
                yield "event: done\ndata: {}\n\n"
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# 生产模式: 托管前端构建产物(开发模式走 vite 5173)
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
