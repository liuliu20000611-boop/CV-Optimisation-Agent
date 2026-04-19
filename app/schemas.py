"""API request/response models."""

from typing import Literal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    resume: str = Field(..., min_length=1, description="Raw resume text")
    jd: str = Field(..., min_length=1, description="Job description text")


class AnalysisResult(BaseModel):
    highlights: list[str] = Field(default_factory=list, description="匹配亮点")
    gaps: list[str] = Field(default_factory=list, description="主要缺口")
    suggestions: list[str] = Field(default_factory=list, description="具体优化建议")


class OptimizeRequest(BaseModel):
    resume: str = Field(..., min_length=1)
    jd: str = Field(..., min_length=1)
    highlights: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class OptimizeResponse(BaseModel):
    optimized_resume: str = Field(..., description="优化后的完整简历正文")


class EstimateResponse(BaseModel):
    resume_chars: int
    jd_chars: int
    approx_input_tokens: int


class ExportRequest(BaseModel):
    """Export optimized plain text into a downloadable file."""

    content: str = Field(..., min_length=1, description="优化后的简历全文（纯文本）")
    format: Literal["docx", "pdf", "txt", "md"] = Field(..., description="目标文件类型")


class ReanalyzeRequest(BaseModel):
    """返修：在上一轮分析基础上重新分析（可填写意见或留空换角度）。"""

    resume: str = Field(..., min_length=1)
    jd: str = Field(..., min_length=1)
    highlights: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    user_feedback: str | None = None


class RefineOptimizeRequest(BaseModel):
    """对「已生成的优化简历」再次返修。"""

    resume: str = Field(..., min_length=1)
    jd: str = Field(..., min_length=1)
    optimized_resume: str = Field(..., min_length=1)
    highlights: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    user_feedback: str | None = None
