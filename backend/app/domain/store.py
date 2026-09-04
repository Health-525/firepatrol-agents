"""黑板 / 任务存储: 共享状态 + Agent 消息流 + 轮次记录, 是前后端与多 Agent 的唯一事实源。

并发说明: 本类全部方法为同步函数且无 await 让出点, 在单事件循环(uvicorn 单 worker)内天然原子;
跨 worker 部署需换外部存储(Redis/DB)并引入真正的锁。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from ..domain.models import (AgentMessage, FireState, MissionPlan, MissionReport, RoundRecord, UAVState)

MAX_MISSIONS = 50  # 黑板保留的任务数上限(超限淘汰最早的已结束任务, 防内存无界)


class Blackboard:
    def __init__(self) -> None:
        self.missions: Dict[str, Dict[str, Any]] = {}

    def new_mission(self, scene_id: str, image_name: str, scenario: str) -> str:
        self._evict_finished()
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

    def _evict_finished(self) -> None:
        """超上限时淘汰最早的已结束任务; 全部在进行中则不淘汰(宁多勿丢)。"""
        overflow = len(self.missions) - MAX_MISSIONS + 1
        if overflow <= 0:
            return
        finished = sorted((m for m in self.missions.values() if m["phase"] in {"completed", "rejected", "error"}),
                          key=lambda m: m["created_at"])
        for mission in finished[:overflow]:
            del self.missions[mission["task_id"]]

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
            "support_plan": mission.get("support_plan"),  # 含疏散路线/人群进度, 前端地图依赖
            "search": mission.get("search"),  # 搜索阶段覆盖度(发现前 fire 为空)
            "last_judgment": mission.get("last_judgment"),  # 最近一轮自主研判(继续/重规划/终止 + 理由)
            "approval_request": mission["approval_request"], "report": mission["report"],
        }


BOARD = Blackboard()
