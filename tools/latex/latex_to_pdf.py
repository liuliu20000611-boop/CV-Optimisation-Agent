#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 LaTeX 源码编译为 PDF。

默认使用 xelatex（与 ctex / 中文文档一致），在 .tex 所在目录执行，以便正确解析
\\includegraphics 等相对路径。

依赖：本机已安装 TeX 发行版（TeX Live / MiKTeX），且 xelatex 在 PATH 中。
纯标准库，无需 pip 安装额外包。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _which(cmd: str) -> str | None:
    path = shutil.which(cmd)
    return path


def compile_tex(
    tex_path: Path,
    *,
    engine: str = "xelatex",
    passes: int = 2,
    halt_on_error: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    在 tex 所在目录运行 engine，编译 passes 遍。
    返回最后一次 subprocess 结果（成功时 returncode==0）。
    """
    tex_path = tex_path.expanduser().resolve()
    if not tex_path.is_file():
        raise FileNotFoundError(f"找不到文件: {tex_path}")
    if tex_path.suffix.lower() != ".tex":
        pass  # 仍允许，仅提示少见

    workdir = tex_path.parent
    fname = tex_path.name

    exe = _which(engine)
    if not exe:
        raise FileNotFoundError(
            f"未在 PATH 中找到命令「{engine}」。请安装 TeX 发行版并确保可执行文件在 PATH 中。"
        )

    args_base = [
        exe,
        "-interaction=nonstopmode",
        "-file-line-error",
        "-synctex=1",
        fname,
    ]
    if halt_on_error:
        # 非交互下尽快失败；部分发行版需显式
        if engine in ("xelatex", "pdflatex", "lualatex", "latex"):
            args_base.insert(1, "-halt-on-error")

    last: subprocess.CompletedProcess[str] | None = None
    env = os.environ.copy()
    # 避免 Windows 控制台编码问题导致奇怪输出
    env.setdefault("PYTHONIOENCODING", "utf-8")

    for _ in range(max(1, passes)):
        last = subprocess.run(
            args_base,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if last.returncode != 0:
            break

    assert last is not None
    return last


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="LaTeX (.tex) -> PDF，默认 xelatex，在源文件目录编译",
    )
    parser.add_argument("tex", type=Path, help="输入 .tex 文件路径")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="将生成的 PDF 复制到该路径（默认留在 .tex 同目录，文件名为 <主名>.pdf）",
    )
    parser.add_argument(
        "--engine",
        default="xelatex",
        help="编译器命令名（默认 xelatex；纯英文可试 pdflatex / lualatex）",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=2,
        help="编译遍数（目录/交叉引用通常 2 遍即可，默认 2）",
    )
    parser.add_argument(
        "--no-halt-on-error",
        action="store_true",
        help="不添加 -halt-on-error（尽量跑完日志，便于排查）",
    )
    parser.add_argument(
        "--show-log",
        action="store_true",
        help="失败或加此选项时，将最后一次编译的 stdout/stderr 打印到终端",
    )
    args = parser.parse_args(argv)

    tex_path = args.tex.expanduser().resolve()
    if not tex_path.is_file():
        print(f"文件不存在: {tex_path}", file=sys.stderr)
        return 1

    try:
        last = compile_tex(
            tex_path,
            engine=args.engine,
            passes=args.passes,
            halt_on_error=not args.no_halt_on_error,
        )
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1

    workdir = tex_path.parent
    jobname = tex_path.stem
    pdf_generated = workdir / f"{jobname}.pdf"

    if last.returncode != 0:
        print(f"编译失败（退出码 {last.returncode}）: {args.engine} {tex_path.name}", file=sys.stderr)
        if last.stdout:
            print("--- stdout ---", file=sys.stderr)
            print(last.stdout, file=sys.stderr)
        if last.stderr:
            print("--- stderr ---", file=sys.stderr)
            print(last.stderr, file=sys.stderr)
        return last.returncode or 1

    if args.show_log:
        if last.stdout:
            print("--- stdout ---")
            print(last.stdout)
        if last.stderr:
            print("--- stderr ---")
            print(last.stderr)

    if not pdf_generated.is_file():
        print(f"未找到输出 PDF（预期路径）: {pdf_generated}", file=sys.stderr)
        if args.show_log:
            if last.stdout:
                print(last.stdout, file=sys.stderr)
        return 1

    out_pdf = args.output
    if out_pdf is not None:
        out_pdf = out_pdf.expanduser().resolve()
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_generated, out_pdf)
        print(f"已生成: {pdf_generated}")
        print(f"已复制到: {out_pdf}")
    else:
        print(f"已生成: {pdf_generated}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
