#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP 服务：PDF ↔ LaTeX 工具（stdio）。

简历推荐流程：
1. pdf_to_latex — 将简历 PDF 转为可编辑 .tex（及图片目录等）
2. 由大模型在 .tex 正文上修改（本服务不负责调用 LLM）
3. latex_to_pdf — 将修改后的 .tex 编译为 PDF

依赖：Python 3.10+、pymupdf（pdf_to_latex）、本机 TeX（xelatex 在 PATH）。
运行：在项目根目录执行  python tools/mcp_latex_server.py
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

# 项目根目录加入 path，便于 import tools.latex
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp.server.fastmcp import FastMCP

from tools.latex.latex_to_pdf import compile_tex

_PDF_TO_LATEX = _ROOT / "tools" / "latex" / "pdf_to_latex.py"
_LATEX_DIR = _ROOT / "tools" / "latex"

mcp = FastMCP(
    name="resume-latex-tools",
    instructions=(
        "提供 pdf_to_latex（PDF→UTF-8 .tex）与 latex_to_pdf（.tex→PDF，默认 xelatex）。"
        "简历场景：先转 LaTeX，再由模型编辑 .tex，最后编译回 PDF。"
        "需要本机安装 PyMuPDF 与 TeX（xelatex）。"
    ),
)


def _json(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False)


@mcp.tool()
def latex_to_pdf(
    tex_path: str,
    output_pdf: str | None = None,
    engine: str = "xelatex",
    passes: int = 2,
) -> str:
    """
    将 LaTeX 源文件编译为 PDF。默认在 .tex 所在目录执行 xelatex，输出 <主文件名>.pdf。

    Args:
        tex_path: .tex 文件的绝对或相对路径
        output_pdf: 若指定，将生成的 PDF 复制到该路径（可选）
        engine: 编译器名，默认 xelatex（中文简历常用）
        passes: 编译遍数，目录/交叉引用一般 2 遍即可

    Returns:
        JSON 字符串：ok、pdf_path、错误时的 stdout/stderr 片段
    """
    tex = Path(tex_path).expanduser().resolve()
    if not tex.is_file():
        return _json({"ok": False, "error": f"找不到文件: {tex}"})
    try:
        last = compile_tex(tex, engine=engine, passes=max(1, int(passes)), halt_on_error=True)
    except FileNotFoundError as e:
        return _json({"ok": False, "error": str(e)})

    pdf_path = tex.parent / f"{tex.stem}.pdf"
    if last.returncode != 0:
        return _json(
            {
                "ok": False,
                "returncode": last.returncode,
                "stdout": (last.stdout or "")[-12000:],
                "stderr": (last.stderr or "")[-12000:],
                "hint": "请检查是否已安装 TeX（xelatex 在 PATH）以及 .tex 是否有语法错误",
            }
        )
    if not pdf_path.is_file():
        return _json({"ok": False, "error": f"未找到输出 PDF: {pdf_path}"})

    result: dict = {"ok": True, "pdf_path": str(pdf_path)}
    if output_pdf:
        outp = Path(output_pdf).expanduser().resolve()
        outp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, outp)
        result["copied_to"] = str(outp)
    return _json(result)


@mcp.tool()
def pdf_to_latex(
    pdf_path: str,
    output_tex: str | None = None,
    layout: str = "flow",
    no_images: bool = False,
    preserve_linebreaks: bool = False,
    page_break: bool = False,
) -> str:
    """
    将 PDF（需含可选中文字层，扫描件请先 OCR）导出为 LaTeX 源码 UTF-8。

    Args:
        pdf_path: 输入 PDF 路径
        output_tex: 输出 .tex 路径；默认与 PDF 同目录同名 .tex
        layout: flow=段落流式（默认，便于改字）；absolute=按坐标 TikZ 复原版式（更复杂）
        no_images: True 时仅抽取文字，不导出嵌入图
        preserve_linebreaks: True 时段内保留换行（简历常用）
        page_break: True 时在页间插入 \\clearpage

    Returns:
        JSON 字符串：ok、tex_path、stdout/stderr
    """
    if not _PDF_TO_LATEX.is_file():
        return _json({"ok": False, "error": f"脚本不存在: {_PDF_TO_LATEX}"})

    pdf = Path(pdf_path).expanduser().resolve()
    if not pdf.is_file():
        return _json({"ok": False, "error": f"找不到文件: {pdf}"})

    cmd: list[str] = [sys.executable, str(_PDF_TO_LATEX), str(pdf)]
    if output_tex:
        cmd.extend(["-o", str(Path(output_tex).expanduser().resolve())])
    if layout and layout != "flow":
        cmd.extend(["--layout", layout])
    if no_images:
        cmd.append("--no-images")
    if preserve_linebreaks:
        cmd.append("--preserve-linebreaks")
    if page_break:
        cmd.append("--page-break")

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return _json({"ok": False, "error": "子进程超时（600s）"})
    except OSError as e:
        return _json({"ok": False, "error": str(e)})

    out_tex = Path(output_tex).expanduser().resolve() if output_tex else pdf.with_suffix(".tex")
    ok = proc.returncode == 0 and out_tex.is_file()
    return _json(
        {
            "ok": ok,
            "returncode": proc.returncode,
            "tex_path": str(out_tex) if out_tex.is_file() else None,
            "stdout": (proc.stdout or "")[-8000:],
            "stderr": (proc.stderr or "")[-8000:],
        }
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
