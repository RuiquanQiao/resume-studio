"""The picked roles' descriptions, read in the background, and how well each fits its track's resume: no AI.

    resume-studio-data/hunt/jds.json   {"P12": {"jd", "read", "deadline", "error"}, ...}

Matching roles filters by title (tracks). A title says "this kind of role", not "a good one for me", so every
picked role's own page is read (jobhunt/reader.py, the browser off the screen, one page at a time) and its
description is compared with the resume subset its track goes out with. The score is a word match, shown as one:
the skills the description names (Python, PyTorch, RAG, 机器学习…) and how many of them the subset's resume
names too. Only the description's skill words count, so a long paragraph about the company changes nothing.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable

from . import reader

MAX_PER_RUN = 80        # pages read after one scan; the rest wait for the next (a run is minutes, never hours)
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
_LATIN = re.compile(r"[a-z][a-z0-9+#.\-]*[a-z0-9+#]|[a-z]")
# Chinese has no spaces between words: these are the skill terms looked for in a description written in it
_ZH_TERMS = """机器学习 深度学习 强化学习 自然语言处理 计算机视觉 大模型 大语言模型 多模态 推荐系统 搜索引擎 知识图谱 数据挖掘
数据分析 数据仓库 数据库 分布式 微服务 高并发 云原生 容器 前端 后端 全栈 客户端 移动端 小程序 算法 数据结构 操作系统 计算机网络
图像处理 语音识别 语音合成 模型训练 模型部署 模型压缩 量化 微调 提示词 智能体 检索增强 向量数据库 产品设计 需求分析 用户研究
交互设计 原型设计 数据驱动 项目管理 敏捷开发 测试开发 自动化测试 性能优化 架构设计 系统设计 地理信息 遥感 嵌入式""".split()


def skill_words(text: str) -> set[str]:
    """The skills a text names: Latin words that aren't plain English ("python", "pytorch", "rag", "c++"), and the
    Chinese skill terms in it."""
    low = str(text or "").casefold()
    out = {w.strip(".-") for w in _LATIN.findall(low)}
    out = {w for w in out if len(w) > 1 and w not in _STOP and not w.isdigit()}
    out |= {z for z in _ZH_TERMS if z in low}
    return out


def fit(jd: str, have: set[str]) -> dict | None:
    """{score 0–100, hit[], miss[]}: of the skills the description names, the ones the resume names too. None when
    the description names fewer than 3 skills (nothing to compare)."""
    want = skill_words(jd)
    if len(want) < 3:
        return None
    hit = sorted(want & have)
    miss = sorted(want - have)
    return {"score": round(100 * len(hit) / len(want)), "hit": hit[:30], "miss": miss[:30]}


class Descriptions:
    """jds.json, and the reader that fills it: one page at a time, in a thread of its own."""

    def __init__(self, hunt: Any, browser_of: Callable[[], Any], publish: Callable[[], None]):
        self.hunt, self.browser_of, self.publish = hunt, browser_of, publish
        self.lock = threading.Lock()
        self.queue: list[tuple[str, str]] = []      # (posting id, its link) still to read in this run
        self.run: dict | None = None                # {total, done} while a run goes on
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

    def start(self, postings: list[dict]) -> int:
        """Read the description of every posting given that has a link and none read yet (a page that failed is
        tried again a day later). Returns how many joined the run."""
        have = self.all()
        day_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        todo = [(p["id"], p["url"]) for p in postings if re.match(r"https?://", p.get("url") or "")
                and not (have.get(p["id"]) or {}).get("jd")
                and not ((have.get(p["id"]) or {}).get("error") and (have[p["id"]].get("read") or "") > day_ago)]
        with self.lock:
            queued = {q[0] for q in self.queue}
            new = [x for x in todo if x[0] not in queued][:MAX_PER_RUN - len(self.queue)]
            if not new:
                return 0
            self.queue += new
            if self.run is not None:
                self.run["total"] += len(new)
                return len(new)
            self.run = {"total": len(new), "done": 0}
        threading.Thread(target=self._work, daemon=True, name="hunt-read-jds").start()
        self.publish()
        return len(new)

    def _work(self) -> None:
        try:
            while True:
                with self.lock:
                    if not self.queue:
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
                self.publish()
                time.sleep(self.pause)
        finally:
            with self.lock:
                self.run, self.queue = None, []
            self.publish()
