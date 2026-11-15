"""The type scale: four roles (style.css :root), set by name everywhere.

The user's rule (2026-11-15): font sizes are a system of a few named tiers, each with its own job, never a number
picked by eye. This reads the web code and fails on any font size written as a number outside the token
definitions, and on the places where a role was once given the wrong tier. tests/e2e/layout_check.js checks the
same thing on screen (sizes that come from a browser default or an em).
"""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
TOKENS = {"--fs-title", "--fs-head", "--fs-body", "--fs-meta"}

# a size written as a number: font-size: 13px, font: 600 12px/1.4 …, font-size: .9em, fontSize = "13px", font-size="11"
NUMBER = re.compile(
    r"font-size\s*:\s*[0-9.]+\s*(px|em|rem|pt|%)"
    r"|font\s*:[^;{}]*?\b[0-9.]+(px|em|rem|pt)\b"
    r"|fontSize\s*[:=]\s*['\"`]?[0-9.]"
    r"|font-size\s*=\s*['\"][0-9.]"
    r"|font-size\s*:\s*(smaller|larger|x-small|small|medium|large)")


def _sources():
    for p in sorted(WEB.glob("*.css")) + sorted(WEB.glob("*.js")) + sorted(WEB.glob("*.html")):
        yield p, p.read_text(encoding="utf-8")


def _root_block(css: str) -> str:
    return css[css.index(":root {"):css.index("}", css.index(":root {"))]


def _rule(css: str, selector: str) -> str:
    m = re.search(r"(^|\n)" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"{selector} is gone from style.css"
    return m.group(2)


def test_no_font_size_is_written_as_a_number():
    found = []
    for p, text in _sources():
        if p.name == "style.css":
            text = text.replace(_root_block(text), "")       # where the tiers themselves are defined
        for n, line in enumerate(text.splitlines(), 1):
            if NUMBER.search(line):
                found.append(f"{p.name}:{n}: {line.strip()[:120]}")
    assert not found, "font sizes written as numbers (use var(--fs-title/head/body/meta)):\n" + "\n".join(found)


def test_the_scale_is_four_named_roles():
    css = (WEB / "style.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"(--fs-[a-z]+)\s*:", _root_block(css)))
    assert defined == TOKENS
    used = {m for _, text in _sources() for m in re.findall(r"var\((--fs-[a-z]+)\)", text)}
    assert used <= TOKENS, f"unknown tiers: {used - TOKENS}"


def test_roles_get_their_tier():
    """Places that once had the smallest tier for text that is read, or for a section's heading (user report
    2026-11-15: 工作经历 and 简历中的建议： on a platform's profile page)."""
    css = (WEB / "style.css").read_text(encoding="utf-8")
    heads = [".fg-title", "button.fg-title"]
    bodies = [".fill-compare",".report-md", ".card-p", ".fit ul", ".fv", ".ffc-v", ".bk-notes", ".connect p"]
    for sel in heads:
        assert "var(--fs-head)" in _rule(css, sel), f"{sel} is a section heading: --fs-head"
    for sel in bodies:
        assert "var(--fs-body)" in _rule(css, sel), f"{sel} is read as content: --fs-body"
    assert "var(--fs-body)" in _rule(css, ".plan-text")         # what the user writes
