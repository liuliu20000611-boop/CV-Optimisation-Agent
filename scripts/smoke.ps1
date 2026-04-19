# 冒烟：检查服务是否存活；若设置了 DEEPSEEK_API_KEY 则额外调用 /api/estimate
param(
  [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

Write-Host "GET $BaseUrl/health"
$h = Invoke-RestMethod -Uri "$BaseUrl/health" -Method Get
Write-Host "health:" ($h | ConvertTo-Json -Compress)

if ($env:DEEPSEEK_API_KEY) {
  Write-Host "POST $BaseUrl/api/estimate (requires running server + valid key for full stack)"
  $body = @{
    resume = "测试简历内容用于长度估算。"
    jd     = "测试 JD 内容。"
  } | ConvertTo-Json -Compress
  try {
    $e = Invoke-RestMethod -Uri "$BaseUrl/api/estimate" -Method Post -Body $body -ContentType "application/json; charset=utf-8"
    Write-Host "estimate:" ($e | ConvertTo-Json -Compress)
  } catch {
    Write-Warning "estimate failed (expected if no server or key): $_"
  }
} else {
  Write-Host "Skip /api/estimate (DEEPSEEK_API_KEY not set)."
}

Write-Host "Smoke done."
