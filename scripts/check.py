"""Run every test, every group at once, and print a short summary (TESTING.md, "One command").

    .venv/Scripts/python scripts/check.py              # everything
    .venv/Scripts/python scripts/check.py ui layout    # some groups (unit, ui, layout, ai_review, session_ui…)

Each group runs in its own pytest process (a single run over everything breaks the Playwright files), all
of them side by side: each gets its own server port (STUDIO_TEST_PORT) and browser profile
(STUDIO_TEST_GROUP), so they never meet. Each log ends with its slowest tests. Full output goes to
.cache/check/<group>.log; the screen gets one line per group, then each failure. A test that fails is run
again on its own: if it passes then, it is listed as intermittent rather than failed.
Exit code: 0 when nothing failed (intermittent ones included), 1 otherwise.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / ".cache" / "check"
FAILED = re.compile(r"^(?:FAILED|ERROR) (.+?)(?: - .*)?$")
COUNTS = re.compile(r"\b(\d+) (passed|failed|errors?|skipped|deselected)\b")


def groups() -> dict[str, list[str]]:
    out = {"unit": ["--ignore=tests/e2e"]}
    for f in sorted((ROOT / "tests" / "e2e").glob("test_*.py")):
        out[f.stem.removeprefix("test_")] = [f.relative_to(ROOT).as_posix()]
    return out


def start(name: str, args: list[str], log: Path, slot: int) -> subprocess.Popen:
    """One group's pytest, on a port and a browser profile of its own (`slot`), so groups run side by side."""
    # --durations: the slowest tests are named at the foot of each log, to see where the time goes
    cmd = [sys.executable, "-m", "pytest", *args, f"--basetemp=.cache/pytest/check-{name}", "-p", "no:cacheprovider", "-rfE",
           "--durations=25", "--durations-min=1"]
    env = {**os.environ, "STUDIO_TEST_PORT": str(18765 + 10 * slot), "STUDIO_TEST_GROUP": name.removesuffix("-again")}
    f = log.open("w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
    p.log_file = f   # type: ignore[attr-defined]
    return p


def finish(p: subprocess.Popen, log: Path) -> tuple[int, str]:
    rc = p.wait()
    p.log_file.close()   # type: ignore[attr-defined]
    return rc, log.read_text(encoding="utf-8", errors="replace")


def failures(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for line in text.splitlines() if (m := FAILED.match(line))))


def counts(text: str) -> str:
    last = next((ln for ln in reversed(text.splitlines()) if COUNTS.search(ln)), "")
    return ", ".join(f"{n} {w}" for n, w in COUNTS.findall(last) if w != "deselected") or "no result"


def clock(s: float) -> str:
    return f"{int(s // 60)}m{int(s % 60):02d}s"


def main() -> int:
    every = groups()
    names = sys.argv[1:] or list(every)
    unknown = [n for n in names if n not in every]
    if unknown:
        print(f"unknown group: {', '.join(unknown)} (groups: {', '.join(every)})")
        return 2
    LOGS.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    flaky: list[str] = []
    began = time.monotonic()
    # every group at once (each in its own process, port and browser profile): the run takes as long as the
    # slowest group, not the sum of them (30 min one after another, 2026-11-10)
    procs = {name: (start(name, every[name], LOGS / f"{name}.log", i), time.monotonic()) for i, name in enumerate(names)}
    ended: dict[str, float] = {}
    while len(ended) < len(procs):   # each group's own time, as it ends
        for name, (p, t0) in procs.items():
            if name not in ended and p.poll() is not None:
                ended[name] = time.monotonic() - t0
        time.sleep(0.5)
    for i, name in enumerate(names):
        rc, text = finish(procs[name][0], LOGS / f"{name}.log")
        bad = failures(text)
        if rc not in (0, 5) and not bad:   # a crash or collection error: no test names to go on
            failed.append(f"{name}: pytest exited {rc} (see .cache/check/{name}.log)")
        again: list[str] = []
        if bad:   # each failure once more, on its own process, to tell a real failure from a timing one
            rc2, text2 = finish(start(name + "-again", bad, LOGS / f"{name}-again.log", i), LOGS / f"{name}-again.log")
            again = failures(text2)
            if rc2 not in (0, 5) and not again:
                again = bad
            failed += again
            flaky += [b for b in bad if b not in again]
        note = f"  ({len(again)} failed, {len(bad) - len(again)} intermittent)" if bad else ""
        print(f"{name:<12} {counts(text):<32} {clock(ended[name])}{note}", flush=True)
    print(f"{'total':<12} {'':<32} {clock(time.monotonic() - began)}")
    for f in failed:
        print("FAILED       " + f)
    for f in flaky:
        print("INTERMITTENT " + f + "  (passed when run again alone)")
    print("ALL PASSED" if not failed and not flaky else "NOT CLEAN: logs in .cache/check/")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
