"""Export endpoint: DOCX / PDF / text."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.export_resume import find_pdf_font


def test_export_docx(client: TestClient) -> None:
    r = client.post(
        "/api/export-resume-file",
        json={"content": "第一行\n第二行", "format": "docx"},
    )
    assert r.status_code == 200
    assert "wordprocessingml" in (r.headers.get("content-type") or "")
    assert r.content[:2] == b"PK"


def test_export_txt(client: TestClient) -> None:
    r = client.post(
        "/api/export-resume-file",
        json={"content": "hello", "format": "txt"},
    )
    assert r.status_code == 200
    assert b"hello" in r.content


def test_export_pdf_when_font_available(client: TestClient) -> None:
    if not find_pdf_font():
        pytest.skip("no CJK font on this runner")
    r = client.post(
        "/api/export-resume-file",
        json={"content": "中文简历一行\n第二行", "format": "pdf"},
    )
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"
