"""GLM 智能层(异步) —— OpenAI 兼容接口, 默认智谱 BigModel。

安全原则不变: GLM 只做解释/研判/问答, 一切安全关键数字由规则引擎产出;
提示词明确要求"只复述给定数字, 不得新增数值"; 任何失败回落确定性模板。
"""
from __future__ import annotations

import json
import os
from typing import Optional

import httpx


def _cfg() -> tuple[str, str, str]:
    base = os.environ.get("FIREOPS_LLM_BASE_URL", "https://open.bigmodel.cn/api/coding/paas/v4")
    key = os.environ.get("FIREOPS_LLM_API_KEY", "")
    model = os.environ.get("FIREOPS_LLM_MODEL", "glm-5.3-flash")
    return base, key, model


def llm_available() -> bool:
    return bool(_cfg()[1])


# 连续失败跟踪(GLM 限流/断连时前端告警)
_fail_streak = 0
_last_error: str | None = None


def _record_success() -> None:
    global _fail_streak, _last_error
    _fail_streak, _last_error = 0, None


def _record_failure(reason: str) -> None:
    global _fail_streak, _last_error
    _fail_streak += 1
    _last_error = reason[:120]


def llm_status() -> dict:
    base, key, model = _cfg()
    return {"connected": bool(key), "model": model if key else None,
            "provider": "zhipu-bigmodel-coding-plan" if key else "offline-deterministic", "base_url": base if key else None,
            "degraded": _fail_streak >= 2, "fail_streak": _fail_streak, "last_error": _last_error}


import re as _re


def audit_numbers(text: str, brief: str) -> list[str]:
    """GLM 数字事后审计: 输出中出现但输入数据里不存在的数字(护栏, 不阻断只标注)。"""
    if not text:
        return []
    allowed = {n for n in _re.findall(r"\d+(?:\.\d+)?", brief)}
    found = _re.findall(r"\d+(?:\.\d+)?", text)
    unknown = []
    for n in found:
        if n in allowed or float(n) in {float(a) for a in allowed}:
            continue
        if n not in unknown:
            unknown.append(n)
    return unknown[:6]


async def _post_chat(body: dict, timeout: float) -> dict | None:
    """底层 HTTP 调用, 失败返回 None; 连续失败计入降级跟踪。"""
    base, key, _ = _cfg()
    if not key:
        return None
    if os.environ.get("FIREOPS_LLM_THINKING", "disabled") == "disabled":
        body.setdefault("thinking", {"type": "disabled"})  # 思考型模型(GLM-5.x)直出结论, 演示节奏更快
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base}/chat/completions", content=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {key}"})
            response.raise_for_status()
            payload = response.json()
        _record_success()
        return payload
    except Exception as error:
        _record_failure(f"{type(error).__name__}: {error}")
        return None


async def glm_chat(system: str, user: str, max_tokens: int = 300, temperature: float = 0.3,
                   timeout: float = 25.0) -> Optional[str]:
    """调用 GLM, 返回文本; 失败返回 None(调用方回落确定性模板)。"""
    payload = await _post_chat({"model": _cfg()[2], "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature, "max_tokens": max_tokens}, timeout)
    if not payload:
        return None
    try:
        text = (payload["choices"][0]["message"].get("content") or "").strip()
        return text or None
    except Exception:
        return None


SAFETY_RULE = ("你是森林火灾无人机调度系统的智能体。铁律: 只允许解释和复述用户提供的数据, "
               "严禁编造或新增任何数值(FLP/SOC/时间/架次等必须来自给定数据)。")


async def agent_analysis(agent_name: str, role: str, data_brief: str, topic: str,
                          max_tokens: int = 200) -> Optional[str]:
    """Agent 关键节点的 GLM 研判: 附带知识库接地片段, 输出简短研判意见。"""
    from ..rules.knowledge import query_knowledge
    knowledge = query_knowledge(topic, top_k=2)
    refs = "\n".join(f"[{r['section']}] {r['text'][:180]}" for r in knowledge["results"][:2])
    system = f"{SAFETY_RULE}你是「{agent_name}」, 职责: {role}。用不超过120字给出专业研判意见, 可引用知识库依据。"
    user = f"当前数据:\n{data_brief}\n\n知识库参考:\n{refs or '无'}"
    return await glm_chat(system, user, max_tokens=max_tokens)


async def glm_explain(system: str, user: str, fallback: str, timeout: int = 8) -> str:
    """兼容旧接口: 带确定性回落的解释调用。"""
    result = await glm_chat(system, user, max_tokens=200, timeout=timeout)
    return result or fallback
