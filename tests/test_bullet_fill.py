"""A bullet fills 70-100% of one line (user, 2026-11-15): the measurement names the ones over a line and, now,
the stubs under 70% too, and every button that writes bullets asks for both."""
from __future__ import annotations

import pytest

from core import i18n
from resume import model, prompts
from resume.service import Studio


@pytest.fixture(autouse=True)
def english():
    """The prompts are checked in English, whatever interface language a test before left on."""
    was = i18n.current()
    i18n.set_lang("en")
    yield
    i18n.set_lang(was)


def test_the_report_names_short_bullets_only_when_asked(data_file):
    studio = Studio(data_file)
    # what the LaTeX measurement reports: {entry: {bullet index: {n: lines, fill: share of one line}}}
    r = {"ok": True, "lang": "en", "lines": {"EXP-002": {"0": {"n": 2, "fill": 1.12}, "1": {"n": 1, "fill": 0.48}},
                                             "EXP-003": {"0": {"n": 1, "fill": 0.82}}, "EXP-001": {"0": {"n": 1, "fill": 0.69}}}}
    assert [(b["id"], b["index"]) for b in studio.bullet_report(r)] == [("EXP-002", 0)]   # the old callers: wrapping only
    got = studio.bullet_report(r, short=True)
    assert sorted((b["id"], b["index"], b["short"]) for b in got) == [("EXP-001", 0, True), ("EXP-002", 0, False), ("EXP-002", 1, True)]
    assert model.LINE_MIN == 0.70                                                    # 82% is a full line, 69% is not


def test_fit_asks_to_lengthen_the_short_ones_and_keeps_languages_apart():
    rows = [{"index": 0, "lines": 2, "fill": 1.12, "short": False, "text": "A long one"},
            {"index": 1, "lines": 1, "fill": 0.48, "short": True, "text": "A stub"}]
    text = prompts.fit_message("Paper2Exam", "zh", "RES-1", "E:/x/resume.yaml", rows)
    assert "run past one line" in text and "bullet 1 (2 lines" in text
    assert "fill less than 70% of their line" in text and "bullet 2 (1 lines, the text fills 48%" in text
    assert "70–100%" in text and "not paired bullet by bullet" in text
    only_short = prompts.fit_message("Paper2Exam", "zh", "RES-1", "E:/x/resume.yaml", rows[1:])
    assert "run past one line:" not in only_short


def test_writing_another_language_is_not_a_line_by_line_translation():
    text = prompts.translate_message("Paper2Exam", "zh", ["en"], "RES-1", "E:/x/resume.yaml")
    assert "not paired bullet by bullet" in text and "Bullets line up by position" not in text
    assert "never translate an English bullet line for line" in text
    assert "70–100%" in text
