"""The tables' views (docs/JOB_HUNT.md, 2026-10-19): kept with the data, cleaned on the way in."""
from __future__ import annotations

import json

import pytest

from jobhunt.views import Views, clean_table
from test_hunt import live  # noqa: F401


def test_views_are_cleaned_and_kept_with_the_data(tmp_path):
    vs = Views(tmp_path / "hunt")
    assert vs.all() == {}                                                      # nothing saved: the window's defaults
    saved = vs.save("apps", {"active": "v9", "views": [
        {"id": "v1", "name": "  进行中 ", "layout": "table",
         "columns": [{"key": "role", "width": 20}, {"key": "status", "width": 5000, "hidden": 1}, {"key": "role"}, {"key": "Bad Key!"}],
         "filters": [{"key": "status", "op": "is", "value": ["saved", "applied"]}, {"key": "status", "op": "explode"}],
         "sorts": [{"key": "deadline", "dir": "desc"}, {"key": "role", "dir": "sideways"}], "group": "status"},
        {"id": "v1", "name": "", "layout": "gallery"}]})
    v1, v2 = saved["views"]
    assert saved["active"] == "v1"                                             # an unknown active view falls back to the first
    assert v1["name"] == "进行中" and v1["group"] == "status"
    assert v1["columns"] == [{"key": "role", "width": 60, "hidden": False, "wrap": False},     # widths kept in range,
                             {"key": "status", "width": 900, "hidden": True, "wrap": False}]   # a key once, bad keys dropped
    assert v1["filters"] == [{"key": "status", "op": "is", "value": ["saved", "applied"]}]
    assert v1["sorts"] == [{"key": "deadline", "dir": "desc"}, {"key": "role", "dir": "asc"}]
    assert v2["id"] != "v1" and v2["layout"] == "table" and v2["name"]       # ids stay unique; a known layout
    on_disk = json.loads((tmp_path / "hunt" / "views.json").read_text(encoding="utf-8"))
    assert on_disk["apps"] == saved and Views(tmp_path / "hunt").all()["apps"] == saved
    vs.save("companies", {"views": [{"id": "v1", "name": "全部公司"}]})
    assert set(vs.all()) == {"apps", "companies"}                             # one table's save leaves the others
    with pytest.raises(ValueError):
        clean_table({"views": []})                                            # a table keeps at least one view
    with pytest.raises(KeyError):
        vs.save("nonsense", {"views": [{"id": "v1"}]})


def test_the_window_saves_views_through_the_api(live):
    code, tb = live.req("PUT", "/api/hunt/views/apps", {"active": "v2", "views": [{"id": "v1", "name": "进行中"}, {"id": "v2", "name": "全部", "layout": "board", "group": "status"}]})
    assert code == 200 and tb["active"] == "v2"
    assert live.req("GET", "/api/hunt/views")[1]["apps"]["views"][1]["layout"] == "board"
    assert live.req("PUT", "/api/hunt/views/apps", {"views": []})[0] == 400
    assert live.req("PUT", "/api/hunt/views/nope", {"views": [{"id": "v1"}]})[0] == 404
