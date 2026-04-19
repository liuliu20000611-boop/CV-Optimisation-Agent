"""Normalize resume/JD text for LLM: strip noise, unify whitespace, no PII logging."""

from __future__ import annotations

import re
import unicodedata

# Zero-width and bidi marks often copied from PDF/Web
_ZW_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
# Common PDF/Word page footer in Chinese
_PAGE_LINE_RE = re.compile(r"^\s*第\s*\d{1,4}\s*页\s*$", re.MULTILINE)
# Repeated separators
_MULTI_NL = re.compile(r"\n{3,}")


def normalize_document_text(text: str) -> str:
    """
    Convert extracted or pasted text into cleaner plain text for the model.
    - Unicode NFKC, remove zero-width chars
    - Unify newlines and collapse excessive blank lines
    - Trim per-line spaces; remove isolated '第 N 页' lines
    """
    if not text:
        return ""
    s = _ZW_RE.sub("", text)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\xa0", " ").replace("\u3000", " ")
    s = _PAGE_LINE_RE.sub("", s)
    lines: list[str] = []
    for line in s.split("\n"):
        line = " ".join(line.split())
        if line:
            lines.append(line)
    s = "\n".join(lines)
    s = _MULTI_NL.sub("\n\n", s)
    return s.strip()


def normalize_jd_text(text: str) -> str:
    """JD uses the same cleaning pipeline so structure stays readable."""
    return normalize_document_text(text)
