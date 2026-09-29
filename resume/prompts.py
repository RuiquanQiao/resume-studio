"""Prompts the UI hands to Claude Code. Claude does the work by editing resume.yaml."""
from __future__ import annotations

from pathlib import Path

SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"


def _header(data_path: str) -> str:
    return (
        "你正在通过 Resume Studio 的界面协助用户编辑简历。\n"
        f"- 数据文件：{data_path}\n"
        f"- 数据格式和编辑规则：先阅读 {SKILL_MD}（resume-studio skill），严格遵守其中的「事实边界」。\n"
        "- 直接用 Edit 工具修改数据文件；只改任务涉及的部分，保留其他内容、字段顺序和注释。\n"
        "- 不需要编译 PDF，界面会自动检测文件变化并重新渲染。\n"
        "- 如果本机装有 offer-toolkit-skill 或 resume-tailoring 等简历写作 skill，可以借鉴它们的写作规则。\n"
    )


def polish_entry(data_path: str, entry_id: str, langs: list[str], instruction: str) -> str:
    lang_text = "、".join(langs) if langs else "现有语言"
    return (
        _header(data_path)
        + f"\n任务：润色经历条目 {entry_id} 的展示层（title、subtitle、tech、bullets 等），语言：{lang_text}。\n"
        "依据：这一条的 notes（事实层）以及 evidence 里列出的本地路径（需要时去读代码、README、提交记录）。\n"
        "要求：\n"
        "1. 不得写出 notes/evidence 无法支撑的事实、数字、职责或规模；拿不准就不写，或保守表述。\n"
        f"2. 只修改 {entry_id} 这一个条目；不要改它的 id、section、notes、evidence。\n"
        "3. 要点用动词开头，突出本人实际做的决策和结果，每条尽量一行。\n"
        + (f"\n用户的补充要求：{instruction}\n" if instruction.strip() else "")
        + "\n完成后用一两句话告诉用户你改了什么、为什么。"
    )


def free_task(data_path: str, version_id: str | None, entry_id: str | None, instruction: str) -> str:
    ctx = []
    if version_id:
        ctx.append(f"用户当前正在看的简历版本：{version_id}（versions 里的这一项）。")
    if entry_id:
        ctx.append(f"用户当前选中的经历条目：{entry_id}。")
    return (
        _header(data_path)
        + ("\n" + "\n".join(ctx) + "\n" if ctx else "")
        + f"\n用户的要求：{instruction}\n"
        "\n除非用户明确要求，不要修改任何条目的 notes 和 evidence。完成后用一两句话说明你改了什么。"
    )
