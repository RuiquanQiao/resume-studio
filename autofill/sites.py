"""The sites whose profile Resume Studio keeps in step with a resume version.

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

    def fields(self, section: str) -> list[str]:
        return [f for f in FIELDS[section] if f not in self.lacks.get(section, [])]

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "home": self.home, "lang": self.lang, "sections": self.sections,
                "basics": self.basics, "limits": self.limits, "preset": self.preset, "notes": self.notes,
                "learned": self.learned, "fields": {s: self.fields(s) for s in self.sections if s in FIELDS}}

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
        login_paths=["/web/user"],
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


DEFAULT = ["linkedin", "boss"]   # a data file that never chose its sites
ALL_SECTIONS = ["experience", "education", "projects", "awards", "skills"]

GENERIC_GUIDE = """\
- Resume Studio has no notes about this site's editor yet: find your way like a careful person would.
  Start at the address above; if it isn't the profile itself, find the signed-in user's own profile or
  online resume from the menus (avatar, "profile", "my resume", 个人中心 / 我的简历 / 在线简历).
- Each item usually has its own edit button and a save button; adding has its own button per section.
  Pickers (dates, degrees, company suggestions): click them and choose the option that matches.
- Never use anything that messages people, connects, follows, posts, applies to jobs, or pays."""


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


def build(rec: dict) -> Site | None:
    """The Site of one entry in the user's list: a preset with the user's changes, or a site they added."""
    sid = str(rec.get("id") or "")
    if not sid:
        return None
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
