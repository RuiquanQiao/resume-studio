"""resume.yaml storage and the data model."""
from __future__ import annotations

from core.store import YamlStore, fingerprint, merge_into, to_plain
from resume import model
from resume.service import Conflict, Studio

import pytest


def test_partial_update_keeps_comments_and_style(data_file):
    before = data_file.read_text(encoding="utf-8")
    st = Studio(data_file)
    doc, _ = st.store.load()
    entry = to_plain(model.get_item(doc, "entry", "EXP-002"))
    entry["subtitle"]["en"] = "Founder"
    st.update_item("entry", "EXP-002", entry, fingerprint(model.get_item(doc, "entry", "EXP-002")))
    after = data_file.read_text(encoding="utf-8")
    assert after.startswith("# Resume Studio sample data.")          # header comment survives
    assert "subtitle: {en: Founder, zh: 独立开发}" in after            # flow style survives
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(changed) == 1                                        # nothing else rewritten


def test_stale_base_is_rejected(data_file):
    st = Studio(data_file)
    with pytest.raises(Conflict):
        st.update_item("entry", "EXP-002", {"id": "EXP-002", "section": "projects", "title": "x"}, "deadbeef")


def test_history_snapshot_on_save(data_file):
    st = Studio(data_file)
    doc, _ = st.store.load()
    st.store.save(doc)
    assert list((data_file.parent / ".studio" / "history").glob("*.yaml"))


def test_rename_entry_updates_versions(data_file):
    st = Studio(data_file)
    doc, _ = st.store.load()
    e = to_plain(model.get_item(doc, "entry", "EXP-003"))
    e["id"] = "MY-OWN-ID"
    st.update_item("entry", "EXP-003", e, None)
    doc, _ = st.store.load()
    for v in doc["versions"][1:]:
        assert "EXP-003" not in v["entries"] and "MY-OWN-ID" in v["entries"]


def test_delete_entry_removes_references(data_file):
    st = Studio(data_file)
    st.delete_entry("EXP-002")
    doc, _ = st.store.load()
    assert model.get_item(doc, "entry", "EXP-002") is None
    assert all("EXP-002" not in v.get("entries", []) for v in doc["versions"])


def test_merge_into_list_reuses_maps():
    store = YamlStore.__new__(YamlStore)
    YamlStore.__init__(store, "unused.yaml")
    doc = store.parse("a:\n  - {x: 1}  # keep\n  - {x: 2}\n")
    merge_into(doc, {"a": [{"x": 1}, {"x": 3}]})
    assert "# keep" in store.dump(doc)


def test_text_helpers():
    assert model.tr({"en": "A", "zh": "甲"}, "zh") == "甲"
    assert model.tr({"en": "A"}, "zh") == "A"           # falls back to any language
    assert model.tr("plain", "zh") == "plain"
    assert model.fmt_date("2025-07", "en") == "Jul 2025"
    assert model.fmt_date("2025-07", "zh") == "2025.07"
    assert model.fmt_date("present", "zh") == "至今"
    assert model.date_range({"start": "2025-01", "end": "present"}, "en") == "Jan 2025 – Present"
    assert model.date_range({"start": "2025-01", "date": "Summer 2025"}, "en") == "Summer 2025"


def test_view_never_contains_fact_layer(data_file):
    st = Studio(data_file)
    doc, _ = st.store.load()
    for v in doc["versions"]:
        view = model.build_view(doc, to_plain(v))
        flat = str(view)
        for e in doc["entries"]:
            notes = str(e.get("notes") or "").strip()
            if notes:
                assert notes.splitlines()[0] not in flat
        assert "evidence" not in flat


def test_view_order_hidden_sections_and_accent(data_file):
    st = Studio(data_file)
    doc, _ = st.store.load()
    v = to_plain(model.get_item(doc, "version", "RES-TECH-EN"))
    v["entries"] = ["EXP-003", "EXP-002", "EXP-001", "MISSING"]
    v["sections"] = ["projects", "education"]
    v["hide_sections"] = ["skills"]
    v["layout"] = {"accent": "#abcdef"}
    view = model.build_view(doc, v)
    assert [s["id"] for s in view["sections"]] == ["projects", "education"]
    assert [e["id"] for e in view["sections"][0]["entries"]] == ["EXP-003", "EXP-002"]
    assert view["layout"]["accent"] == "ABCDEF"


def test_validate_and_ids(data_file):
    st = Studio(data_file)
    doc, _ = st.store.load()
    assert model.validate(doc) == []
    assert model.next_entry_id(doc) == "EXP-006"                      # one counter, whatever the section
    model.get_item(doc, "version", "RES-TECH-EN")["entries"].append("EXP-404")
    assert any("EXP-404" in w for w in model.validate(doc))


def test_moving_section_keeps_the_id(data_file):
    st = Studio(data_file)
    doc, _ = st.store.load()
    e = to_plain(model.get_item(doc, "entry", "EXP-003"))
    e["section"] = "experience"
    st.update_item("entry", "EXP-003", e, None)
    doc, _ = st.store.load()
    assert model.get_item(doc, "entry", "EXP-003")["section"] == "experience"


def test_all_is_the_full_set(data_file):
    st = Studio(data_file)
    doc, _ = st.store.load()
    all_view = model.build_view(doc, to_plain(model.get_version(doc, "ALL")))
    ids = [e["id"] for sec in all_view["sections"] for e in sec["entries"]]
    assert sorted(ids) == sorted(str(e["id"]) for e in doc["entries"])
    assert all_view["profile"]["headline"] == "MSc Computing student"
    assert len(all_view["profile"]["contacts"]) == 3
    zh = model.build_view(doc, to_plain(model.get_version(doc, "RES-TECH-ZH")))
    assert [c["label"] for c in zh["profile"]["contacts"]] == ["email", "github"]   # hide_contacts
    assert zh["profile"]["headline"].startswith("计算机硕士在读 · ")


def test_all_is_reserved(data_file):
    st = Studio(data_file)
    with pytest.raises(ValueError):
        st.delete_version("ALL")
    v = to_plain(model.get_item(st.store.load()[0], "version", "RES-TECH-EN"))
    v["id"] = "ALL"
    with pytest.raises(ValueError):
        st.update_item("version", "RES-TECH-EN", v, None)
    vid, s = st.create_version("ALL", None, "AI Agent 工程师")
    v = next(x for x in s["doc"]["versions"] if x["id"] == vid)
    assert vid == "JOB-001" and v["label"] == "AI Agent 工程师"
    assert v["entries"] == [e["id"] for e in s["doc"]["entries"]]   # starts with everything ticked
    assert v["headline"] == ""                                        # but not ALL's headline
    vid, s = st.create_version(None, None, "管培生")
    assert vid == "JOB-002" and next(x for x in s["doc"]["versions"] if x["id"] == vid)["entries"] == []


def test_files_without_all_still_get_it(tmp_path):
    f = tmp_path / "resume.yaml"
    f.write_text("schema_version: 1\n"
                 "profile: {name: X, headline: Old}\n"
                 "entries: []\n"
                 "versions:\n"
                 "  - {id: RES-A, label: A, entries: []}\n", encoding="utf-8")
    st = Studio(f)
    s = st.state()
    assert [v["id"] for v in s["doc"]["versions"]] == ["ALL", "RES-A"]
    assert "ALL" not in f.read_text(encoding="utf-8")                  # reading never writes
    doc, _ = st.store.load()
    assert model.build_view(doc, to_plain(model.get_item(doc, "version", "RES-A")))["profile"]["headline"] == "Old"
    st.update_item("version", "ALL", {"id": "ALL", "template": "modern"}, None)
    assert model.get_item(st.store.load()[0], "version", "ALL")["template"] == "modern"


def test_skeleton_starts_with_all_only():
    sk = model.skeleton()
    assert [v["id"] for v in sk["versions"]] == ["ALL"] and "headline" not in sk["profile"]


def test_move_entry_reorders_library_within_section(data_file):
    st = Studio(data_file)
    s = st.move_entry("EXP-003", -1)
    order = [e["id"] for e in s["doc"]["entries"]]
    assert order.index("EXP-003") < order.index("EXP-002")
    s = st.move_entry("EXP-003", -1)                                    # EXP-001 is another section
    assert [e["id"] for e in s["doc"]["entries"]] == order
