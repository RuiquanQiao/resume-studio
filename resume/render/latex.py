"""LaTeX renderer: Jinja2 templates -> .tex -> xelatex -> PDF."""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import jinja2

from .base import Renderer, RenderResult

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "latex"

_TEX_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_ESC_RE = re.compile(r"[\\&%$#_{}~^]")
# **bold**, *italic*, [text](url)
_MARK_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\(([^)\s]+)\)")
_CJK_RE = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")


def escape(text: str) -> str:
    return _ESC_RE.sub(lambda m: _TEX_ESCAPES[m.group(0)], str(text))


def escape_url(url: str) -> str:
    return re.sub(r"([%#\\{}])", r"\\\1", str(url))


def markup(text: str) -> str:
    """Escape text for LaTeX while honouring the tiny inline markup."""
    text = str(text or "")
    out, pos = [], 0
    for m in _MARK_RE.finditer(text):
        out.append(escape(text[pos:m.start()]))
        if m.group(1) is not None:
            out.append(r"\textbf{" + markup(m.group(1)) + "}")
        elif m.group(2) is not None:
            out.append(r"\textit{" + markup(m.group(2)) + "}")
        else:
            out.append(r"\href{" + escape_url(m.group(4)) + "}{" + markup(m.group(3)) + "}")
        pos = m.end()
    out.append(escape(text[pos:]))
    return "".join(out)


def _has_cjk(obj) -> bool:
    if isinstance(obj, str):
        return bool(_CJK_RE.search(obj))
    if isinstance(obj, dict):
        return any(_has_cjk(v) for k, v in obj.items() if k != "layout")
    if isinstance(obj, list):
        return any(_has_cjk(v) for v in obj)
    return False


def _read_meta(path: Path) -> dict:
    """First lines of a template: `%% name: ...` / `%% description: ...`."""
    meta = {"id": path.name.removesuffix(".tex.j2"), "name": "", "description": ""}
    for line in path.read_text(encoding="utf-8").splitlines()[:5]:
        m = re.match(r"%%\s*(name|description):\s*(.+)", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    meta["name"] = meta["name"] or meta["id"]
    return meta


class LatexRenderer(Renderer):
    source_suffix = ".tex"

    def __init__(self) -> None:
        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
            block_start_string="((*", block_end_string="*))",
            variable_start_string="(((", variable_end_string=")))",
            comment_start_string="((=", comment_end_string="=))",
            trim_blocks=True, lstrip_blocks=True, autoescape=False,
            undefined=jinja2.StrictUndefined,
        )
        self.env.filters["tex"] = markup
        self.env.filters["url"] = escape_url
        self.env.filters["num"] = lambda x: f"{float(x):.2f}"
        self.env.filters["bare"] = lambda u: re.sub(r"^(https?://|mailto:)", "", str(u)).rstrip("/")
        self._locks: dict[str, threading.Lock] = {}

    def templates(self) -> list[dict]:
        # files starting with "_" are shared partials, not selectable templates
        return [_read_meta(p) for p in sorted(TEMPLATE_DIR.glob("*.tex.j2")) if not p.name.startswith("_")]

    def compiler(self) -> str | None:
        return shutil.which("xelatex")

    def render(self, view: dict, build_dir: Path) -> RenderResult:
        t0 = time.time()
        build_dir.mkdir(parents=True, exist_ok=True)
        lock = self._locks.setdefault(str(build_dir), threading.Lock())
        with lock:
            try:
                tpl = self.env.get_template(f"{view['template']}.tex.j2")
            except jinja2.TemplateNotFound:
                return RenderResult(False, errors=[f"找不到模板：{view['template']}"])
            try:
                tex = tpl.render(**view, has_cjk=_has_cjk(view))
            except jinja2.TemplateError as e:
                return RenderResult(False, errors=[f"模板错误：{e}"])
            src = build_dir / "main.tex"
            src.write_text(tex, encoding="utf-8")
            exe = self.compiler()
            if not exe:
                return RenderResult(False, source=src, errors=["找不到 xelatex，请安装 TeX Live 或 MiKTeX。"])
            try:
                proc = subprocess.run(
                    [exe, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "main.tex"],
                    cwd=str(build_dir), capture_output=True, timeout=120,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                return RenderResult(False, source=src, errors=["xelatex 超时（120 秒）"])
            log_path = build_dir / "main.log"
            log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else \
                proc.stdout.decode("utf-8", errors="replace")
            pdf = build_dir / "main.pdf"
            pages = 0
            m = re.search(r"Output written on .*?\((\d+) pages?", log, re.S)
            if m:
                pages = int(m.group(1))
            errors = []
            if proc.returncode != 0 or not pdf.exists():
                errors = [ln for ln in log.splitlines()
                          if ln.startswith("!") or re.match(r".*main\.tex:\d+:", ln)][:8] \
                    or ["xelatex 编译失败，详见 main.log"]
            return RenderResult(ok=not errors, pages=pages, pdf=pdf if pdf.exists() else None,
                                source=src, seconds=time.time() - t0, errors=errors,
                                lines=_bullet_lines(log, view))


_RSLINE_RE = re.compile(r"^RSLINE (\d+)\.(\d+) (\d+) ([\d.]+)pt ([\d.]+)pt", re.M)


def _bullet_lines(log: str, view: dict) -> dict:
    """Read what \\rsitem wrote to the log: lines per bullet and how full the text is."""
    ids = {e["ref"]: e["id"] for s in view.get("sections", []) for e in s.get("entries", [])}
    out: dict = {}
    for m in _RSLINE_RE.finditer(log):
        eid = ids.get(int(m.group(1)))
        if eid is None:
            continue
        width, line = float(m.group(4)), float(m.group(5))
        out.setdefault(eid, {})[m.group(2)] = {"n": int(m.group(3)), "fill": round(width / line, 3) if line else 0}
    return out
