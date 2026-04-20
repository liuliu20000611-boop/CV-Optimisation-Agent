"""FastAPI application: resume analysis & optimization via DeepSeek API."""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import quote
from app.cache import clear_expired, get_cached, set_cached
from app.config import get_settings
from app.llm import (
    run_analysis,
    run_optimize,
    run_template_rewrite,
    run_template_rewrite_error_explain,
)
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
    TemplateRewriteRequest,
    TemplateRewriteResponse,
    TexTemplateItem,
)
from app.template_service import (
    TEMPLATE_ROOT,
    build_preview_url,
    discover_templates,
    get_render_job,
    get_template_by_id,
    read_template_text,
    render_template_to_pdf,
)
from app.text_normalize import normalize_document_text, normalize_jd_text
from app.sse_utils import sse_data

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
    """加载配置；未设置 DEEPSEEK_API_KEY 时仍可启动（仅不调模型）。"""
    settings = get_settings()
    if not settings.deepseek_api_key.strip():
        logger.warning(
            "未设置 DEEPSEEK_API_KEY：大模型相关接口将不可用，请在环境变量或项目根目录 .env 中配置。"
        )


def _require_deepseek_key() -> None:
    """调用 DeepSeek 前校验密钥已配置。"""
    if not get_settings().deepseek_api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="未配置 DEEPSEEK_API_KEY：请在环境变量或项目根目录 .env 中设置 DEEPSEEK_API_KEY 后重启服务。",
        )


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

    _require_deepseek_key()
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
    _require_deepseek_key()
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


@app.get("/api/tex-templates", response_model=list[TexTemplateItem])
async def list_tex_templates() -> list[TexTemplateItem]:
    """List available LaTeX templates from tex warehouse."""
    items: list[TexTemplateItem] = []
    for t in discover_templates():
        items.append(
            TexTemplateItem(
                id=t.id,
                name=t.name,
                tex_rel_path=t.tex_rel_path,
                preview_url=build_preview_url(t.preview_rel_path),
            )
        )
    return items


@app.post("/api/template-rewrite", response_model=TemplateRewriteResponse)
async def template_rewrite(body: TemplateRewriteRequest) -> TemplateRewriteResponse:
    """Rewrite optimized resume into selected LaTeX template and compile PDF."""
    text = body.optimized_resume.strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容为空")
    settings = get_settings()
    if len(text) > settings.max_resume_chars:
        raise HTTPException(status_code=400, detail="正文过长")

    template = get_template_by_id(body.template_id.strip())
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    previous_tex: str | None = None
    if body.previous_job_id:
        old_job = get_render_job(body.previous_job_id.strip())
        if old_job and getattr(old_job, "template_id", None) == template.id and old_job.tex_path.is_file():
            previous_tex = old_job.tex_path.read_text(encoding="utf-8")

    _require_deepseek_key()
    try:
        rewritten_tex = await run_template_rewrite(
            settings=settings,
            optimized_resume=text,
            template_tex=read_template_text(template),
            user_feedback=body.user_feedback,
            previous_tex=previous_tex,
        )
        job = render_template_to_pdf(template, rewritten_tex)
    except httpx.HTTPStatusError as e:
        logger.warning("DeepSeek HTTP 错误: %s", e.response.status_code)
        raise HTTPException(status_code=502, detail="大模型服务暂时不可用，请稍后重试") from e
    except httpx.RequestError as e:
        logger.warning("网络错误: %s", e)
        raise HTTPException(status_code=502, detail="无法连接大模型服务，请稍后重试") from e
    except Exception as e:
        logger.exception("模板改写或编译失败")
        msg = f"模板改写失败：{e!s}"
        try:
            msg = await run_template_rewrite_error_explain(settings, msg)
        except Exception:
            pass
        raise HTTPException(status_code=503, detail=msg) from e

    return TemplateRewriteResponse(
        job_id=job.job_id,
        zip_download_url=f"/api/template-rewrite/{job.job_id}/bundle",
        tex_download_url=f"/api/template-rewrite/{job.job_id}/tex",
        pdf_download_url=f"/api/template-rewrite/{job.job_id}/pdf",
        preview_image_url=f"/api/template-rewrite/{job.job_id}/preview"
        if getattr(job, "preview_path", None)
        else None,
        preview_image_urls=[
            f"/api/template-rewrite/{job.job_id}/preview/{i + 1}"
            for i, _ in enumerate(getattr(job, "preview_paths", []) or [])
        ],
        template_name=job.template_name,
    )


@app.post("/api/template-rewrite/stream")
async def template_rewrite_stream(body: TemplateRewriteRequest) -> StreamingResponse:
    """SSE：模板改写进度（模型改写 -> LaTeX 编译 -> 打包）。"""
    text = body.optimized_resume.strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容为空")
    settings = get_settings()
    if len(text) > settings.max_resume_chars:
        raise HTTPException(status_code=400, detail="正文过长")
    template = get_template_by_id(body.template_id.strip())
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    previous_tex: str | None = None
    if body.previous_job_id:
        old_job = get_render_job(body.previous_job_id.strip())
        if old_job and getattr(old_job, "template_id", None) == template.id and old_job.tex_path.is_file():
            previous_tex = old_job.tex_path.read_text(encoding="utf-8")

    async def _gen():
        if not settings.deepseek_api_key.strip():
            yield sse_data(
                {
                    "type": "error",
                    "message": "未配置 DEEPSEEK_API_KEY，无法调用大模型。",
                    "code": "NO_API_KEY",
                }
            )
            return
        try:
            yield sse_data({"type": "progress", "percent": 10, "stage": "prepare", "message": "准备模板改写"})
            rewritten_tex = await run_template_rewrite(
                settings=settings,
                optimized_resume=text,
                template_tex=read_template_text(template),
                user_feedback=body.user_feedback,
                previous_tex=previous_tex,
            )
            yield sse_data({"type": "progress", "percent": 65, "stage": "llm_done", "message": "模型改写完成，开始编译"})
            job = render_template_to_pdf(template, rewritten_tex)
            yield sse_data({"type": "progress", "percent": 95, "stage": "compiled", "message": "编译完成，准备下载"})
            yield sse_data(
                {
                    "type": "done",
                    "percent": 100,
                    "job_id": job.job_id,
                    "template_name": job.template_name,
                    "zip_download_url": f"/api/template-rewrite/{job.job_id}/bundle",
                    "tex_download_url": f"/api/template-rewrite/{job.job_id}/tex",
                    "pdf_download_url": f"/api/template-rewrite/{job.job_id}/pdf",
                    "preview_image_url": f"/api/template-rewrite/{job.job_id}/preview"
                    if getattr(job, "preview_path", None)
                    else None,
                    "preview_image_urls": [
                        f"/api/template-rewrite/{job.job_id}/preview/{i + 1}"
                        for i, _ in enumerate(getattr(job, "preview_paths", []) or [])
                    ],
                }
            )
        except Exception as e:
            logger.exception("模板改写流失败")
            err_text = f"模板改写失败：{e!s}"
            try:
                user_msg = await run_template_rewrite_error_explain(settings, err_text)
            except Exception:
                user_msg = err_text
            yield sse_data({"type": "error", "message": user_msg, "code": "TEMPLATE_REWRITE_FAILED"})

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/template-rewrite/{job_id}/tex")
async def download_rewritten_tex(job_id: str) -> FileResponse:
    job = get_render_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="文件不存在或已过期")
    return FileResponse(
        job.tex_path,
        media_type="application/x-tex",
        headers=_attachment_headers("rendered-resume.tex", "模板改写简历.tex"),
    )


@app.get("/api/template-rewrite/{job_id}/pdf")
async def download_rewritten_pdf(job_id: str) -> FileResponse:
    job = get_render_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="文件不存在或已过期")
    return FileResponse(
        job.pdf_path,
        media_type="application/pdf",
        headers=_attachment_headers("rendered-resume.pdf", "模板改写简历.pdf"),
    )


@app.get("/api/template-rewrite/{job_id}/bundle")
async def download_rewrite_bundle(job_id: str) -> FileResponse:
    """Download a zip bundle with tex + assets + compiled pdf."""
    job = get_render_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="文件不存在或已过期")
    return FileResponse(
        job.bundle_path,
        media_type="application/zip",
        headers=_attachment_headers("rendered-resume-bundle.zip", "模板改写简历打包.zip"),
    )


@app.get("/api/template-rewrite/{job_id}/preview")
async def download_rewrite_preview(job_id: str) -> FileResponse:
    """Preview image of rewritten PDF first page."""
    job = get_render_job(job_id)
    if not job or not job.preview_path or not job.preview_path.is_file():
        raise HTTPException(status_code=404, detail="预览图不存在")
    return FileResponse(job.preview_path, media_type="image/png")


@app.get("/api/template-rewrite/{job_id}/preview/{page}")
async def download_rewrite_preview_page(job_id: str, page: int) -> FileResponse:
    """Preview image by page index (1-based)."""
    job = get_render_job(job_id)
    paths = getattr(job, "preview_paths", None) if job else None
    if not paths or page < 1 or page > len(paths):
        raise HTTPException(status_code=404, detail="预览图不存在")
    p = paths[page - 1]
    if not p.is_file():
        raise HTTPException(status_code=404, detail="预览图不存在")
    return FileResponse(p, media_type="image/png")


# 显式提供 ES 模块脚本：避免 Windows 下 StaticFiles/FileResponse 偶发 ERR_EMPTY_RESPONSE 或错误 MIME
_JS_MT = "application/javascript"


def _static_file_bytes(name: str) -> bytes:
    path = STATIC_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="静态资源不存在")
    return path.read_bytes()


if STATIC_DIR.is_dir():

    @app.get("/static/app.js", include_in_schema=False)
    async def static_app_js() -> Response:
        return Response(content=_static_file_bytes("app.js"), media_type=_JS_MT)

    @app.get("/static/sse.js", include_in_schema=False)
    async def static_sse_js() -> Response:
        return Response(content=_static_file_bytes("sse.js"), media_type=_JS_MT)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if TEMPLATE_ROOT.is_dir():
    app.mount("/tex-warehouse-assets", StaticFiles(directory=TEMPLATE_ROOT), name="tex_warehouse_assets")


@app.get("/")
async def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="前端未部署")
    return FileResponse(index_path)
