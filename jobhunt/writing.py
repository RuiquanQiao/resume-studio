"""Which language a company's research is written in (docs/JOB_HUNT.md, 2026-10-27).

Not the interface's language: that changes whenever the user switches it, and the research would be in whichever
one was on when Claude wrote it. It follows where the user goes for the company, among the languages their
resume is written in (the resume's own setting, `settings.languages`):

  1. where they apply: the countries of their roles there (a German company's office in Sydney is English)
  2. else where it is from: the country of its headquarters (RSM Australia: English; 拼多多: Chinese)
  3. when neither country speaks one of the resume's languages: English if the resume has it, else its first

A country's languages are its official ones, the most spoken first (CLDR, jobhunt/data/languages.json):
Singapore is English before Chinese. Simplified and traditional Chinese stand in for each other (a resume in
简体中文 reads a Taiwanese company in 简体中文).
"""
from __future__ import annotations

import re
from typing import Any

from . import geo

_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_HAN = re.compile(r"[㐀-鿿]")
_WORD = re.compile(r"[A-Za-zÀ-ɏ]+")
# the commonest words of the Latin-script languages, to tell which one a text is in
_COMMON = {
    "en": {"the", "and", "of", "in", "is", "to", "with", "for", "its", "a", "on", "as", "by", "from", "are"},
    "de": {"der", "die", "und", "das", "ist", "mit", "von", "den", "im", "für", "ein", "eine", "auf", "zu", "des"},
    "fr": {"le", "la", "les", "et", "des", "est", "du", "en", "une", "un", "pour", "dans", "sur", "au", "avec"},
    "es": {"el", "la", "los", "las", "y", "de", "es", "con", "en", "una", "un", "por", "para", "del", "su"},
    "it": {"il", "la", "di", "e", "che", "è", "con", "per", "una", "un", "del", "della", "nel", "sono", "le"},
    "pt": {"o", "a", "os", "as", "e", "de", "do", "da", "com", "em", "uma", "um", "para", "no", "na"},
    "nl": {"de", "het", "en", "van", "een", "is", "met", "op", "voor", "in", "zijn", "te", "die", "dat", "bij"},
}


def _same(a: str, b: str) -> bool:
    return a == b or (a.startswith("zh") and b.startswith("zh"))


def _match(lang: str, mine: list[str]) -> str:
    """The resume language that reads as `lang`: itself, or the other Chinese."""
    return next((m for m in mine if m == lang), "") or next((m for m in mine if _same(m, lang)), "")


def research_language(company: dict, applications: list[dict], resume_langs: list[str]) -> dict:
    """{lang, why, place}: what the company's research is written in, and why (applying: where a role is;
    hq: its headquarters; fallback: neither speaks a resume language; unknown: where it is isn't known yet, so
    what is written stays as it is; place: the place id it went by)."""
    mine = [str(x) for x in resume_langs or [] if x] or ["en"]
    tried: list[str] = []
    for a in sorted(applications, key=lambda a: a.get("order") or 0):
        for p in a.get("places") or []:
            if isinstance(p, dict) and p.get("id") and p["id"] not in tried:
                tried.append(p["id"])
    seen = ""
    for why, pids in (("applying", tried), ("hq", [(company.get("hq") or {}).get("id")])):
        for pid in pids:
            langs = geo.languages(geo.country(pid))
            seen = seen or (pid if langs else "")
            for lang in langs:
                hit = _match(lang, mine)
                if hit:
                    return {"lang": hit, "why": why, "place": pid}
    return {"lang": "en" if "en" in mine else mine[0], "why": "fallback" if seen else "unknown", "place": seen}


def research_text(company: dict) -> str:
    """What Claude wrote about a company in words: industry, strength, size and the About."""
    ab = company.get("about") or {}
    parts = [company.get("industry"), company.get("strength"), company.get("size"), ab.get("summary")]
    for p in ab.get("points") or []:
        if isinstance(p, dict):
            parts += [p.get("topic"), p.get("text")]
    return "\n".join(str(x) for x in parts if x)


def written_in(text: str, expected: str = "en") -> str:
    """The language a text is in, near enough to tell it from another: Japanese (kana), Korean (hangul),
    Chinese (Chinese characters that outnumber the Latin words, which names like "RSM Australia" bring), or a
    Latin-script language by its commonest words. "" for no text; `expected` when the words don't say."""
    if not text.strip():
        return ""
    words = _WORD.findall(text)
    if len(_HANGUL.findall(text)) >= max(3, len(words)):
        return "ko"
    if len(_KANA.findall(text)) >= 3:
        return "ja"
    if len(_HAN.findall(text)) >= max(3, len(words)):
        return expected if expected.startswith("zh") else "zh"
    if not words:
        return expected
    low = [w.casefold() for w in words]
    score = {lang: sum(w in common for w in low) for lang, common in _COMMON.items()}
    best = max(score, key=lambda k: score[k])
    if score[best] < 2:   # a few words ("Accounting / Advisory"): the expected one, if it is written in letters
        return "en" if expected.startswith(("zh", "ja", "ko")) else expected
    return expected if score.get(expected, -1) >= score[best] * 0.8 else best


def needs_rewrite(company: dict, research: dict) -> str:
    """The language the company's research is in when it isn't the one `research_language` names (it was written
    for another place, or before 2026-10-27, in the interface's language); "" when it is right, there is none, or
    where the company is isn't known yet."""
    lang = research["lang"]
    if research.get("why") == "unknown":
        return ""
    got = written_in(research_text(company), lang)
    return got if got and not _same(got, lang) else ""
