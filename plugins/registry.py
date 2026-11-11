"""The plugins on this computer (docs/PLUGINS.md): the bundled ones (plugins/bundled/) and those installed from the
market (<skill>/.local/plugins/), which of them are on (prefs.json `plugins`), installing and uninstalling."""
from __future__ import annotations

import io
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Any

from . import manifest

BUNDLED = Path(__file__).resolve().parent / "bundled"


class Registry:
    def __init__(self, installed_dir: Path, prefs: Any, bundled_dir: Path = BUNDLED):
        self.installed_dir = Path(installed_dir)
        self.bundled_dir = Path(bundled_dir)
        self.prefs = prefs              # core.i18n.Prefs: `plugins` = {id: on}
        self.lock = threading.RLock()

    # ---- what is here ----
    def _folders(self) -> list[tuple[Path, str]]:
        out = []
        for root, source in ((self.bundled_dir, "bundled"), (self.installed_dir, "market")):
            if root.is_dir():
                out += [(f, source) for f in sorted(root.iterdir()) if f.is_dir()]
        return out

    def all(self) -> list[dict]:
        """Every plugin here: {manifest, source, on, folder} — or {id, source, error} for one that doesn't pass.
        A market plugin with the same id as a bundled one is the newer copy of it: it wins."""
        on = self._switches()
        found: dict[str, dict] = {}
        for folder, source in self._folders():
            try:
                m = manifest.read(folder)
            except manifest.ManifestError as e:
                found.setdefault(folder.name, {"id": folder.name, "source": source, "error": str(e), "on": False})
                continue
            if folder.name != m["id"]:
                found.setdefault(folder.name, {"id": folder.name, "source": source, "on": False,
                                               "error": f"the folder is {folder.name} but plugin.json says {m['id']}"})
                continue
            found[m["id"]] = {"id": m["id"], "manifest": m, "source": source, "folder": folder,
                              "bundled": source == "bundled" or (found.get(m["id"]) or {}).get("bundled", False),
                              "on": bool(on.get(m["id"], False))}
        return sorted(found.values(), key=lambda p: (p["source"] != "bundled", p["id"]))

    def enabled(self) -> list[dict]:
        return [p for p in self.all() if p.get("on") and "manifest" in p]

    def get(self, pid: str) -> dict:
        for p in self.all():
            if p["id"] == pid and "manifest" in p:
                return p
        raise KeyError(f"no plugin {pid!r}")

    def guide(self, pid: str) -> str:
        p = self.get(pid)
        g = p["manifest"].get("guide")
        return (p["folder"] / g).read_text(encoding="utf-8") if g else ""

    # ---- on and off ----
    def _switches(self) -> dict:
        v = self.prefs.load().get("plugins")
        return v if isinstance(v, dict) else {}

    def switch(self, pid: str, on: bool) -> None:
        with self.lock:
            self.get(pid)   # only a plugin that is here
            self.prefs.save(plugins={**self._switches(), pid: bool(on)})

    # ---- installing ----
    def install_zip(self, data: bytes, expect_id: str | None = None) -> dict:
        """Unpack a plugin's zip into the installed folder, checked first (in a folder of its own, then moved in):
        a zip that doesn't pass leaves nothing behind. The plugin is turned on."""
        if len(data) > manifest.MAX_PLUGIN * 2:
            raise manifest.ManifestError("the download is too large")
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise manifest.ManifestError("the download isn't a zip")
        names = [n for n in z.namelist() if not n.endswith("/")]
        # the files may sit in one top folder (a release zip) or at the zip's root
        tops = {n.split("/")[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names)
        with self.lock:
            self.installed_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.installed_dir / ".incoming"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir()
            try:
                total = 0
                for n in names:
                    rel = n.split("/", 1)[1] if strip else n
                    if not manifest.FILES.match(rel):     # also refuses "..", folders, absolute paths
                        raise manifest.ManifestError(f"{rel}: a plugin holds only plugin.json, .md files and an icon")
                    info = z.getinfo(n)
                    total += info.file_size
                    if total > manifest.MAX_PLUGIN:
                        raise manifest.ManifestError("the plugin is larger than 2 MB")
                    (tmp / rel).write_bytes(z.read(n))
                m = manifest.read(tmp)
                if expect_id and m["id"] != expect_id:
                    raise manifest.ManifestError(f"the download is {m['id']}, not {expect_id}")
                dest = self.installed_dir / m["id"]
                shutil.rmtree(dest, ignore_errors=True)
                tmp.rename(dest)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            self.prefs.save(plugins={**self._switches(), m["id"]: True})
        return self.get(m["id"])

    def uninstall(self, pid: str) -> None:
        """A market plugin's files go; its records stay in the data folder (installing it again brings them back).
        A bundled plugin can only be turned off."""
        with self.lock:
            folder = self.installed_dir / pid
            if not manifest.ID.match(pid) or not folder.is_dir():
                raise KeyError(f"no installed plugin {pid!r}")
            shutil.rmtree(folder)
            sw = self._switches()
            sw.pop(pid, None)
            if any(p["id"] == pid for p in self.all()):   # a bundled one of that id stays, off
                sw[pid] = False
            self.prefs.save(plugins=sw)
