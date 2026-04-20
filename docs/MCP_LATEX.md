# LaTeX / PDF 工具与 MCP（简历编辑流水线）

本仓库在 `tools/latex/` 提供两个脚本，并由 `tools/mcp_latex_server.py` 以 **MCP（stdio）** 暴露为工具，便于在 Cursor 等客户端里由 Agent 调用。

## 推荐流程

1. **`pdf_to_latex`**：将含可选中文字层的简历 PDF 转为 UTF-8 `.tex`（扫描件需先 OCR）。
2. **大模型**：在 `.tex` 上直接修改正文与结构（MCP 服务本身不调用 LLM）。
3. **`latex_to_pdf`**：将修改后的 `.tex` 编译为 PDF（默认 `xelatex`，中文简历常用）。

## 本机依赖

- **Python**：与项目一致（建议 3.11+）。
- **PyMuPDF**：`pdf_to_latex` 需要（已在根目录 `requirements.txt` 中含 `pymupdf`）。
- **TeX 发行版**：TeX Live / MiKTeX，且 **`xelatex` 在 PATH**（`latex_to_pdf` 使用）。

## 安装 MCP 相关依赖

开发/Agent 环境请安装开发依赖（含官方 `mcp` 包）：

```bash
pip install -r requirements-dev.txt
```

若安装其它包时出现 **Starlette 版本与 FastAPI 不兼容**，请保持与 `fastapi==0.115.6` 匹配的 `starlette`（例如 `0.41.x`），**不要**单独安装 PyPI 上的 `fastmcp` 包；本项目的 MCP 入口使用 **`mcp.server.fastmcp`**（随 `mcp` 官方包提供）。

## 手动验证 MCP 进程

在项目根目录执行（stdio 服务会阻塞终端，用于确认能启动）：

```bash
python tools/mcp_latex_server.py
```

正常时进程等待标准输入；在 Cursor 中应由客户端拉起，无需长期占用终端。

## 在 Cursor 中注册 MCP

在 Cursor 的 MCP 配置中增加一项（路径请按本机仓库根目录修改），**工作目录设为项目根**，以便脚本解析 `tools/latex` 与 `import tools.latex`：

```json
{
  "mcpServers": {
    "resume-latex-tools": {
      "command": "python",
      "args": ["D:/code/Agent/t/tools/mcp_latex_server.py"],
      "cwd": "D:/code/Agent/t"
    }
  }
}
```

Windows 下可将 `command` 改为虚拟环境中的解释器，例如 `D:/code/Agent/t/.venv/Scripts/python.exe`。

暴露的工具名：

- **`latex_to_pdf`**：参数见 `tools/mcp_latex_server.py` 中说明（`tex_path`、`output_pdf`、`engine`、`passes`）。
- **`pdf_to_latex`**：参数见同文件（`pdf_path`、`output_tex`、`layout` 等）。

## 脚本位置

| 能力 | 文件 |
| --- | --- |
| `.tex` → PDF | `tools/latex/latex_to_pdf.py`（`compile_tex`） |
| PDF → `.tex` | `tools/latex/pdf_to_latex.py`（由 MCP 子进程调用） |
| MCP 入口 | `tools/mcp_latex_server.py` |
