"""Languages a resume can be written in.

A resume's text is a plain string (every language) or a map keyed by these codes. The list is the
languages the LaTeX templates set well: Latin, Cyrillic and Greek scripts, plus Chinese, Japanese
and Korean through xeCJK. Right-to-left scripts (Arabic, Hebrew, Persian…) are left out on purpose:
they need a different layout, not just a font.

script: latin | cyrillic | greek | zh | zh-tw | ja | ko  (the CJK ones pick their own fonts)
present: the word for an ongoing end date; date: 'mon' = "Jan 2025", 'num' = "08/2025", 'dot' = "2025.08"
"""
from __future__ import annotations

LANGS: dict[str, dict] = {
    "en": {"name": "English", "script": "latin", "present": "Present", "date": "mon"},
    "zh": {"name": "简体中文", "script": "zh", "present": "至今", "date": "dot"},
    "zh-TW": {"name": "繁體中文", "script": "zh-tw", "present": "至今", "date": "dot"},
    "ja": {"name": "日本語", "script": "ja", "present": "現在", "date": "dot"},
    "ko": {"name": "한국어", "script": "ko", "present": "현재", "date": "dot"},
    "es": {"name": "Español", "script": "latin", "present": "actualidad", "date": "num"},
    "fr": {"name": "Français", "script": "latin", "present": "aujourd'hui", "date": "num"},
    "de": {"name": "Deutsch", "script": "latin", "present": "heute", "date": "num"},
    "it": {"name": "Italiano", "script": "latin", "present": "oggi", "date": "num"},
    "pt": {"name": "Português", "script": "latin", "present": "atual", "date": "num"},
    "nl": {"name": "Nederlands", "script": "latin", "present": "heden", "date": "num"},
    "sv": {"name": "Svenska", "script": "latin", "present": "nu", "date": "num"},
    "nb": {"name": "Norsk bokmål", "script": "latin", "present": "nå", "date": "num"},
    "da": {"name": "Dansk", "script": "latin", "present": "nu", "date": "num"},
    "fi": {"name": "Suomi", "script": "latin", "present": "nyt", "date": "num"},
    "pl": {"name": "Polski", "script": "latin", "present": "obecnie", "date": "num"},
    "cs": {"name": "Čeština", "script": "latin", "present": "současnost", "date": "num"},
    "sk": {"name": "Slovenčina", "script": "latin", "present": "súčasnosť", "date": "num"},
    "hu": {"name": "Magyar", "script": "latin", "present": "jelenleg", "date": "num"},
    "ro": {"name": "Română", "script": "latin", "present": "prezent", "date": "num"},
    "hr": {"name": "Hrvatski", "script": "latin", "present": "danas", "date": "num"},
    "tr": {"name": "Türkçe", "script": "latin", "present": "günümüz", "date": "num"},
    "id": {"name": "Bahasa Indonesia", "script": "latin", "present": "sekarang", "date": "num"},
    "ms": {"name": "Bahasa Melayu", "script": "latin", "present": "kini", "date": "num"},
    "vi": {"name": "Tiếng Việt", "script": "latin", "present": "nay", "date": "num"},
    "el": {"name": "Ελληνικά", "script": "greek", "present": "σήμερα", "date": "num"},
    "ru": {"name": "Русский", "script": "cyrillic", "present": "наст. время", "date": "num"},
    "uk": {"name": "Українська", "script": "cyrillic", "present": "дотепер", "date": "num"},
    "bg": {"name": "Български", "script": "cyrillic", "present": "досега", "date": "num"},
}

CJK_SCRIPTS = {"zh", "zh-tw", "ja", "ko"}

# default section titles in each language (ids from model.DEFAULT_SECTIONS)
SECTION_TITLES: dict[str, dict[str, str]] = {
    "en": {"education": "Education", "experience": "Experience", "projects": "Projects", "skills": "Skills", "awards": "Awards"},
    "zh": {"education": "教育背景", "experience": "工作经历", "projects": "项目经历", "skills": "专业技能", "awards": "荣誉奖项"},
    "zh-TW": {"education": "學歷", "experience": "工作經歷", "projects": "專案經歷", "skills": "專業技能", "awards": "榮譽獎項"},
    "ja": {"education": "学歴", "experience": "職歴", "projects": "プロジェクト", "skills": "スキル", "awards": "受賞歴"},
    "ko": {"education": "학력", "experience": "경력", "projects": "프로젝트", "skills": "기술", "awards": "수상 경력"},
    "es": {"education": "Formación", "experience": "Experiencia", "projects": "Proyectos", "skills": "Habilidades", "awards": "Premios"},
    "fr": {"education": "Formation", "experience": "Expérience", "projects": "Projets", "skills": "Compétences", "awards": "Distinctions"},
    "de": {"education": "Ausbildung", "experience": "Berufserfahrung", "projects": "Projekte", "skills": "Kenntnisse", "awards": "Auszeichnungen"},
    "it": {"education": "Istruzione", "experience": "Esperienza", "projects": "Progetti", "skills": "Competenze", "awards": "Riconoscimenti"},
    "pt": {"education": "Formação", "experience": "Experiência", "projects": "Projetos", "skills": "Competências", "awards": "Prémios"},
    "nl": {"education": "Opleiding", "experience": "Werkervaring", "projects": "Projecten", "skills": "Vaardigheden", "awards": "Prijzen"},
    "sv": {"education": "Utbildning", "experience": "Erfarenhet", "projects": "Projekt", "skills": "Kompetenser", "awards": "Utmärkelser"},
    "nb": {"education": "Utdanning", "experience": "Erfaring", "projects": "Prosjekter", "skills": "Ferdigheter", "awards": "Utmerkelser"},
    "da": {"education": "Uddannelse", "experience": "Erfaring", "projects": "Projekter", "skills": "Kompetencer", "awards": "Priser"},
    "fi": {"education": "Koulutus", "experience": "Työkokemus", "projects": "Projektit", "skills": "Osaaminen", "awards": "Palkinnot"},
    "pl": {"education": "Wykształcenie", "experience": "Doświadczenie", "projects": "Projekty", "skills": "Umiejętności", "awards": "Nagrody"},
    "cs": {"education": "Vzdělání", "experience": "Praxe", "projects": "Projekty", "skills": "Dovednosti", "awards": "Ocenění"},
    "sk": {"education": "Vzdelanie", "experience": "Prax", "projects": "Projekty", "skills": "Zručnosti", "awards": "Ocenenia"},
    "hu": {"education": "Tanulmányok", "experience": "Szakmai tapasztalat", "projects": "Projektek", "skills": "Készségek", "awards": "Díjak"},
    "ro": {"education": "Educație", "experience": "Experiență", "projects": "Proiecte", "skills": "Competențe", "awards": "Premii"},
    "hr": {"education": "Obrazovanje", "experience": "Radno iskustvo", "projects": "Projekti", "skills": "Vještine", "awards": "Nagrade"},
    "tr": {"education": "Eğitim", "experience": "Deneyim", "projects": "Projeler", "skills": "Yetenekler", "awards": "Ödüller"},
    "id": {"education": "Pendidikan", "experience": "Pengalaman", "projects": "Proyek", "skills": "Keahlian", "awards": "Penghargaan"},
    "ms": {"education": "Pendidikan", "experience": "Pengalaman", "projects": "Projek", "skills": "Kemahiran", "awards": "Anugerah"},
    "vi": {"education": "Học vấn", "experience": "Kinh nghiệm", "projects": "Dự án", "skills": "Kỹ năng", "awards": "Giải thưởng"},
    "el": {"education": "Εκπαίδευση", "experience": "Εμπειρία", "projects": "Έργα", "skills": "Δεξιότητες", "awards": "Διακρίσεις"},
    "ru": {"education": "Образование", "experience": "Опыт работы", "projects": "Проекты", "skills": "Навыки", "awards": "Награды"},
    "uk": {"education": "Освіта", "experience": "Досвід роботи", "projects": "Проєкти", "skills": "Навички", "awards": "Нагороди"},
    "bg": {"education": "Образование", "experience": "Професионален опит", "projects": "Проекти", "skills": "Умения", "awards": "Награди"},
}

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def info(code: str) -> dict:
    """A known language, or a Latin-script stand-in for a code typed into the file by hand."""
    return LANGS.get(code) or {"name": code, "script": "latin", "present": LANGS["en"]["present"], "date": "num"}


def script(code: str) -> str:
    return info(code)["script"]


def is_cjk(code: str) -> bool:
    return script(code) in CJK_SCRIPTS


def match(tag: str | None) -> str:
    """The resume language for a computer locale: 'zh-Hant-TW' -> 'zh-TW', 'de-AT' -> 'de'."""
    tag = str(tag or "").replace("_", "-")
    parts = tag.split("-")
    base = parts[0].lower()
    if base == "zh":
        rest = {p.upper() for p in parts[1:]} | {p.title() for p in parts[1:]}
        return "zh-TW" if rest & {"TW", "HK", "MO", "Hant"} else "zh"
    if base in ("no", "nn"):
        return "nb"
    return base if base in LANGS else "en"


def format_month(year: int, month: int, code: str) -> str:
    style = info(code)["date"]
    if style == "mon":
        return f"{_MONTHS[month - 1]} {year}"
    if style == "dot":
        return f"{year}.{month:02d}"
    return f"{month:02d}/{year}"


def present(code: str) -> str:
    return info(code)["present"]


def catalog() -> list[dict]:
    """What the page needs to list and check languages."""
    return [{"code": c, "name": v["name"], "script": v["script"], "cjk": v["script"] in CJK_SCRIPTS} for c, v in LANGS.items()]
