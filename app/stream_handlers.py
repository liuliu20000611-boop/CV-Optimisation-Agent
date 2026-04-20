"""SSE stream generators for analyze / optimize / reanalyze / refine."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.cache import clear_expired, get_cached, set_cached
from app.config import Settings
from app.llm import (
    _parse_analysis_json,
    analysis_repair_user_message,
    analysis_user_message,
    chat_completion,
    optimize_user_message,
    run_analysis,
    run_optimize,
    run_reanalyze,
    run_refine_optimize,
    stream_chat_completion,
)
from app.prompts import (
    ANALYSIS_REPAIR_SYSTEM,
    ANALYSIS_SYSTEM,
    OPTIMIZE_SYSTEM,
    REFINE_OPTIMIZE_SYSTEM,
    reanalysis_user_message,
    refine_optimize_user_message,
)
from app.sse_utils import sse_data

logger = logging.getLogger(__name__)

_NO_API_KEY_MSG = (
    "未配置 DEEPSEEK_API_KEY，无法调用大模型。请在环境变量或项目根目录 .env 中设置后重启服务。"
)


def _sse_no_api_key() -> str:
    return sse_data({"type": "error", "message": _NO_API_KEY_MSG, "code": "NO_API_KEY"})


async def stream_analyze(
    settings: Settings,
    resume: str,
    jd: str,
    *,
    use_cache: bool = True,
) -> AsyncIterator[str]:
    if use_cache and settings.enable_analysis_cache:
        clear_expired(float(settings.analysis_cache_ttl_seconds))
        cached = get_cached(resume, jd, float(settings.analysis_cache_ttl_seconds))
        if cached is not None:
            yield sse_data({"type": "done", "result": cached.model_dump(), "cached": True})
            return

    if not settings.deepseek_api_key.strip():
        yield _sse_no_api_key()
        return

    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM},
        {"role": "user", "content": analysis_user_message(resume, jd)},
    ]
    buf: list[str] = []
    try:
        async for delta in stream_chat_completion(settings, messages, temperature=0.2):
            buf.append(delta)
            yield sse_data({"type": "delta", "text": delta})
        full = "".join(buf)
        try:
            result = _parse_analysis_json(full)
        except ValueError:
            logger.warning("流式分析 JSON 解析失败，走修复提示")
            repair_user = analysis_repair_user_message(full)
            content2 = await chat_completion(
                settings,
                system=ANALYSIS_REPAIR_SYSTEM,
                user=repair_user,
                temperature=0.0,
            )
            result = _parse_analysis_json(content2)
        if settings.enable_analysis_cache:
            set_cached(resume, jd, result)
        yield sse_data({"type": "done", "result": result.model_dump()})
    except Exception as e:
        logger.warning("流式分析失败，非流式兜底: %s", e)
        try:
            result = await run_analysis(settings, resume, jd)
            yield sse_data({"type": "done", "result": result.model_dump(), "fallback": True})
        except Exception as e2:
            logger.exception("分析兜底失败: %s", e2)
            yield sse_data(
                {
                    "type": "error",
                    "message": "分析暂时不可用，请稍后重试或缩短输入。",
                    "code": "ANALYZE_FAILED",
                }
            )


async def stream_reanalyze(
    settings: Settings,
    resume: str,
    jd: str,
    prev_h: list[str],
    prev_g: list[str],
    prev_s: list[str],
    user_feedback: str | None,
) -> AsyncIterator[str]:
    if not settings.deepseek_api_key.strip():
        yield _sse_no_api_key()
        return

    user = reanalysis_user_message(resume, jd, prev_h, prev_g, prev_s, user_feedback)
    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM},
        {"role": "user", "content": user},
    ]
    buf: list[str] = []
    try:
        async for delta in stream_chat_completion(settings, messages, temperature=0.35):
            buf.append(delta)
            yield sse_data({"type": "delta", "text": delta})
        full = "".join(buf)
        try:
            result = _parse_analysis_json(full)
        except ValueError:
            repair_user = analysis_repair_user_message(full)
            content2 = await chat_completion(
                settings,
                system=ANALYSIS_REPAIR_SYSTEM,
                user=repair_user,
                temperature=0.0,
            )
            result = _parse_analysis_json(content2)
        yield sse_data({"type": "done", "result": result.model_dump()})
    except Exception as e:
        logger.warning("流式返修分析失败，兜底: %s", e)
        try:
            result = await run_reanalyze(settings, resume, jd, prev_h, prev_g, prev_s, user_feedback)
            yield sse_data({"type": "done", "result": result.model_dump(), "fallback": True})
        except Exception as e2:
            logger.exception("返修分析失败: %s", e2)
            yield sse_data({"type": "error", "message": "返修分析失败，请稍后重试。", "code": "REANALYZE_FAILED"})


async def stream_optimize(
    settings: Settings,
    resume: str,
    jd: str,
    highlights: list[str],
    gaps: list[str],
    suggestions: list[str],
) -> AsyncIterator[str]:
    if not settings.deepseek_api_key.strip():
        yield _sse_no_api_key()
        return

    user = optimize_user_message(resume, jd, highlights, gaps, suggestions)
    messages = [
        {"role": "system", "content": OPTIMIZE_SYSTEM},
        {"role": "user", "content": user},
    ]
    buf: list[str] = []
    try:
        async for delta in stream_chat_completion(settings, messages, temperature=0.5):
            buf.append(delta)
            yield sse_data({"type": "delta", "text": delta})
        full = "".join(buf).strip()
        yield sse_data({"type": "done", "text": full})
    except Exception as e:
        logger.warning("流式优化失败，非流式兜底: %s", e)
        try:
            text = await run_optimize(settings, resume, jd, highlights, gaps, suggestions)
            yield sse_data({"type": "done", "text": text, "fallback": True})
        except Exception as e2:
            logger.exception("优化兜底失败: %s", e2)
            yield sse_data({"type": "error", "message": "生成优化简历失败，请稍后重试。", "code": "OPTIMIZE_FAILED"})


async def stream_refine_optimize(
    settings: Settings,
    resume: str,
    jd: str,
    highlights: list[str],
    gaps: list[str],
    suggestions: list[str],
    optimized_resume: str,
    user_feedback: str | None,
) -> AsyncIterator[str]:
    if not settings.deepseek_api_key.strip():
        yield _sse_no_api_key()
        return

    user = refine_optimize_user_message(
        resume, jd, highlights, gaps, suggestions, optimized_resume, user_feedback
    )
    messages = [
        {"role": "system", "content": REFINE_OPTIMIZE_SYSTEM},
        {"role": "user", "content": user},
    ]
    buf: list[str] = []
    try:
        async for delta in stream_chat_completion(settings, messages, temperature=0.45):
            buf.append(delta)
            yield sse_data({"type": "delta", "text": delta})
        full = "".join(buf).strip()
        yield sse_data({"type": "done", "text": full})
    except Exception as e:
        logger.warning("流式返修简历失败，兜底: %s", e)
        try:
            text = await run_refine_optimize(
                settings, resume, jd, highlights, gaps, suggestions, optimized_resume, user_feedback
            )
            yield sse_data({"type": "done", "text": text, "fallback": True})
        except Exception as e2:
            logger.exception("返修简历失败: %s", e2)
            yield sse_data({"type": "error", "message": "返修失败，请稍后重试。", "code": "REFINE_FAILED"})
