#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 PDF 中的文本层抽取为 LaTeX 源码（UTF-8），并导出嵌入图片供编译时复原。

说明：
- 仅处理“可选中文字”的 PDF；扫描件需先 OCR。
- 默认可编辑模式：抽取真实文字 + 仅导出 PDF 内嵌插图/照片到子目录（不是整页截图），
  适合在 .tex 里继续改字、调格式。
- --exact：整页光栅为 PNG，仅作“视觉复印”，正文中没有可编辑文字；需要后期编辑时请勿使用。
- 图片（插图）会保存到与 .tex 同目录下的子文件夹；若完全不要任何图片文件，用 --no-images。
- --layout absolute：按 PDF 坐标用 TikZ 放置每个文字 span 与图片；并用 get_drawings 还原矢量线、
  矩形填充/描边（如横线、边框）；图形放在 backgrounds 层，通常位于文字与插图之下。

依赖：pip install pymupdf
编译：xelatex 输出.tex（需在 .tex 所在目录编译，以便找到图片）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def escape_latex(text: str) -> str:
    """转义 LaTeX 特殊字符（正文环境）。"""
    out: list[str] = []
    for ch in text:
        if ch == "\\":
            out.append(r"\textbackslash{}")
        elif ch == "{":
            out.append(r"\{")
        elif ch == "}":
            out.append(r"\}")
        elif ch == "$":
            out.append(r"\$")
        elif ch == "%":
            out.append(r"\%")
        elif ch == "&":
            out.append(r"\&")
        elif ch == "#":
            out.append(r"\#")
        elif ch == "_":
            out.append(r"\_")
        elif ch == "^":
            out.append(r"\textasciicircum{}")
        elif ch == "~":
            out.append(r"\textasciitilde{}")
        else:
            out.append(ch)
    return "".join(out)


def normalize_whitespace_lines(text: str) -> str:
    """合并行尾空格，统一换行。"""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines)


def text_to_latex_body(raw: str, preserve_linebreaks: bool = False) -> str:
    """
    将纯文本转为 LaTeX 段落：
    - 空行分段 -> 段间空行
    - 默认：段内单换行合并为空格
    - preserve_linebreaks：段内保留换行，用 LaTeX 的 \\\\ 连接各行（便于简历等排版）
    """
    raw = normalize_whitespace_lines(raw)
    paragraphs = re.split(r"\n\s*\n+", raw)
    chunks: list[str] = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        lines = [ln.strip() for ln in p.split("\n") if ln.strip()]
        if not lines:
            continue
        if preserve_linebreaks:
            merged = " \\\\\n".join(escape_latex(ln) for ln in lines)
        else:
            merged = escape_latex(" ".join(lines))
        chunks.append(merged + "\n\n")
    return "".join(chunks).rstrip() + "\n"


def extract_text_from_block(block: dict) -> str:
    """从 get_text('dict') 的文本块拼出字符串。"""
    parts: list[str] = []
    for line in block.get("lines") or []:
        for span in line.get("spans") or []:
            t = span.get("text") or ""
            parts.append(t)
        parts.append("\n")
    return "".join(parts)


def save_image_from_block(doc, block: dict) -> tuple[str, bytes] | None:
    """
    从图片块或 xref 取出图像字节。返回 (扩展名不含点, 原始字节)，失败返回 None。
    """
    xref = block.get("xref")
    if xref:
        try:
            info = doc.extract_image(xref)
            ext = (info.get("ext") or "png").lower()
            data = info.get("image")
            if data:
                if ext == "jpeg":
                    ext = "jpg"
                return ext, data
        except Exception:
            pass

    raw = block.get("image")
    if isinstance(raw, (bytes, bytearray)) and len(raw) > 0:
        ext = (block.get("ext") or "png").lower()
        if ext == "jpeg":
            ext = "jpg"
        return ext, bytes(raw)

    return None


def save_image_by_xref(doc, xref: int) -> tuple[str, bytes] | None:
    try:
        info = doc.extract_image(xref)
        ext = (info.get("ext") or "png").lower()
        data = info.get("image")
        if not data:
            return None
        if ext == "jpeg":
            ext = "jpg"
        return ext, data
    except Exception:
        return None


def _normalize_pdf_font_name(name: str) -> str:
    name = (name or "").strip()
    if "+" in name:
        name = name.split("+")[-1]
    return name.strip()


def _map_pdf_font_to_latex(pdf_font: str) -> str:
    """将 PDF 内部字体名映射为 \\fontspec{...}（需系统已装对应字体）。"""
    raw = _normalize_pdf_font_name(pdf_font)
    low = raw.lower()
    if "yahei" in low or "msyh" in low or "微软雅黑" in raw:
        return r"\fontspec{Microsoft YaHei}"
    if "simhei" in low or "heiti" in low or "黑体" in raw:
        return r"\fontspec{SimHei}"
    if "simsun" in low or ("song" in low and "yahei" not in low):
        return r"\fontspec{SimSun}"
    if "fangsong" in low or "仿宋" in raw:
        return r"\fontspec{FangSong}"
    if "kaiti" in low or "kai" == low[:3]:
        return r"\fontspec{KaiTi}"
    if "times" in low or "nimbus" in low or "timesnewroman" in low:
        return r"\fontspec{Times New Roman}"
    if "arial" in low or "helvetica" in low or "arialmt" in low:
        return r"\fontspec{Arial}"
    if "consolas" in low or "courier" in low or "mono" in low:
        return r"\fontspec{Consolas}"
    if raw and all(ord(c) < 128 for c in raw):
        safe = raw.replace("_", " ")
        return f"\\fontspec{{{safe}}}"
    return r"\rmfamily"


def _span_color_html(span: dict) -> str | None:
    c = span.get("color")
    if c is None:
        return None
    if isinstance(c, int):
        if c == 0:
            return None
        return f"{(c & 0xFFFFFF):06x}"
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        r, g, b = c[0], c[1], c[2]
        if isinstance(r, float):
            r, g, b = int(r * 255), int(g * 255), int(b * 255)
        return f"{int(r) & 255:02x}{int(g) & 255:02x}{int(b) & 255:02x}"
    return None


def _span_bold_italic(flags: int) -> tuple[bool, bool]:
    """PyMuPDF span flags：常见为 bit1=斜体，bit4=粗体。"""
    f = int(flags or 0)
    return bool(f & (1 << 4)), bool(f & (1 << 1))


def _latex_span_content(span: dict) -> str:
    """生成一个 span 的 LaTeX 内容（已转义），含颜色/粗斜体/字号/字体。"""
    text = span.get("text") or ""
    if text == "":
        return ""
    size = float(span.get("size") or 10.5)
    base = max(size * 1.2, size + 1.0)
    flags = int(span.get("flags") or 0)
    bold, italic = _span_bold_italic(flags)
    font_cmd = _map_pdf_font_to_latex(str(span.get("font") or ""))
    inner = escape_latex(text)
    col = _span_color_html(span)
    if col:
        inner = f"\\textcolor[HTML]{{{col}}}{{{inner}}}"
    if bold and italic:
        inner = f"\\textbf{{\\textit{{{inner}}}}}"
    elif bold:
        inner = f"\\textbf{{{inner}}}"
    elif italic:
        inner = f"\\textit{{{inner}}}"
    return f"{{{font_cmd}\\fontsize{{{size:.4f}}}{{{base:.4f}}}\\selectfont {inner}}}"


def _latex_tikz_text_node(span: dict, page_h: float) -> str | None:
    bbox = span.get("bbox")
    if not bbox or len(bbox) < 4:
        return None
    x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    content = _latex_span_content(span)
    if not content.strip():
        return None
    y_base = page_h - y1
    return (
        f"\\node[anchor=south west, inner sep=0pt, outer sep=0pt] "
        f"at ({x0:.4f},{y_base:.4f}) {{{content}}};\n"
    )


def _latex_tikz_image_node(x0: float, y0: float, x1: float, y1: float, page_h: float, rel: str) -> str:
    w = max(x1 - x0, 0.01)
    h = max(y1 - y0, 0.01)
    yb = page_h - y1
    return (
        f"\\node[anchor=south west, inner sep=0pt, outer sep=0pt] "
        f"at ({x0:.4f},{yb:.4f}) {{\\includegraphics"
        f"[width={w:.4f}pt,height={h:.4f}pt,keepaspectratio]{{{rel}}}}};\n"
    )


def _pdf_rgb_to_rgb01(rgb) -> tuple[float, float, float] | None:
    if rgb is None:
        return None
    if isinstance(rgb, (tuple, list)) and len(rgb) >= 3:
        r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
        if max(r, g, b) > 1.0 + 1e-6:
            r, g, b = r / 255.0, g / 255.0, b / 255.0
        return (r, g, b)
    return None


def _color_define_name(rgb: tuple[float, float, float], cache: dict[tuple[float, float, float], str]) -> tuple[str, str | None]:
    """返回 (LaTeX 颜色名, 若新建则返回 \\definecolor 行，否则 None)。"""
    key = tuple(round(float(x), 5) for x in rgb)
    if key in cache:
        return cache[key], None
    n = len(cache) + 1
    name = f"pdfvec{n}"
    cache[key] = name
    r, g, b = key
    line = f"\\definecolor{{{name}}}{{rgb}}{{{r:.5f},{g:.5f},{b:.5f}}}\n"
    return name, line


def _drawing_item_point(obj, page_h: float) -> str:
    """fitz.Point / tuple -> TikZ 坐标（PDF 原点在左上，y 向下）。"""
    import fitz

    if isinstance(obj, fitz.Point):
        x, y = float(obj.x), float(obj.y)
    elif hasattr(obj, "x") and hasattr(obj, "y"):
        x, y = float(obj.x), float(obj.y)
    else:
        x, y = float(obj[0]), float(obj[1])
    return f"({x:.4f},{page_h - y:.4f})"


def _tikz_rect_cmd(r, page_h: float) -> str:
    """矩形：fill/stroke 共用，坐标为左下与右上（TikZ y 轴向上）。"""
    x0, y0, x1, y1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
    p_bl = f"({x0:.4f},{page_h - y1:.4f})"
    p_tr = f"({x1:.4f},{page_h - y0:.4f})"
    return f"{p_bl} rectangle {p_tr}"


def _pdf_drawings_to_tikz(page, page_h: float) -> str:
    """
    将 page.get_drawings() 中的矢量路径转为 TikZ \\path/\\draw/\\fill。
    横线、矩形框、描边等会尽量保留；裁剪路径 type=clip 会跳过。
    """
    import fitz

    paths = page.get_drawings(extended=False)
    paths = sorted(paths, key=lambda p: int(p.get("seqno") or 0))
    color_cache: dict[tuple[float, float, float], str] = {}
    chunks: list[str] = []

    for path in paths:
        ptype = path.get("type") or ""
        if ptype == "clip":
            continue
        items = path.get("items") or []
        if not items:
            continue

        fill_rgb = _pdf_rgb_to_rgb01(path.get("fill"))
        stroke_rgb = _pdf_rgb_to_rgb01(path.get("color"))
        width = path.get("width")
        w_pt = float(width) if width is not None else None
        fill_op = path.get("fill_opacity")
        stroke_op = path.get("stroke_opacity")
        dashes = path.get("dashes")

        do_fill = ptype in ("f", "fs", "b") or (ptype == "" and fill_rgb is not None)
        do_stroke = ptype in ("s", "fs", "b") or (ptype == "" and stroke_rgb is not None and w_pt is not None)
        if ptype == "f":
            do_fill, do_stroke = True, False
        elif ptype == "s":
            do_fill, do_stroke = False, True
        elif ptype in ("fs", "b"):
            do_fill, do_stroke = True, True

        style_parts: list[str] = []
        def_lines: list[str] = []

        if do_fill and fill_rgb:
            nm, dl = _color_define_name(fill_rgb, color_cache)
            if dl:
                def_lines.append(dl)
            fo = float(fill_op) if fill_op is not None else 1.0
            style_parts.append(f"fill={nm}")
            if fo < 0.999:
                style_parts.append(f"fill opacity={fo:.4f}")
        elif do_fill and not fill_rgb:
            fo = float(fill_op) if fill_op is not None else 1.0
            style_parts.append("fill=black")
            if fo < 0.999:
                style_parts.append(f"fill opacity={fo:.4f}")

        if do_stroke:
            sr = stroke_rgb or (0.0, 0.0, 0.0)
            nm, dl = _color_define_name(sr, color_cache)
            if dl:
                def_lines.append(dl)
            style_parts.append(f"draw={nm}")
            sw = w_pt if w_pt is not None else 0.4
            style_parts.append(f"line width={sw:.4f}pt")
            so = float(stroke_op) if stroke_op is not None else 1.0
            if so < 0.999:
                style_parts.append(f"draw opacity={so:.4f}")
            if dashes:
                try:
                    ds = [float(x) for x in dashes[:12]]
                    pat_parts: list[str] = []
                    for j, v in enumerate(ds):
                        pat_parts.append(("on" if j % 2 == 0 else "off") + f" {v:.3f}pt")
                    style_parts.append("dash pattern={" + " ".join(pat_parts) + "}")
                except (TypeError, ValueError):
                    pass

        if not style_parts:
            continue

        style = ",".join(style_parts)

        # 单矩形（简历里常见分隔横线）
        if len(items) == 1 and items[0][0] == "re":
            rect = items[0][1]
            if not isinstance(rect, fitz.Rect):
                continue
            rc = _tikz_rect_cmd(rect, page_h)
            chunks.extend(def_lines)
            chunks.append(f"\\path[{style}] {rc};\n")
            continue

        # 通用路径：m / l / c / re 串联（moveto 后接 lineto，不重复 --）
        sub: list[str] = []
        for it in items:
            op = it[0]
            if op == "re":
                rect = it[1]
                if isinstance(rect, fitz.Rect):
                    sub.append(_tikz_rect_cmd(rect, page_h))
            elif op == "m":
                if sub:
                    sub.append(" ")
                sub.append(_drawing_item_point(it[1], page_h))
            elif op == "l":
                sub.append(" -- " + _drawing_item_point(it[1], page_h))
            elif op == "c" and len(it) >= 4:
                p1, p2, p3 = it[1], it[2], it[3]
                c1 = _drawing_item_point(p1, page_h)
                c2 = _drawing_item_point(p2, page_h)
                end = _drawing_item_point(p3, page_h)
                sub.append(f" .. controls {c1} and {c2} .. {end}")
            elif op == "qu" and len(it) >= 3:
                p1, p2 = it[1], it[2]
                c1 = _drawing_item_point(p1, page_h)
                end = _drawing_item_point(p2, page_h)
                sub.append(f" .. controls {c1} .. {end}")
            elif op == "h":
                sub.append(" -- cycle")

        path_str = "".join(sub).strip()
        if not path_str:
            continue
        chunks.extend(def_lines)
        chunks.append(f"\\path[{style}] {path_str};\n")

    return "".join(chunks)


def extract_pdf_absolute_layout(
    pdf_path: Path,
    tex_path: Path,
    fig_dir_name: str,
    embed_images: bool,
) -> tuple[str, tuple[float, float]]:
    """
    按块顺序用 TikZ 绝对坐标排版：矢量线/矩形（横线、框等）来自 get_drawings；
    文字保留 span 字号/字体/颜色；图片按 bbox 放置。
    返回 (正文片段, 首页纸张宽高 pt)。
    """
    import fitz

    assets_dir = tex_path.parent / fig_dir_name
    if embed_images:
        assets_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    parts: list[str] = []
    img_serial = 0

    first_wh: tuple[float, float] | None = None

    for page_index in range(len(doc)):
        page = doc[page_index]
        pw = float(page.rect.width)
        ph = float(page.rect.height)
        if first_wh is None:
            first_wh = (pw, ph)

        if page_index > 0:
            parts.append("\\clearpage\n")
            parts.append(f"\\newgeometry{{margin=0pt,papersize={{{pw:.4f}bp,{ph:.4f}bp}}}}\n")
        parts.append("\\thispagestyle{empty}\n")
        parts.append("\\noindent\n")
        parts.append("\\begin{tikzpicture}[x=1pt,y=1pt]\n")
        parts.append(f"\\useasboundingbox (0,0) rectangle ({pw:.4f},{ph:.4f});\n")

        vec = _pdf_drawings_to_tikz(page, ph)
        if vec.strip():
            parts.append("\\begin{scope}[on background layer]\n")
            parts.append(vec)
            parts.append("\\end{scope}\n")

        blocks = page.get_text("dict").get("blocks") or []
        extracted_xrefs: set[int] = set()

        for block in blocks:
            btype = block.get("type")
            if btype == 0:
                for line in block.get("lines") or []:
                    for span in line.get("spans") or []:
                        node = _latex_tikz_text_node(span, ph)
                        if node:
                            parts.append(node)
            elif btype == 1 and embed_images:
                got = save_image_from_block(doc, block)
                if got is None:
                    continue
                ext, data = got
                fname = f"img_p{page_index:03d}_{img_serial:03d}.{ext}"
                img_serial += 1
                fpath = assets_dir / fname
                fpath.write_bytes(data)
                xr = block.get("xref")
                if xr:
                    extracted_xrefs.add(int(xr))
                rel = f"{fig_dir_name}/{fname}".replace("\\", "/")
                bb = block.get("bbox")
                if bb and len(bb) >= 4:
                    x0, y0, x1, y1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
                    parts.append(_latex_tikz_image_node(x0, y0, x1, y1, ph, rel))

        if embed_images:
            for img in page.get_images(full=True):
                xref = int(img[0])
                if xref in extracted_xrefs:
                    continue
                rects = page.get_image_rects(xref)
                if not rects:
                    continue
                got = save_image_by_xref(doc, xref)
                if got is None:
                    continue
                ext, data = got
                fname = f"img_p{page_index:03d}_{img_serial:03d}.{ext}"
                img_serial += 1
                fpath = assets_dir / fname
                fpath.write_bytes(data)
                extracted_xrefs.add(xref)
                rel = f"{fig_dir_name}/{fname}".replace("\\", "/")
                for r in rects:
                    x0, y0 = float(r.x0), float(r.y0)
                    x1, y1 = float(r.x1), float(r.y1)
                    parts.append(_latex_tikz_image_node(x0, y0, x1, y1, ph, rel))

        parts.append("\\end{tikzpicture}\n")

    doc.close()
    if first_wh is None:
        first_wh = (595.0, 842.0)
    return "".join(parts), first_wh


def build_document_absolute(
    body: str,
    first_page_wh: tuple[float, float],
    use_hyperref: bool,
) -> str:
    """TikZ 绝对坐标版导言区：零边距，纸张与首页 PDF 一致。"""
    w0, h0 = first_page_wh
    hyper = ""
    if use_hyperref:
        hyper = (
            "\\usepackage{hyperref}\n"
            "\\hypersetup{colorlinks=true, linkcolor=black, urlcolor=blue}\n"
        )
    font_setup = (
        "\\usepackage{fontspec}\n"
        "\\setmainfont{Times New Roman}[Ligatures=TeX]\n"
        "\\setCJKmainfont{SimSun}[AutoFakeBold=2.5,AutoFakeSlant=0.2]\n"
        "\\setCJKsansfont{Microsoft YaHei}\n"
        "\\setCJKmonofont{FangSong}\n"
    )
    return (
        "% !TEX program = xelatex\n"
        "% --layout absolute：按 PDF 坐标排版，文字仍为可编辑内容；与原版完全一致取决于 PDF 复杂度。\n"
        "\\documentclass{article}\n"
        "\\usepackage[UTF8,fontset=none]{ctex}\n"
        f"{font_setup}"
        "\\usepackage{tikz}\n"
        "\\usetikzlibrary{backgrounds}\n"
        "\\usepackage{xcolor}\n"
        "\\usepackage{graphicx}\n"
        f"\\usepackage[margin=0pt,papersize={{{w0:.4f}bp,{h0:.4f}bp}}]{{geometry}}\n"
        f"{hyper}"
        "\\pagestyle{empty}\n"
        "\\setlength{\\parindent}{0pt}\n"
        "\\setlength{\\parskip}{0pt}\n"
        "\\begin{document}\n\n"
        f"{body}"
        "\n\\end{document}\n"
    )


def extract_pdf_text_only(pdf_path: Path, page_sep: str = "\n\n") -> str:
    """仅抽取全文文本（旧行为，用于 --text-only）。"""
    import fitz

    doc = fitz.open(pdf_path)
    parts: list[str] = []
    for i in range(len(doc)):
        page = doc[i]
        t = page.get_text("text") or ""
        t = normalize_whitespace_lines(t)
        parts.append(t.strip())
    doc.close()
    return page_sep.join(s for s in parts if s)


def extract_pdf_with_images(
    pdf_path: Path,
    tex_path: Path,
    fig_dir_name: str,
    page_break_cmd: str | None,
    preserve_linebreaks: bool = False,
) -> str:
    """
    按页内块顺序输出 LaTeX：文本段落 + 图片占位。
    图片写入 tex_path.parent / fig_dir_name / ...
    返回完整正文（不含 documentclass）。
    """
    import fitz

    assets_dir = tex_path.parent / fig_dir_name
    assets_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    body_chunks: list[str] = []
    img_serial = 0

    for page_index in range(len(doc)):
        page = doc[page_index]
        blocks = page.get_text("dict").get("blocks") or []

        extracted_xrefs: set[int] = set()

        for block in blocks:
            btype = block.get("type")
            if btype == 0:
                raw = extract_text_from_block(block)
                raw = normalize_whitespace_lines(raw).strip()
                if raw:
                    body_chunks.append(
                        text_to_latex_body(raw, preserve_linebreaks=preserve_linebreaks)
                    )
            elif btype == 1:
                got = save_image_from_block(doc, block)
                if got is None:
                    continue
                ext, data = got
                fname = f"img_p{page_index:03d}_{img_serial:03d}.{ext}"
                img_serial += 1
                fpath = assets_dir / fname
                fpath.write_bytes(data)
                # 块内 xref 若存在则记下，避免 fallback 重复
                xr = block.get("xref")
                if xr:
                    extracted_xrefs.add(int(xr))
                rel = f"{fig_dir_name}/{fname}".replace("\\", "/")
                body_chunks.append(
                    f"\\begin{{center}}\n"
                    f"\\includegraphics[width=0.92\\linewidth,keepaspectratio]{{{rel}}}\n"
                    f"\\end{{center}}\n\n"
                )

        # 部分 PDF 不把图放进 dict 的 type=1 块，用 get_image_rects 补全
        for img in page.get_images(full=True):
            xref = int(img[0])
            if xref in extracted_xrefs:
                continue
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            got = save_image_by_xref(doc, xref)
            if got is None:
                continue
            ext, data = got
            fname = f"img_p{page_index:03d}_{img_serial:03d}.{ext}"
            img_serial += 1
            fpath = assets_dir / fname
            fpath.write_bytes(data)
            extracted_xrefs.add(xref)
            rel = f"{fig_dir_name}/{fname}".replace("\\", "/")
            for _ in rects:
                body_chunks.append(
                    f"\\begin{{center}}\n"
                    f"\\includegraphics[width=0.92\\linewidth,keepaspectratio]{{{rel}}}\n"
                    f"\\end{{center}}\n\n"
                )

        if page_break_cmd and page_index < len(doc) - 1:
            body_chunks.append(page_break_cmd + "\n\n")

    doc.close()
    return "".join(body_chunks)


def extract_exact_page_images(
    pdf_path: Path,
    tex_path: Path,
    pages_dir_name: str,
    dpi: float,
) -> tuple[list[tuple[float, float]], list[str]]:
    """
    将每一页整页光栅化为 PNG，用于 LaTeX 中按原纸张尺寸 1:1 插入。
    返回 (每页宽高点数列表 [(w_pt,h_pt), ...], 相对 tex 的图片路径列表)。
    """
    import fitz

    out_dir = tex_path.parent / pages_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    sizes: list[tuple[float, float]] = []
    rel_paths: list[str] = []

    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)

    for i in range(len(doc)):
        page = doc[i]
        r = page.rect
        sizes.append((float(r.width), float(r.height)))
        pix = page.get_pixmap(matrix=mat, alpha=False)
        fname = f"page_{i:03d}.png"
        fpath = out_dir / fname
        pix.save(str(fpath))
        rel_paths.append(f"{pages_dir_name}/{fname}".replace("\\", "/"))

    doc.close()
    return sizes, rel_paths


def build_document_exact(
    page_sizes: list[tuple[float, float]],
    page_image_rels: list[str],
    use_hyperref: bool,
) -> str:
    """
    每页一张整页图，纸张与 PDF MediaBox 一致（bp），边距 0，实现版式 1:1。
    不插入额外标题，避免破坏首页版式。
    """
    if len(page_sizes) != len(page_image_rels) or not page_sizes:
        raise ValueError("page_sizes 与图片路径数量不一致或为空")

    hyper = ""
    if use_hyperref:
        hyper = (
            "\\usepackage{hyperref}\n"
            "\\hypersetup{colorlinks=true, linkcolor=black, urlcolor=blue}\n"
        )

    w0, h0 = page_sizes[0]
    body_parts: list[str] = []

    for idx, rel in enumerate(page_image_rels):
        w_pt, h_pt = page_sizes[idx]
        if idx > 0:
            body_parts.append("\\clearpage\n")
            pw, ph = page_sizes[idx - 1]
            if (w_pt, h_pt) != (pw, ph):
                body_parts.append(
                    f"\\newgeometry{{margin=0pt,papersize={{{w_pt:.4f}bp,{h_pt:.4f}bp}}}}\n"
                )
        body_parts.append("\\thispagestyle{empty}\n")
        body_parts.append("\\noindent\n")
        body_parts.append(
            f"\\includegraphics[width=\\paperwidth,height=\\paperheight,keepaspectratio]{{{rel}}}\n"
        )

    body = "".join(body_parts)

    return (
        "% !TEX program = xelatex\n"
        "% 由 --exact 生成：每页为整页位图，无法在正文中编辑文字。需要可编辑 .tex 请去掉 --exact 重新导出。\n"
        "\\documentclass{article}\n"
        f"\\usepackage[margin=0pt,papersize={{{w0:.4f}bp,{h0:.4f}bp}}]{{geometry}}\n"
        "\\usepackage{graphicx}\n"
        "\\usepackage[UTF8]{ctex}\n"
        f"{hyper}"
        "\\pagestyle{empty}\n"
        "\\begin{document}\n\n"
        f"{body}"
        "\n\\end{document}\n"
    )


def build_document(body: str, title: str | None, use_hyperref: bool) -> str:
    title = title or "PDF 导出"
    hyper = ""
    if use_hyperref:
        hyper = (
            "\\usepackage{hyperref}\n"
            "\\hypersetup{colorlinks=true, linkcolor=black, urlcolor=blue}\n"
        )
    # 全文统一字体：西文 Times New Roman，中文宋体（Windows 常见字体，与 ctex 协调）
    font_setup = (
        "\\usepackage{fontspec}\n"
        "\\setmainfont{Times New Roman}[Ligatures=TeX]\n"
        "\\setCJKmainfont{SimSun}[AutoFakeBold=2.5,AutoFakeSlant=0.2]\n"
        "\\setCJKsansfont{Microsoft YaHei}\n"
        "\\setCJKmonofont{FangSong}\n"
    )
    return (
        "% !TEX program = xelatex\n"
        "\\documentclass[11pt,a4paper]{article}\n"
        "\\usepackage[UTF8,fontset=none]{ctex}\n"
        f"{font_setup}"
        "\\usepackage[margin=2.2cm]{geometry}\n"
        "\\usepackage{graphicx}\n"
        f"{hyper}"
        "\\setlength{\\parindent}{0pt}\n"
        "\\setlength{\\parskip}{0.45em}\n"
        "\\begin{document}\n\n"
        f"\\begin{{center}}{{\\Large\\textbf{{{escape_latex(title)}}}}}\\end{{center}}\n"
        "\\vspace{0.8em}\n\n"
        f"{body}"
        "\n\\end{document}\n"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="PDF 文本层 + 图片 -> LaTeX（ctex + xelatex）")
    parser.add_argument("pdf", type=Path, help="输入 PDF 路径")
    parser.add_argument("-o", "--output", type=Path, default=None, help="输出 .tex 路径（默认与 PDF 同名）")
    parser.add_argument("--title", default=None, help="文档标题（默认取 PDF 文件名）")
    parser.add_argument("--raw", action="store_true", help="仅输出转义后的正文，不含导言区")
    parser.add_argument("--no-hyperref", action="store_true", help="不加载 hyperref 包")
    parser.add_argument(
        "--page-break",
        action="store_true",
        help="分页之间插入 \\clearpage（默认用空行分隔各页文本）",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="不导出图片，仅抽取文本（旧行为）",
    )
    parser.add_argument(
        "--fig-dir",
        default=None,
        help="图片保存子目录名（默认：<输出tex主名>_figs）",
    )
    parser.add_argument(
        "--preserve-linebreaks",
        action="store_true",
        help="段内保留 PDF 换行（用 LaTeX \\\\），便于简历等；默认可编辑模式仍合并为段落",
    )
    parser.add_argument(
        "--layout",
        choices=("flow", "absolute"),
        default="flow",
        help="flow=段落流式（默认）；absolute=按坐标/TikZ 尽量复原字号字体与图文位置（可编辑）",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="视觉 1:1：每页整页光栅图（不可编辑文字）。要后期改字请勿使用，改用默认导出",
    )
    parser.add_argument(
        "--dpi",
        type=float,
        default=150.0,
        help="配合 --exact：整页光栅化的 DPI（默认 150，越大越清晰但文件越大）",
    )
    parser.add_argument(
        "--pages-dir",
        default=None,
        help="配合 --exact：整页 PNG 子目录名（默认：<输出tex主名>_pages）",
    )
    args = parser.parse_args(argv)

    pdf_path: Path = args.pdf.expanduser().resolve()
    if not pdf_path.is_file():
        print(f"文件不存在: {pdf_path}", file=sys.stderr)
        return 1

    try:
        import fitz  # noqa: F401
    except ImportError:
        print("请先安装: pip install pymupdf", file=sys.stderr)
        return 1

    out_path = args.output
    if out_path is None:
        out_path = pdf_path.with_suffix(".tex")
    else:
        out_path = out_path.expanduser().resolve()

    fig_dir_name = args.fig_dir
    if fig_dir_name is None:
        fig_dir_name = f"{out_path.stem}_figs"

    pages_dir_name = args.pages_dir
    if pages_dir_name is None:
        pages_dir_name = f"{out_path.stem}_pages"

    page_break_cmd = "\n\n\\clearpage\n\n" if args.page_break else "\n\n"
    page_sep = page_break_cmd if args.page_break else "\n\n"

    if args.exact and args.layout == "absolute":
        print("不能同时使用 --exact 与 --layout absolute", file=sys.stderr)
        return 2

    if args.exact:
        if args.raw:
            print("--exact 与 --raw 不能同时使用", file=sys.stderr)
            return 2
        print(
            "提示: --exact 为整页位图，无法在 .tex 里编辑正文文字；需要可编辑请去掉 --exact。",
            file=sys.stderr,
        )
        sizes, rels = extract_exact_page_images(
            pdf_path,
            out_path,
            pages_dir_name=pages_dir_name,
            dpi=float(args.dpi),
        )
        out_tex = build_document_exact(
            sizes,
            rels,
            use_hyperref=not args.no_hyperref,
        )
    elif args.layout == "absolute":
        if args.raw:
            print("--layout absolute 不能与 --raw 同时使用", file=sys.stderr)
            return 2
        body_abs, first_wh = extract_pdf_absolute_layout(
            pdf_path,
            out_path,
            fig_dir_name=fig_dir_name,
            embed_images=not args.no_images,
        )
        out_tex = build_document_absolute(
            body_abs,
            first_wh,
            use_hyperref=not args.no_hyperref,
        )
    elif args.no_images:
        combined = extract_pdf_text_only(pdf_path, page_sep=page_sep)
        body = text_to_latex_body(
            combined,
            preserve_linebreaks=args.preserve_linebreaks,
        )
        if args.raw:
            out_tex = body
        else:
            title = args.title if args.title else pdf_path.stem
            out_tex = build_document(
                body,
                title=title,
                use_hyperref=not args.no_hyperref,
            )
    else:
        body = extract_pdf_with_images(
            pdf_path,
            out_path,
            fig_dir_name=fig_dir_name,
            page_break_cmd="\n\n\\clearpage\n\n" if args.page_break else None,
            preserve_linebreaks=args.preserve_linebreaks,
        )
        if args.raw:
            out_tex = body
        else:
            title = args.title if args.title else pdf_path.stem
            out_tex = build_document(
                body,
                title=title,
                use_hyperref=not args.no_hyperref,
            )

    out_path.write_text(out_tex, encoding="utf-8")
    print(f"已写入: {out_path}")
    if args.exact:
        print(f"整页图目录: {out_path.parent / pages_dir_name}")
    elif not args.no_images and not args.raw:
        print(f"图片目录: {out_path.parent / fig_dir_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
