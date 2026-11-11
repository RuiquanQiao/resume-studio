"""Plugins (docs/PLUGINS.md): manifests checked strictly, records checked against them, installing from the market
leaves only a valid plugin, and the window, the AI's command and the chat's note all see the same records."""
import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from core.i18n import Prefs
from plugins import manifest, market
from plugins.records import Records
from core import i18n


@pytest.fixture(autouse=True)
def english():
    """Messages are checked in English, whatever language an earlier test left on."""
    was = i18n.current()
    i18n.set_lang("en")
    yield
    i18n.set_lang(was)
from plugins.registry import BUNDLED, Registry

ROOT = Path(__file__).resolve().parent.parent
BUNDLED_IDS = ("civil-service", "phd-applications", "talent-programs")


def labels(m):
    """Every {lang: text} label in a manifest, with where it is."""
    out = [("name", m["name"]), ("description", m["description"])] + [(f"suggestion {i}", s) for i, s in enumerate(m.get("suggestions", []))]
    for c in m["collections"]:
        out += [(f"{c['id']} name", c["name"]), (f"{c['id']} item", c["item"])]
        out += [(f"{c['id']} view {v['id']}", v["name"]) for v in c.get("views", [])]
        for p in c["properties"]:
            out.append((f"{c['id']}.{p['key']}", p["label"]))
            if "hint" in p:
                out.append((f"{c['id']}.{p['key']} hint", p["hint"]))
            out += [(f"{c['id']}.{p['key']}.{o['value']}", o["label"]) for o in p.get("options", [])]
    return out


@pytest.mark.parametrize("pid", BUNDLED_IDS)
def test_bundled_plugins_pass_and_speak_every_language_of_the_app(pid):
    m = manifest.read(BUNDLED / pid)
    missing = [f"{where}: {sorted(set(manifest.LANGS) - set(lab))}" for where, lab in labels(m) if set(manifest.LANGS) - set(lab)]
    assert not missing, missing
    assert (BUNDLED / pid / m["guide"]).read_text(encoding="utf-8").strip()


def test_the_market_list_offers_every_bundled_plugin_at_its_version():
    entries = {e["id"]: e for e in market._entries(json.loads(market.LOCAL.read_text(encoding="utf-8")))}
    for pid in BUNDLED_IDS:
        assert entries[pid]["bundled"] and entries[pid]["version"] == manifest.read(BUNDLED / pid)["version"]


def good() -> dict:
    return {"id": "demo", "version": "1.0.0", "name": {"en": "Demo"}, "description": {"en": "A demo"},
            "collections": [{"id": "things", "name": {"en": "Things"}, "item": {"en": "thing"},
                             "properties": [{"key": "title", "type": "title", "label": {"en": "Name"}},
                                            {"key": "kind", "type": "select", "label": {"en": "Kind"},
                                             "options": [{"value": "big", "label": {"en": "Big", "zh": "大"}}]},
                                            {"key": "due", "type": "date", "label": {"en": "Due"}},
                                            {"key": "site", "type": "url", "label": {"en": "Site"}},
                                            {"key": "n", "type": "number", "label": {"en": "Count"}}],
                             "dates": ["due"], "status": "kind"}]}


@pytest.mark.parametrize("change, says", [
    (lambda m: m.update(script="run.js"), "unknown key 'script'"),
    (lambda m: m.update(service="paid"), "paid services"),
    (lambda m: m.update(id="Demo!"), "id:"),
    (lambda m: m["collections"][0]["properties"].append({"key": "other", "type": "title", "label": {"en": "X"}}), "exactly one title"),
    (lambda m: m["collections"][0]["properties"].append({"key": "code", "type": "javascript", "label": {"en": "X"}}), "type is one of"),
    (lambda m: m["collections"][0]["properties"][1].update(options=[]), "1 to 40 options"),
    (lambda m: m["collections"][0].update(status="due"), "status names a select"),
    (lambda m: m["collections"][0].update(dates=["kind"]), "dates name date"),
    (lambda m: m["collections"][0]["properties"].append({"key": "id", "type": "text", "label": {"en": "X"}}), "not ['by', 'created', 'id', 'updated']"),
    (lambda m: m.update(name={"zh": "演示"}), "needs English"),
    (lambda m: m.update(app=">=9"), "newer Resume Studio"),
])
def test_a_manifest_that_isnt_exactly_the_format_is_refused(change, says):
    m = good()
    change(m)
    with pytest.raises(manifest.ManifestError, match=says.replace("[", r"\[").replace("]", r"\]").replace("(", r"\(")):
        manifest.check(m)


def write_plugin(folder: Path, m: dict, extra: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.json").write_text(json.dumps(m), encoding="utf-8")
    for name, text in (extra or {}).items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def test_a_plugin_folder_holds_data_only(tmp_path):
    write_plugin(tmp_path / "demo", good(), {"GUIDE.md": "# guide", "hook.py": "import os"})
    with pytest.raises(manifest.ManifestError, match="hook.py"):
        manifest.read(tmp_path / "demo")


def registry(tmp_path) -> Registry:
    return Registry(tmp_path / "installed", Prefs(tmp_path / "prefs"))


def test_bundled_plugins_start_off_and_a_switch_is_kept(tmp_path):
    reg = registry(tmp_path)
    assert [p["id"] for p in reg.all()] == sorted(BUNDLED_IDS) and reg.enabled() == []
    reg.switch("civil-service", True)
    assert [p["id"] for p in Registry(tmp_path / "installed", Prefs(tmp_path / "prefs")).enabled()] == ["civil-service"]
    with pytest.raises(KeyError):
        reg.switch("nothing-here", True)


def test_records_are_checked_against_their_properties(tmp_path):
    recs = Records(tmp_path, manifest.check(good()))
    r = recs.add("things", {"title": "  One   thing ", "kind": "大", "due": "2026-12-01T00:00", "n": "1,200", "site": "https://a.example/x"})
    assert r["id"] == "R1" and r["title"] == "One thing" and r["kind"] == "big" and r["due"] == "2026-12-01" and r["n"] == 1200
    for bad, says in ([{"title": "x", "kind": "huge"}, "one of big"], [{"title": "x", "due": "next week"}, "YYYY-MM-DD"],
                      [{"title": "x", "site": "a.example"}, "http"], [{"title": "x", "colour": "red"}, "no property “colour”"],
                      [{"kind": "big"}, "Give it a title"]):
        with pytest.raises(ValueError, match=says):
            recs.add("things", bad)
    recs.edit("things", "R1", {"kind": "", "n": 3})
    got = recs.get("things", "R1")
    assert "kind" not in got and got["n"] == 3 and got["by"] == "you"
    with pytest.raises(ValueError, match="can't be empty"):
        recs.edit("things", "R1", {"title": ""})
    assert recs.add("things", {"title": "Two"})["id"] == "R2"
    recs.delete("things", "R1")
    assert [x["id"] for x in Records(tmp_path, manifest.check(good())).rows("things")] == ["R2"]   # on disk
    assert recs.add("things", {"title": "Three"})["id"] == "R3"                                     # ids never reused


def zipped(files: dict, top: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(top + name, text)
    return buf.getvalue()


def test_installing_a_zip_checks_it_first_and_leaves_nothing_when_it_fails(tmp_path):
    reg = registry(tmp_path)
    m = good()
    for files in ({"plugin.json": json.dumps(m), "../evil.md": "x"}, {"plugin.json": json.dumps(m), "run.js": "x"},
                  {"plugin.json": "{not json"}):
        with pytest.raises(manifest.ManifestError):
            reg.install_zip(zipped(files))
        assert not (tmp_path / "evil.md").exists()
        assert [p.name for p in (tmp_path / "installed").iterdir()] == []
    with pytest.raises(manifest.ManifestError, match="not other"):
        reg.install_zip(zipped({"plugin.json": json.dumps(m)}), expect_id="other")
    p = reg.install_zip(zipped({"plugin.json": json.dumps(m), "GUIDE.md": "# g"}, top="demo-1.0.0/"))   # a release zip's top folder
    assert p["id"] == "demo" and p["on"] and p["source"] == "market"
    reg.uninstall("demo")
    assert "demo" not in [x["id"] for x in reg.all()]


@pytest.fixture
def market_server(tmp_path, monkeypatch):
    """A market on this computer: its list and one plugin's zip."""
    zip_bytes = zipped({"plugin.json": json.dumps(good()), "GUIDE.md": "# demo"})
    files = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            self.wfile.write(body or b"")
    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    files["/demo.zip"] = zip_bytes
    files["/market.json"] = json.dumps({"plugins": [
        {"id": "demo", "version": "1.1.0", "name": {"en": "Demo"}, "download": base + "/demo.zip", "sha256": hashlib.sha256(zip_bytes).hexdigest()},
        {"id": "nosum", "version": "1.0.0", "download": base + "/demo.zip"},                 # no checksum: not offered
        {"id": "civil-service", "bundled": True, "version": "1.0.0"}]}).encode()
    monkeypatch.setenv("RESUME_STUDIO_MARKET", base + "/missing.json," + base + "/market.json")   # the first fails: the next is read
    yield files, base
    httpd.shutdown()


def test_the_market_is_read_from_the_first_address_that_answers(market_server):
    got = market.listing()
    assert [e["id"] for e in got["plugins"]] == ["demo", "civil-service"] and got["source"].endswith("/market.json")


def test_a_download_whose_checksum_differs_is_refused(market_server):
    files, base = market_server
    entry = next(e for e in market.listing()["plugins"] if e["id"] == "demo")
    files["/demo.zip"] = b"tampered"
    with pytest.raises(market.MarketError, match="checksum"):
        market.download(entry)


def test_offline_the_market_shows_the_bundled_plugins(monkeypatch):
    monkeypatch.setenv("RESUME_STUDIO_MARKET", "http://127.0.0.1:9/market.json")
    got = market.listing()
    assert got["offline"] and {e["id"] for e in got["plugins"]} >= set(BUNDLED_IDS)


def test_the_window_turns_a_plugin_on_and_keeps_its_records(server):
    st, got = server.req("GET", "/api/plugins")
    assert st == 200 and {p["id"]: p["on"] for p in got["plugins"]} == {pid: False for pid in BUNDLED_IDS}
    assert server.req("POST", "/api/plugins/civil-service/records/positions", {"title": "X"})[0] == 409   # off
    assert server.req("PUT", "/api/plugins/civil-service", {"on": True})[1]["on"] is True
    st, r = server.req("POST", "/api/plugins/civil-service/records/positions",
                       {"title": "综合管理岗", "exam": "国考", "apply_by": "2026-10-24", "quota": "2"})
    assert st == 200 and r["exam"] == "national" and r["quota"] == 2
    assert server.req("POST", "/api/plugins/civil-service/records/positions", {"title": "Y", "exam": "lottery"})[0] == 400
    st, r = server.req("PUT", f"/api/plugins/civil-service/records/positions/{r['id']}", {"stage": "registered"})
    assert st == 200 and r["stage"] == "registered"
    rows = server.req("GET", "/api/plugins")[1]["records"]["civil-service"]["positions"]
    assert [x["title"] for x in rows] == ["综合管理岗"]
    # its views are kept like the other tables'
    tb = {"active": "v1", "views": [{"id": "v1", "name": "全部职位", "layout": "board", "group": "stage"}]}
    assert server.req("PUT", "/api/hunt/views/p.civil-service.positions", tb)[0] == 200
    assert server.req("GET", "/api/hunt/views")[1]["p.civil-service.positions"]["views"][0]["group"] == "stage"
    # off: the tab goes, the records stay
    server.req("PUT", "/api/plugins/civil-service", {"on": False})
    assert "civil-service" not in server.req("GET", "/api/plugins")[1]["records"]
    server.req("PUT", "/api/plugins/civil-service", {"on": True})
    assert len(server.req("GET", "/api/plugins")[1]["records"]["civil-service"]["positions"]) == 1
    st, _ = server.req("DELETE", f"/api/plugins/civil-service/records/positions/{rows[0]['id']}")
    assert st == 200 and server.req("GET", "/api/plugins")[1]["records"]["civil-service"]["positions"] == []


def test_installing_from_the_market_through_the_window(server, market_server):
    st, got = server.req("GET", "/api/plugin-market")
    assert st == 200 and {e["id"]: e["installed"] for e in got["plugins"]} == {"demo": None, "civil-service": "1.0.0"}
    st, p = server.req("POST", "/api/plugin-market/install", {"id": "demo"})
    assert st == 200 and p["on"] and p["source"] == "market"
    assert server.req("POST", "/api/plugin-market/install", {"id": "civil-service"})[1]["on"]   # bundled: turned on
    assert server.req("POST", "/api/plugin-market/install", {"id": "nothing"})[0] == 404
    assert server.req("DELETE", "/api/plugins/demo")[0] == 200
    assert server.req("DELETE", "/api/plugins/civil-service")[0] == 404       # bundled: only turned off


def test_every_chat_is_told_about_the_plugins_that_are_on(server):
    def note():
        return "\n".join(n() for n in server.inst.chats.notes)
    assert "## Plugins" not in note()
    server.req("PUT", "/api/plugins/phd-applications", {"on": True})
    text = note()
    assert "## Plugins" in text and "`phd-applications`" in text and "scripts" in text and "plugin.py" in text
    assert "GUIDE.md" in text and "funded=全奖" in text


def test_the_ais_command_writes_what_the_window_reads(server, data_file):
    server.req("PUT", "/api/plugins/talent-programs", {"on": True})
    env = {**os.environ, "RESUME_STUDIO_PREFS": str(data_file.parent / "local"), "PYTHONIOENCODING": "utf-8"}
    run = lambda *a, stdin=None: subprocess.run([sys.executable, str(ROOT / "scripts" / "plugin.py"), "--data", str(data_file), *a],   # noqa: E731
                                                input=stdin, capture_output=True, text=True, encoding="utf-8", env=env)
    r = run("add", "talent-programs", "programs", "--stdin",
            stdin=json.dumps({"title": "上海留学人员落户", "level": "市级", "for": "returnee", "notice": "https://rsj.sh.gov.cn/x"}, ensure_ascii=False))
    assert r.returncode == 0, r.stderr
    rows = server.req("GET", "/api/plugins")[1]["records"]["talent-programs"]["programs"]
    assert rows[0]["title"] == "上海留学人员落户" and rows[0]["level"] == "city" and rows[0]["by"] == "claude"
    r = run("edit", "talent-programs", "programs", rows[0]["id"], "--stdin", stdin='{"stage": "nope"}')
    assert r.returncode != 0 and "nothing was saved" in r.stderr
    r = run("show", "civil-service")
    assert r.returncode != 0 and "is off" in r.stderr
    assert run("check", str(BUNDLED / "civil-service")).stdout.startswith("ok: civil-service")
