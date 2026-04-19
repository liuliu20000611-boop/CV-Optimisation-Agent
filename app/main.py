"""FastAPI application: resume analysis & optimization via DeepSeek API."""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from urllib.parse import quote
from fastapi.staticfiles import StaticFiles

from app.cache import clear_expired, get_cached, set_cached
from app.config import get_settings
from app.llm import run_analysis, run_optimize
from app.stream_handlers import (
    stream_analyze,
    stream_optimize,
    stream_reanalyze,
    stream_refine_optimize,
)
from app.middleware import RateLimitMiddleware, RequestIdMiddleware
from app.export_resume import build_docx_bytes, build_pdf_bytes, build_plain_bytes, find_pdf_font
from app.resume_extract import ResumeExtractError, extract_resume_plain_text
from app.resume_source import detect_resume_source_kind
from app.schemas import (
    AnalyzeRequest,
    EstimateResponse,
    ExportRequest,
    OptimizeRequest,
    OptimizeResponse,
    ReanalyzeRequest,
    RefineOptimizeRequest,
)
from app.text_normalize import normalize_document_text, normalize_jd_text

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

APP_VERSION = "2.0.0"


def _configure_logging() -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
            },
            "root": {"level": "INFO", "handlers": ["console"]},
            "loggers": {
                "uvicorn": {"level": "INFO"},
                "uvicorn.access": {"level": "WARNING"},
                "resume_agent": {"level": "INFO"},
            },
        }
    )


_configure_logging()

app = FastAPI(
    title="简历优化 Agent",
    description="分析简历与 JD 匹配度，并在确认后生成优化简历。",
    version=APP_VERSION,
)


@app.on_event("startup")
async def startup_load_settings() -> None:
    """Fail fast if DEEPSEEK_API_KEY is missing or invalid."""
    get_settings()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Last added runs first on incoming request: RequestId -> RateLimit -> CORS -> routes
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIdMiddleware)


def _validate_lengths(resume: str, jd: str) -> None:
    settings = get_settings()
    if len(resume) > settings.max_resume_chars:
        raise HTTPException(
            status_code=400,
            detail=f"简历过长，请控制在 {settings.max_resume_chars} 字符以内",
        )
    if len(jd) > settings.max_jd_chars:
        raise HTTPException(
            status_code=400,
            detail=f"JD 过长，请控制在 {settings.max_jd_chars} 字符以内",
        )


def _approx_tokens(resume: str, jd: str) -> int:
    """Rough input token estimate for mixed Chinese/English (no tokenizer)."""
    n = len(resume) + len(jd)
    return max(1, int(n / 2.5))


def _prepare_resume_text(raw: str) -> str:
    """Normalize pasted or extracted resume for LLM and caching."""
    return normalize_document_text(raw.strip())


def _prepare_jd_text(raw: str) -> str:
    """Normalize job description (noise removal, consistent whitespace)."""
    return normalize_jd_text(raw.strip())


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


@app.post("/api/estimate", response_model=EstimateResponse)
async def estimate_tokens(body: AnalyzeRequest) -> EstimateResponse:
    """Approximate token count for resume+JD (heuristic; for cost awareness only)."""
    resume = _prepare_resume_text(body.resume)
    jd = _prepare_jd_text(body.jd)
    if not resume or not jd:
        raise HTTPException(status_code=400, detail="简历与 JD 均不能为空")
    _validate_lengths(resume, jd)
    return EstimateResponse(
        resume_chars=len(resume),
        jd_chars=len(jd),
        approx_input_tokens=_approx_tokens(resume, jd),
    )


@app.post("/api/analyze")
async def analyze(body: AnalyzeRequest, request: Request) -> Response:
    resume = _prepare_resume_text(body.resume)
    jd = _prepare_jd_text(body.jd)
    if not resume or not jd:
        raise HTTPException(status_code=400, detail="简历与 JD 均不能为空")
    _validate_lengths(resume, jd)
    settings = get_settings()

    if settings.enable_analysis_cache:
        clear_expired(float(settings.analysis_cache_ttl_seconds))
        cached = get_cached(resume, jd, float(settings.analysis_cache_ttl_seconds))
        if cached is not None:
            rid = getattr(request.state, "request_id", "")
            logger.info("analysis_cache_hit request_id=%s", rid)
            return JSONResponse(
                content=cached.model_dump(),
                headers={"X-Analysis-Cache": "HIT"},
            )

    try:
        result = await run_analysis(settings, resume, jd)
    except ValueError as e:
        logger.warning("分析解析失败: %s", e)
        raise HTTPException(status_code=502, detail="模型返回格式异常，请稍后重试或缩短输入") from e
    except httpx.HTTPStatusError as e:
        logger.warning("DeepSeek HTTP 错误: %s", e.response.status_code)
        raise HTTPException(
            status_code=502,
            detail="大模型服务暂时不可用，请检查密钥与网络后重试",
        ) from e
    except httpx.RequestError as e:
        logger.warning("网络错误: %s", e)
        raise HTTPException(status_code=502, detail="无法连接大模型服务，请稍后重试") from e
    except Exception as e:
        logger.exception("分析未预期错误")
        raise HTTPException(status_code=500, detail="服务内部错误") from e

    if settings.enable_analysis_cache:
        set_cached(resume, jd, result)

    return JSONResponse(
        content=result.model_dump(),
        headers={"X-Analysis-Cache": "MISS"},
    )


@app.post("/api/optimize", response_model=OptimizeResponse)
async def optimize(body: OptimizeRequest) -> OptimizeResponse:
    resume = _prepare_resume_text(body.resume)
    jd = _prepare_jd_text(body.jd)
    if not resume or not jd:
        raise HTTPException(status_code=400, detail="简历与 JD 均不能为空")
    _validate_lengths(resume, jd)
    settings = get_settings()
    try:
        text = await run_optimize(
            settings,
            resume,
            jd,
            body.highlights,
            body.gaps,
            body.suggestions,
        )
        return OptimizeResponse(optimized_resume=text)
    except httpx.HTTPStatusError as e:
        logger.warning("DeepSeek HTTP 错误: %s", e.response.status_code)
        raise HTTPException(
            status_code=502,
            detail="大模型服务暂时不可用，请检查密钥与网络后重试",
        ) from e
    except httpx.RequestError as e:
        logger.warning("网络错误: %s", e)
        raise HTTPException(status_code=502, detail="无法连接大模型服务，请稍后重试") from e
    except Exception as e:
        logger.exception("优化未预期错误")
        raise HTTPException(status_code=500, detail="服务内部错误") from e


@app.post("/api/analyze/stream")
async def analyze_stream(body: AnalyzeRequest) -> StreamingResponse:
    """SSE：流式输出模型 token，结束时推送解析后的 JSON 分析结果。"""
    resume = _prepare_resume_text(body.resume)
    jd = _prepare_jd_text(body.jd)
    if not resume or not jd:
        raise HTTPException(status_code=400, detail="简历与 JD 均不能为空")
    _validate_lengths(resume, jd)
    settings = get_settings()
    return StreamingResponse(
        stream_analyze(settings, resume, jd, use_cache=True),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/reanalyze/stream")
async def reanalyze_stream(body: ReanalyzeRequest) -> StreamingResponse:
    """SSE：用户对上一轮分析不满意时，返修匹配分析（可留空意见换角度）。"""
    resume = _prepare_resume_text(body.resume)
    jd = _prepare_jd_text(body.jd)
    if not resume or not jd:
        raise HTTPException(status_code=400, detail="简历与 JD 均不能为空")
    _validate_lengths(resume, jd)
    settings = get_settings()
    return StreamingResponse(
        stream_reanalyze(
            settings,
            resume,
            jd,
            body.highlights,
            body.gaps,
            body.suggestions,
            body.user_feedback,
        ),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/optimize/stream")
async def optimize_stream(body: OptimizeRequest) -> StreamingResponse:
    """SSE：流式输出优化后的简历正文，结束事件含全文。"""
    resume = _prepare_resume_text(body.resume)
    jd = _prepare_jd_text(body.jd)
    if not resume or not jd:
        raise HTTPException(status_code=400, detail="简历与 JD 均不能为空")
    _validate_lengths(resume, jd)
    settings = get_settings()
    return StreamingResponse(
        stream_optimize(
            settings,
            resume,
            jd,
            body.highlights,
            body.gaps,
            body.suggestions,
        ),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/refine-optimize/stream")
async def refine_optimize_stream(body: RefineOptimizeRequest) -> StreamingResponse:
    """SSE：对当前优化结果再次返修（可留空意见）。"""
    resume = _prepare_resume_text(body.resume)
    jd = _prepare_jd_text(body.jd)
    opt = body.optimized_resume.strip()
    if not resume or not jd or not opt:
        raise HTTPException(status_code=400, detail="简历、JD 与当前优化稿均不能为空")
    _validate_lengths(resume, jd)
    if len(opt) > get_settings().max_resume_chars:
        raise HTTPException(status_code=400, detail="优化稿过长")
    settings = get_settings()
    return StreamingResponse(
        stream_refine_optimize(
            settings,
            resume,
            jd,
            body.highlights,
            body.gaps,
            body.suggestions,
            body.optimized_resume,
            body.user_feedback,
        ),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/upload-resume")
async def upload_resume(file: UploadFile = File(...)) -> JSONResponse:
    """Extract text from PDF / DOCX / TXT / Markdown; normalize for LLM."""
    settings = get_settings()
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大，上限 {settings.max_upload_bytes} 字节",
        )
    try:
        raw_text, warnings = extract_resume_plain_text(file.filename, data)
    except ResumeExtractError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    text = normalize_document_text(raw_text)
    if not text:
        raise HTTPException(status_code=400, detail="提取并清洗后内容为空，请检查文件或换用文本型 PDF/Word")
    payload: dict = {
        "content": text,
        "filename": file.filename or "upload",
        "warnings": warnings,
        "source_kind": detect_resume_source_kind(file.filename),
    }
    return JSONResponse(payload)


def _attachment_headers(ascii_name: str, utf8_name: str) -> dict[str, str]:
    q = quote(utf8_name, safe="")
    return {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{q}",
    }


@app.post("/api/export-resume-file")
async def export_resume_file(body: ExportRequest) -> Response:
    """将优化后的纯文本导出为 Word / PDF / 文本；失败时返回 JSON 提示改用 .txt/.md 本地兜底。"""
    text = body.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容为空")
    settings = get_settings()
    if len(text) > settings.max_resume_chars:
        raise HTTPException(status_code=400, detail="正文过长")

    fmt = body.format
    try:
        if fmt == "docx":
            data = build_docx_bytes(text)
            media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            headers = _attachment_headers("optimized-resume.docx", "优化简历.docx")
        elif fmt == "pdf":
            fp = find_pdf_font()
            if not fp:
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "PDF 导出需要中文字体，请配置 PDF_FONT_PATH 或使用 Docker 镜像。",
                        "fallback": ["txt", "md"],
                        "code": "EXPORT_PDF_NO_FONT",
                    },
                )
            data = build_pdf_bytes(text, fp)
            media = "application/pdf"
            headers = _attachment_headers("optimized-resume.pdf", "优化简历.pdf")
        elif fmt == "txt":
            data, media = build_plain_bytes(text, ".txt")
            headers = _attachment_headers("optimized-resume.txt", "优化简历.txt")
        elif fmt == "md":
            data, media = build_plain_bytes(text, ".md")
            headers = _attachment_headers("optimized-resume.md", "优化简历.md")
        else:
            raise HTTPException(status_code=400, detail="不支持的导出格式")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("导出失败")
        return JSONResponse(
            status_code=503,
            content={
                "detail": f"导出失败：{e!s}",
                "fallback": ["txt", "md"],
                "code": "EXPORT_FAILED",
            },
        )

    return Response(content=data, media_type=media, headers=headers)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="前端未部署")
    return FileResponse(index_path)
