"""LaTeX template discovery and render job helpers."""

from __future__ import annotations

import hashlib
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from tools.latex import compile_tex

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = BASE_DIR / "tex warehouse"
RENDER_ROOT = BASE_DIR / "generated" / "tex_renders"


@dataclass(frozen=True)
class TemplateDef:
    id: str
    name: str
    tex_path: Path
    tex_rel_path: str
    preview_rel_path: str | None


@dataclass
class RenderJob:
    job_id: str
    template_id: str
    template_name: str
    job_dir: Path
    tex_path: Path
    pdf_path: Path
    bundle_path: Path
    preview_path: Path | None = None
    preview_paths: list[Path] | None = None
    stdout: str = ""
    stderr: str = ""


_JOBS: dict[str, RenderJob] = {}


def _is_git_path(path: Path) -> bool:
    return ".git" in path.parts


def _template_id(rel: str) -> str:
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]


def _guess_preview(tex_file: Path) -> Path | None:
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = tex_file.with_suffix(ext)
        if p.is_file():
            return p
    return None


def discover_templates() -> list[TemplateDef]:
    if not TEMPLATE_ROOT.is_dir():
        return []
    out: list[TemplateDef] = []
    for tex_file in TEMPLATE_ROOT.rglob("*.tex"):
        if _is_git_path(tex_file):
            continue
        rel = tex_file.relative_to(TEMPLATE_ROOT).as_posix()
        preview = _guess_preview(tex_file)
        preview_rel = preview.relative_to(TEMPLATE_ROOT).as_posix() if preview else None
        out.append(
            TemplateDef(
                id=_template_id(rel),
                name=f"{tex_file.parent.name}/{tex_file.stem}",
                tex_path=tex_file,
                tex_rel_path=rel,
                preview_rel_path=preview_rel,
            )
        )
    out.sort(key=lambda x: x.tex_rel_path)
    return out


def get_template_by_id(template_id: str) -> TemplateDef | None:
    for t in discover_templates():
        if t.id == template_id:
            return t
    return None


def build_preview_url(preview_rel_path: str | None) -> str | None:
    if not preview_rel_path:
        return None
    return "/tex-warehouse-assets/" + quote(preview_rel_path, safe="/")


def read_template_text(t: TemplateDef) -> str:
    return t.tex_path.read_text(encoding="utf-8")


def _copy_template_assets(template_dir: Path, target_dir: Path) -> None:
    def _ignore(dir_path: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        if Path(dir_path).name == ".git":
            return set(names)
        for n in names:
            if n == ".git":
                ignored.add(n)
        return ignored

    shutil.copytree(template_dir, target_dir, dirs_exist_ok=True, ignore=_ignore)


def _new_job_dir() -> tuple[str, Path]:
    RENDER_ROOT.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:16]
    job_dir = RENDER_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    return job_id, job_dir


def _create_bundle_zip(job_dir: Path, bundle_path: Path) -> None:
    """Zip the full render workspace so users can compile locally."""
    with zipfile.ZipFile(bundle_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(job_dir.rglob("*")):
            if not p.is_file():
                continue
            if p.resolve() == bundle_path.resolve():
                continue
            zf.write(p, p.relative_to(job_dir))


def _render_preview_pngs(pdf_path: Path, out_dir: Path) -> list[Path]:
    """Render all PDF pages as preview images."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_paths: list[Path] = []
    try:
        import fitz

        doc = fitz.open(pdf_path)
        if doc.page_count <= 0:
            doc.close()
            return out_paths
        for idx in range(doc.page_count):
            page = doc[idx]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
            p = out_dir / f"preview_{idx + 1:03d}.png"
            pix.save(str(p))
            out_paths.append(p)
        doc.close()
        return out_paths
    except Exception:
        return out_paths


def render_template_to_pdf(template: TemplateDef, rewritten_tex: str) -> RenderJob:
    job_id, job_dir = _new_job_dir()
    _copy_template_assets(template.tex_path.parent, job_dir)
    out_tex = job_dir / template.tex_path.name
    out_tex.write_text(rewritten_tex, encoding="utf-8")

    last = compile_tex(out_tex, engine="xelatex", passes=2, halt_on_error=True)
    out_pdf = out_tex.with_suffix(".pdf")
    if last.returncode != 0:
        raise RuntimeError(
            "模板编译失败。\n"
            f"stdout:\n{(last.stdout or '')[-12000:]}\n"
            f"stderr:\n{(last.stderr or '')[-12000:]}"
        )
    if not out_pdf.is_file():
        raise RuntimeError("模板编译完成但未找到 PDF 输出文件")
    preview_paths = _render_preview_pngs(out_pdf, job_dir / "previews")
    preview_path = preview_paths[0] if preview_paths else None
    bundle_path = job_dir / "render_bundle.zip"
    _create_bundle_zip(job_dir, bundle_path)

    job = RenderJob(
        job_id=job_id,
        template_id=template.id,
        template_name=template.name,
        job_dir=job_dir,
        tex_path=out_tex,
        pdf_path=out_pdf,
        bundle_path=bundle_path,
        preview_path=preview_path,
        preview_paths=preview_paths,
        stdout=(last.stdout or "")[-4000:],
        stderr=(last.stderr or "")[-4000:],
    )
    _JOBS[job_id] = job
    return job


def get_render_job(job_id: str) -> RenderJob | None:
    return _JOBS.get(job_id)
