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
