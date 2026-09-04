"""黑板 / 任务存储: 共享状态 + Agent 消息流 + 轮次记录, 是前后端与多 Agent 的唯一事实源。"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional

from ..domain.models import (AgentMessage, FireState, MissionPlan, MissionReport, RoundRecord, UAVState)


class Blackboard:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.missions: Dict[str, Dict[str, Any]] = {}

    def new_mission(self, scene_id: str, image_name: str, scenario: str) -> str:
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        self.missions[task_id] = {
            "task_id": task_id,
            "scene_id": scene_id,
            "image_name": image_name,
            "scenario": scenario,
            "phase": "created",  # created|analyzing|awaiting_approval|executing|replanning|completed|rejected
            "created_at": time.time(),
            "fire": None,
            "fleet": [],
            "inventory": {},
            "environment": {},
            "candidates": [],
            "plan": None,
            "plan_history": [],
            "rounds": [],
            "messages": [],
            "report": None,
            "approval_request": None,
            "replans": 0,
            "rev": 0,
        }
        return task_id

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.missions.get(task_id)

    def require(self, task_id: str) -> Dict[str, Any]:
        mission = self.missions.get(task_id)
        if mission is None:
            raise KeyError(f"mission not found: {task_id}")
        return mission

    def update(self, task_id: str, **fields: Any) -> None:
        mission = self.require(task_id)
        mission.update(fields)
        mission["rev"] += 1

    def post_message(self, task_id: str, msg_type: str, frm: str, to: str, content: str, data: Optional[Dict[str, Any]] = None) -> AgentMessage:
        mission = self.require(task_id)
        message = AgentMessage(seq=len(mission["messages"]) + 1, t=round(time.time(), 2), task_id=task_id,
                               msg_type=msg_type, frm=frm, to=to, content=content, data=data or {})
        mission["messages"].append(message.model_dump())
        mission["rev"] += 1
        return message

    def snapshot(self, task_id: str) -> Dict[str, Any]:
        mission = self.require(task_id)
        return {
            "task_id": task_id, "phase": mission["phase"], "rev": mission["rev"], "replans": mission["replans"],
            "scene_id": mission["scene_id"], "scenario": mission["scenario"],
            "fire": mission["fire"], "fleet": mission["fleet"], "inventory": mission["inventory"],
            "environment": mission["environment"], "candidates": mission["candidates"],
            "plan": mission["plan"], "rounds": mission["rounds"], "messages": mission["messages"],
            "approval_request": mission["approval_request"], "report": mission["report"],
        }


BOARD = Blackboard()
