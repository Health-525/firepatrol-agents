"""任务服务: 后台驱动 LangGraph 任务图; 审批通过 Command 恢复中断。"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from ..agents import build_mission_graph
from ..domain.store import BOARD


class MissionService:
    def __init__(self) -> None:
        self.graph = build_mission_graph()
        self.runners: Dict[str, asyncio.Task] = {}

    # ---- 启动新任务: 建案并后台推进到审批中断 ----
    async def start(self, scenario: str, image_name: str = "default", scene_id: str = "zijing-mountain-01") -> str:
        task_id = BOARD.new_mission(scene_id, image_name, scenario)
        await self._launch(task_id, {"task_id": task_id, "scene_id": scene_id,
                                     "image_name": image_name, "scenario": scenario})
        return task_id

    # ---- 审批: resume 中断的图 ----
    async def approve(self, task_id: str, decision: str, feedback: str = "",
                      people_status: Optional[str] = None) -> None:
        mission = BOARD.require(task_id)
        # 同步 CAS: 检查并立刻离开待审批态, 阻止并发重复审批穿过检查(单事件循环内无让出点)
        if mission.get("phase") != "awaiting_approval":
            raise ValueError(f"当前阶段 {mission['phase']} 不可审批")
        mission["phase"] = "analyzing"
        mission["rev"] += 1
        resume = Command(resume={"decision": decision, "feedback": feedback, "people_status": people_status})
        await self._launch(task_id, resume)

    # ---- 手动触发重规划(演示用) ----
    async def force_replan(self, task_id: str, reason: str = "用户手动触发重规划") -> None:
        mission = BOARD.require(task_id)
        if mission.get("phase") != "executing":
            raise ValueError("只有执行中的任务可以触发重规划")
        mission["messages"].append({"seq": len(mission["messages"]) + 1, "msg_type": "REPLAN_TRIGGER",
                                    "frm": "human", "to": "commander", "content": reason, "data": {}})
        # 执行轮次会在下一轮读取风档/FLP 触发; 用户手动触发通过场景脚本之外的方式暂不强制中断

    async def _launch(self, task_id: str, payload: Any) -> None:
        previous = self.runners.get(task_id)
        if previous and not previous.done():
            await asyncio.wait_for(asyncio.shield(previous), timeout=30)
        config = {"configurable": {"thread_id": task_id}}

        async def runner() -> None:
            try:
                await self.graph.ainvoke(payload, config=config)
            except GraphInterrupt:
                pass  # 审批门: 正常挂起, 等待 Command(resume)
            except Exception as error:  # noqa: BLE001
                BOARD.update(task_id, phase="error")
                BOARD.post_message(task_id, "ERROR", "system", "human", f"任务执行异常: {error}")

        self.runners[task_id] = asyncio.create_task(runner())


SERVICE = MissionService()
