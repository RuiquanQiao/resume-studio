"""The sites whose profile Resume Studio keeps in step with a resume subset.

A site says where its profile is, which of the resume's sections it has, the field limits it is known
to enforce, and a short guide for Claude about its editor. No selectors: Claude reads the page with
snapshots, so a redesign of the site doesn't break anything here.

The user picks their sites (autofill/service.py keeps the list): PRESETS come with a guide written for
them; any other site starts with every section and no limits, and its first read tells Resume Studio
what it really has (`learned`).
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# the fields of one item, per section (the same names in what the resume says and what the site has)
FIELDS = {
    "experience": ["title", "org", "location", "start", "end", "description"],
    "education": ["org", "degree", "start", "end", "description"],
    "projects": ["name", "role", "start", "end", "link", "description"],
    "awards": ["title", "issuer", "date", "description"],
}
BASICS = ["headline", "about"]


@dataclass
class Site:
    id: str
    name: str
    home: str                        # the user's own profile (or where to find it)
    domains: list[str]
    lang: str                        # the resume language the site usually wants
    sections: list[str]              # FIELDS sections it has, plus "skills"
    basics: list[str]                # which BASICS it has
    limits: dict[str, int] = field(default_factory=dict)   # "section.field" -> max characters
    login_paths: list[str] = field(default_factory=list)  # being sent here means "not signed in"
    bullet: str = "• "
    guide: str = ""
    lacks: dict[str, list[str]] = field(default_factory=dict)   # section -> FIELDS it has no field for
    preset: str = ""                 # the PRESETS entry it comes from ("" = a site the user added)
    notes: str = ""                  # the user's own notes for Claude about this site
    learned: bool = False            # a read told us its sections and limits (sites the user added)
    kind: str = "platform"           # "platform" (LinkedIn: one profile many companies see) | "company" (a 网申 profile)
    company: str = ""                # kind "company": the company (jobhunt) whose careers site it is
    system: str = ""                 # kind "company": the recruiting system its site runs on (北森, Moka…), when known
    learned_from: str = ""           # its sections and limits came from this site, on the same system
    # its app on the App Store, "<country>:<id>": where its logo comes from when the site shows a script only a
    # sign-in check or a 16 px icon (BOSS直聘, 2026-11-15). The id stays; the picture's address changes with each version.
    app_store: str = ""
    # what a recorded write recipe may never press here, on top of NEVER_PRESS (regular expressions, matched against
    # the control's name): the guide says it to Claude in words, this holds the replay to it
    never_press: list[str] = field(default_factory=list)
    # "Open {site}" shows it in a plain browser window (the same profile, no remote control) instead of Resume Studio's
    # controlled one: the site's security check sees a driven browser and never lets it through (BOSS直聘, 2026-11-24:
    # 职位 went to /web/passport/zp/security.html and back, over and over)
    plain_window: bool = False

    def forbids(self, name: str) -> str:
        """The rule a control's name breaks ("" for none): applying, messaging, a job-seeking status, paying."""
        rules = [*NEVER_PRESS, *self.never_press, *(NEVER_PRESS_COMPANY if self.kind == "company" else [])]
        return next((r for r in rules if re.search(r, name or "", re.I)), "")

    def fields(self, section: str) -> list[str]:
        return [f for f in FIELDS[section] if f not in self.lacks.get(section, [])]

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "home": self.home, "lang": self.lang, "sections": self.sections,
                "basics": self.basics, "limits": self.limits, "preset": self.preset, "notes": self.notes,
                "learned": self.learned, "kind": self.kind, "company": self.company, "system": self.system,
                "learned_from": self.learned_from, "app_store": self.app_store, "plain_window": self.plain_window,
                "fields": {s: self.fields(s) for s in self.sections if s in FIELDS}}

    def owns(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in self.domains)

    def signed_out(self, url: str) -> bool:
        """The site sent the browser to its sign-in page."""
        if not self.owns(url):
            return False
        path = urlparse(url).path.lower()
        return any(path.startswith(p) for p in self.login_paths)


PRESETS: dict[str, Site] = {
    "linkedin": Site(
        id="linkedin", name="LinkedIn", preset="linkedin", home="https://www.linkedin.com/in/me/", domains=["linkedin.com"], lang="en",
        sections=["experience", "education", "projects", "awards", "skills"], basics=["headline", "about"],
        limits={"basics.headline": 220, "basics.about": 2600, "experience.title": 100, "experience.description": 2000,
                "education.description": 1000, "projects.description": 2000},
        login_paths=["/login", "/authwall", "/checkpoint", "/uas/", "/signup"],
        never_press=[r"\bconnect\b", r"\bfollow\b", r"\bshare\b", r"\bpost\b", r"\bendorse", r"open to work"],
        lacks={"projects": ["role"]},   # a project there is name, dates, description (and what it is associated with)
        guide="""\
- `https://www.linkedin.com/in/me/` opens the signed-in user's own profile. Each section card links to a
  full list ("Show all", `/in/<slug>/details/experience/` and the like) where every item has its own
  edit pencil; the "+" on a card adds an item. Read the full lists, not just the cards (cards show 2-3 items).
- The intro (pencil next to the name) holds the headline. "About" has its own card.
- Dates are month and year pickers; "I am currently working in this role" removes the end date.
- Before saving a new or changed position, LinkedIn may offer to share the update with the network
  ("Notify network" / "Share with network"): switch it off unless the plan says to share.
- Skills: the Skills card's "+" (or "Add skill") takes one skill at a time; pick the suggestion that
  matches the name exactly, or the typed text if none does. Say no to the follow-up prompts (which
  position the skill was used in, endorsements) unless the plan says otherwise.
- Never use anything that messages people, connects, follows, posts, or applies to jobs.""",
    ),
    "boss": Site(
        id="boss", name="BOSS直聘", preset="boss", home="https://www.zhipin.com/", domains=["zhipin.com"], lang="zh",
        sections=["experience", "projects", "education"], basics=["about"],
        login_paths=["/web/user"], app_store="cn:887314963", plain_window=True,
        never_press=["沟通", "打招呼", "求职状态", "在职-", "离职-", "到岗"],
        guide="""\
- The editable online resume (在线简历 / 我的简历) is reached from the signed-in job seeker's menu
  (avatar or 个人中心 → 我的简历 / 在线简历). Find it from the home page and remember its address
  for the rest of the chat.
- "about" is 个人优势. 工作经历 (experience) and 实习经历 may be one list; 项目经历 is projects;
  教育经历 is education. 求职期望, 资格证书 and the attachment resume (附件简历) are not part of the plan.
- Each item has its own edit link (编辑) and a 完成 / 保存 button; adding is 添加. Some fields are
  pickers (公司 suggestions, 时间 pickers, 学历): click them and choose the option that matches.
- The site shows a character counter under long text fields; keep within it.
- Never use anything that greets or messages a recruiter (立即沟通 / 打招呼), applies, or changes 求职状态.""",
    ),
}


# never pressed by a replayed recipe, on any site: applying, messaging people, a job-seeking status, paying
NEVER_PRESS = [r"\bapply\b", r"easy apply", r"submit (an |my |your )?application", r"\bmessage\b", r"\bsend\b", r"\binvite\b",
               r"\bpay\b", r"\bbuy\b", r"\bsubscribe\b", r"premium", "投递", "申请职位", "立即申请", "提交申请", "一键投递", "确认投递",
               "撤回", "立即沟通", "打招呼", "发送", "求职状态", "支付", "购买", "开通", "充值", "会员"]
# on a company's careers site a bare "submit" often sends the application itself
NEVER_PRESS_COMPANY = [r"\bsubmit\b", "提交", "志愿"]

DEFAULT = ["linkedin", "boss"]   # a data file that never chose its sites
ALL_SECTIONS = ["experience", "education", "projects", "awards", "skills"]

GENERIC_GUIDE = """\
- Resume Studio has no notes about this site's editor yet: find your way like a careful person would.
  Start at the address above; if it isn't the profile itself, find the signed-in user's own profile or
  online resume from the menus (avatar, "profile", "my resume", 个人中心 / 我的简历 / 在线简历).
- Each item usually has its own edit button and a save button; adding has its own button per section.
  Pickers (dates, degrees, company suggestions): click them and choose the option that matches.
- Never use anything that messages people, connects, follows, posts, applies to jobs, or pays."""


COMPANY_GUIDE = """\
- This is {company}'s own careers site (网申): the user's profile here is the online resume {company} reads with
  every application made on this site. Start at the address above; the online resume is usually under 个人中心,
  我的简历, 简历管理 or 在线简历, or it is the sign-up form itself. Find it and remember its address for the chat.
- Map the site's sections: 实习经历 / 工作经历 = experience, 项目经历 = projects, 教育经历 = education,
  获奖经历 / 荣誉奖励 = awards, 技能 / 专业技能 = skills, 自我评价 / 个人优势 = about.
- Careers sites also ask for what the copy doesn't keep: 身份证号, 家庭住址, 政治面貌, 期望薪资, 照片,
  紧急联系人 and the like. Leave those fields as they are and never read them into the copy: they are the user's.
- Never press anything that submits, sends or withdraws an application (投递, 提交申请, 确认投递, 申请职位,
  撤回) or changes the order of preferences (志愿): the user does that on the site."""

SYSTEM_LINE = ("\n- {company}'s careers site runs on {system}. Sites on one recruiting system share its editor: what you "
               "learn here holds for the other companies on {system} too.")


def own_system(system: str) -> bool:
    """A system that is the company's own (the AI writes "own site"): no other company shares it."""
    return not system or bool(re.match(r"own site\b", system, re.I))


def company_site(rec: dict, company: dict) -> Site | None:
    """A company's 网申 profile: its name, address and recruiting system come from the company (jobhunt), so
    a changed sign-up address or name is the copy's at once."""
    home = str(company.get("site") or "")
    if not valid_home(home):
        return None
    urls = [home, *[str(x.get("url") or "") for x in company.get("sites") or []]]
    domains = list(dict.fromkeys(d for d in (domain_of(u) for u in urls) if d))
    system = next((str(x.get("system") or "") for x in company.get("sites") or [] if x.get("url") == home and x.get("system")), "") \
        or next((str(x.get("system") or "") for x in company.get("sites") or [] if x.get("system")), "")
    name = str(company.get("name") or "") or domain_of(home)
    guide = COMPANY_GUIDE.format(company=name) + ("" if own_system(system) else SYSTEM_LINE.format(company=name, system=system))
    sections = [s for s in rec.get("sections") or ALL_SECTIONS if s in ALL_SECTIONS]
    basics = [b for b in rec.get("basics") or ["about"] if b in BASICS]
    limits = {str(k): int(v) for k, v in (rec.get("limits") or {}).items() if isinstance(v, int) and v > 0}
    lacks = {s: [f for f in fs if f in FIELDS.get(s, [])] for s, fs in (rec.get("lacks") or {}).items() if s in FIELDS}
    return Site(id=str(rec["id"]), name=name, home=home, domains=domains, lang=str(rec.get("lang") or ""),
                sections=sections, basics=basics, limits=limits, lacks=lacks, guide=guide, notes=str(rec.get("notes") or ""),
                learned=bool(rec.get("learned")), kind="company", company=str(company.get("id") or ""),
                system="" if own_system(system) else system, learned_from=str(rec.get("learned_from") or ""))


# an address that is a sign-in page, on any site: /login, /signin, /passport/…, /sso, /auth/… (国聘 sent a signed-out
# visitor to /login?redirect=…; a site the user added has no sign-in paths of its own)
_LOGIN = re.compile(r"(^|[/._-])(log-?in|sign-?in|sign_in|signon|log-?on|passport|sso|auth(orize)?|authwall|uas|cas)([/._?]|$)", re.I)   # "login-tips" is a page about it
_BACK = ("redirect", "redirect_uri", "redirecturl", "redirect_url", "callbackurl", "callback", "returnurl", "return_url",
         "return_to", "returnto", "next", "service", "continue", "from", "target")


def login_url(url: str) -> bool:
    p = urlparse(url or "")
    return bool(_LOGIN.search(p.path or "")) or bool(_LOGIN.match((p.hostname or "").split(".")[0] + "/"))


def after_login(url: str) -> str:
    """Where a sign-in page sends the user back to (its redirect parameter), "" when it doesn't say."""
    from urllib.parse import parse_qsl
    if not login_url(url):
        return ""
    for k, v in parse_qsl(urlparse(url).query):
        if k.lower() in _BACK and v.startswith(("https://", "http://")) and not login_url(v):
            return v
    return ""


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def valid_home(url: str) -> bool:
    u = urlparse(url.strip())
    return u.scheme in ("http", "https") and bool(u.hostname)


def slug(name: str, home: str) -> str:
    base = domain_of(home).split(".")[0] if home else ""
    s = re.sub(r"[^a-z0-9]+", "-", (base or name).lower()).strip("-")
    return s or "site"


def build(rec: dict, companies: dict[str, dict] | None = None) -> Site | None:
    """The Site of one entry in the user's list: a preset with the user's changes, a site they added, or a
    company's 网申 profile (`companies`: id -> the company; a company that is gone takes its site off the list)."""
    sid = str(rec.get("id") or "")
    if not sid:
        return None
    if rec.get("kind") == "company":
        c = (companies or {}).get(str(rec.get("company") or ""))
        return company_site(rec, c) if c else None
    base = PRESETS.get(str(rec.get("preset") or ""))
    over = {k: str(rec[k]) for k in ("name", "home", "lang", "notes") if rec.get(k)}
    if base is not None:
        site = dataclasses.replace(base, id=sid, **over)
        if site.home != base.home and domain_of(site.home) not in base.domains:
            site.domains = [*base.domains, domain_of(site.home)]
        return site
    home = over.get("home", "")
    if not valid_home(home):
        return None
    sections = [s for s in rec.get("sections") or ALL_SECTIONS if s in ALL_SECTIONS]
    basics = [b for b in rec.get("basics") or BASICS if b in BASICS]
    limits = {str(k): int(v) for k, v in (rec.get("limits") or {}).items() if isinstance(v, int) and v > 0}
    lacks = {s: [f for f in fs if f in FIELDS.get(s, [])] for s, fs in (rec.get("lacks") or {}).items() if s in FIELDS}
    return Site(id=sid, name=over.get("name") or domain_of(home), home=home, domains=[domain_of(home)],
                lang=over.get("lang", ""), sections=sections, basics=basics, limits=limits, lacks=lacks,
                guide=GENERIC_GUIDE, notes=over.get("notes", ""), learned=bool(rec.get("learned")))
