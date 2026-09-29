"""What the UI adds on top of a normal Claude Code session.

Kept short on purpose: Claude Code's own system prompt, the user's CLAUDE.md
and the installed skills do the heavy lifting. This only says where Claude is
and what the user is looking at. No language is forced.
"""
from __future__ import annotations

from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_ROOT / "SKILL.md"


def ui_context(data_path: str, version: dict | None, entry: dict | None) -> str:
    lines = [
        "# Resume Studio",
        "",
        "The user is talking to you from the Resume Studio window (a resume editor built on Claude Code),",
        "not from a terminal. They can see a form editor and a live PDF preview next to this chat.",
        "",
        f"- Resume data file: `{data_path}`. The window watches it: after you edit it, the form and preview",
        "  refresh by themselves, so there is no need to compile or tell the user to reload.",
        f"- Data format and the fact-boundary editing rules: `{SKILL_MD}` (the resume-studio skill). Read it",
        "  before your first edit of the data file in this conversation.",
        f"- To check page count yourself: `python \"{SKILL_ROOT / 'scripts' / 'render.py'}\" <VERSION-ID> --data \"{data_path}\"`.",
    ]
    if version:
        lines.append(f"- The user is currently looking at resume version `{version.get('id')}`"
                     f" ({version.get('label') or ''}, language `{version.get('lang')}`,"
                     f" template `{version.get('template')}`).")
    if entry:
        lines.append(f"- The entry open in the editor is `{entry.get('id')}` (section `{entry.get('section')}`).")
    return "\n".join(lines) + "\n"


def polish_message(entry_id: str, instruction: str) -> str:
    """What the 'polish with Claude' button says on the user's behalf."""
    msg = f"请根据事实层（notes 和 evidence）润色经历 {entry_id} 的展示层。只改这一条，不要改 notes 和 evidence。"
    if instruction.strip():
        msg += f"\n补充要求：{instruction.strip()}"
    return msg
