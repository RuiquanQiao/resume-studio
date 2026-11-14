"""A subset's version numbers: a new major (v2) when the entries it picks or the order of its sections change,
a minor one (v2.1) when only the wording, the layout or the skills do. Nobody types them in."""
from resume import versions

SECTIONS = [{"id": "skills"}, {"id": "projects"}, {"id": "education"}]


def c(entries=("P1", "P2", "E1"), order=("skills", "projects", "education"), hide=(), words="a", layout=None):
    sec = {"P1": "projects", "P2": "projects", "P3": "projects", "E1": "education", "S1": "skills"}
    return {"subset": {"id": "SUB-1", "entries": list(entries), "sections": list(order), "hide_sections": list(hide),
                       "layout": layout or {}},
            "profile": {}, "sections": SECTIONS,
            "entries": [{"id": e, "section": sec[e], "title": words} for e in entries]}


def kept(*cs):
    return [{"n": i + 1, "content": x} for i, x in enumerate(cs)]


def test_wording_is_a_minor_number_and_picking_entries_a_major_one():
    vs = kept(c(), c(words="b"), c(words="c", layout={"font_size": 10}), c(entries=("P1", "E1")), c(entries=("P1", "E1"), words="d"))
    assert versions.names(vs) == {1: "1", 2: "1.1", 3: "1.2", 4: "2", 5: "2.1"}


def test_moving_a_section_or_reordering_entries_is_a_major_number():
    # 2026-11-15: education moved to the bottom for Australian employers is a new version, not a touch-up
    vs = kept(c(order=("education", "skills", "projects")), c(order=("skills", "projects", "education")),
              c(entries=("P2", "P1", "E1")), c(entries=("P2", "P1", "E1"), hide=("education",)))
    assert list(versions.names(vs).values()) == ["1", "2", "3", "4"]


def test_order_of_sections_with_nothing_in_them_changes_nothing():
    vs = kept(c(entries=("P1",), order=("skills", "projects", "education")),
              c(entries=("P1",), order=("education", "skills", "projects")))   # neither skills nor education shows
    assert versions.names(vs) == {1: "1", 2: "1.1"}


def test_the_next_number_follows_the_same_rule():
    assert versions.next_name([], c()) == "1"
    vs = kept(c(), c(words="b"))
    assert versions.next_name(vs, c(words="z")) == "1.2"
    assert versions.next_name(vs, c(entries=("P1",))) == "2"


def test_going_back_to_an_older_shape_is_a_new_major_number():
    vs = kept(c(), c(entries=("P1",)), c())
    assert versions.names(vs) == {1: "1", 2: "2", 3: "3"}
