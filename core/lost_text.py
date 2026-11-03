"""Text whose non-ASCII letters were lost on the way in.

Windows PowerShell 5.1 pipes text into a program as ASCII ($OutputEncoding), and sends a string body with
Invoke-RestMethod as Latin-1, so every Chinese letter arrives as "?" (an agent there sent 苏州智加科技有限公司 as
??????????, and it was saved). Such text is pure ASCII with runs of "?" where the words were: it is refused
rather than kept.
"""
from __future__ import annotations

from typing import Any


def lost_letters(text: str) -> bool:
    return text.isascii() and ("???" in text or (text.count("?") >= 2 and not text.strip("? \t\r\n")))


def first_lost(value: Any) -> str | None:
    """The first string in `value` (a string, or lists and dicts of them) that lost its letters."""
    if isinstance(value, str):
        return value if lost_letters(value) else None
    items = value.values() if isinstance(value, dict) else value if isinstance(value, (list, tuple)) else ()
    for x in items:
        hit = first_lost(x)
        if hit is not None:
            return hit
    return None


def sample(text: str) -> str:
    i = text.find("??")
    return (text[max(0, i - 30):i + 30] if i >= 0 else text[:60]).replace("\n", " ")


HOW = ("The shell that ran the command dropped them; Windows PowerShell hands piped text to programs as ASCII. "
       "Write the text to a UTF-8 file (in the system's temp folder, not the user's) and pass it with --file <path>, "
       "or in JSON write each non-ASCII letter as \\uXXXX.")
