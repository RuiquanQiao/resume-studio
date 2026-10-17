"""What the UI adds on top of a normal Claude Code session.

Kept short on purpose: Claude Code's own system prompt, the user's CLAUDE.md
and the installed skills do the heavy lifting. This only says where Claude is
and what the user is looking at, plus the messages the editor's buttons send
on the user's behalf. No reply language is forced.
"""
from __future__ import annotations

from pathlib import Path

from core.i18n import t

from . import langs

SKILL_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_ROOT / "SKILL.md"
RENDER = SKILL_ROOT / "scripts" / "render.py"


def lang_name(code: str) -> str:
    """A resume language by its own name ('Deutsch', '简体中文')."""
    return langs.info(code)["name"]


def render_cmd(subset_id: str, data_path: str, lang: str | None = None) -> str:
    return f'python "{RENDER}" {subset_id}' + (f" --lang {lang}" if lang else "") + f' --data "{data_path}"'


def ui_context(data_path: str, subset: dict | None, entry: dict | None, project_roots: list[str] | None = None,
               screen: str = "") -> str:
    lines = [
        "# Resume Studio",
        "",
        "The user is talking to you from the Resume Studio window (a resume editor built on Claude Code),",
        "not from a terminal. The same chat sits beside all three of its workspaces: Resume (the experience",
        "library as a document, and a live PDF preview), Job search (career-ops' own interface) and Applying",
        "(the applications, companies and platforms tables, and the browser Resume Studio drives).",
        "",
        f"- Resume data file: `{data_path}`. The window watches it: after you edit it, the form and preview",
        "  refresh by themselves, so there is no need to compile or tell the user to reload.",
        f"- Data format, the multilingual rules and the fact-boundary editing rules: `{SKILL_MD}` (the",
        "  resume-studio skill). Read it before your first edit of the data file in this conversation.",
        f"- To check pages and which bullets wrap onto a second line: `{render_cmd('<SUBSET-ID>', data_path)}`",
        "  (add `--lang <code>` to check another resume language with the same template and layout).",
    ]
    if project_roots:
        lines.append("- The user's own projects live under: " + ", ".join(f"`{p}`" for p in project_roots)
                     + ". You may read them (code, README, git log) as evidence.")
    if subset and str(subset.get("id")) == "ALL":
        lines.append(f"- The user is looking at `ALL`, the full set: every entry in the library"
                     f" (language `{subset.get('lang')}`, template `{subset.get('template')}`)."
                     " The other `subsets` are the resumes the user sends, one per kind of role.")
    elif subset:
        lines.append(f"- The user is currently looking at the subset `{subset.get('id')}` (a resume for one kind of role)"
                     f" ({subset.get('label') or ''}, language `{subset.get('lang')}`,"
                     f" template `{subset.get('template')}`).")
    if entry:
        lines.append(f"- The entry being edited is `{entry.get('id')}` (section `{entry.get('section')}`)."
                     " Entry ids are internal: the user never sees them, so in replies call entries by"
                     " their title (or section and number, e.g. \"Projects #2\").")
    if screen:
        lines.append(f"- On screen right now: {screen}. \"This\" and \"here\" in the user's message most likely mean it.")
    return "\n".join(lines) + "\n"


# ---- the editor's buttons ----------------------------------------------------
# These are sent as the user's own message and shown in the chat, so they are in the interface
# language (t). Codes and commands stay as they are.
def one_line(cmd: str) -> str:
    return t("Every bullet must fit on one line with the current template and layout (the user's hard rule: "
             "one line looks better). When done, run {cmd} to check: keep shortening the bullets it lists as "
             "\"take more than one line\" until all fit; the percentage is how much of a line the text fills, "
             "so 112% means cutting about 12%.", cmd=cmd)


def writing_style(lang: str) -> str:
    """How a resume reads natively in `lang`."""
    if lang == "en":
        return t("English resume style: start with a strong past-tense verb, no pronouns, quantify where the facts "
                 "allow, keep each bullet tight; rebuild other languages' phrasing into natural English, not word by word.")
    if langs.script(lang) in ("zh", "zh-tw"):
        return t("Chinese resume style: start with a verb (负责, 主导, 搭建, 设计, 优化, 实现…), never \"I\"; drop articles, "
                 "clauses and filler from other languages and keep the information dense; keep technical terms, product "
                 "names and numbers as they are (don't force-translate words like LLM or React); Chinese is shorter, so two "
                 "bullets may merge into one, and one that doesn't matter for the job may be left out.")
    return t("{lang} resume style: write it the way a native {lang} resume reads (verbs, tense, formality and length "
             "as local recruiters expect), keep technical terms, product names and numbers as they are, and rebuild "
             "the phrasing instead of translating word by word.", lang=lang_name(lang))


def _extra(instruction: str) -> str:
    return "\n" + t("Also: {text}", text=instruction.strip()) if instruction.strip() else ""


def polish_message(name: str, instruction: str, lang: str | None = None, subset_id: str | None = None,
                   data_path: str = "") -> str:
    """Polish: rewrite the visible layer from the facts, in the language being edited."""
    msg = (t("Using the fact layer (notes and evidence; read the project's code and README when needed), polish the "
             "visible layer of the entry “{name}” ({lang}). Change only this entry and leave notes and evidence alone.",
             name=name, lang=lang_name(lang)) if lang else
           t("Using the fact layer (notes and evidence; read the project's code and README when needed), polish the "
             "visible layer of the entry “{name}”. Change only this entry and leave notes and evidence alone.", name=name))
    if subset_id and data_path:
        msg += "\n" + one_line(f"`{render_cmd(subset_id, data_path, lang)}`")
    return msg + _extra(instruction)


def _translate_rules(target: str, others: list[str], subset_id: str, data_path: str) -> str:
    keep = ", ".join(f"`{o}`" for o in others) or "`en`"
    return ("- " + t("This is not a sentence-by-sentence translation.") + " " + writing_style(target) + "\n"
            "- " + t("Bullets line up by position (each one is a map like {example}). When a bullet isn't needed in the "
                     "{lang} version, set its `{code}` to an empty string '' and it shows only in the other languages; "
                     "when the {lang} version needs an extra bullet, add one and set the other languages to ''.",
                     example="{" + ", ".join(f"{c}: ..." for c in [*others, target]) + "}",
                     lang=lang_name(target), code=target) + "\n"
            "- " + t("Don't change {keep}, notes or evidence; the fact boundary stays: write only what notes / evidence "
                     "support.", keep=keep) + "\n"
            "- " + one_line(f"`{render_cmd(subset_id, data_path, target)}`"))


def translate_message(name: str, target: str, others: list[str], subset_id: str, data_path: str,
                      instruction: str = "") -> str:
    """Write another language's version: a native rewrite, not a translation."""
    msg = (t("Write the {lang} version of “{name}”: from its existing text and the fact layer (notes, evidence), fill "
             "in or rewrite `{code}` in every text field of this entry.", lang=lang_name(target), name=name, code=target)
           + "\n" + _translate_rules(target, others, subset_id, data_path))
    return msg + _extra(instruction)


def fit_message(name: str, lang: str, subset_id: str, data_path: str, long_bullets: list[dict]) -> str:
    """Fit on one line: shorten only the bullets that wrap."""
    lines = "\n".join("  - " + t("bullet {n} ({lines} lines, the text fills {pct}% of a line): {text}",
                                 n=b["index"] + 1, lines=b["lines"], pct=round(b["fill"] * 100), text=b["text"])
                      for b in long_bullets)
    return (t("These {lang} bullets of “{name}” run past one line:", lang=lang_name(lang), name=name) + f"\n{lines}\n"
            + t("Change only `{code}` of these bullets and bring each onto one line: keep the strongest actions and "
                "results, drop modifiers and repetition, don't change the facts, and don't touch other languages or "
                "other entries.", code=lang) + " "
            + one_line(f"`{render_cmd(subset_id, data_path, lang)}`"))


def sync_rules(codes: list[str], subset_id: str, data_path: str) -> str:
    names = ", ".join(lang_name(c) for c in codes)
    return (t("Look at these changes (commits, changed files, README), decide what is worth putting on the resume, then "
              "update the visible layer (bullets, tech, subtitle if needed). Update every language ({langs}), each "
              "written its own way rather than translated from another. If the changes aren't worth adding, don't "
              "change anything and tell me why. Leave notes and evidence alone.", langs=names) + "\n"
            + one_line(", ".join(f"`{render_cmd(subset_id, data_path, c)}`" for c in codes)))


def sync_message(name: str, change_text: str, codes: list[str], subset_id: str, data_path: str) -> str:
    """Update from the project: the folder changed since Claude last read it."""
    return (t("The project folder of “{name}” changed since the last sync:", name=name) + f"\n{change_text}\n\n"
            + sync_rules(codes, subset_id, data_path))


def batch_message(kind: str, subset: dict, data_path: str, names: list[str], target: str | None = None,
                  others: list[str] | None = None) -> str:
    """Whole-subset actions from the subset menu."""
    vid = str(subset.get("id"))
    job = subset.get("label") or vid
    lst = ", ".join(f"“{n}”" for n in names)
    if kind == "translate":
        return (t("Write the {lang} version of these entries in the current subset ({job}): {list}. One at a time, each "
                  "following these rules:", lang=lang_name(target), job=job, list=lst) + "\n"
                + _translate_rules(target, others or [], vid, data_path))
    if kind == "fit":
        return (t("These entries in the current subset ({job}) have bullets running past one line: {list}. Bring every "
                  "such bullet onto one line, entry by entry, without changing facts or other languages.", job=job, list=lst)
                + " " + one_line(f"`{render_cmd(vid, data_path)}`"))
    raise ValueError(kind)


def batch_sync_message(parts: list[str], codes: list[str], subset_id: str, data_path: str) -> str:
    return (t("The project folders of these entries changed since the last sync; handle them one by one:") + "\n\n"
            + "\n\n".join(parts) + "\n\n" + sync_rules(codes, subset_id, data_path))


def _where(app: dict, report: str) -> str:
    return (t("The evaluation of this posting is at `{report}`: read it first, especially the requirement-by-requirement "
              "match, the customization plan and the extracted keywords (the posting itself is archived in it).", report=report)
            if report else t("This posting has no evaluation report yet; its link: {url}", url=app.get("url") or "?"))


def tailor_message(subset: dict, app: dict, report: str, data_path: str, new: bool = False) -> str:
    """Make or adjust a subset (a resume for a kind of role) from one evaluated posting."""
    vid = str(subset.get("id"))
    job = subset.get("label") or vid
    posting = " — ".join(x for x in (app.get("company"), app.get("role")) if x) or f"#{app.get('id')}"
    first = (t("None of my resume subsets fits the posting {posting} well, so the new subset “{job}” (`{id}`) starts from it.",
               posting=posting, job=job, id=vid) if new else
             t("Adjust the subset “{job}” (`{id}`) so it also fits the posting {posting}.", job=job, id=vid, posting=posting))
    return (first + " " + _where(app, report) + "\n"
            "- " + t("A subset is a resume for a kind of role that I send to many similar postings, not to one company: never "
                     "put this company's name (or anything only true of this one posting) into the resume, and name the subset "
                     "after the kind of role (its `label`).") + "\n"
            "- " + t("Pick the library entries that serve this kind of role best and make them this subset's `entries`, strongest "
                     "first within each section; change its section order if another order reads better for it.") + "\n"
            "- " + t("Write its `headline` for this kind of role, in each resume language.") + "\n"
            "- " + t("Don't reword library bullets yourself: every subset shares them. Where a bullet would clearly land better "
                     "in the posting's words and notes / evidence back it, list the rewording in your reply and let me decide.") + "\n"
            "- " + t("Keep it to one page: adjust this subset's layout before suggesting fewer entries. Check with {cmd}.",
                     cmd=f"`{render_cmd(vid, data_path)}`") + "\n"
            "- " + t("Then tell me which requirements the resume now covers, which gaps remain, and which entries you left out and why."))


def choose_message(app: dict, report: str, ranked: list[dict], tool_id: str) -> str:
    """Which existing subset to send for one evaluated posting."""
    posting = " — ".join(x for x in (app.get("company"), app.get("role")) if x) or f"#{app.get('id')}"
    lines = [f"- `{v['id']}` {v['label']}: " + (t("{score}% by word match", score=v["score"]) if v["score"] is not None else "—")
             for v in ranked]
    return (t("Which of my resume subsets should I send for the posting {posting}?", posting=posting) + " " + _where(app, report) + "\n"
            + t("My subsets (`subsets` in the resume data file), with the window's rough word match:") + "\n" + "\n".join(lines or ["-"]) + "\n"
            "- " + t("Compare what each subset's resume actually says (its headline and chosen entries) with what the posting "
                     "asks for, and recommend one, with the requirements it covers and the gaps that remain.") + "\n"
            "- " + t("If none fits well, say whether adjusting one of them or a new subset for this kind of role would be better. "
                     "Don't change any subset yet.") + "\n"
            "- " + t("Once I agree on a subset, link it: add `{tool: {tool}, id: '{id}'}` to that subset's `postings` list in the "
                     "data file and remove it from any other subset's.", tool=tool_id, id=app.get("id")))


def attachments_note(paths: list[str]) -> str:
    return "\n\n" + t("Attachments (open them with Read):") + "\n" + "\n".join(f"- {p}" for p in paths)
