"""Pytest fixtures: env must be set before importing the FastAPI app."""

from __future__ import annotations

import os

# CI / local tests: fake key; disable rate limiting via TESTING=1
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-not-for-production")
os.environ.setdefault("TESTING", "1")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clear_analysis_cache() -> None:
    from app.cache import clear_all

    clear_all()


@pytest.fixture
def client() -> TestClient:
    from app.config import get_settings
    from app.main import app

    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()
