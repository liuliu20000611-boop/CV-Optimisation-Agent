"""API tests with LLM calls mocked — no real DeepSeek requests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.schemas import AnalysisResult


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert r.headers.get("X-Request-ID")


def test_request_id_on_api(client: TestClient) -> None:
    r = client.post(
        "/api/estimate",
        json={"resume": "a" * 200, "jd": "b" * 200},
    )
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID")


def test_estimate(client: TestClient) -> None:
    r = client.post(
        "/api/estimate",
        json={"resume": "hello " * 100, "jd": "world " * 50},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["resume_chars"] > 0
    assert j["jd_chars"] > 0
    assert j["approx_input_tokens"] > 0


def test_analyze_validation_empty(client: TestClient) -> None:
    r = client.post("/api/analyze", json={"resume": "", "jd": "jd"})
    assert r.status_code == 422


@patch("app.main.run_analysis", new_callable=AsyncMock)
def test_analyze_success(mock_run: AsyncMock, client: TestClient) -> None:
    mock_run.return_value = AnalysisResult(
        highlights=["h1"],
        gaps=["g1"],
        suggestions=["s1"],
    )
    r = client.post(
        "/api/analyze",
        json={"resume": "我的简历内容足够长一些用于测试。", "jd": "岗位要求描述。"},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["highlights"] == ["h1"]
    assert r.headers.get("X-Analysis-Cache") == "MISS"
    mock_run.assert_awaited_once()


@patch("app.main.run_analysis", new_callable=AsyncMock)
def test_analyze_cache_hit(mock_run: AsyncMock, client: TestClient) -> None:
    mock_run.return_value = AnalysisResult(
        highlights=["a"],
        gaps=["b"],
        suggestions=["c"],
    )
    body = {"resume": "同一简历用于缓存测试。", "jd": "同一 JD。"}
    r1 = client.post("/api/analyze", json=body)
    assert r1.status_code == 200
    r2 = client.post("/api/analyze", json=body)
    assert r2.status_code == 200
    assert r2.headers.get("X-Analysis-Cache") == "HIT"
    assert mock_run.await_count == 1


@patch("app.main.run_optimize", new_callable=AsyncMock)
def test_optimize_success(mock_opt: AsyncMock, client: TestClient) -> None:
    mock_opt.return_value = "优化后的简历全文"
    r = client.post(
        "/api/optimize",
        json={
            "resume": "简历",
            "jd": "JD",
            "highlights": ["h"],
            "gaps": ["g"],
            "suggestions": ["s"],
        },
    )
    assert r.status_code == 200
    assert r.json()["optimized_resume"] == "优化后的简历全文"
    mock_opt.assert_awaited_once()


def test_upload_resume_too_large(client: TestClient) -> None:
    big = b"x" * (3_000_000)
    r = client.post(
        "/api/upload-resume",
        files={"file": ("a.txt", big, "text/plain")},
    )
    assert r.status_code == 400
