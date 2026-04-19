"""Detect resume input kind from filename for export format defaults."""

from __future__ import annotations

from pathlib import Path


def detect_resume_source_kind(filename: str | None) -> str:
    """
    Return one of: pdf, docx, text — used to suggest matching export format.
    Legacy .doc is rejected before this is called.
    """
    name = (filename or "").strip().lower()
    suf = Path(name).suffix
    if suf == ".pdf" or name.endswith(".pdf"):
        return "pdf"
    if suf == ".docx" or name.endswith(".docx"):
        return "docx"
    return "text"
