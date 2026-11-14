"""What a role's description asks for, and which of the user's tracks it reads like: by rules, no AI.

Matching roles sorts every title into the user's tracks by the tracks' words (jobhunt/postings.py), with no AI, so a
scan of 50,000 titles costs nothing. A title can mislead: "AI 应用开发工程师" whose duties are all about agents. Once
the user picks a role (Want to apply) its description is read from its page, and two things are worked out from it:

- `skills`: the skills it asks for, in any field (Python, CPA, 财务报表分析, 招投标, AutoCAD). They are the phrases a
  requirement names after 熟悉 / 掌握 / 精通 / 有…经验 / "experience with" / "proficient in", the tool-like words in
  it (C++, SQL, Excel), and every skill learned from earlier descriptions that this one names too: the list grows
  with each description read (hunt/skills.json), in the user's own field, without a word list made for one field.
- `hint`: when its duties use another track's words far more than its own track's, that track, with the counts as
  the reason. The user moves it or keeps it; nothing is moved for them.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from .postings import _fold, _keyword, _takes

MAX_SKILLS = 30
VERSION = 2   # of the rules below: a role's skills worked out by older rules are worked out again (not hand-set ones)
LEXICON = "skills.json"   # hunt/skills.json  {"terms": {"<folded>": {"name": "<as written>", "n": <descriptions>}}}

# the headings a description's parts start with (中文 and English), the requirements and the duties
_REQ = re.compile(r"^\s*[\[【(（]?\s*(任职要求|任职资格|岗位要求|职位要求|职位资格|岗位资格|应聘要求|招聘要求|资格要求|我们希望你|"
                  r"你需要具备|你需要|加分项|优先条件|qualifications?|requirements?|minimum qualifications|"
                  r"preferred qualifications|what you(?:'|’)ll need|what we(?:'|’)re looking for|who you are|"
                  r"skills(?: and| &) experience|about you|you have|nice to have)\b", re.I)
_DUTY = re.compile(r"^\s*[\[【(（]?\s*(岗位职责|工作职责|职位职责|职位描述|岗位描述|工作内容|职责描述|你将负责|你的职责|"
                   r"responsibilities|what you(?:'|’)ll do|the role|about the role|your role|key responsibilities|"
                   r"duties|job description)\b", re.I)

# the words a requirement names a skill after
_CUE_ZH = re.compile(r"(?:熟练掌握|熟练使用|熟练运用|熟练操作|熟悉使用|精通|掌握|熟悉|了解|会使用|会用|能够使用|能使用|使用过|"
                     r"擅长|具备|具有|拥有|持有|通过)([^。；;！!？?\n]{2,120})")
_HAVE_ZH = re.compile(r"有(?:过)?([^，,。；;！!？?\n]{2,40}?)(?:相关)?(?:的)?(?:工作|项目|开发|实习|实践|从业)?经验")
_CUE_EN = re.compile(r"(?:proficien(?:t|cy) (?:in|with)|experience (?:with|in|using|of)|familiar(?:ity)? with|"
                     r"knowledge of|expertise (?:in|with)|skilled (?:in|with)|hands-on (?:experience )?with|"
                     r"working knowledge of|fluen(?:t|cy) in|competen(?:t|ce|cy) (?:in|with)|certifi(?:ed|cation) in)"
                     r"\s+([^.;\n]{2,160})", re.I)
_SPLIT = re.compile(r"[、，,/／;；:：()（）\[\]【】<>《》\"“”]|及其|以及|及|和|与|或者|或|并且|进行|用于|用来|来|"
                    r"\band\b|\bor\b|\bincluding\b|"
                    r"\bsuch as\b|\be\.g\.|\betc\.?|等")
_LEAD = re.compile(r"^(?:包括但不限于|但不限于|不限于|熟练掌握|熟练使用|熟练运用|熟练|精通|掌握|熟悉|了解|会使用|会用|能够|能(?!力)|"
                   r"有过|有|具备|具有|扎实的?|良好的?|较强的?|至少|一种或多种|一门或多门|一种以上|一门以上|一种|一门|常用的?|"
                   r"主流的?|基本的?|相关的?|常见的?|各类|各种|以下|如|例如|包括|包含|运用|使用|应用|"
                   r"(?:the|a|an|any|modern|common|related|relevant|strong|solid|good|basic|advanced)\s|"
                   r"one or more(?: of)?\s|at least one(?: of)?\s|\s)+", re.I)
_TAIL = re.compile(r"(?:的?(?:基本)?(?:原理|知识|基础知识|基础概念|概念|基础|理论|技能|技术栈|使用|应用|操作|经验|能力|环境|系列|"
                   r"平台使用|平台)?(?:者优先|优先|者|即可|为佳|更佳|加分|及以上|以上)|编程|语言|相关(?:技术|知识)?|技术|等|的|"
                   r"中的?一种|之一|开发环境|环境|系列|平台使用|基础知识|基础概念|基础|原理|的?基本|证书|资格证|"
                   r"\s*任一(?:开发|种)?|\s*任意一种|"
                   r"\s+(?:skills?|experience|knowledge|is a plus|a plus|preferred|required|or similar|frameworks?|tools?))+$",
                   re.I)
# a requirement about a degree or a major names subjects, not skills ("计算机、数学、自动化等相关专业")
_DEGREE = re.compile(r"专业|学历|学位|毕业|本科|硕士|博士|大专|\bdegree\b|\bmajor\b|\bbachelor|\bmaster'?s\b|\bphd\b", re.I)
# words too general to be a skill on their own, in any field
_GENERAL = set("""工作 项目 团队 数据 工程 实践 熟悉 能够 流程 质量 实习 技能 技术 开发 设计 原理 框架 工具 代码 操作 实现
落地 合成 自然 电子 竞赛 扎实 编程 计算机 人工智能 数学 自动化 算法 应用 系统 平台 业务 产品 管理 研发 服务 能力 方法 方案
模型 软件 硬件 互联网 信息 知识 经验 场景 解决方案 问题 需求 功能 相关 主流 常用 基础 理论 技巧 规范 标准 文档 资料 环境
项目经历 实习经历 工作经验 计算机科学 计算机基础 计算机基础知识 逻辑思维 逻辑分析 学习 研究 分析 写作 表达 实际 逻辑
加分项 加分 核心 深入理解 高效 结果导向 优化 迭代 评估 部署 训练 调研 沟通 协作 条件 验证 工业 技术栈 工程实践 比赛
意愿 方式 方向 思路 视野 经历 背景 类似工具 同类工具 开发工具 客户端""".split())
# a phrase that is a quality, a degree or a duty rather than a skill
_NOT = re.compile(r"(?:能力|精神|意识|态度|习惯|素质|素养|责任心|心态|热情|兴趣|学历|本科|硕士|博士|专科|大专|学位|"
                  r"专业|毕业|年以上|年及以上|岗位|公司|行业|领域|优先|经验|以上|良好|优秀|较强|一定|负责|参与|"
                  r"沟通|协作|钻研|思维|出差|独立|完成|读懂|能够|能独立|不限于|力强|加分|我们|你|\bdegree|\bbachelor|\bmaster|\bphd\b|\byears?\b|\babilit|\bpassion|\bmotivated|\battitude|"
                  r"\benvironment|\bstakeholders?\b|\bcompany\b|\bindustry\b)", re.I)
_LATIN = re.compile(r"(?<![A-Za-z0-9+#.\-])[A-Za-z][A-Za-z0-9+#.\-]*[A-Za-z0-9+#]|(?<![A-Za-z0-9])[A-Za-z](?![A-Za-z0-9])")
# Latin words that are never a skill: the English around the skills, in a description written in Chinese or English
_STOP = set("""a about above after all also an and any are as at be been being both but by can could do does each
for from get go has have he her his how if in into is it its just like make many may more most must my new no not
now of on one only or other our out over own per please so some such than that the their them then there these
they this those through to under up us use used using very via was we well were what when where which while who
why will with within without work would you your yours team teams years year experience strong ability able good
great excellent including include etc plus related relevant preferred required requirements responsibilities
skills skill knowledge understanding familiar familiarity proficient proficiency solid hands degree bachelor
master phd university field fields equivalent across build building develop developing development design
designing support supporting help helping key based level role roles job position candidate candidates
company business product products customer customers best high time least communication written verbal
english chinese japanese korean german french spanish fluent native speaker opportunity opportunities hr jd
we're you'll you're who's what's it's app apps web data system systems online offline ok top base""".split())
_CJK = re.compile(r"[㐀-鿿]")
# common English words that are also the names of tools, written with a capital when they are
_NAMES = {"go", "rust", "swift", "dart", "julia", "r", "spring", "flask", "express", "unity", "excel", "word", "access",
          "outlook", "visio", "project", "office", "teams", "jira"}


def _sections(jd: str) -> tuple[str, str]:
    """(duties, requirements) of a description: the lines under each heading; the whole text for either part that
    has no heading of its own."""
    duty, req, now = [], [], None
    for line in str(jd or "").splitlines():
        if _REQ.match(line):
            now = req
        elif _DUTY.match(line):
            now = duty
        if now is not None:
            now.append(line)
    text = str(jd or "")
    return ("\n".join(duty) or text), ("\n".join(req) or text)


def _clean(x: str) -> str:
    x = re.sub(r"\s+", " ", re.sub(r"^[\s\d.、)）\-–—•·*]+", "", x)).strip(" .·-–—'\"")
    for _ in range(3):
        y = _TAIL.sub("", _LEAD.sub("", x)).strip(" .·-–—'\"")
        if y == x:
            break
        x = y
    return x


def _ok(x: str) -> bool:
    if not x or _NOT.search(x) or x.isdigit() or x in _GENERAL:
        return False
    if _CJK.search(x):
        # a Latin name left with a Chinese verb before it ("熟悉ONNX") was cut by _LEAD; one left with a cue
        # inside it is a sentence, not a name
        # and one with a clause's little words in it ("业务目标之间的关系", "并能结合具体任务") is a sentence too
        return 2 <= len(x) <= 10 and not re.match(r"[对为在把将让给]|向(?!量)", x) and not re.search(
            r"熟悉|掌握|使用|精通|了解|具备|的|之间|之一|并能|并且|从而|从全局|结合|理解|紧跟|统筹|自己|见解|对比|相关|全局|"
            r"具体|真实|问题|目标|关系|全流程|提升|最终|效果|获奖|敏感|自驱", x)
    words = x.split()
    # "Go" is a language, "go" a verb
    return 2 <= len(x) <= 40 and len(words) <= 4 and (x.casefold() not in _STOP or x[:1].isupper() and len(words) == 1
                                                       and x.casefold() in _NAMES)


def _named(jd: str) -> list[str]:
    """The skills a description's requirements name: after a word that names one, and the tool-like words in it."""
    _, req = _sections(jd)
    out: list[str] = []
    for rx in (_CUE_ZH, _HAVE_ZH, _CUE_EN):
        for m in rx.finditer(req):
            if _DEGREE.search(m.group(1)):
                continue
            for part in _SPLIT.split(m.group(1)):
                x = _clean(part)
                if _ok(x):
                    out.append(x)
    zh = len(_CJK.findall(req)) > len(re.findall(r"[A-Za-z]", req)) / 4
    # a clause about a degree names subjects, not tools ("Computer Science, Mathematics"); the next clause of
    # its line may name one ("……本科以上学历，持有CPA证书者优先")
    req = "\n".join(x for x in re.split(r"[\n，,；;。]", req) if not _DEGREE.search(x))
    for m in _LATIN.finditer(req):
        w = m.group(0).strip(".-")
        if len(w) < 2 or (w.casefold() in _STOP and not (w[:1].isupper() and w.casefold() in _NAMES)):
            continue
        # in Chinese text a Latin word is nearly always a tool or a term; in English only one that looks like one
        # (C++, Node.js, SQL, PyTorch, Excel in the middle of a sentence), never every word of the paragraph
        tool = (re.search(r"[+#.\d]", w) or (w.isupper() and len(w) <= 6) or re.search(r"[a-z][A-Z]", w)
                or (w[0].isupper() and m.start() > 0 and not re.search(r"[.!?:]\s*$", req[:m.start()]))
                # the first of a list right after a colon ("Skills: Python, Java"): the whole of its clause, so a
                # name, not a sentence's start (the clauses were split at the commas above)
                or (w[0].isupper() and re.match(r"[ \t]*(?:\n|$|/|、)", req[m.end():])))
        if zh or tool:
            out.append(w)
    return out


class Lexicon:
    """The skills learned from the descriptions read so far (hunt/skills.json): a skill one description names
    after 熟悉 is looked for in every other one, so the list fits the user's own field."""

    def __init__(self, folder: Path):
        self.path = Path(folder) / LEXICON
        self.lock = threading.Lock()
        self._terms: dict[str, dict] | None = None
        self._rx: tuple[int, re.Pattern | None] | None = None

    def terms(self) -> dict[str, dict]:
        if self._terms is None:
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                self._terms = d.get("terms") if isinstance(d.get("terms"), dict) else {}
            except (OSError, ValueError, AttributeError):
                self._terms = {}
        return self._terms

    def correct(self, removed: list[str], added: list[str]) -> None:
        """The user took these off a role as not skills (never named again) and typed these in (looked for in
        every description from now on): a correction made once fixes every role after it."""
        with self.lock:
            terms = self.terms()
            for x in removed:
                terms.setdefault(_fold(x), {"name": x, "n": 0})["no"] = True
            for x in added:
                v = terms.setdefault(_fold(x), {"name": x, "n": 0})
                v.pop("no", None)
                v["n"] = max(int(v.get("n") or 0), 2)
                v["typed"] = True   # the user's word: looked for even where the rules wouldn't take it
            self._save(terms)
            self._rx = None

    def blocked(self, x: str) -> bool:
        return bool((self.terms().get(_fold(x)) or {}).get("no"))

    def _save(self, terms: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"terms": terms}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def learn(self, names: list[str]) -> None:
        with self.lock:
            terms = self.terms()
            changed = False
            for x in dict.fromkeys(names):
                k = _fold(x)
                if k not in terms:
                    terms[k] = {"name": x, "n": 0}
                terms[k]["n"] = int(terms[k].get("n") or 0) + 1
                changed = changed or terms[k]["n"] == 2   # named twice: looked for from now on
            if names:
                self._save(terms)
            if changed:
                self._rx = None

    def find(self, text: str, typed: bool = False) -> list[tuple[int, str]]:
        """(where, name) of every learned skill a text names; `typed`: only those the user typed in."""
        terms = self.terms()
        if self._rx is None:
            def pattern(keys: list[str]) -> re.Pattern | None:
                keys = sorted(keys, key=len, reverse=True)
                pats = [(r"(?<![a-z0-9+#])" + re.escape(k) + r"(?![a-z0-9+#])") if not _CJK.search(k) else re.escape(k)
                        for k in keys]
                return re.compile("|".join(pats)) if pats else None
            # a skill two descriptions named at least: one named once may be one description's odd phrase
            learned = [k for k, v in terms.items() if len(k) >= 2 and int(v.get("n") or 0) >= 2 and not v.get("no")
                       and (v.get("typed") or _ok(v.get("name") or k))]
            self._rx = (pattern(learned), pattern([k for k in learned if terms[k].get("typed")]))
        rx = self._rx[1 if typed else 0]
        if rx is None:
            return []
        folded = _fold(text)
        return [(m.start(), terms[m.group(0)]["name"]) for m in rx.finditer(folded) if m.group(0) in terms]


def skills_of(jd: str, lexicon: Lexicon | None = None, learn: bool = True) -> list[str]:
    """The skills a description asks for, as it writes them, in order, each once (at most MAX_SKILLS)."""
    if not str(jd or "").strip():
        return []
    named = _named(jd)
    if lexicon is not None and learn:
        lexicon.learn(named)
    found = list(named)
    if lexicon is not None:
        _, req = _sections(jd)
        found += [name for _, name in sorted(lexicon.find(req))]
        # a skill the user typed in is theirs to look for anywhere in a description, its duties too
        found += [name for _, name in sorted(lexicon.find(jd, typed=True))]
    out, seen = [], set()
    for x in found:
        k = _fold(x)
        if lexicon is not None and lexicon.blocked(x):   # the user said it isn't one
            continue
        if k not in seen:
            seen.add(k)
            out.append(x)
    # never a word of a longer one there too ("Vibe" and "Coding" beside "Vibe Coding")
    words = {w for x in out if " " in x.strip() for w in _fold(x).split()}
    out = [x for x in out if " " in x.strip() or _fold(x) not in words]
    return out[:MAX_SKILLS]


def _hits(folded: str, keyword: str) -> bool:
    parts = _keyword(keyword)
    return bool(parts) and all((x in folded) if isinstance(x, str) else x.search(folded) is not None for x in parts)


def track_hint(jd: str, title_tracks: list[str], tracks: list[dict], nature: str = "") -> dict | None:
    """{track, words: [[keyword, lines]], from, own}: the track whose words the role's duties use in far more of
    their lines (3 lines at least, and twice as many as its own track's), or None. A line counts once per track,
    however many of its words are in it ("Java 后端开发" is one line, not three hits). A track for some kinds of
    company only is never one for a company of another kind."""
    duties, _ = _sections(jd)
    lines = [_fold(x) for x in re.split(r"[\n。；;!?！？]", duties) if x.strip()]
    if not lines:
        return None
    scores = {}
    for tr in tracks:
        per = {k: sum(1 for ln in lines if _hits(ln, k)) for k in tr.get("keywords") or []}
        n = sum(1 for ln in lines if any(_hits(ln, k) for k, c in per.items() if c))
        scores[tr["id"]] = (n, [[k, c] for k, c in per.items() if c])
    own = max((scores[x][0] for x in title_tracks if x in scores), default=0)
    best = None
    for tr in tracks:
        if tr["id"] in title_tracks or not tr.get("keywords"):
            continue
        if tr.get("natures") and not any(_takes(e, nature) for e in tr["natures"]):
            continue
        n = scores[tr["id"]][0]
        if n >= 3 and n >= 2 * max(own, 1) and (best is None or n > scores[best][0]):
            best = tr["id"]
    if best is None:
        return None
    mine = next((x for x in title_tracks if x in scores), "")
    top = lambda ws: sorted(ws, key=lambda w: -w[1])[:4]   # noqa: E731
    return {"track": best, "words": top(scores[best][1]), "from": mine, "own": top(scores[mine][1]) if mine else []}


# a description's hint is worked out once per description, set of tracks and nature: the table is drawn again on
# every change, and 300 roles' descriptions against 24 tracks took a second each time
_HINTS: dict[tuple, dict | None] = {}
_HINTS_LOCK = threading.Lock()
_SIG: list = [None, ""]


def view(a: dict, title_tracks: list[str], tracks: list[dict], nature: str = "") -> dict:
    """An application's track fields for the window: `tracks` (the one the user set, else its title's), and the
    `track_hint` when its description reads like another track and the user hasn't set one."""
    known = {tr["id"] for tr in tracks}
    mine = a.get("track") if a.get("track") in known else ""
    out: dict[str, Any] = {"tracks": [mine] if mine else [x for x in title_tracks if x in known]}
    # any track the user set, "-" (keep its title's) too, is theirs: no hint second-guesses it
    if not a.get("track") and a.get("jd"):
        last = _SIG[:]   # the tracks asked about last and their key: once per table, not once per role
        if last[0] is not tracks:
            last = [tracks, json.dumps(tracks, sort_keys=True, ensure_ascii=False)]
            _SIG[:] = last
        key = (hash(a["jd"]), last[1], tuple(out["tracks"]), nature)
        with _HINTS_LOCK:
            hit = key in _HINTS
            h = _HINTS.get(key)
        if not hit:
            h = track_hint(a["jd"], out["tracks"], tracks, nature)
            with _HINTS_LOCK:
                if len(_HINTS) > 5000:
                    _HINTS.clear()
                _HINTS[key] = h
        if h:
            out["track_hint"] = h
    return out
