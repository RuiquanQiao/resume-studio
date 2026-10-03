"""What the UI adds on top of a normal Claude Code session.

Kept short on purpose: Claude Code's own system prompt, the user's CLAUDE.md
and the installed skills do the heavy lifting. This only says where Claude is
and what the user is looking at, plus the messages the editor's buttons send
on the user's behalf. No reply language is forced.
"""
from __future__ import annotations

from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_ROOT / "SKILL.md"
RENDER = SKILL_ROOT / "scripts" / "render.py"
LANG_ZH = {"en": "英文", "zh": "中文"}


def render_cmd(version_id: str, data_path: str, lang: str | None = None) -> str:
    return f'python "{RENDER}" {version_id}' + (f" --lang {lang}" if lang else "") + f' --data "{data_path}"'


def ui_context(data_path: str, version: dict | None, entry: dict | None, project_roots: list[str] | None = None) -> str:
    lines = [
        "# Resume Studio",
        "",
        "The user is talking to you from the Resume Studio window (a resume editor built on Claude Code),",
        "not from a terminal. They can see a form editor and a live PDF preview next to this chat.",
        "",
        f"- Resume data file: `{data_path}`. The window watches it: after you edit it, the form and preview",
        "  refresh by themselves, so there is no need to compile or tell the user to reload.",
        f"- Data format, the bilingual rules and the fact-boundary editing rules: `{SKILL_MD}` (the",
        "  resume-studio skill). Read it before your first edit of the data file in this conversation.",
        f"- To check pages and which bullets wrap onto a second line: `{render_cmd('<VERSION-ID>', data_path)}`",
        "  (add `--lang zh` or `--lang en` to check the other language with the same template and layout).",
    ]
    if project_roots:
        lines.append("- The user's own projects live under: " + ", ".join(f"`{p}`" for p in project_roots)
                     + ". You may read them (code, README, git log) as evidence.")
    if version and str(version.get("id")) == "ALL":
        lines.append(f"- The user is looking at `ALL`, the full set: every entry in the library"
                     f" (language `{version.get('lang')}`, template `{version.get('template')}`)."
                     " Job targets are the other `versions`; each one is a subset.")
    elif version:
        lines.append(f"- The user is currently looking at the job target (version) `{version.get('id')}`"
                     f" ({version.get('label') or ''}, language `{version.get('lang')}`,"
                     f" template `{version.get('template')}`).")
    if entry:
        lines.append(f"- The entry open in the editor is `{entry.get('id')}` (section `{entry.get('section')}`)."
                     " Entry ids are internal: the user never sees them, so in replies call entries by"
                     " their title (or section and number, e.g. \"Projects #2\").")
    return "\n".join(lines) + "\n"


# ---- the editor's buttons ----------------------------------------------------
ONE_LINE = ("每条要点在当前模板和版面下只占一行（这是用户的硬要求，一行更美观）。改完运行 {cmd} 检查：输出里列出的"
            "「take more than one line」的要点要继续精简，直到全部一行；百分比表示文字占一行的多少，"
            "112% 就是大约要删掉 12%。")

WRITING_ZH = ("中文简历的写法：动词开头（负责、主导、搭建、设计、优化、实现……），不写「我」；去掉英文里的冠词、"
              "从句和客套修饰，信息密度要高；技术名词、产品名、数字照原样保留（不硬译 LLM、React 这类词）；"
              "中文更短，可以把两条英文合成一条，也可以省掉对中文岗位不重要的一条。")
WRITING_EN = ("English resume style: start with a strong past-tense verb, no pronouns, quantify where the facts allow, "
              "keep each bullet tight; Chinese phrasing should be rebuilt into natural English, not translated word by word.")


def polish_message(name: str, instruction: str, lang: str | None = None, version_id: str | None = None,
                   data_path: str = "") -> str:
    """'让 Claude 润色': rewrite the visible layer from the facts, in the language being edited."""
    msg = (f"请根据事实层（notes 和 evidence，需要时去读项目代码和 README）润色「{name}」这条经历的展示层"
           + (f"（{LANG_ZH.get(lang, lang)}）" if lang else "") + "。只改这一条，不要改 notes 和 evidence。")
    if version_id and data_path:
        msg += "\n" + ONE_LINE.format(cmd=f"`{render_cmd(version_id, data_path, lang)}`")
    if instruction.strip():
        msg += f"\n补充要求：{instruction.strip()}"
    return msg


def _translate_rules(target: str, version_id: str, data_path: str) -> str:
    other = "en" if target == "zh" else "zh"
    return (f"- 这不是逐句翻译。{WRITING_ZH if target == 'zh' else WRITING_EN}\n"
            f"- 要点按位置一一对应（每条是一个 {{en: ..., zh: ...}}）。某条在{LANG_ZH[target]}版里不需要时，"
            f"把它的 `{target}` 写成空字符串 ''，它就只在{LANG_ZH[other]}版出现；需要比{LANG_ZH[other]}版多一条时，"
            f"新增一条并把 `{other}` 写成 ''。\n"
            f"- 不要改 `{other}`，也不要改 notes 和 evidence；事实边界不变：只写 notes / evidence 能支撑的内容。\n"
            f"- " + ONE_LINE.format(cmd=f"`{render_cmd(version_id, data_path, target)}`"))


def translate_message(name: str, target: str, version_id: str, data_path: str, instruction: str = "") -> str:
    """'写中文版 / 写英文版': a native rewrite into the other language, not a translation."""
    other = "en" if target == "zh" else "zh"
    msg = (f"请为「{name}」写{LANG_ZH[target]}版本：根据已有的{LANG_ZH[other]}内容和事实层（notes、evidence），"
           f"补齐或重写这一条所有文本字段的 `{target}`。\n" + _translate_rules(target, version_id, data_path))
    if instruction.strip():
        msg += f"\n补充要求：{instruction.strip()}"
    return msg


def fit_message(name: str, lang: str, version_id: str, data_path: str, long_bullets: list[dict]) -> str:
    """'压成一行': shorten only the bullets that wrap."""
    lines = "\n".join(f"  - 第 {b['index'] + 1} 条（{b['lines']} 行，文字占一行的 {round(b['fill'] * 100)}%）：{b['text']}"
                      for b in long_bullets)
    return (f"「{name}」有这些{LANG_ZH.get(lang, lang)}要点超过了一行：\n{lines}\n"
            f"请只改这几条的 `{lang}`，把每条压到一行：保留最有分量的动作和结果，删掉修饰和重复信息，"
            f"不改变事实，也不要改别的语言和其他条目。"
            + ONE_LINE.format(cmd=f"`{render_cmd(version_id, data_path, lang)}`"))


def sync_rules(langs: list[str], version_id: str, data_path: str) -> str:
    both = "、".join(LANG_ZH.get(l, l) for l in langs)
    return (f"请去看这些变化（提交记录、改动的文件、README），判断哪些值得写进简历，再更新展示层"
            f"（bullets、tech，必要时 subtitle）。{both}都要同步更新，每种语言按各自的写法来写，不是互相翻译。"
            f"如果改动不值得写进简历，就不要改，直接告诉我为什么。不要改 notes 和 evidence。\n"
            + ONE_LINE.format(cmd=" 和 ".join(f"`{render_cmd(version_id, data_path, l)}`" for l in langs)))


def sync_message(name: str, change_text: str, langs: list[str], version_id: str, data_path: str) -> str:
    """'按项目新进展更新': the project folder changed since Claude last read it."""
    return f"「{name}」的项目文件夹自从上次同步后有更新：\n{change_text}\n\n" + sync_rules(langs, version_id, data_path)


def batch_message(kind: str, version: dict, data_path: str, names: list[str], target: str | None = None) -> str:
    """Whole-version actions from the job menu."""
    vid = str(version.get("id"))
    lst = "、".join(f"「{n}」" for n in names)
    if kind == "translate":
        return (f"请为当前岗位（{version.get('label') or vid}）里的这些经历写{LANG_ZH[target]}版本：{lst}。"
                f"一条一条来，每条都按下面的规则：\n" + _translate_rules(target, vid, data_path))
    if kind == "fit":
        return (f"当前岗位（{version.get('label') or vid}）里这些经历有要点超过一行：{lst}。"
                f"请逐条把超过一行的要点压到一行，不改事实，不动其他语言。"
                + ONE_LINE.format(cmd=f"`{render_cmd(vid, data_path)}`"))
    raise ValueError(kind)
