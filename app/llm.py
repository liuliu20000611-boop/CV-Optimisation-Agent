"""DeepSeek OpenAI-compatible chat completions — sync, streaming, reanalyze, refine."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings
from app.prompts import (
    ANALYSIS_REPAIR_SYSTEM,
    ANALYSIS_SYSTEM,
    OPTIMIZE_SYSTEM,
    REFINE_OPTIMIZE_SYSTEM,
    TEMPLATE_REWRITE_ERROR_SYSTEM,
    TEMPLATE_REWRITE_SYSTEM,
    analysis_repair_user_message,
    analysis_user_message,
    optimize_user_message,
    reanalysis_user_message,
    refine_optimize_user_message,
    template_rewrite_user_message,
    template_rewrite_error_user_message,
)
from app.schemas import AnalysisResult

logger = logging.getLogger(__name__)


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _strip_code_fence(text: str) -> str:
    """Strip optional markdown code fence from model output."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    t = re.sub(r"^```(?:[a-zA-Z0-9_-]+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _parse_analysis_json(content: str) -> AnalysisResult:
    raw = _strip_json_fence(content)
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(raw[start : end + 1])
        else:
            raise ValueError("模型返回无法解析为 JSON") from None

    if not isinstance(data, dict):
        raise ValueError("分析结果格式错误：根节点须为对象")

    def to_list(key: str) -> list[str]:
        v = data.get(key)
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str):
            return [line.strip() for line in v.splitlines() if line.strip()]
        return [str(v)]

    return AnalysisResult(
        highlights=to_list("highlights"),
        gaps=to_list("gaps"),
        suggestions=to_list("suggestions"),
    )


async def chat_completion(
    settings: Settings,
    *,
    system: str,
    user: str,
    temperature: float = 0.3,
) -> str:
    url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    timeout = httpx.Timeout(settings.request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        body = resp.json()

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("API 返回缺少 choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content or not isinstance(content, str):
        raise RuntimeError("API 返回缺少正文内容")
    return content


async def stream_chat_completion(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
) -> AsyncIterator[str]:
    """Yield text deltas from OpenAI-compatible streaming API."""
    url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.deepseek_model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    timeout = httpx.Timeout(settings.request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                line = line.strip()
                if line == "data: [DONE]":
                    break
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content


async def run_analysis(settings: Settings, resume: str, jd: str) -> AnalysisResult:
    user = analysis_user_message(resume, jd)
    content = await chat_completion(
        settings,
        system=ANALYSIS_SYSTEM,
        user=user,
        temperature=0.2,
    )
    try:
        return _parse_analysis_json(content)
    except ValueError as e:
        logger.warning("分析 JSON 首次解析失败，尝试修复重试: %s", e)
        repair_user = analysis_repair_user_message(content)
        content2 = await chat_completion(
            settings,
            system=ANALYSIS_REPAIR_SYSTEM,
            user=repair_user,
            temperature=0.0,
        )
        return _parse_analysis_json(content2)


async def run_optimize(
    settings: Settings,
    resume: str,
    jd: str,
    highlights: list[str],
    gaps: list[str],
    suggestions: list[str],
) -> str:
    user = optimize_user_message(resume, jd, highlights, gaps, suggestions)
    content = await chat_completion(
        settings,
        system=OPTIMIZE_SYSTEM,
        user=user,
        temperature=0.5,
    )
    text = content.strip()
    if not text:
        raise RuntimeError("模型未返回优化正文")
    return text


async def run_reanalyze(
    settings: Settings,
    resume: str,
    jd: str,
    prev_h: list[str],
    prev_g: list[str],
    prev_s: list[str],
    user_feedback: str | None,
) -> AnalysisResult:
    user = reanalysis_user_message(resume, jd, prev_h, prev_g, prev_s, user_feedback)
    content = await chat_completion(
        settings,
        system=ANALYSIS_SYSTEM,
        user=user,
        temperature=0.35,
    )
    try:
        return _parse_analysis_json(content)
    except ValueError as e:
        logger.warning("返修分析 JSON 解析失败，尝试修复: %s", e)
        repair_user = analysis_repair_user_message(content)
        content2 = await chat_completion(
            settings,
            system=ANALYSIS_REPAIR_SYSTEM,
            user=repair_user,
            temperature=0.0,
        )
        return _parse_analysis_json(content2)


async def run_refine_optimize(
    settings: Settings,
    resume: str,
    jd: str,
    highlights: list[str],
    gaps: list[str],
    suggestions: list[str],
    optimized_resume: str,
    user_feedback: str | None,
) -> str:
    user = refine_optimize_user_message(
        resume, jd, highlights, gaps, suggestions, optimized_resume, user_feedback
    )
    content = await chat_completion(
        settings,
        system=REFINE_OPTIMIZE_SYSTEM,
        user=user,
        temperature=0.45,
    )
    text = content.strip()
    if not text:
        raise RuntimeError("模型未返回返修正文")
    return text


async def run_template_rewrite(
    settings: Settings,
    optimized_resume: str,
    template_tex: str,
    user_feedback: str | None = None,
    previous_tex: str | None = None,
) -> str:
    user = template_rewrite_user_message(
        optimized_resume,
        template_tex,
        user_feedback,
        previous_tex,
    )
    content = await chat_completion(
        settings,
        system=TEMPLATE_REWRITE_SYSTEM,
        user=user,
        temperature=0.2,
    )
    text = _strip_code_fence(content)
    if not text:
        raise RuntimeError("模型未返回模板改写结果")
    if "\\begin{document}" not in text or "\\end{document}" not in text:
        raise RuntimeError("模型返回的 TeX 结构不完整")
    return text


async def run_template_rewrite_error_explain(settings: Settings, error_text: str) -> str:
    """Turn compiler/runtime error into user-friendly Chinese guidance."""
    content = await chat_completion(
        settings,
        system=TEMPLATE_REWRITE_ERROR_SYSTEM,
        user=template_rewrite_error_user_message(error_text),
        temperature=0.1,
    )
    text = _strip_code_fence(content)
    return text.strip() or "模板改写失败，请检查输入内容与模板兼容性后重试。"


async def accumulate_stream(
    gen: AsyncIterator[str],
) -> str:
    parts: list[str] = []
    async for d in gen:
        parts.append(d)
    return "".join(parts)
