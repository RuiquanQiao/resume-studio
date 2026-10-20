"""Interface language (follows the computer, can be picked) and resume languages (any number,
from the list the templates set well)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from core import i18n
from core.store import to_plain
from resume import langs, model
from resume.render import fonts, get_renderer
from resume.service import Studio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import i18n_keys  # noqa: E402

needs_tex = pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")


def test_every_catalog_is_complete_and_keeps_its_placeholders():
    report = i18n_keys.check()
    assert set(report) == set(i18n.UI_LANGS) - {"en"}
    for lang, r in report.items():
        assert not r["missing"], f"{lang} lacks {r['missing'][:5]}"
        assert not r["bad"], f"{lang}: {list(r['bad'].items())[:5]}"
        assert not r["unused"], f"{lang} has stale keys {r['unused'][:5]}"


def test_t_translates_counts_and_falls_back_to_english():
    assert i18n.t("Saved", ui="en") == "Saved"
    assert i18n.t("Saved", ui="zh") == "已保存"
    assert i18n.t("Not in any catalog", ui="de") == "Not in any catalog"
    assert i18n.t("{n} page", "{n} pages", ui="en", n=1) == "1 page"
    assert i18n.t("{n} page", "{n} pages", ui="en", n=3) == "3 pages"
    assert i18n.t("{n} page", "{n} pages", ui="de", n=3) == "3 Seiten"
    assert i18n.t("{n} page", "{n} pages", ui="fr", n=0) == "0 page"       # French: 0 is singular
    assert i18n.t("Opened {path}", ui="ja", path="x.yaml") == "x.yaml を開きました"
    # Chinese: a space between Han characters and a Latin word filled in, none next to punctuation or Han
    assert i18n.t("Fill in the {lang} version", ui="zh", lang="English") == "补齐 English 版"
    assert i18n.t("Fill in the {lang} version", ui="zh", lang="简体中文") == "补齐简体中文版"
    assert i18n.t("Follow the computer ({lang})", ui="zh", lang="English") == "跟随电脑（English）"
    assert i18n.t("Fill in the {lang} version", ui="ja", lang="English") == i18n.catalog("ja")["Fill in the {lang} version"].replace("{lang}", "English")
    # a placeholder may be called lang: it fills the text, it doesn't pick the catalog
    assert i18n.t("Editing {lang}", ui="zh", lang="Deutsch") == "正在编辑 Deutsch"


def test_computer_locale_picks_the_interface_and_resume_language(monkeypatch):
    assert i18n.normalize("zh-Hans-CN") == "zh" and i18n.normalize("de_AT.UTF-8") == "de"
    assert i18n.normalize("sw-KE") is None
    monkeypatch.setenv("RESUME_STUDIO_LANG", "sw-KE")
    assert i18n.system_lang() == "en"                              # no catalog: English
    assert langs.match("zh-Hant-TW") == "zh-TW" and langs.match("zh-CN") == "zh"
    assert langs.match("pt-BR") == "pt" and langs.match("nn-NO") == "nb" and langs.match("ar-EG") == "en"


def test_right_to_left_languages_are_not_offered():
    assert not {"ar", "he", "fa", "ur"} & set(langs.LANGS)
    assert all(set(langs.SECTION_TITLES[c]) == {s["id"] for s in model.DEFAULT_SECTIONS} for c in langs.LANGS)


def test_a_new_file_starts_in_the_computers_language(tmp_path, monkeypatch):
    monkeypatch.setenv("RESUME_STUDIO_LANG", "de-DE")
    st = Studio(tmp_path / "resume.yaml")
    assert st.ensure_file()
    doc = st.state()["doc"]
    assert doc["settings"]["languages"] == ["de"]
    assert [s["title"] for s in doc["sections"]][:3] == ["Ausbildung", "Berufserfahrung", "Projekte"]
    assert doc["subsets"][0]["id"] == "ALL" and doc["subsets"][0]["lang"] == "de"
    vid, s = st.create_subset(None, None, "Backend")
    assert next(v for v in s["doc"]["subsets"] if v["id"] == vid)["lang"] == "de"


def test_dates_follow_each_resume_language():
    e = {"start": "2025-08", "end": "present"}
    assert model.date_range(e, "en") == "Aug 2025 – Present"
    assert model.date_range(e, "zh") == "2025.08 – 至今"
    assert model.date_range(e, "de") == "08/2025 – heute"
    assert model.date_range(e, "ja") == "2025.08 – 現在"
    assert model.date_range({"start": "2024-01", "end": "heute"}, "fr") == "01/2024 – aujourd'hui"


def test_adding_a_language_gives_the_default_sections_their_titles(data_file):
    st = Studio(data_file)
    settings = to_plain(st.store.load()[0]["settings"])
    settings["languages"] = ["en", "zh", "ja"]
    s = st.update_item("settings", None, settings, None)
    edu = next(x for x in s["doc"]["sections"] if x["id"] == "education")
    assert edu["title"]["ja"] == "学歴" and edu["title"]["en"] == "Education"
    assert langs.match("ja") in s["doc"]["settings"]["languages"]


def test_missing_text_knows_which_languages_use_cjk():
    assert model.missing_text({"bullets": ["Built a thing"]}, "ko") == {"fields": [], "bullets": [0]}
    assert model.missing_text({"bullets": ["Built a thing"]}, "de") == {}
    assert model.missing_text({"bullets": ["设计了系统"]}, "fr") == {"fields": [], "bullets": [0]}


def test_app_language_can_be_picked_and_reset(server):
    js = server.req("GET", "/i18n.js")[1].decode("utf-8")
    data = json.loads(js.split("=", 1)[1].rstrip().rstrip(";"))
    assert data["lang"] == "zh" and data["chosen"] is None and data["system"] == "zh" and "dict" in data
    assert server.req("PUT", "/api/prefs", {"ui_lang": "ko"})[1]["ui_lang"] == "ko"
    assert '"lang": "ko"' in server.req("GET", "/i18n.js")[1].decode("utf-8")
    code, err = server.req("DELETE", "/api/subsets/ALL")          # the API speaks the chosen language too
    assert code == 400 and err["error"] == i18n.t("ALL (the full set) can't be deleted", ui="ko")
    assert server.req("PUT", "/api/prefs", {"ui_lang": None})[1] == {"ui_lang": "zh", "chosen": None}


@needs_tex
@pytest.mark.parametrize("lang,text", [
    ("ja", "評価パイプラインを設計し、生成された問題をすべて検証"),
    ("ko", "평가 파이프라인을 설계하고 생성된 문제를 모두 검증"),
    ("ru", "Спроектировал конвейер оценки и проверил все задания"),
    ("el", "Σχεδίασα σύστημα αξιολόγησης για κάθε ερώτηση"),
])
def test_other_scripts_compile(data_file, lang, text):
    st = Studio(data_file)
    doc, _ = st.store.load()
    doc["entries"][1]["bullets"].append({lang: text})
    doc["entries"][1]["title"] = {"en": "QuizLoom", lang: "QuizLoom " + text.split()[0]}
    v = to_plain(model.get_item(doc, "subset", "RES-TECH-EN"))
    v["lang"] = lang
    view = model.build_view(doc, v)
    r = get_renderer().render(view, data_file.parent / f"script-{lang}")
    assert r.ok, r.errors
    src = (data_file.parent / f"script-{lang}" / "main.tex").read_text(encoding="utf-8")
    if langs.is_cjk(lang):   # the language's own CJK font, not the Chinese default
        default = fonts.default_cjk(langs.script(lang))
        assert "xeCJK" in src and (fonts._spec(default, True)["name"] in src)
        assert "FandolSong" not in src or default == "FandolSong"
    else:
        assert "SourceSerif4" in src   # a font that has these letters, not Latin Modern
