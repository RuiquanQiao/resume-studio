"""A subset's versions: kept when the user says so, compared, previewed and gone back to."""
from __future__ import annotations

import json

from core.store import to_plain
from resume import model
from resume.service import Studio


def edit_entry(st: Studio, eid: str, fn) -> None:
    e = to_plain(model.get_item(st.store.load()[0], "entry", eid))
    fn(e)
    st.update_item("entry", eid, e, None)


def edit_subset(st: Studio, sid: str, fn) -> None:
    v = to_plain(model.get_item(st.store.load()[0], "subset", sid))
    fn(v)
    st.update_item("subset", sid, v, None)


def test_a_version_freezes_what_the_subset_renders(data_file):
    st = Studio(data_file)
    assert st.state()["subset_versions"]["RES-TECH-EN"] == {"count": 0, "latest": None, "changed": True, "same_as": None}
    r = st.commit_subset("RES-TECH-EN", "  sent to the first round  ")
    assert r["created"] and r["made"]["n"] == 1 and r["made"]["note"] == "sent to the first round"
    assert st.state()["subset_versions"]["RES-TECH-EN"] == {"count": 1, "latest": 1, "changed": False, "same_as": 1}
    assert not st.commit_subset("RES-TECH-EN")["created"]                # nothing changed: no new version

    # the fact layer, the name and the linked postings are not what was sent
    edit_entry(st, "EXP-002", lambda e: e.update(notes="private story", evidence=["E:/x"],
                                                 interview=[{"q": "Why?", "a": "private answer"}]))
    edit_subset(st, "RES-TECH-EN", lambda v: v.update(label="Renamed", postings=[{"tool": "career-ops", "id": "9"}]))
    assert st.state()["subset_versions"]["RES-TECH-EN"]["changed"] is False
    stored = json.loads((data_file.parent / ".studio/subsets/RES-TECH-EN.json").read_text(encoding="utf-8"))
    assert "private story" not in json.dumps(stored, ensure_ascii=False) and "notes" not in stored["versions"][0]["content"]["entries"][0]
    assert "private answer" not in json.dumps(stored, ensure_ascii=False)          # nor the mock interview

    # a shared entry it uses changed: this subset changed, one that doesn't use it didn't
    st.commit_subset("RES-TECH-ZH")
    edit_entry(st, "EXP-002", lambda e: e["bullets"].append({"en": "Shipped v2", "zh": "上线第二版"}))
    sv = st.state()["subset_versions"]
    zh_uses = "EXP-002" in model.get_item(st.store.load()[0], "subset", "RES-TECH-ZH").get("entries", [])
    assert sv["RES-TECH-EN"]["changed"] is True and sv["RES-TECH-ZH"]["changed"] is zh_uses
    assert st.commit_subset("RES-TECH-EN", "v2")["made"]["n"] == 2
    assert [v["n"] for v in st.subset_versions("RES-TECH-EN")["versions"]] == [2, 1]   # newest first


def test_compare_shows_what_changed_per_entry(data_file):
    st = Studio(data_file)
    st.commit_subset("RES-TECH-EN")
    edit_entry(st, "EXP-002", lambda e: e["bullets"].append({"en": "Shipped v2", "zh": "上线第二版"}))
    edit_subset(st, "RES-TECH-EN", lambda v: (v.update(template="modern"), v["entries"].remove(v["entries"][0])))
    items = st.compare_versions("RES-TECH-EN", 1)["items"]
    kinds = {(i["kind"], i["status"]) for i in items}
    assert ("subset", "changed") in kinds and ("entry", "removed") in kinds
    exp2 = next(i for i in items if i["id"] == "EXP-002")
    assert "Shipped v2" in exp2["after"] and "Shipped v2" not in exp2["before"]
    st.commit_subset("RES-TECH-EN")
    assert st.compare_versions("RES-TECH-EN", 1, 2)["items"] == items            # between two versions too
    assert st.compare_versions("RES-TECH-EN", 2)["items"] == []                   # now = v2


def test_a_version_renders_as_it_was_and_exports_beside_the_current_one(data_file):
    st = Studio(data_file)
    st.commit_subset("RES-TECH-EN")
    edit_subset(st, "RES-TECH-EN", lambda v: v.update(headline={"en": "Something else entirely"}))
    r = st.render_version("RES-TECH-EN", 1)
    assert r["ok"] and r["pages"] == 1 and r["version"] == 1 and r["images"] >= 1
    assert st.page_image("RES-TECH-EN", 1, 1) is not None and st.pdf_path("RES-TECH-EN", 1).exists()
    ex = st.export("RES-TECH-EN", 1)
    assert ex["exported"]["pdf"].endswith("RES-TECH-EN-v1.pdf")


def test_going_back_keeps_now_as_a_version_and_says_who_else_is_affected(data_file):
    st = Studio(data_file)
    st.commit_subset("RES-TECH-EN")
    before = json.dumps(to_plain(model.get_item(st.store.load()[0], "entry", "EXP-002")), ensure_ascii=False)
    edit_entry(st, "EXP-002", lambda e: (e["bullets"].append({"en": "Shipped v2"}), e.update(notes="newer notes", interview=[{"q": "Why?", "a": "kept"}])))
    edit_subset(st, "RES-TECH-EN", lambda v: v.update(template="modern", label="Kept name"))
    impact = st.restore_impact("RES-TECH-EN", 1)
    assert impact["entries"] and not impact["profile"]
    users = [v.get("label") for v in st.store.load()[0]["subsets"]
             if v["id"] not in ("ALL", "RES-TECH-EN") and "EXP-002" in (v.get("entries") or [])]
    assert impact["others"] == users

    s = st.restore_version("RES-TECH-EN", 1)
    sub = next(v for v in s["doc"]["subsets"] if v["id"] == "RES-TECH-EN")
    assert sub["template"] != "modern" and sub["label"] == "Kept name"           # the name stays
    e = to_plain(model.get_item(st.store.load()[0], "entry", "EXP-002"))
    assert e["notes"] == "newer notes"                                            # the fact layer stays
    assert e["interview"] == [{"q": "Why?", "a": "kept"}]                         # and so does the mock interview
    assert "Shipped v2" not in json.dumps(e) and json.loads(before)["bullets"] == e["bullets"]
    vs = st.subset_versions("RES-TECH-EN")
    assert [v["n"] for v in vs["versions"]] == [2, 1] and vs["versions"][0]["auto"]   # what it said before is v2
    assert vs["status"] == {"count": 2, "latest": 2, "changed": True, "same_as": 1}   # now = v1 again, not v2
    assert st.compare_versions("RES-TECH-EN", 1)["items"] == []


def test_going_back_brings_a_deleted_entry_back(data_file):
    st = Studio(data_file)
    st.commit_subset("RES-TECH-EN")
    sub = model.get_item(st.store.load()[0], "subset", "RES-TECH-EN")
    gone = str(sub["entries"][0])
    st.delete_entry(gone)
    assert gone in [str(x) for x in []] or st.restore_impact("RES-TECH-EN", 1)["back"]
    st.restore_version("RES-TECH-EN", 1)
    doc = st.store.load()[0]
    assert model.get_item(doc, "entry", gone) is not None and gone in model.get_item(doc, "subset", "RES-TECH-EN")["entries"]


def test_history_follows_a_renamed_subset_and_outlives_a_deleted_one(data_file):
    st = Studio(data_file)
    vid, _ = st.create_subset("RES-TECH-EN", None, "Copy")
    st.commit_subset(vid, "first")
    edit_subset(st, vid, lambda v: v.update(id="SUB-RENAMED"))
    assert st.subset_versions("SUB-RENAMED")["versions"][0]["note"] == "first"
    assert st.subset_versions(vid)["versions"] == []
    st.delete_subset("SUB-RENAMED")
    gone = st.subset_versions("SUB-RENAMED")
    assert gone["exists"] is False and gone["versions"][0]["note"] == "first"
    st.restore_version("SUB-RENAMED", 1)                                          # and it can come back
    assert any(v["id"] == "SUB-RENAMED" for v in st.state()["doc"]["subsets"])


def test_a_new_subset_never_takes_the_id_of_a_deleted_one_with_history(data_file):
    st = Studio(data_file)
    a, _ = st.create_subset(None, None, "A")
    st.commit_subset(a)
    st.delete_subset(a)
    b, _ = st.create_subset(None, None, "B")
    assert b != a and st.subset_versions(b)["versions"] == []


def test_versions_over_http(server):
    code, r = server.req("POST", "/api/subsets/RES-TECH-EN/versions", {"note": "sent"})
    assert code == 200 and r["made"]["n"] == 1 and r["status"]["changed"] is False
    code, r = server.req("PUT", "/api/subsets/RES-TECH-EN/versions/1", {"note": "sent to Acme's round 1"})
    assert code == 200 and r["versions"][0]["note"] == "sent to Acme's round 1"
    assert server.req("GET", "/api/subsets/RES-TECH-EN/versions/1/compare")[1]["items"] == []
    code, r = server.req("GET", "/api/subsets/RES-TECH-EN/versions/0/compare?to=1")     # what v1 holds
    assert code == 200 and r["to"] == 1 and {i["status"] for i in r["items"]} == {"added"}
    assert server.req("GET", "/api/subsets/RES-TECH-EN/versions/7/compare")[0] == 404
    assert server.req("GET", "/api/subsets/ALL/versions")[0] == 400
    code, r = server.req("POST", "/api/subsets/RES-TECH-EN/versions/1/render")
    assert code == 200 and r["ok"]
    code, png = server.req("GET", "/api/subsets/RES-TECH-EN/versions/1/page/1")
    assert code == 200
    code, r = server.req("GET", "/api/subsets/RES-TECH-EN/versions/1/restore")
    assert code == 200 and r["entries"] == []
    code, r = server.req("POST", "/api/subsets/RES-TECH-EN/versions/1/restore")
    assert code == 200 and r["subset_versions"]["RES-TECH-EN"]["latest"] == 1    # nothing changed: no extra version
