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
    e["id"] = "EXP-900"
    st.update_item("entry", "EXP-003", e, None)
    doc, _ = st.store.load()
    for v in doc["versions"]:
        assert "EXP-003" not in v["entries"] and "EXP-900" in v["entries"]


def test_delete_entry_removes_references(data_file):
    st = Studio(data_file)
    st.delete_entry("EXP-002")
    doc, _ = st.store.load()
    assert model.get_item(doc, "entry", "EXP-002") is None
    assert all("EXP-002" not in v["entries"] for v in doc["versions"])


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
    assert model.next_entry_id(doc) == "EXP-006"
    doc["versions"][0]["entries"].append("EXP-404")
    assert any("EXP-404" in w for w in model.validate(doc))
