"""Bring the newest career-ops job providers into jobs/vendor/career-ops/ (2026-11-12).

career-ops (https://github.com/santifer/career-ops) is MIT licensed: its code may be copied and changed as long as
its copyright and license notice go with it (jobs/vendor/career-ops/LICENSE). Its providers read some 160 careers
systems' public job APIs (Moka, Feishu, Tencent, Meituan, Greenhouse, Lever, Workday...) with no AI; the scan runs
them through jobs/co_scan.mjs and needs no career-ops install.

    python scripts/update_career_ops.py            # newest main
    python scripts/update_career_ops.py --ref v1.40.0

It clones into .cache/career-ops-src (git ignored), copies providers/ and the few files they import (found by
following their imports, so a provider that starts importing a new file brings it along), and writes
jobs/vendor/career-ops/SOURCE.txt with the commit. Then run scripts/check.py.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/santifer/career-ops.git"
SRC = ROOT / ".cache" / "career-ops-src"
DEST = ROOT / "jobs" / "vendor" / "career-ops"
# bare imports node resolves itself; js-yaml is kept in DEST/node_modules (MIT, Vitaly Puzrin); undici is optional
BUILT_IN = {"fs", "url", "path", "crypto", "util", "child_process", "os", "net", "dns", "http", "https", "stream",
            "zlib", "events", "buffer", "worker_threads", "readline", "assert"}
KEPT_PACKAGES = {"js-yaml", "undici"}
# real import statements only: a comment's `tell "absent" from "empty"` is no import
_IMPORT = re.compile(r"""^\s*(?:import|export)\b[^;'"]*?\bfrom\s*['"]([^'"\s]+)['"]"""
                     r"""|^\s*import\s*['"]([^'"\s]+)['"]|\bimport\(\s*['"]([^'"\s]+)['"]\s*\)""", re.M)


def closure(src: Path) -> tuple[set[Path], set[str]]:
    """providers/* and every file of the checkout they import, and the packages they import."""
    todo = [*src.joinpath("providers").glob("*.mjs"), *src.joinpath("providers").glob("*.js")]
    seen: set[Path] = set()
    packages: set[str] = set()
    while todo:
        f = todo.pop().resolve()
        if f in seen or not f.is_file():
            continue
        seen.add(f)
        for groups in _IMPORT.findall(f.read_text(encoding="utf-8", errors="replace")):
            name = next(g for g in groups if g)
            if name.startswith("."):
                todo.append(f.parent / name)
            elif not name.startswith("node:") and name not in BUILT_IN:
                packages.add(name)
    return seen, packages


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default="main")
    a = ap.parse_args()
    if SRC.exists():   # git keeps its packs read-only, which Windows won't delete as they are
        shutil.rmtree(SRC, onexc=lambda fn, path, exc: (os.chmod(path, stat.S_IWRITE), fn(path)))
    SRC.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", "--branch", a.ref, REPO, str(SRC)], check=True)
    commit = subprocess.run(["git", "-C", str(SRC), "log", "-1", "--format=%H %cs"], check=True,
                            capture_output=True, text=True).stdout.strip()
    files, packages = closure(SRC)
    unknown = sorted(packages - KEPT_PACKAGES)
    if unknown:
        print("providers now import packages not kept here:", ", ".join(unknown), file=sys.stderr)
        print("add each under jobs/vendor/career-ops/node_modules (with its license) before updating", file=sys.stderr)
        return 1
    for old in (DEST / "providers", DEST / "lib"):
        if old.exists():
            shutil.rmtree(old)
    for f in DEST.glob("*.mjs"):
        f.unlink()
    for f in files:
        to = DEST / f.relative_to(SRC.resolve())
        to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, to)
    shutil.copy2(SRC / "LICENSE", DEST / "LICENSE")
    (DEST / "SOURCE.txt").write_text(
        f"career-ops providers, copied from {REPO.removesuffix('.git')}\ncommit {commit}\n"
        f"MIT: see LICENSE (career-ops) and node_modules/js-yaml/LICENSE (js-yaml).\n"
        f"Refresh with: python scripts/update_career_ops.py\n", encoding="utf-8")
    print(f"copied {len(files)} files from career-ops {commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
