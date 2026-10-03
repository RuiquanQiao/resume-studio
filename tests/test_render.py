"""LaTeX rendering: escaping, every template in both languages, overflow detection."""
from __future__ import annotations

import shutil

import pytest

from core.store import to_plain
from resume import model
from resume.render import all_templates, get_renderer
from resume.render.latex import markup
from resume.render.raster import find_pdftoppm, rasterize
from resume.service import Studio

pytestmark = pytest.mark.latex
needs_tex = pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")


def test_markup_escapes_and_formats():
    assert markup("R&D 100% $5 #1 a_b {x} ~ ^ \\") == \
        r"R\&D 100\% \$5 \#1 a\_b \{x\} \textasciitilde{} \textasciicircum{} \textbackslash{}"
    assert markup("**bold** and *it*") == r"\textbf{bold} and \textit{it}"
    assert markup("[site](https://a.b/c_d?x=1#y)") == r"\href{https://a.b/c_d?x=1\#y}{site}"
    assert markup("**a_b**") == r"\textbf{a\_b}"


@needs_tex
@pytest.mark.parametrize("template", [t["id"] for t in all_templates()])
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_every_template_renders_one_page(data_file, template, lang):
    st = Studio(data_file)
    doc, _ = st.store.load()
    v = to_plain(model.get_item(doc, "version", "RES-TECH-EN"))
    v["template"], v["lang"] = template, lang
    out = data_file.parent / f"build-{template}-{lang}"
    r = get_renderer().render(model.build_view(doc, v), out)
    assert r.ok, r.errors
    assert r.pages == 1
    if find_pdftoppm():
        assert len(rasterize(r.pdf, out)) == 1


@needs_tex
def test_hostile_characters_compile(data_file):
    st = Studio(data_file)
    doc, _ = st.store.load()
    doc["entries"][1]["bullets"].append({"en": "C# & C++, 100% R&D_ops ~ ^ {x} \\ $5 #1 **bold_x** [a_b](https://x.y/a_b#c)",
                                         "zh": "中文 & 符号 100% 与 [链接](https://x.y)"})
    for lang in ("en", "zh"):
        v = to_plain(model.get_item(doc, "version", "RES-TECH-EN"))
        v["lang"] = lang
        r = get_renderer().render(model.build_view(doc, v), data_file.parent / f"hostile-{lang}")
        assert r.ok, r.errors


@needs_tex
def test_overflow_is_reported(data_file):
    st = Studio(data_file)
    doc, _ = st.store.load()
    long = {"en": "Designed and shipped a thing that did something measurable for real users " * 2}
    doc["entries"][1]["bullets"] = [long] * 60
    r = get_renderer().render(model.build_view(doc, to_plain(doc["versions"][0])), data_file.parent / "over")
    assert r.ok and r.pages >= 2


@needs_tex
def test_service_render_and_export(data_file):
    st = Studio(data_file)
    res = st.render("RES-TECH-ZH")
    assert res["ok"] and res["pages"] == 1
    if find_pdftoppm():
        assert res["images"] == 1 and st.page_image("RES-TECH-ZH", 1).exists()
    ex = st.export("RES-TECH-ZH")
    assert (data_file.parent / "exports" / "RES-TECH-ZH.pdf").exists()
    assert (data_file.parent / "exports" / "RES-TECH-ZH.tex").exists()
    assert ex["exported"]["pdf"].endswith("RES-TECH-ZH.pdf")


# ---------------------------------------------------------------- one line per bullet
LONG_EN = "Designed an evaluation harness that scores every generated question against the source paper and flags hallucinated facts before export"


@needs_tex
@pytest.mark.parametrize("template", [t["id"] for t in all_templates()])
def test_bullet_line_counts_are_measured(data_file, template):
    st = Studio(data_file)
    doc, _ = st.store.load()
    doc["entries"][1]["bullets"].append({"en": LONG_EN, "zh": "设计评测流程，导出前逐题对照原文打分"})
    st.store.save(doc)
    v = to_plain(model.get_item(doc, "version", "RES-TECH-EN"))
    v["template"] = template
    r = get_renderer().render(model.build_view(doc, v), data_file.parent / f"lines-{template}")
    lines = r.lines["EXP-002"]
    assert lines["2"]["n"] == 2 and lines["2"]["fill"] > 1           # the long one wraps, and we know by how much
    assert lines["1"]["n"] == 1 and 0 < lines["1"]["fill"] < 1
    zh = st.render("RES-TECH-EN", lang="zh", images=False)             # same template + layout, other language
    assert zh["lang"] == "zh" and zh["lines"]["EXP-002"]["2"]["n"] == 1
    report = st.bullet_report(st.render("RES-TECH-EN", images=False))
    assert any(b["id"] == "EXP-002" and b["index"] == 2 and b["lines"] == 2 for b in report)


def _gray(pdf):
    import subprocess
    raw = subprocess.run([find_pdftoppm(), "-r", "90", "-gray", "-singlefile", str(pdf)], capture_output=True).stdout
    return raw


@needs_tex
@pytest.mark.skipif(not find_pdftoppm(), reason="pdftoppm not installed")
@pytest.mark.parametrize("template", [t["id"] for t in all_templates()])
def test_measuring_never_changes_the_page(data_file, tmp_path, template):
    r"""\rsitem must typeset exactly like a plain \item: same pixels."""
    import jinja2
    from resume.render.latex import TEMPLATE_DIR, LatexRenderer

    plain = tmp_path / "plain-templates"
    shutil.copytree(TEMPLATE_DIR, plain)
    pre = plain / "_preamble.tex.j2"
    text = pre.read_text(encoding="utf-8")
    start = text.index(r"\newcommand{\rsitem}")
    end = text.index("\n", start)
    pre.write_text(text[:start] + r"\newcommand{\rsitem}[2]{\item #2}" + text[end:], encoding="utf-8")

    st = Studio(data_file)
    doc, _ = st.store.load()
    doc["entries"][1]["bullets"].append({"en": LONG_EN, "zh": "设计评测流程"})
    for lang in ("en", "zh"):
        v = to_plain(model.get_item(doc, "version", "RES-TECH-EN"))
        v["template"], v["lang"] = template, lang
        view = model.build_view(doc, v)
        measured = LatexRenderer().render(view, tmp_path / f"m-{lang}")
        r = LatexRenderer()
        r.env.loader = jinja2.FileSystemLoader(str(plain))
        reference = r.render(view, tmp_path / f"p-{lang}")
        assert measured.ok and reference.ok
        assert _gray(measured.pdf) == _gray(reference.pdf), f"{template}/{lang} layout changed"


@needs_tex
def test_empty_string_hides_a_bullet_in_that_language(data_file):
    st = Studio(data_file)
    doc, _ = st.store.load()
    doc["entries"][1]["bullets"][1] = {"en": "English only bullet", "zh": ""}
    doc["entries"][1]["bullets"].append({"en": "Not yet translated"})
    st.store.save(doc)
    zh = st.render("RES-TECH-EN", lang="zh", images=False)
    tex = (data_file.parent / ".studio" / "build" / "RES-TECH-EN@zh" / "main.tex").read_text(encoding="utf-8")
    assert "English only bullet" not in tex                 # '' = not on the Chinese resume
    assert "Not yet translated" in tex                      # missing key = falls back, and is reported
    assert {"id": "EXP-002", "fields": [], "bullets": [2]} in zh["missing"]
    assert set(zh["lines"]["EXP-002"]) == {"0", "2"}          # indexes stay the entry's own
