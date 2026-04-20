# 简历优化 Agent

基于 **FastAPI** 与 **DeepSeek**（OpenAI 兼容 `chat/completions`）的 Web 应用：粘贴或上传简历与目标 **JD**，通过 **SSE** 流式输出**匹配亮点 / 主要缺口 / 具体优化建议**；用户确认后**流式生成**完整优化简历。支持两类导出：1）常规导出 **Word（.docx）/ PDF / Markdown / 纯文本**；2）**模板改写导出**（从 `tex warehouse` 选模板，生成整份预览图，支持反馈重写，最终下载可编译 ZIP）。支持**返修分析**与**返修优化稿**。**API Key 仅通过环境变量注入**，仓库内不包含密钥。

---

## 1. 快速开始


| 步骤  | 操作                                                                                           |
| --- | -------------------------------------------------------------------------------------------- |
| 1   | 安装 **Python 3.11+**（推荐 3.12）或 **Docker**                                                     |
| 2   | 从 [DeepSeek 开放平台](https://platform.deepseek.com) 获取 **API Key**                              |
| 3   | 复制项目根目录 `**[.env.example](.env.example)`** 为 `**.env`**，编辑并填写 `**DEEPSEEK_API_KEY=`**        |
| 4   | **二选一启动**：见下文 [3. 本地运行（不用 Docker）](#3-本地运行不用-docker) 或 [4. Docker 构建与运行](#4-docker-构建与运行)    |
| 5   | 浏览器打开 **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**                                   |
| 6   | **建议流程**：粘贴简历 + JD → 分析（可返修）→ 生成优化简历 →（可选）返修优化稿 → 选模板并改写（可反馈重写）→ 预览整份简历 → 下载 ZIP 或常规导出 |


**健康检查**（可选）：`GET http://127.0.0.1:8000/health` 应返回 `{"status":"ok","version":"…"}`。

---

## 2. 项目价值与 LLM 交互说明

### 2.1 分析结果是否有用

- 分析阶段要求模型输出 **严格 JSON**（`highlights` / `gaps` / `suggestions`），并与 **JD 关键词、简历可验证经历**对齐（见 `[app/prompts.py](app/prompts.py)` 中 `ANALYSIS_SYSTEM`）。
- 流式结束后若 JSON 解析失败，会走 **修复提示** 再请求一次模型（`ANALYSIS_REPAIR_SYSTEM`），提高可用性。
- 相同「简历 + JD」在 TTL 内可命中 **服务端分析缓存**（单进程），减少重复调用。

### 2.2 优化结果是否有用

- 优化阶段强调 **在保留原简历章节结构、列表符号与表述习惯** 的前提下润色（`OPTIMIZE_SYSTEM`），避免「换模板式」重写；并约束 **不编造** 学校、公司与项目。
- 返修分析（`reanalysis_user_message`）与返修简历（`REFINE_OPTIMIZE_SYSTEM`）会把 **上一轮结果 + 用户反馈**（可空）一并交给模型，便于迭代。
- 模板改写支持 **同模板记忆返修**（`previous_job_id` + 上一轮 tex 上下文）；用户切换模板时前端会清理旧上下文，避免跨模板污染。

### 2.3 流式与兜底

- 流式接口：`/api/analyze/stream`、`/api/reanalyze/stream`、`/api/optimize/stream`、`/api/refine-optimize/stream`（`media_type=text/event-stream; charset=utf-8`）。
- 流式链路异常时，服务端尽量 **回退到非流式** 同逻辑（见 `[app/stream_handlers.py](app/stream_handlers.py)`）；仍失败则 SSE 推送 `type: error`。
- 导出 **docx/pdf** 失败时返回 **503 + JSON**（含 `fallback: ["txt","md"]`）；前端用当前正文生成 **UTF-8** 的 `.txt`/`.md` 下载兜底。

### 2.4 代码结构（摘要）

```
app/
  main.py           # 路由、静态资源、导出、模板改写流
  config.py         # 环境变量（pydantic-settings）
  llm.py            # 同步/流式调用、JSON 解析与返修
  prompts.py        # 系统提示与用户消息模板
  stream_handlers.py# SSE 生成器与兜底
  schemas.py        # 请求/响应模型
  export_resume.py  # docx/pdf/纯文本
  template_service.py # 模板扫描、渲染、预览图、ZIP 打包
  resume_extract.py # PDF/Word 等提取
  text_normalize.py
  cache.py / middleware.py
frontend/           # Vue 3 + Vite（可选：npm run build → 覆盖 static/）
static/             # 默认：Vue CDN 单页；Docker 构建后为 Vite 产物
tests/              # pytest（Mock，不调真实 API）
```

---

## 3. 本地运行（不用 Docker）

**前置**：Python 3.11+；已配置 `**.env`** 中的 `DEEPSEEK_API_KEY`（或将变量写入 shell）。

```powershell
cd <项目根目录>
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Linux / macOS：

```bash
cd <项目根目录>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DEEPSEEK_API_KEY=你的密钥   # 若未使用 .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**访问**：浏览器打开 **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**

**说明**：默认 `static/` 已含 **Vue 3（CDN）** 页面，**无需 Node**。若需 Vite 打包前端：安装 Node 18+，执行 `cd frontend && npm install && npm run build`，产物输出到 `static/`。

---

## 4. Docker 构建与运行

### 4.1 构建镜像

- **要求**：本机已安装 Docker，构建阶段需 **联网**（拉取 `node`、`python` 基础镜像并执行 `npm install`）。
- **说明**：`Dockerfile` 为**多阶段**：先用 Node 编译 `frontend/`（Vite）生成 `static/`，再组装 Python 镜像。

在项目根目录执行：

```bash
docker build -t resume-agent:latest .
```

### 4.2 启动容器（docker run）

```bash
docker run --rm -p 8000:8000 -e DEEPSEEK_API_KEY=你的密钥 resume-agent:latest
```

可选传入其它环境变量（与下表一致），例如：

```bash
docker run --rm -p 8000:8000 ^
  -e DEEPSEEK_API_KEY=你的密钥 ^
  -e DEEPSEEK_BASE_URL=https://api.deepseek.com/v1 ^
  -e DEEPSEEK_MODEL=deepseek-chat ^
  resume-agent:latest
```

访问：**[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**

### 4.3 Docker Compose（推荐）

1. 复制 `**.env.example`** 为 `**.env`**，至少填写 `**DEEPSEEK_API_KEY`**。
2. 项目根目录执行：

```bash
docker compose up --build
```

- Compose 会读取根目录 `**.env**` 注入容器（含可选的 `PDF_FONT_PATH` 等）。
- 若报错 `**DEEPSEEK_API_KEY` 未设置**：请确认已创建 `.env` 且包含该键，或在当前 shell 中 `export DEEPSEEK_API_KEY=...` 后再执行。

访问：**[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**

---

## 5. 环境变量说明

配置方式：**环境变量** 或项目根目录 `**.env`**（UTF-8；`[app/config.py](app/config.py)` 使用 `pydantic-settings` 加载）。


| 变量                           | 必填    | 说明                                            |
| ---------------------------- | ----- | --------------------------------------------- |
| `DEEPSEEK_API_KEY`           | 使用 LLM 时必填 | 未设置时服务可启动，`/health`、静态页、`/api/estimate`、上传与导出仍可用；分析/优化接口返回 503 或 SSE `NO_API_KEY` |
| `DEEPSEEK_BASE_URL`          | 否     | 默认 `https://api.deepseek.com/v1`              |
| `DEEPSEEK_MODEL`             | 否     | 默认 `deepseek-chat`                            |
| `MAX_RESUME_CHARS`           | 否     | 简历最大字符数，默认 `120000`                           |
| `MAX_JD_CHARS`               | 否     | JD 最大字符数，默认 `50000`                           |
| `MAX_UPLOAD_BYTES`           | 否     | 上传文件最大字节，默认 `2097152`（约 2MiB）                 |
| `REQUEST_TIMEOUT_SECONDS`    | 否     | 调用大模型超时（秒），默认 `120`                           |
| `RATE_LIMIT_PER_MINUTE`      | 否     | 单 IP 每分钟 `/api/`* 请求上限，默认 `60`（单进程内存限流）       |
| `RATE_LIMIT_ENABLED`         | 否     | 是否启用限流，默认 `true`                              |
| `ENABLE_ANALYSIS_CACHE`      | 否     | 是否缓存相同「简历+JD」的分析，默认 `true`                    |
| `ANALYSIS_CACHE_TTL_SECONDS` | 否     | 分析缓存 TTL（秒），默认 `300`                          |
| `TESTING`                    | 否     | 设为 `1` 时关闭 API 限流（**仅测试/CI**），生产勿设            |
| `PDF_FONT_PATH`              | 否     | PDF 用中文字体路径；不设则自动探测；**Docker 镜像已安装 Noto CJK** |


更细的说明见 `[docs/CONFIG.md](docs/CONFIG.md)`；多进程/多副本部署见 `[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)`。

---

## 6. 使用说明与功能要点

1. **简历**：粘贴中英文均可（UTF-8）；或上传 **PDF / Word（.docx）/ TXT / Markdown**（服务端提取纯文本并规范化；旧版 `.doc` 请先另存为 `.docx` 或 PDF）。
2. **JD**：粘贴目标岗位完整描述。
3. **分析**：点击「开始匹配分析（流式）」→ 观察流式输出 → 结束后展示「匹配亮点 / 主要缺口 / 优化建议」。缓存命中时界面可显示「缓存」标记。
4. **返修分析**：填写意见或留空 →「返修分析」。
5. **优化**：勾选确认 →「生成优化简历」→ 流式展示全文。
6. **返修优化稿（可选）**：对当前正文填写意见或留空 →「返修优化稿」。
7. **模板改写（独立）**：从 `tex warehouse` 选模板，点击生成预览；可填写反馈后重写，确认后下载 ZIP（含 `.tex`、资源、`.pdf`）。
8. **常规导出**：Word / PDF / 纯文本 / Markdown；若服务端 docx/pdf 不可用，前端自动下载 `.txt` 与 `.md`。

---

## 7. 自动化测试

**不会**调用真实 DeepSeek（使用 Mock）：

```bash
pip install -r requirements-dev.txt
set DEEPSEEK_API_KEY=test
set TESTING=1
pytest tests -q
```

Linux / macOS：`export DEEPSEEK_API_KEY=test` 与 `export TESTING=1`。

---

## 8. API 摘要


| 方法     | 路径                            | 说明                                             |
| ------ | ----------------------------- | ---------------------------------------------- |
| `GET`  | `/health`                     | 健康检查（`status`、`version`）                       |
| `POST` | `/api/estimate`               | 简历/JD 字符数与启发式 token 估算                         |
| `POST` | `/api/analyze`                | 匹配分析（JSON；响应头 `X-Analysis-Cache`）              |
| `POST` | `/api/analyze/stream`         | SSE：流式分析，结束事件含 JSON 结果                         |
| `POST` | `/api/reanalyze/stream`       | SSE：返修分析                                       |
| `POST` | `/api/optimize`               | 优化简历全文（非流式）                                    |
| `POST` | `/api/optimize/stream`        | SSE：流式优化简历正文                                   |
| `POST` | `/api/refine-optimize/stream` | SSE：返修优化稿                                      |
| `POST` | `/api/upload-resume`          | `multipart/form-data`，字段名 `file`               |
| `POST` | `/api/export-resume-file`     | JSON：`content` 与 `format`（取值为 docx、pdf、txt、md） |
| `GET`  | `/api/tex-templates`          | 模板列表（来自 `tex warehouse`，含预览图）                      |
| `POST` | `/api/template-rewrite/stream` | SSE：模板改写进度（改写→编译→打包），完成后返回预览与下载 URL         |
| `GET`  | `/api/template-rewrite/{job_id}/preview/{page}` | 获取指定页预览图 |
| `GET`  | `/api/template-rewrite/{job_id}/bundle` | 下载可编译 ZIP（tex+资源+pdf） |


响应含 `**X-Request-ID**`；日志**不记录**简历正文与密钥。

---

## 9. 冒烟脚本（可选）

服务已启动且可访问 `http://127.0.0.1:8000` 时：

- Windows：`powershell -File scripts/smoke.ps1`
- Bash：`bash scripts/smoke.sh`

若环境变量中有 `DEEPSEEK_API_KEY`，脚本会尝试调用 `/api/estimate`。

---

## 10. 故障排查


| 现象                        | 处理                                                                  |
| ------------------------- | ------------------------------------------------------------------- |
| 启动报错缺少 `DEEPSEEK_API_KEY` | 配置 `.env` 或导出环境变量                                                   |
| Docker Compose 构建慢或失败     | 检查网络；确保能拉取 `node`、`python` 镜像                                       |
| 首页 404「前端未部署」             | 确认存在 `static/index.html`；或重新构建镜像 / 执行 `frontend` 下 `npm run build`  |
| PDF 导出失败                  | 容器内已有 Noto；本地可设 `PDF_FONT_PATH` 指向 `.ttf`/`.ttc`）；失败时用 `.txt`/`.md` |
| 限流 429                    | 降低请求频率或调大 `RATE_LIMIT_PER_MINUTE`；开发可设 `TESTING=1`（勿用于生产）           |


---

## 11. 文档与后续

- 配置细节：`[docs/CONFIG.md](docs/CONFIG.md)`；多进程/多副本：`[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)`。
- **PDF ↔ LaTeX MCP 工具**（简历：PDF→TeX→模型改字→编译 PDF）：`[docs/MCP_LATEX.md](docs/MCP_LATEX.md)`。
- 需求分析与任务说明：`[R_A.md](R_A.md)`；项目说明与简历用语：`[CV.md](CV.md)`。
- 后续可优化方向：`[todo.md](todo.md)`。

---

## 12. 许可证

按课程/作业要求自行补充。