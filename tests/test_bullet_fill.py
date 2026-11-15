"""How long a bullet may be is a range the user sets under Layout (user, 2026-11-15): 70-100% of one line by
default, 70-150% for someone who accepts two lines. Never shorter than the least, never longer than the most, any
length between is fine: the measurement names only the bullets out of range, and every button that writes
bullets asks for the range, not for a target length (the AI cut every bullet to 70-80% when 70% read as the goal)."""
from __future__ import annotations

import pytest

from core import i18n
from resume import model, prompts, versions
from resume.service import Studio


@pytest.fixture(autouse=True)
def english():
    """The prompts are checked in English, whatever interface language a test before left on."""
    was = i18n.current()
    i18n.set_lang("en")
    yield
    i18n.set_lang(was)


def test_the_range_comes_from_the_layout_and_is_kept_sane():
    assert model.bullet_range({}) == (0.70, 1.00)                                   # the default
    assert model.bullet_range({"bullet_min": 60, "bullet_max": 150}) == (0.60, 1.50)
    assert model.bullet_range({"bullet_min": "abc", "bullet_max": None}) == (0.70, 1.00)
    assert model.bullet_range({"bullet_min": 90, "bullet_max": 60}) == (0.90, 0.90)  # the most is never under the least
    assert model.bullet_range({"bullet_min": -5, "bullet_max": 900}) == (0.0, 3.0)
    assert model.DEFAULT_LAYOUT["bullet_min"] == 70 and model.DEFAULT_LAYOUT["bullet_max"] == 100


def test_out_of_range_is_judged_by_both_ends():
    one = (0.70, 1.00)
    assert model.off_range({"n": 1, "fill": 0.82}, *one) is None
    assert model.off_range({"n": 1, "fill": 0.69}, *one) == "short"
    assert model.off_range({"n": 2, "fill": 1.12}, *one) == "long"
    assert model.off_range({"n": 2, "fill": 0.98}, *one) == "long"     # wrapped on a long word: still two lines
    assert model.off_range({"n": 1, "fill": 0.95}, 0.70, 0.90) == "long"
    two = (0.70, 1.50)
    assert model.off_range({"n": 2, "fill": 1.30}, *two) is None       # two lines are fine for this user
    assert model.off_range({"n": 2, "fill": 1.62}, *two) == "long"


def test_the_report_names_only_bullets_out_of_the_subsets_range(data_file):
    studio = Studio(data_file)
    # what the LaTeX measurement reports: {entry: {bullet index: {n: lines, fill: share of one line}}}
    lines = {"EXP-002": {"0": {"n": 2, "fill": 1.12}, "1": {"n": 1, "fill": 0.48}},
             "EXP-003": {"0": {"n": 1, "fill": 0.82}}, "EXP-001": {"0": {"n": 1, "fill": 0.69}}}
    r = {"ok": True, "lang": "en", "lines": lines}
    assert [(b["id"], b["index"]) for b in studio.bullet_report(r)] == [("EXP-002", 0)]   # the old callers: too long only
    got = studio.bullet_report(r, short=True)
    assert sorted((b["id"], b["index"], b["short"]) for b in got) == [("EXP-001", 0, True), ("EXP-002", 0, False), ("EXP-002", 1, True)]
    loose = studio.bullet_report({**r, "range": [0.40, 1.50]}, short=True)          # a user who takes two lines
    assert loose == []


def test_a_render_carries_the_subsets_range(data_file):
    studio = Studio(data_file)
    doc, _ = studio.store.load()
    sid = next(str(s["id"]) for s in doc["subsets"] if not model.is_all(s))
    view = model.build_view(doc, model.get_subset(doc, sid))
    assert model.bullet_range(view["layout"]) == (0.70, 1.00)


def test_the_range_is_not_part_of_what_a_version_holds(data_file):
    studio = Studio(data_file)
    doc, _ = studio.store.load()
    sub = next(s for s in doc["subsets"] if not model.is_all(s))
    before = versions.digest(versions.content(doc, sub))
    sub.setdefault("layout", {})["bullet_max"] = 150
    assert versions.digest(versions.content(doc, sub)) == before                    # it guides writing, prints nothing
    sub["layout"]["font_size"] = 9.5
    assert versions.digest(versions.content(doc, sub)) != before


def test_fit_asks_for_the_range_and_to_leave_bullets_in_range_alone():
    rows = [{"index": 0, "lines": 2, "fill": 1.12, "short": False, "text": "A long one"},
            {"index": 1, "lines": 1, "fill": 0.48, "short": True, "text": "A stub"}]
    text = prompts.fit_message("Paper2Exam", "zh", "RES-1", "E:/x/resume.yaml", rows)
    assert "are longer than 100% of a line" in text and "bullet 1 (2 lines" in text
    assert "are shorter than 70% of a line" in text and "bullet 2 (1 lines, the text fills 48%" in text
    assert "between 70% and 100% of one line (never onto a second line)" in text and "not paired bullet by bullet" in text
    assert "It is a range, not a target" in text and "rather than down to 70%" in text
    assert "don't touch the bullets already in range" in text
    only_short = prompts.fit_message("Paper2Exam", "zh", "RES-1", "E:/x/resume.yaml", rows[1:])
    assert "are longer than" not in only_short


def test_the_users_own_range_is_what_the_ai_is_told():
    token = prompts.RANGE.set((0.70, 1.50))
    try:
        text = prompts.polish_message("Paper2Exam", "", "en", "RES-1", "E:/x/resume.yaml")
        assert "between 70% and 150% of one line (it may run onto a second line up to 150%)" in text
        assert "never longer than 150%" in text
    finally:
        prompts.RANGE.reset(token)


def test_writing_another_language_is_not_a_line_by_line_translation():
    text = prompts.translate_message("Paper2Exam", "zh", ["en"], "RES-1", "E:/x/resume.yaml")
    assert "not paired bullet by bullet" in text and "Bullets line up by position" not in text
    assert "never translate an English bullet line for line" in text
    assert "between 70% and 100% of one line" in text
