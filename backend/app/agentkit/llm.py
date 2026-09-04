"""可选 LLM 解释层 —— OpenAI 兼容接口(GLM/DeepSeek/Ollama 均可), 无配置时回落确定性模板。"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional


def llm_available() -> bool:
    return bool(os.environ.get("FIREOPS_LLM_API_KEY") and os.environ.get("FIREOPS_LLM_BASE_URL"))


def llm_explain(system: str, user: str, fallback: str, timeout: int = 8) -> str:
    """LLM 只做解释性文字; 任何失败都回落到确定性 fallback, 演示永不因 LLM 中断。"""
    if not llm_available():
        return fallback
    base = os.environ["FIREOPS_LLM_BASE_URL"].rstrip("/")
    model = os.environ.get("FIREOPS_LLM_MODEL", "glm-4-flash")
    body = json.dumps({"model": model, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.3, "max_tokens": 220}).encode()
    request = urllib.request.Request(base + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {os.environ['FIREOPS_LLM_API_KEY']}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
        text = payload["choices"][0]["message"]["content"].strip()
        return text or fallback
    except Exception:
        return fallback
