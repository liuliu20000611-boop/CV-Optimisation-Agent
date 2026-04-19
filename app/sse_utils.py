"""SSE helpers — UTF-8 JSON lines for EventSource-style streaming."""

from __future__ import annotations

import json
from typing import Any


def sse_data(event: dict[str, Any]) -> str:
    """One SSE message block; event must be JSON-serializable (ensure_ascii=False for CJK)."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
