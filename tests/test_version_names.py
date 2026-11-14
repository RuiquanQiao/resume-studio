"""A subset's version numbers are given only to what went out, and nobody types them (2026-11-15): the major
number moves when the entries it picks, their order or the sections' order differ from the last one sent; the
minor number counts the different wordings sent under that major (v2.3 is the third)."""
from resume import versions

SECTIONS = [{"id": "skills"}, {"id": "projects"}, {"id": "education"}]


def c(entries=("P1", "P2", "E1"), order=("skills", "projects", "education"), hide=(), words="a", layout=None):
    sec = {"P1": "projects", "P2": "projects", "P3": "projects", "E1": "education", "S1": "skills"}
    return {"subset": {"id": "SUB-1", "entries": list(entries), "sections": list(order), "hide_sections": list(hide),
                       "layout": layout or {}},
            "profile": {}, "sections": SECTIONS,
            "entries": [{"id": e, "section": sec[e], "title": words} for e in entries]}


def sent(*cs):
    return [{"n": i + 1, "content": x, "sent": True, "sent_at": f"2026-11-{i + 1:02d}T10:00:00"} for i, x in enumerate(cs)]


def test_each_different_wording_sent_is_the_next_minor_number():
    vs = sent(c(), c(words="b"), c(words="c", layout={"font_size": 10}), c(entries=("P1", "E1")), c(entries=("P1", "E1"), words="d"))
    assert versions.names(vs) == {1: "1.1", 2: "1.2", 3: "1.3", 4: "2.1", 5: "2.2"}


def test_moving_a_section_or_reordering_entries_is_a_major_number():
    # education moved to the bottom for Australian employers is a new version, not a touch-up
    vs = sent(c(order=("education", "skills", "projects")), c(order=("skills", "projects", "education")),
              c(entries=("P2", "P1", "E1")), c(entries=("P2", "P1", "E1"), hide=("education",)))
    assert list(versions.names(vs).values()) == ["1.1", "2.1", "3.1", "4.1"]


def test_order_of_sections_with_nothing_in_them_changes_nothing():
    vs = sent(c(entries=("P1",), order=("skills", "projects", "education")),
              c(entries=("P1",), order=("education", "skills", "projects")))   # neither skills nor education shows
    assert versions.names(vs) == {1: "1.1", 2: "1.2"}


def test_a_copy_never_sent_has_no_number_and_takes_none():
    vs = sent(c(), c(words="b"))
    vs.insert(1, {"n": 9, "content": c(words="kept before going back"), "auto": True})
    assert versions.names(vs) == {1: "1.1", 2: "1.2"}


def test_a_copy_sent_later_comes_after_the_ones_sent_before_it():
    vs = sent(c(), c(words="b"))
    vs.insert(1, {"n": 3, "content": c(words="z"), "sent": True, "sent_at": "2026-11-20T10:00:00"})
    assert versions.names(vs) == {1: "1.1", 2: "1.2", 3: "1.3"}


def test_what_the_subset_goes_out_as_next():
    assert versions.next_name([], c()) == "1.1"
    vs = sent(c(), c(words="b"))
    assert versions.next_name(vs, c(words="z")) == "1.3"
    assert versions.next_name(vs, c(entries=("P1",))) == "2.1"
    assert versions.next_name(vs, c(), same=1) == "1.1"                # says what v1.1 said: goes out as v1.1


def test_going_back_to_an_older_shape_is_a_new_major_number():
    vs = sent(c(), c(entries=("P1",)), c())
    assert versions.names(vs) == {1: "1.1", 2: "2.1", 3: "3.1"}
