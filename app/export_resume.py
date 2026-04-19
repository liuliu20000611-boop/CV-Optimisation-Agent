"""Build downloadable resume files (DOCX / PDF / plain text) from optimized plain text."""

from __future__ import annotations

import logging
import os
from io import BytesIO
from typing import Final

logger = logging.getLogger(__name__)

_DOCX_MAX_CHARS: Final[int] = 500_000
_PDF_MAX_CHARS: Final[int] = 500_000
# Approximate chars per page for CJK text in A4 textbox (avoid mid-stream API quirks).
_PDF_CHUNK_CHARS: Final[int] = 3200


def _default_cjk_font_paths() -> list[str]:
    return [
        p
        for p in (
            os.environ.get("PDF_FONT_PATH", "").strip(),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
        )
        if p
    ]


def find_pdf_font() -> str | None:
    """First existing font path for CJK PDF rendering."""
    for p in _default_cjk_font_paths():
        if os.path.isfile(p):
            logger.info("PDF 使用字体: %s", p)
            return p
    return None


def build_docx_bytes(text: str) -> bytes:
    """Create a .docx with one paragraph per line (preserve line breaks)."""
    if len(text) > _DOCX_MAX_CHARS:
        raise ValueError("正文过长，无法导出为 Word")
    import docx

    doc = docx.Document()
    if not text:
        doc.add_paragraph("")
    else:
        for line in text.split("\n"):
            doc.add_paragraph(line)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def build_pdf_bytes(text: str, font_path: str) -> bytes:
    """Create a multi-page PDF; requires a CJK-capable font (TTF/TTC)."""
    if len(text) > _PDF_MAX_CHARS:
        raise ValueError("正文过长，无法导出为 PDF")
    import fitz

    doc = fitz.open()
    rect = fitz.Rect(40, 40, 555, 802)
    fontsize = 10.5
    if not text:
        doc.new_page(width=595, height=842)
        out = doc.tobytes()
        doc.close()
        return out

    pos = 0
    while pos < len(text):
        chunk = text[pos : pos + _PDF_CHUNK_CHARS]
        pos += _PDF_CHUNK_CHARS
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(rect, chunk, fontfile=font_path, fontsize=fontsize)

    out = doc.tobytes()
    doc.close()
    return out


def build_plain_bytes(text: str, suffix: str) -> tuple[bytes, str]:
    raw = text.encode("utf-8")
    mime = "text/plain; charset=utf-8"
    if suffix.lower() == ".md":
        mime = "text/markdown; charset=utf-8"
    return raw, mime
