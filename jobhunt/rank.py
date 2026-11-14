"""The picked roles' descriptions, read in the background, and how well each fits its track's resume: no AI.

    resume-studio-data/hunt/jds.json   {"P12": {"jd", "read", "deadline", "error"}, ...}

Matching roles filters by title (tracks). A title says "this kind of role", not "a good one for me", so every
picked role's own page is read (jobhunt/reader.py, the browser off the screen, one page at a time) and its
description is compared with the resume subset its track goes out with. The score is a word match, shown as one:
the skills the description asks for (jobhunt/skills.py, any field: Python, 用户研究, 财务报表分析…) and how many of
them the subset's resume names too. Only the description's skill words count, so a long paragraph about the company changes nothing.
"""
from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from typing import Any, Callable

from . import reader

MAX_PER_RUN = 80        # pages a scan or a track change reads by itself; the rest wait (such a run is minutes, never hours)
PAUSE = 1.0             # seconds between two pages, so a careers site never sees a burst

# Latin words that are never a skill, in a job description
_STOP = set("""a about above after all also an and any are as at be been being both but by can could do does each
for from get go has have he her his how if in into is it its just like make many may more most must my new no not
now of on one only or other our out over own per please so some such than that the their them then there these
they this those through to under up us use used using very via was we well were what when where which while who
why will with within without work would you your yours team teams years year experience strong ability able good
great excellent including include etc plus related relevant preferred required requirements responsibilities
skills skill knowledge understanding familiar familiarity proficient proficiency solid hands degree bachelor
master phd university field fields equivalent across build building develop developing development design
designing support supporting help helping new key based level role roles job position candidate candidates
company business product products customer customers best high time least least communication written verbal
english chinese japanese korean german french spanish fluent native speaker opportunity opportunities""".split())


def _has(text: str, k: str) -> bool:
    """`k` is in `text` as a word of its own: "java" is not in "javascript", "c" not in "c++"; Han text has no
    spaces, so a Han term is found anywhere."""
    if not k:
        return False
    if re.search(r"[a-z0-9+#]", k[0] + k[-1]):
        return re.search(r"(?<![a-z0-9+#])" + re.escape(k) + r"(?![a-z0-9+#])", text) is not None
    return k in text


def fit(jd: str, have: str, lexicon: Any = None, levels: dict | None = None) -> dict | None:
    """Two numbers for one description, by rules (no AI), and the skills behind each:

    - `score` (resume coverage, 0–100), `hit[]`, `miss[]`: of the skills the description asks for (jobhunt/skills.py:
      in any field, learning from every description read), the ones the resume's text `have` names. It says whether
      the resume passes a keyword screen, not whether the user can do the work: a project's `tech` may be code an AI
      wrote (user, 2026-11-14).
    - `real` (0–100, None while none of the description's skills is rated): how much of the same ask the user really
      has, from the skill catalog's levels (resume/skills.py, `levels`: key -> {name, level ''}) whether or not this
      resume names the skill: expert and strong (hold up under a normal test or interview) count in full, working (does not hold up) 0.3, none nothing, and a skill the
      resume doesn't name at all nothing. A skill the resume names without a level is left out of it until rated.
      `have[]` (expert, strong), `working[]`, `cant[]` are the skills by level; `unrated[]` the catalog's skills it names that have no level yet.

    A skill written with more around it counts when its core is there: "Python编程开发" by Python, "深度学习框架" by
    深度学习. None when the description names fewer than 3 skills (nothing to compare).

    Until 2026-11-14 the skills were Latin words and a fixed list of IT terms, so a product manager's description
    (用户研究, 竞品分析, Axure) had no skills and no score: the user couldn't tell one 产品经理 from another."""
    from resume.skills import WEIGHT, key as skill_key

    from .skills import _fold, skills_of
    want = skills_of(jd, lexicon, learn=False)
    if len(want) < 3:
        return None
    text = _fold(have)

    def cores(term: str) -> list[str]:
        k = _fold(term)
        out = [k] + [w for w in re.findall(r"[a-z][a-z0-9+#.\-]*[a-z0-9+#]", k) if w not in _STOP and w != k]
        if lexicon is not None:
            out += [_fold(name) for _, name in lexicon.find(term) if _fold(name) not in out]
        return out

    hit = [w for w in want if any(_has(text, c) for c in cores(w))]
    miss = [w for w in want if w not in hit]
    out = {"score": round(100 * len(hit) / len(want)), "hit": hit[:30], "miss": miss[:30]}
    if not levels:
        return {**out, "real": None}

    def level(term: str) -> str | None:
        """Its level; '' for a skill of the catalog with none yet; None for a word no entry's `tech` names."""
        found = None
        for c in cores(term):
            got = levels.get(skill_key(c))
            if got and got["level"]:
                return got["level"]
            if got:
                found = ""
        return found

    by = {w: level(w) for w in want}
    unrated = [w for w in hit if by[w] == ""]   # skills the user can rate (chips), and hasn't
    if not any(by.values()):   # none of its skills rated yet: a 0 would say "can't do any of it"
        return {**out, "real": None, "unrated": unrated[:30]}
    # out of: the skills rated, and the ones the resume doesn't name at all (nothing says the user has them);
    # one the resume names without a level isn't known either way, so it is left out until it is rated
    counted = [w for w in want if by[w] or w not in hit]
    real = sum(WEIGHT[by[w]] for w in counted if by[w])
    return {**out, "real": round(100 * real / len(counted)) if counted else None,
            "have": [w for w, lv in by.items() if lv in ("expert", "strong")][:30],
            "working": [w for w, lv in by.items() if lv == "working"][:30],
            "cant": [w for w, lv in by.items() if lv == "none"][:30],
            "unrated": unrated[:30]}


class Descriptions:
    """jds.json, and the reader that fills it: one page at a time, in a thread of its own."""

    def __init__(self, hunt: Any, browser_of: Callable[[], Any], publish: Callable[[], None]):
        self.hunt, self.browser_of, self.publish = hunt, browser_of, publish
        self.lock = threading.Lock()
        self.queue: list[tuple[str, str]] = []      # (posting id, its link) still to read in this run
        self.run: dict | None = None                # {total, done, read, failed} while a run goes on
        self.last: dict = {}                        # the run that ended last: {at, read, failed, stopped}, for its toast
        self.pause = PAUSE

    # ---- the file ----
    def all(self) -> dict[str, dict]:
        try:
            d = json.loads(self.hunt._load("jds.json") or "{}")
        except ValueError:
            d = {}
        return d if isinstance(d, dict) else {}

    def _put(self, pid: str, row: dict) -> None:
        with self.hunt.lock:
            d = self.all()
            d[pid] = row
            self.hunt._write("jds.json", d)

    # ---- reading ----
    def status(self) -> dict | None:
        with self.lock:
            return dict(self.run) if self.run else None

    def unread(self, postings: list[dict]) -> list[dict]:
        """The postings given that have a link and no description read yet (a page that failed: again a day later)."""
        have = self.all()
        day_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        return [p for p in postings if re.match(r"https?://", p.get("url") or "")
                and not (have.get(p["id"]) or {}).get("jd")
                and not ((have.get(p["id"]) or {}).get("error") and (have[p["id"]].get("read") or "") > day_ago)]

    def stop(self) -> None:
        """The run stops after the page it is reading: what was read keeps its score, the rest waits."""
        with self.lock:
            self.queue = []
            if self.run is not None:
                self.run["stopped"] = True

    def start(self, postings: list[dict], limit: int | None = MAX_PER_RUN) -> int:
        """Read the description of every posting given that has a link and none read yet (a page that failed is
        tried again a day later). `limit`: at most this many in the run (the reading a scan or a track change starts
        by itself); None, every one (the user's own click: they see it go and can stop it). Returns how many joined
        the run."""
        todo = [(p["id"], p["url"]) for p in self.unread(postings)]
        with self.lock:
            queued = {q[0] for q in self.queue}
            new = [x for x in todo if x[0] not in queued]
            if limit is not None:
                new = new[:max(0, limit - len(self.queue))]
            if not new:
                return 0
            self.queue += new
            if self.run is not None:
                self.run["total"] += len(new)
                return len(new)
            self.run = {"total": len(new), "done": 0, "read": 0, "failed": 0}
        threading.Thread(target=self._work, daemon=True, name="hunt-read-jds").start()
        self.publish()
        return len(new)

    def _work(self) -> None:
        b = self.browser_of()
        # the browser a scan opened stays open while descriptions are read after it (it closes itself when idle)
        with (b.hold() if hasattr(b, "hold") else contextlib.nullcontext()):
            self._read_all()

    def _read_all(self) -> None:
        try:
            while True:
                with self.lock:
                    if not self.queue:
                        if self.run is not None:
                            self.last = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "read": self.run["read"],
                                         "failed": self.run["failed"], "stopped": bool(self.run.get("stopped"))}
                        self.run = None
                        break
                    pid, url = self.queue.pop(0)
                row: dict = {"read": time.strftime("%Y-%m-%d %H:%M")}
                try:
                    browser = self.browser_of()
                    if browser is None:
                        raise RuntimeError("no browser")
                    got = reader.read_posting(browser, url)
                    if got.get("jd"):
                        row.update({"jd": got["jd"], **({"deadline": got["deadline"]} if got.get("deadline") else {})})
                    else:
                        row["error"] = "no description on the page"
                except Exception as e:  # noqa: BLE001  one page's trouble never stops the rest
                    row["error"] = str(e)[:300]
                self._put(pid, row)
                with self.lock:
                    if self.run is not None:
                        self.run["done"] += 1
                        self.run["failed" if row.get("error") else "read"] += 1
                self.publish()
                time.sleep(self.pause)
        finally:
            with self.lock:
                self.run, self.queue = None, []
            self.publish()
