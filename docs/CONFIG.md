# 配置与启动前检查清单

在首次部署或排障时，按顺序确认以下项。

## 环境变量


| 检查项                                                      | 说明                                                                                      |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `DEEPSEEK_API_KEY`                                       | **生产/正式调用模型**须已设置且非空；本地可暂空以便先启动服务（仅大模型相关接口不可用）。**绝不**把真实密钥写入代码或提交 Git（使用 `-e` / Compose / Secret）。 |
| `DEEPSEEK_BASE_URL`                                      | 默认可不设置；使用代理或兼容网关时改为对应地址。                                                                |
| `DEEPSEEK_MODEL`                                         | 默认 `deepseek-chat`；更换模型时同步评估费用与延迟。                                                      |
| `TESTING`                                                | 仅测试/CI 设为 `1`：关闭 API 限流便于自动化；**生产环境勿设置**。                                               |
| `PDF_FONT_PATH`                                          | PDF 导出用中文字体文件路径；Docker 镜像已安装 Noto CJK，可不设；裸机 Windows 可指向 `C:\Windows\Fonts\msyh.ttc` 等。 |
| `RATE_LIMIT_ENABLED`                                     | 默认开启；单实例内存限流，多副本需网关层限流。                                                                 |
| `RATE_LIMIT_PER_MINUTE`                                  | 单 IP 每分钟 `/api/`* 调用上限，默认 `60`。                                                         |
| `ENABLE_ANALYSIS_CACHE`                                  | 默认 `true`；短时重复分析命中缓存（单进程有效）。                                                            |
| `ANALYSIS_CACHE_TTL_SECONDS`                             | 缓存 TTL，默认 `300`。                                                                        |
| `MAX_RESUME_CHARS` / `MAX_JD_CHARS` / `MAX_UPLOAD_BYTES` | 防止异常大请求；上传默认上限约 **2MiB** 以容纳 PDF/Word。                                                  |
| `REQUEST_TIMEOUT_SECONDS`                                | 调用大模型超时。                                                                                |


## 网络与端口


| 检查项      | 说明                                                |
| -------- | ------------------------------------------------- |
| 出站 HTTPS | 运行环境能访问 `DEEPSEEK_BASE_URL`（默认 DeepSeek 官方 API）。  |
| 监听端口     | 容器内默认 `8000`，与 `Dockerfile` / Compose `ports` 一致。 |


## 健康检查

- `GET /health` 应返回 `{"status":"ok","version":"..."}`。
- 编排系统（如 Kubernetes）可将该路径配置为 **liveness/readiness** 探针。

## 日志与隐私

- 日志仅包含 `request_id`、HTTP 方法与路径、状态码；**不记录**简历/JD 正文与 API Key。
- 排障请用响应头 `X-Request-ID` 关联请求。

## 测试与 CI

- 自动化测试设置 `DEEPSEEK_API_KEY` 为占位符、`TESTING=1`，**不会**调用真实大模型（使用 mock）。