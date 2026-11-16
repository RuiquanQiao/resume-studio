"""The plan-usage card keeps what it knows, and never shows a stray "0" (2026-11-17: once Codex's limit was reached,
the card lost its 5-hour / weekly rows and showed a lone "0" under the context bar)."""
import re
from pathlib import Path
from types import SimpleNamespace

from core.chat import ChatRunner
from core.drivers import codex_limits

WEB = Path(__file__).resolve().parent.parent / "web"

# what `account/rateLimits/read` answered at that moment
REACHED = {"limitId": "codex", "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": 1794861035},
           "secondary": {"usedPercent": 81, "windowDurationMins": 10080, "resetsAt": 1795101507},
           "planType": "plus", "rateLimitReachedType": "rate_limit_reached"}


def runner():
    sent = []
    return SimpleNamespace(limits={}, bus=SimpleNamespace(publish=lambda kind, data: sent.append(data))), sent


def test_codex_at_its_limit_reads_both_windows_and_says_rejected():
    lim = codex_limits(REACHED)
    assert lim["status"] == "rejected"
    assert lim["windows"]["five_hour"]["utilization"] == 1.0 and lim["windows"]["seven_day"]["utilization"] == 0.81


def test_an_update_with_no_windows_keeps_the_known_ones():
    r, sent = runner()
    ChatRunner.set_limits(r, "codex", codex_limits(REACHED))
    ChatRunner.set_limits(r, "codex", codex_limits({"limitId": "codex", "primary": None, "secondary": None,
                                                    "rateLimitReachedType": "rate_limit_reached"}))
    assert r.limits["codex"]["windows"]["five_hour"]["utilization"] == 1.0
    assert r.limits["codex"]["status"] == "rejected"
    assert sent[-1]["windows"]["seven_day"]["utilization"] == 0.81   # the window is told the kept windows too


def test_a_new_window_set_replaces_the_old_one():
    r, _ = runner()
    ChatRunner.set_limits(r, "codex", codex_limits(REACHED))
    ChatRunner.set_limits(r, "codex", codex_limits({"primary": {"usedPercent": 3, "windowDurationMins": 300}}))
    assert r.limits["codex"]["windows"] == {"five_hour": {"utilization": 0.03, "resetsAt": None, "minutes": 300}}


def test_no_count_is_handed_to_h_as_a_child():
    """`list.length && h(...)` gives 0 for an empty list, and h() draws 0 as text. A count is compared (`> 0`)."""
    bad = []
    for f in sorted(WEB.glob("*.js")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            # the count stands alone as an operand (`a > b.length && h(` compares, and is fine)
            if re.search(r"(?:^|[(,\[?:]|&&|\|\|)\s*[\w.\])]+\.(length|size)\)?\s*&&\s*(h|suggest|icon)\(", line):
                bad.append(f"{f.name}:{n}: {line.strip()[:120]}")
    assert not bad, "\n".join(bad)
