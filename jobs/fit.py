"""Which of the user's resume versions fits a posting best: a quick, explainable ranking.

A resume is made for a kind of role (AI engineer, product engineer…), not for one company, so a posting
is answered with the version that already covers most of what it asks for. This counts, per version,
the posting's requirements and keywords whose words appear in that version's resume (headline, the
chosen entries' titles, tech and bullets, in every resume language). It is a word match, shown as such;
Claude can be asked for a judgement when it is close.
"""
from __future__ import annotations

import re
from typing import Any

from core.store import to_plain
from resume import model

WEIGHT = {"critical": 3.0, "high": 2.0, "meaningful": 1.5, "preferred": 1.0, "low_signal": 0.5}
_WORD = re.compile(r"[a-z0-9][a-z0-9+#]*|[一-鿿]{2,}")
_STOP = set("""a an and are as at be by for from has have in into is it its of on or our the to with you your
will we who what this that their they using use used work working experience years year strong ability
skills skill knowledge good great plus etc e g ie including across within building build team teams""".split())


def words(text: str) -> set[str]:
    return {w for w in _WORD.findall(str(text or "").lower()) if w not in _STOP and len(w) > 1}


def version_text(doc: Any, version: dict) -> str:
    """Everything a version's resume says, in every resume language."""
    langs = [str(x) for x in ((doc.get("settings") or {}).get("languages") or ["en"])]
    parts: list[str] = [str(version.get("label") or "")]
    for lang in langs:
        view = model.build_view(doc, {**to_plain(version), "lang": lang})
        parts.append(view["profile"].get("headline") or "")
        for sec in view["sections"]:
            for e in sec["entries"]:
                parts += [e.get("title") or "", e.get("subtitle") or "", " ".join(e.get("tech") or []), *(e.get("bullets") or [])]
    return "\n".join(parts)


def keywords(detail: dict) -> list[str]:
    """career-ops' "Extracted Keywords" section (comma separated), when the report has one."""
    for s in detail.get("sections") or []:
        if "keyword" in str(s.get("title") or "").lower():
            return [k.strip(" .*`") for k in re.split(r"[,\n;·]", str(s.get("markdown") or "")) if k.strip(" .*`-")][:40]
    return []


def _covered(item: str, have: set[str]) -> bool:
    need = words(item)
    if not need:
        return False
    return len(need & have) / len(need) >= (0.5 if len(need) > 2 else 1.0)


def rank(doc: Any, detail: dict) -> list[dict]:
    """[{id, label, score (0–100), covered[], missing[], keywords: [hit, total]}], best first."""
    reqs = (detail.get("summary") or {}).get("requirements") or []
    kws = keywords(detail)
    out = []
    for v in doc.get("versions") or []:
        if v.get("id") == model.ALL_ID:
            continue
        have = words(version_text(doc, v))
        covered, missing, got, total = [], [], 0.0, 0.0
        for r in reqs:
            w = WEIGHT.get(str(r.get("importance") or "").lower(), 1.0)
            total += w
            if _covered(r.get("requirement", ""), have):
                got += w
                covered.append(r["requirement"])
            else:
                missing.append(r["requirement"])
        kw_hit = sum(1 for k in kws if _covered(k, have))
        parts = []
        if total:
            parts.append((got / total, 0.7 if kws else 1.0))
        if kws:
            parts.append((kw_hit / len(kws), 0.3 if total else 1.0))
        score = round(100 * sum(a * b for a, b in parts)) if parts else None
        out.append({"id": str(v.get("id")), "label": str(v.get("label") or v.get("id")),
                    "headline": model.tr(v.get("headline"), str(v.get("lang") or "")) if v.get("headline") else "",
                    "score": score, "covered": covered, "missing": missing, "keywords": [kw_hit, len(kws)]})
    out.sort(key=lambda x: -(x["score"] if x["score"] is not None else -1))
    return out
