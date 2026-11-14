"""What kind of employer a company is, by how one prepares to apply to it (2026-11-13).

A company's `nature` is one id of this catalog: a kind every country has (KINDS: a state-owned enterprise, a
university…), or one of a country's own kinds (SUBTYPES: 央企, 事业单位, the APS…), each under one of KINDS.
It is the hiring entity's own: 上海沐瞳 is the company in the table, so 外企 is a fact of it wherever the user
comes from. A track for some natures only (jobhunt/postings.py) may name kinds, which take every subtype of
them, or subtypes. "" is not known yet: never a guess.

Some employers take only their own citizens, or residents (`needs`). The user says in their profile
(resume.yaml `profile.work`) which countries they are a citizen or permanent resident of, which they hold a
work visa for: the window offers those countries' own kinds and says when a company's roles need more than the
user has. A country is added there only to look for work in it, so no other list says where they look (2026-11-14).
Never shown on a resume.

Country catalogs are data, so one can move into a country's plugin later as it is.
"""
from __future__ import annotations

import re
from typing import Any

# id, English name (the window translates it), what it covers
KINDS = [
    ("government", "Government", "ministries, agencies, the civil service, the armed forces"),
    ("public", "Public institution", "state-funded bodies that aren't government: public hospitals, schools, broadcasters, statutory authorities"),
    ("soe", "State-owned enterprise", "a company the state owns or controls"),
    ("academic", "University / research", "universities and research institutes"),
    ("nonprofit", "Non-profit", "charities, NGOs, foundations"),
    ("intl_org", "International organisation", "the UN and its agencies, the World Bank, ADB, OECD…"),
    ("private", "Private company", "privately owned, listed or not, owned in the country it hires in"),
    ("foreign", "Foreign-invested company", "a local entity a foreign company owns, wholly or jointly (外企, WFOE, JV, a multinational's subsidiary)"),
    ("other", "Other", "none of these"),
]
KIND_IDS = [k for k, _, _ in KINDS]

# what an employer asks of an applicant's status in its country, least to most
NEEDS = ["work", "resident", "citizen"]

# id, country, kind, English name, needs, how one prepares (for the AI and `show`)
SUBTYPES = [
    ("cn.civil", "CN", "government", "Party and government organs (公务员, 选调生)", "citizen",
     "国考 / 省考: 行测 and 申论, then interview and 政审; 选调生 through one's university; party membership often counts"),
    ("cn.public", "CN", "public", "Public institution (事业单位)", "citizen",
     "事业单位公开招聘: 职测 and 综合应用能力 written tests, interview; 编制; announcements on each province's 人社 site"),
    ("cn.central_soe", "CN", "soe", "Central SOE (央企)", "citizen",
     "unified 校招 on 国聘 and the group's own site, autumn first; online test (行测-style plus the trade), interviews, 政审; most postings ask for Chinese nationality"),
    ("cn.local_soe", "CN", "soe", "Local SOE (地方国企)", "citizen",
     "the province's or city's 国资委 groups; 校招 or 社招 announcements with written tests; local 户口 can count"),
    ("cn.state_finance", "CN", "soe", "State-owned bank or financial firm (国有金融)", "citizen",
     "head office and branch 校招; written test of 行测, finance, English and the bank's own knowledge; several interview rounds"),
    ("cn.university", "CN", "academic", "University or research institute (高校 / 科研院所)", "citizen",
     "research posts by 人才引进 (papers, a PhD); administrative and counsellor (辅导员) posts by written test and interview"),
    ("cn.private", "CN", "private", "Private company (民企)", "work",
     "big tech: 提前批 and 秋招 from July, online assessment, 2-4 technical interviews, internships count most"),
    ("cn.foreign", "CN", "foreign", "Foreign-invested company (外企 / 合资)", "work",
     "graduate programmes often open early (Aug-Nov), English interviews, assessment centres; wholly foreign-owned (WFOE) or a joint venture"),
    ("au.aps", "AU", "government", "Australian Public Service (APS)", "citizen",
     "APS graduate programmes (the AGGP and agencies' own) open Feb-Apr for the next year; selection criteria answered in STAR; a security clearance"),
    ("au.state_ps", "AU", "government", "State or territory public service", "resident",
     "each state's graduate programme and jobs board; key selection criteria; many roles take permanent residents"),
    ("au.gbe", "AU", "soe", "Government business enterprise", "work",
     "Australia Post, NBN Co and the like: graduate programmes run like a private company's"),
    ("au.university", "AU", "academic", "University", "work",
     "academic posts by selection criteria and a research record; professional staff posts like the public service's"),
]
SUBTYPE = {s[0]: s for s in SUBTYPES}
IDS = KIND_IDS + [s[0] for s in SUBTYPES]

# words people (and the AI) write for them
WORDS = {"国央企": "soe", "国企": "soe", "国有企业": "soe", "央企": "cn.central_soe", "中央企业": "cn.central_soe",
         "地方国企": "cn.local_soe", "事业单位": "cn.public", "公务员": "cn.civil", "党政机关": "cn.civil",
         "选调生": "cn.civil", "高校/科研院所": "cn.university", "高校": "cn.university", "科研院所": "cn.university",
         "民企": "cn.private", "民营企业": "cn.private", "民营": "cn.private", "外企": "cn.foreign", "合资": "cn.foreign",
         "外资": "cn.foreign", "国有银行": "cn.state_finance", "其他": "other",
         "state-owned": "soe", "state owned enterprise": "soe", "university": "academic", "ngo": "nonprofit",
         "charity": "nonprofit", "civil service": "government", "aps": "au.aps", "gbe": "au.gbe"}

# where to read who owns a company: every country's, then each country's own (`fill --help`, `show`)
SOURCES = {
    "": ["the company's own annual report or 'about / investors' page: its parent and controlling shareholder",
         "a listed parent's filings: SEC EDGAR (US), HKEX, SSE / SZSE, ASX, SGX, LSE, TSE / EDINET (Japan)",
         "OpenCorporates for a company registered abroad; a state's list of the companies it owns (its finance ministry)"],
    "CN": ["国家企业信用信息公示系统 (gsxt.gov.cn), 企查查, 天眼查: shareholders and the 实际控制人",
           "国资委's list of 中央企业 (sasac.gov.cn); a province's 国资委 for its 地方国企",
           "a listed company's annual report: 实际控制人 (国务院国资委, a local 国资委, a person)"],
    "AU": ["ASIC Connect and ABN Lookup (abr.business.gov.au): the entity and its type",
           "the Department of Finance's list of government business enterprises and Commonwealth companies",
           "the APS agency list (apsc.gov.au) and each state's public sector commission"],
    "GB": ["Companies House: officers and persons with significant control", "gov.uk/government/organisations for public bodies"],
    "US": ["SEC EDGAR for listed companies; a state's business registry", "USAJOBS and usa.gov's agency list for federal employers"],
    "SG": ["ACRA BizFile+", "Temasek's portfolio for government-linked companies"],
    "HK": ["the Companies Registry (ICRIS) and HKEX filings"],
    "DE": ["the Unternehmensregister / Handelsregister; the Bund's Beteiligungsbericht for federal holdings"],
    "JP": ["the National Tax Agency's 法人番号 site and EDINET filings"],
}


def kind(nid: str) -> str:
    """The kind a nature is of: itself for a kind, a subtype's kind; "" for none."""
    return nid if nid in KIND_IDS else SUBTYPE[nid][2] if nid in SUBTYPE else ""


def country(nid: str) -> str:
    return SUBTYPE[nid][1] if nid in SUBTYPE else ""


def needs(nid: str) -> str:
    """What the employer asks of an applicant's status in its country: citizen, resident or work."""
    return SUBTYPE[nid][4] if nid in SUBTYPE else "work"


def nature(v: Any) -> str:
    """A nature as kept ("" for not known); a word the catalog doesn't have is an error, never a guess."""
    s = " ".join(str(v or "").split())[:60]
    if not s:
        return ""
    k = s.lower()
    if k in IDS:
        return k
    if k in WORDS or s in WORDS:
        return WORDS.get(k) or WORDS[s]
    from core.i18n import t
    raise ValueError(t("“{value}” isn't a company nature: use one of {list}, or leave it empty", value=s, list=", ".join(IDS)))


def takes(entry: str, nid: str) -> bool:
    """Whether a track's nature `entry` takes a company of nature `nid`: the same, or `entry` is its kind."""
    return bool(nid) and (entry == nid or entry == kind(nid))


# ---- the user's status: resume.yaml profile.work ----
# One record per country: {country, status, visa, from, until}. status is citizen, resident (permanent), work (a
# work visa) or study (a student visa, whose limited work rights still count for internships). from / until are
# months, "2027-07", or a year, "2027"; a trailing "~" marks one only roughly known (ISO 8601-2 / EDTF), as a 485
# visa's start is: it is applied for within six months of graduating and granted some months later.
STATUSES = ("citizen", "resident", "work", "study")
LEVEL = {"citizen": "citizen", "resident": "resident", "work": "work", "study": "work"}
_FUZZY = re.compile(r"\d{4}(-(0[1-9]|1[0-2]))?~?")


def _codes(xs: Any) -> list[str]:
    xs = re.split(r"[,，、;；\s]+", xs) if isinstance(xs, str) else xs if isinstance(xs, list) else []
    return [c for c in dict.fromkeys(str(x or "").strip().upper() for x in xs) if re.fullmatch(r"[A-Z]{2}", c)]


def _when(v: Any) -> str:
    s = str(v or "").strip().replace("～", "~").replace(".", "-")
    return s if _FUZZY.fullmatch(s) else ""


def clean_work(v: Any) -> dict:
    """{rights: [{country, status, visa, from, until}]}: what the user is in each country they look for work in.
    Codes are ISO 3166 (CN, AU); anything unreadable is left out. The first shape ({citizen, resident, visa: [codes],
    note}, 2026-11-13) is read as records; its `targets` (where they look) went with the field (2026-11-14)."""
    v = v if isinstance(v, dict) else {}
    raw = v.get("rights") if isinstance(v.get("rights"), list) else []
    if not raw:   # the first shape
        old = [(k, s) for k, s in (("citizen", "citizen"), ("resident", "resident"), ("visa", "work"))]
        raw = [{"country": cc, "status": s} for k, s in old for cc in _codes(v.get(k))]
        note = " ".join(str(v.get("note") or "").split())
        first = next((r for r in raw if r["status"] == "work"), None)
        if note and first:
            first["visa"] = note
    rights, seen = [], set()
    for r in raw:
        if not isinstance(r, dict):
            continue
        cc = (_codes([r.get("country")]) or [""])[0]
        st = str(r.get("status") or "").strip().lower()
        if not cc or st not in STATUSES or (cc, st) in seen:
            continue
        seen.add((cc, st))
        out = {"country": cc, "status": st}
        if st in ("work", "study"):
            visa = " ".join(str(r.get("visa") or "").split())[:200]
            out.update({k: x for k, x in (("visa", visa), ("from", _when(r.get("from"))), ("until", _when(r.get("until")))) if x})
        rights.append(out)
    return {"rights": rights}


def _over(until: str, today: str) -> bool:
    """A visa whose last month, known exactly, is before this month: it no longer counts. A rough one still does."""
    if not until or until.endswith("~"):
        return False
    return (until + "-12")[:7] < today[:7]


def status(work: dict, cc: str, today: str = "") -> str:
    """What the user is in a country: citizen, resident, work (a visa that lets them work) or ""."""
    import datetime as _dt
    today = today or _dt.date.today().isoformat()
    have = [LEVEL[r["status"]] for r in work.get("rights", []) if r.get("country") == cc and not _over(r.get("until", ""), today)]
    return max(have, key=NEEDS.index) if have else ""


def short_of(nid: str, cc: str, work: dict, today: str = "") -> str:
    """What a company of this nature in country `cc` asks that the user doesn't have ("" when they have it, or
    when it can't be told: nature, country or the user's status not known)."""
    cc = country(nid) or cc
    if not nid or not cc or not work.get("rights"):
        return ""
    need, have = needs(nid), status(work, cc, today)
    return "" if have and NEEDS.index(have) >= NEEDS.index(need) else need


def countries_first(work: dict, extra: str = "") -> list[str]:
    """The countries whose own kinds the user is shown: those in their profile, in its order."""
    xs = [*(r["country"] for r in work.get("rights", [])), extra]
    return [c for c in dict.fromkeys(xs) if c]


def catalog(t: Any, country_name: Any) -> dict:
    """The catalog for the window, in the interface's language."""
    return {"kinds": [{"id": k, "name": t(n)} for k, n, _ in KINDS],
            "subtypes": [{"id": i, "country": cc, "country_name": country_name(cc), "kind": k, "name": t(n), "needs": nd}
                         for i, cc, k, n, nd, _ in SUBTYPES]}


def label(nid: str) -> str:
    """A nature for Claude: its id and English name."""
    if nid in SUBTYPE:
        return f"{nid} ({SUBTYPE[nid][3]})"
    return next((f"{k} ({n})" for k, n, _ in KINDS if k == nid), nid)


def help_text() -> str:
    """The catalog and where to read ownership, for `fill --help` and `tracks --help`."""
    lines = ["Kinds (every country): " + "; ".join(f"{k} ({n}: {d})" for k, n, d in KINDS)]
    by: dict[str, list] = {}
    for s in SUBTYPES:
        by.setdefault(s[1], []).append(s)
    for cc, ss in by.items():
        lines.append(f"{cc}'s own (use these, not the kind, for a company there): "
                     + "; ".join(f"{i} ({n}, a kind of {k}; needs: {nd})" for i, _, k, n, nd, _ in ss))
    lines.append("Who owns it, read from: " + "; ".join(SOURCES[""]))
    lines += [f"  {cc}: " + "; ".join(xs) for cc, xs in SOURCES.items() if cc]
    return "\n".join(lines)
