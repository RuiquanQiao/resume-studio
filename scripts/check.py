"""Run every test, one group at a time, and print a short summary (TESTING.md, "One command").

    .venv/Scripts/python scripts/check.py              # everything
    .venv/Scripts/python scripts/check.py ui layout    # some groups (unit, ui, layout, ai_review, session_ui…)

Each group runs in its own pytest process: a single run over everything breaks the Playwright files, and
the e2e servers share one port, so the groups run one after another. Full output goes to
.cache/check/<group>.log; the screen gets one line per group, then each failure. A test that fails is run
again on its own: if it passes then, it is listed as intermittent rather than failed.
Exit code: 0 when nothing failed (intermittent ones included), 1 otherwise.
"""
from __future__ import annotations

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


def pytest(name: str, args: list[str], log: Path) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", *args, f"--basetemp=.cache/pytest/check-{name}", "-p", "no:cacheprovider", "-rfE"]
    with log.open("w", encoding="utf-8", errors="replace") as f:
        rc = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT).returncode
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
    start = time.monotonic()
    for name in names:
        t0 = time.monotonic()
        rc, text = pytest(name, every[name], LOGS / f"{name}.log")
        bad = failures(text)
        if rc not in (0, 5) and not bad:   # a crash or collection error: no test names to go on
            failed.append(f"{name}: pytest exited {rc} (see .cache/check/{name}.log)")
        again: list[str] = []
        if bad:   # each failure once more, on its own process, to tell a real failure from a timing one
            rc2, text2 = pytest(name + "-again", bad, LOGS / f"{name}-again.log")
            again = failures(text2)
            if rc2 not in (0, 5) and not again:
                again = bad
            failed += again
            flaky += [b for b in bad if b not in again]
        note = f"  ({len(again)} failed, {len(bad) - len(again)} intermittent)" if bad else ""
        print(f"{name:<12} {counts(text):<32} {clock(time.monotonic() - t0)}{note}", flush=True)
    print(f"{'total':<12} {'':<32} {clock(time.monotonic() - start)}")
    for f in failed:
        print("FAILED       " + f)
    for f in flaky:
        print("INTERMITTENT " + f + "  (passed when run again alone)")
    print("ALL PASSED" if not failed and not flaky else "NOT CLEAN: logs in .cache/check/")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
