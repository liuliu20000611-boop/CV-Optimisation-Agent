#!/usr/bin/env bash
# 冒烟：检查 /health；若设置了 DEEPSEEK_API_KEY 则尝试 /api/estimate
set -euo pipefail
BASE="${1:-http://127.0.0.1:8000}"

echo "GET ${BASE}/health"
curl -fsS "${BASE}/health" | cat
echo

if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "POST ${BASE}/api/estimate"
  curl -fsS -X POST "${BASE}/api/estimate" \
    -H "Content-Type: application/json" \
    -d '{"resume":"测试简历。","jd":"测试 JD。"}' | cat
  echo
else
  echo "Skip /api/estimate (DEEPSEEK_API_KEY not set)."
fi

echo "Smoke done."
