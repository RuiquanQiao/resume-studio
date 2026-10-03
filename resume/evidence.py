"""Evidence folders: notice when a project changed since Claude last read it.

Each entry lists local paths in `evidence` (the fact layer). For every path we
take a cheap signature:
  - git repo: HEAD + a hash of `git status --porcelain` (uncommitted work counts too);
  - plain folder: (relative path, mtime, size) of its files, skipping build/vendor dirs;
  - file: mtime + size.
The signature at the moment Claude last worked on the entry is the baseline, kept in
`.studio/evidence.json` (UI state, not resume data). Comparing the two gives the status
the UI shows, and a short change summary that goes into the "update from project" prompt.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".next", ".nuxt", "dist", "build",
    "out", "target", ".cache", ".pytest_cache", ".mypy_cache", ".idea", ".vscode", ".gradle", "bin",
    "obj", ".turbo", ".parcel-cache", "coverage", ".studio", ".DS_Store", "Pods", ".dart_tool",
}
MAX_FILES = 20000          # a folder bigger than this is summarised by its newest files only
LISTING_KEEP = 4000        # file listing stored in the baseline (for "which files changed")
CACHE_SECONDS = 3         # only absorbs bursts (focus + visibilitychange fire together)


def _git(path: Path, *args: str, timeout: float = 8) -> str | None:
    # --no-optional-locks: only ever read the user's repo (plain `git status` may rewrite .git/index)
    try:
        p = subprocess.run(["git", "--no-optional-locks", "-C", str(path), *args], capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.decode("utf-8", errors="replace")


def _is_git(path: Path) -> bool:
    return path.is_dir() and (path / ".git").exists()


def _walk(root: Path) -> tuple[dict[str, list[int]], bool]:
    """{relative path: [mtime_ns, size]} for the files under root; True if truncated."""
    files: dict[str, list[int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            files[os.path.relpath(full, root).replace("\\", "/")] = [st.st_mtime_ns, st.st_size]
            if len(files) >= MAX_FILES:
                return files, True
    return files, False


def signature(path_str: str) -> dict:
    """Current state of one evidence path."""
    path = Path(os.path.expanduser(path_str))
    if not path.exists():
        return {"exists": False}
    if path.is_file():
        st = path.stat()
        return {"exists": True, "kind": "file", "sig": f"{st.st_mtime_ns}:{st.st_size}", "mtime": st.st_mtime}
    if _is_git(path):
        head = (_git(path, "rev-parse", "HEAD") or "").strip() or None
        status = _git(path, "status", "--porcelain") or ""
        last = (_git(path, "log", "-1", "--format=%ct") or "").strip()
        # editing an already-modified file does not change `status`, so fold in the files' mtimes
        stamp = []
        for line in status.splitlines():
            rel = line[3:].split(" -> ")[-1].strip().strip('"')
            try:
                stamp.append(f"{line}:{(path / rel).stat().st_mtime_ns}")
            except OSError:
                stamp.append(line)
        dirty = hashlib.sha1("\n".join(stamp).encode()).hexdigest()[:10] if stamp else ""
        return {"exists": True, "kind": "git", "head": head, "dirty": dirty,
                "dirty_files": len([l for l in status.splitlines() if l.strip()]),
                "sig": f"{head}:{dirty}", "mtime": float(last) if last.isdigit() else None}
    files, truncated = _walk(path)
    raw = json.dumps(sorted(files.items())).encode()
    newest = max((v[0] for v in files.values()), default=0) / 1e9
    listing = dict(sorted(files.items(), key=lambda kv: -kv[1][0])[:LISTING_KEEP])
    return {"exists": True, "kind": "dir", "sig": hashlib.sha1(raw).hexdigest()[:16], "files": len(files),
            "truncated": truncated, "mtime": newest or None, "listing": listing}


def changes(base: dict, cur: dict, path_str: str) -> dict:
    """What changed between a baseline and now, in a form both the UI and the prompt can use."""
    out: dict[str, Any] = {}
    path = Path(os.path.expanduser(path_str))
    if cur.get("kind") == "git":
        old = base.get("head")
        if old and cur.get("head") and old != cur["head"]:
            count = (_git(path, "rev-list", "--count", f"{old}..HEAD") or "").strip()
            log = _git(path, "log", "--format=%h %ad %s", "--date=short", "-n", "30", f"{old}..HEAD") or ""
            stat = _git(path, "diff", "--stat", "--stat-width=100", old, "HEAD") or ""
            if count.isdigit():
                out["commits"] = int(count)
                out["log"] = [l for l in log.splitlines() if l.strip()]
                out["stat"] = [l for l in stat.splitlines() if l.strip()][-25:]
            else:  # history rewritten: the old commit is gone
                out["rewritten"] = True
        if cur.get("dirty") != base.get("dirty") and cur.get("dirty_files"):
            out["uncommitted"] = cur["dirty_files"]
    elif cur.get("kind") == "dir":
        a, b = base.get("listing") or {}, cur.get("listing") or {}
        added = [p for p in b if p not in a]
        modified = [p for p in b if p in a and b[p] != a[p]]
        removed = [p for p in a if p not in b] if not cur.get("truncated") else []
        out.update({"added": added[:20], "modified": modified[:20], "removed": removed[:20],
                    "counts": [len(added), len(modified), len(removed)]})
    return out


class EvidenceTracker:
    def __init__(self, state_dir: Path):
        self.path = state_dir / "evidence.json"
        self.lock = threading.RLock()
        self._cache: dict[str, tuple[float, dict]] = {}

    # ---- baseline store ------------------------------------------------
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def _sig(self, path: str, force: bool) -> dict:
        hit = self._cache.get(path)
        if hit and not force and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        sig = signature(path)
        self._cache[path] = (time.time(), sig)
        return sig

    def mark_synced(self, entry_id: str, paths: list[str]) -> None:
        """Claude has read these paths for this entry: their current state is the new baseline."""
        with self.lock:
            data = self._load()
            rec = data.setdefault(entry_id, {})
            for p in paths:
                sig = self._sig(p, force=True)
                if sig.get("exists"):
                    rec[p] = {**sig, "synced_at": time.time()}
            for p in [p for p in rec if p not in paths]:
                del rec[p]
            self._save(data)

    def rename(self, old: str, new: str) -> None:
        with self.lock:
            data = self._load()
            if old in data:
                data[new] = data.pop(old)
                self._save(data)

    # ---- status --------------------------------------------------------
    def status(self, entries: list[dict], force: bool = False) -> dict:
        """{entry_id: {state, paths: [...]}} for every entry that has evidence.

        Path states: missing | new (Claude has not read it yet) | same | changed.
        Entry state: the most pressing path state."""
        with self.lock:
            data = self._load()
        out = {}
        for e in entries:
            eid = str(e.get("id"))
            paths = [str(p) for p in e.get("evidence") or [] if str(p).strip()]
            if not paths:
                continue
            base_rec = data.get(eid, {})
            rows = []
            for p in paths:
                cur = self._sig(p, force)
                base = base_rec.get(p)
                row: dict[str, Any] = {"path": p, "kind": cur.get("kind"), "mtime": cur.get("mtime"),
                                       "synced_at": (base or {}).get("synced_at")}
                if not cur.get("exists"):
                    row["state"] = "missing"
                elif not base:
                    row["state"] = "new"
                elif base.get("sig") == cur.get("sig"):
                    row["state"] = "same"
                else:
                    row["state"] = "changed"
                    row["changes"] = changes(base, cur, p)
                rows.append(row)
            rank = {"missing": 3, "changed": 2, "new": 1, "same": 0}
            out[eid] = {"state": max((r["state"] for r in rows), key=rank.get), "paths": rows}
        return out


def describe(row: dict) -> str:
    """One path's changes as plain text for the prompt."""
    c = row.get("changes") or {}
    lines = [f"- {row['path']}"]
    if c.get("commits"):
        lines.append(f"  {c['commits']} 个新提交：")
        lines += [f"    {l}" for l in c.get("log", [])]
        if c.get("stat"):
            lines.append("  改动的文件（git diff --stat）：")
            lines += [f"    {l.strip()}" for l in c["stat"]]
    if c.get("rewritten"):
        lines.append("  git 历史被改写过，无法列出新提交；请直接看 git log 和代码。")
    if c.get("uncommitted"):
        lines.append(f"  另有 {c['uncommitted']} 个文件有未提交的改动。")
    if c.get("counts"):
        a, m, r = c["counts"]
        lines.append(f"  新增 {a} 个文件、修改 {m} 个、删除 {r} 个。")
        for label, key in (("新增", "added"), ("修改", "modified"), ("删除", "removed")):
            if c.get(key):
                lines.append(f"  {label}：" + "，".join(c[key][:12]))
    return "\n".join(lines)
