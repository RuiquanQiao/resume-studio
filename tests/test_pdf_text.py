"""The exported PDF reads as text in a resume parser.

Recruiting systems (北森, Moka, Workday…) parse the uploaded PDF into fields from its text layer. Two
independent parsers read every template in both languages: pypdf (reads only /ToUnicode, like PDFBox) and
pdfminer. Found on 2026-11-18: pypdf read every Chinese character as noise (FandolSong had no /ToUnicode),
both read the contact icons as letters ("Ȳ +61 400…"), the hyphen came out as U+2011 so "scikit-learn" missed
a keyword match, and TeX split words at a line's end ("Interac-" / "tion").
"""
from __future__ import annotations

import re
import shutil

import pytest

from core.store import to_plain
from resume import model
from resume.render import all_templates, get_renderer

pytestmark = pytest.mark.latex
needs_tex = pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")

pypdf = pytest.importorskip("pypdf")
pdfminer = pytest.importorskip("pdfminer.high_level")

# what the sample resume (examples/resume.sample.yaml) says, per language; compared with all white space
# taken out, since parsers differ in where they put spaces
EXPECTED = {
    "en": ["Alex Chen", "Human-Computer Interaction", "scikit-learn", "Harbour City University",
           "Built a multi-stage LLM pipeline that turns lecture slides into practice quizzes",
           # the contact line, icons and all: nothing may be read between the three
           "alex@example.com github.com/alexchen +61 400 000 000"],
    "zh": ["陈亚历", "计算机硕士在读", "主修课程：机器学习、分布式系统、数据库、人机交互", "scikit-learn",
           "搭建多阶段大模型流水线，把课件自动转成练习题", "alex@example.com github.com/alexchen +61 400 000 000"],
}
BAD = re.compile(r"[‐‑­�-]|\(cid:\d+\)")


def _flat(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _readers(pdf):
    yield "pypdf", "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(str(pdf)).pages)
    yield "pdfminer", pdfminer.extract_text(str(pdf))


def _render(data_file, template, lang, edit=None):
    from resume.service import Studio
    doc, _ = Studio(data_file).store.load()
    if edit:
        edit(doc)
    v = to_plain(model.get_item(doc, "subset", "RES-TECH-EN"))
    v["template"], v["lang"] = template, lang
    r = get_renderer().render(model.build_view(doc, v), data_file.parent / f"text-{template}-{lang}")
    assert r.ok, r.errors
    return r.pdf


@needs_tex
@pytest.mark.parametrize("template", [t["id"] for t in all_templates()])
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_every_parser_reads_the_resume(data_file, template, lang):
    pdf = _render(data_file, template, lang)
    for name, text in _readers(pdf):
        flat = _flat(text)
        missing = [s for s in EXPECTED[lang] if _flat(s) not in flat]
        assert not missing, f"{name} can't read {missing} in {template}/{lang}:\n{text[:600]}"
        assert not BAD.findall(text), f"{name} reads {sorted(set(BAD.findall(text)))} in {template}/{lang}"


@needs_tex
def test_every_font_has_a_text_map(data_file):
    """A font with no /ToUnicode is read by guesswork or not at all."""
    pdf = _render(data_file, "classic", "zh")
    bare = []
    for page in pypdf.PdfReader(str(pdf)).pages:
        for font in page["/Resources"]["/Font"].values():
            font = font.get_object()
            if font.get("/Subtype") == "/Type0" and "/ToUnicode" not in font:
                bare.append(str(font["/BaseFont"]))
    assert not bare


@needs_tex
@pytest.mark.parametrize("template", [t["id"] for t in all_templates()])
def test_no_word_is_split_at_a_line_end(data_file, template):
    """A word split as "Kuber-" / "netes" is two words to a keyword match."""
    long = {"en": "Ran internationalization, containerization and Kubernetes-based orchestration for "
                  "microservices, observability dashboards, infrastructure-as-code and disaster recovery "
                  "rehearsals across geographically distributed environments"}

    def edit(doc):
        doc["entries"][1]["bullets"] = [long] * 4
    pdf = _render(data_file, template, "en", edit)
    for name, text in _readers(pdf):
        split = re.findall(r"[A-Za-z]+-\n[a-z]+", text)
        assert not split, f"{name} reads split words {split} in {template}"
        assert "Kubernetes-based" in text and "internationalization" in text, name
