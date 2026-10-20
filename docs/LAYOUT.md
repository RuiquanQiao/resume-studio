# The window: one chat, the resume as a document

Status (2026-10-17): built as designed (both steps at the end), not committed yet. Choices made on the way are
marked *Built:*.

## The request (2026-10-17)

Recorded so it is never asked again. The rest of this file is the design that answers it.

- The Claude chat exists in all three workspaces; merge it into one. Resume is the hard case, since
  its Library is the left pane: perhaps keep only the chat on the left, all the fields in the middle,
  and an outline on top that shows on hover and jumps between entries.
- Resume's Applications tab is confusing now that Applying exists; it may not be needed.

The proposal below was approved as proposed: merge the chat and drop the Applications tab first, then
make the resume one continuous document with a foldable outline, writing this shape down before
starting.

What the user wants, point by point:

1. **One chat across the three workspaces** (Resume, Job search, Applying), always in the same place.
2. **The left pane holds only the chat**, in Resume too.
3. **The middle of Resume shows every field**, one document, with **an outline to jump between entries**.
   The user floated an outline that appears on hover; the approved form keeps that as the collapsed state
   (see below).
4. **No Applications tab in Resume**: the Applying workspace and Job search cover it.

## 1. One chat

### Why there were three

The three workspaces already share one chat panel. What made it look like three:

- `visibleChats()` filtered the list by workspace: Applying showed only its chats, Job search only those
  that run in career-ops' folder, Resume all of them.
- Each workspace remembered its own open chat, so switching workspace switched conversation.
- An Applying chat (`workspace: "autofill"`) had its context replaced by the browser and tracker notes,
  so it didn't know which entry was open; a Resume chat didn't get the tracker or browser commands.

### The one real limit

A chat is a Claude Code session, and its working folder is fixed when it starts. career-ops' chats must run
in career-ops' folder, where its CLAUDE.md and modes load. A session can't move folders halfway through.

### The design

- **One list**, the same in every workspace. A chat that runs in career-ops' folder is grouped under its
  name, so the two kinds stay apart without hiding either.
- **The open chat stays open** when the workspace changes.
- **What Claude is told follows the screen**, not the workspace the chat was started in. Every message
  from a chat in Resume Studio's data folder carries:
  - the resume context it always had (the subset, the open entry),
  - the tracker commands (`scripts/hunt.py`) and the browser commands (`scripts/browser.py`),
  - one line saying what is on screen: the workspace and the company, application, platform or
    career-ops page open there.

  So "Xinghe 一面过了" works from Resume, and "第三条经历改短一点" works from Applying.
- **New chat** starts in Resume Studio's data folder, with all of the above. In Job search it still starts
  in career-ops' folder, as before, because that is what a chat there is for; career-ops' modes always do.
- **The chat pane can be hidden in every workspace** (Job search could already do this), from the Claude
  button in the top bar (Ctrl+J).
- Old Applying chats (`workspace: "autofill"`) run as ordinary data-folder chats and get the same context.

## 2. No Applications tab in Resume

What the tab held, and where each piece is now:

| In the tab | Now |
|---|---|
| career-ops' list of evaluated postings | Job search shows career-ops' own interface, with more. A career-ops version without one shows the list in Job search. |
| Paste a posting to evaluate | Job search's top bar: **Evaluate a posting** |
| Install career-ops / use an existing copy | Job search (it already offered this) |
| Warnings (cv.md changed there, a check failed) | Job search, above career-ops' interface |
| Fill / update career-ops' profile | Job search's top bar (already there) |
| Progress of every application, 网申 | Applying |

The **posting page** (verdict, fit, requirements, which subset goes out, tailoring) stays: it needs the
resume preview beside it. It is no longer reached from a tab, but from:

- Job search: the resume picker of the posting open in career-ops' interface → **Open its page in Resume**;
- Applying: a role's **Resume** button opens the subset that role is sent with (its career-ops evaluation,
  when it has one, from the role's page).

## 3. Resume: the document and its outline

```
┌──────────┬──────────────────────────────────────────┬────────────┐
│ Chat     │ Outline     │ The document               │ Preview    │
│ (same in │ Profile     │ ┌ Profile ───────────────┐ │            │
│  every   │ Sections…   │ └────────────────────────┘ │            │
│ work-    │ ── Work ──  │ ┌ 01 Xinghe intern ─────────┐ │            │
│ space)   │ ☑ 01 Xinghe ↑↓ │ │ basics · bullets        │ │            │
│          │ ☐ 02 …      │ │ ▸ fact layer & folders  │ │            │
│          │ 《 collapse  │ └────────────────────────┘ │            │
└──────────┴──────────────────────────────────────────┴────────────┘
```

### Why not only a hover outline

The Library is more than a table of contents. It is where the user:

- ticks which entries a subset has, and orders them (↑↓);
- sees each section's count ("3/7"), the "new progress" notice, the missing-language marks;
- adds an entry to a section.

Ticking and ordering are frequent. Behind a hover menu each would take a hover and a wait, a list of
twenty or thirty entries wouldn't fit, and the keyboard couldn't reach it. Taking that away is the kind of
lost freedom this project doesn't do, so the outline keeps all of it and the hover form is an option.

### The design

- **The left pane is the chat**, as in the other workspaces.
- **The middle is the outline rail and the document**, side by side.
  - **The outline rail** is the Library as it was: Profile, Sections & settings, Subset settings,
    Versions, then every section with its entries, ticks, ↑↓, counts and "+". A click on an entry scrolls
    the document to it; while scrolling, the rail marks the entry in view.
  - **The document** shows Profile and then every entry in the subset's order, each as a card whose fields
    are edited right there: basics and bullets open, the fact layer and project folders folded under each
    card (one click opens them). Entries not ticked in the open subset are folded to one line at the end of
    their section; opening one shows its card.
  - Clicking into a card makes it the one being edited: the chat's context names it, the AI buttons act on
    it, the rail highlights it.
  - Sections & settings, Subset settings, Versions and a posting's page are pages of their own: they
    replace the document until the user goes back to it (the rail stays).
- **The rail can be collapsed.** Collapsed, it is a strip at the top of the document that opens the outline
  as a panel on hover or click, the form the user first described. Pinned or collapsed is the user's choice,
  remembered.
- **The preview can be hidden too**, so the document has room on a laptop screen. *Built:* the Preview
  button sits in the document's own top row (beside the language switch), so it is there whether the
  preview is shown or not.
- *Built:* when the window is narrow the preview gives way first: the middle keeps at least 600px (the rail
  212px, the document the rest), the preview at least 280px. At 1440px wide with the chat open that leaves
  the document about 390px; hiding the preview, the chat or the rail gives it more, and every width is still
  draggable.

### How editing works underneath

Each card keeps its own draft, base hash and save timer, so leaving a card with unsaved typing still saves
it, and a change made by Claude or another window to one entry refreshes that card alone (or shows the
conflict banner on it, if it has unsaved edits). The item being edited is the one the user last clicked
or typed into; everything that acted on "the open item" before acts on it. *Built:* only that card carries
the element ids the editor's code looks for (`#active`, `#bullets`, `#evidence`…); the line chips, the
evidence state and Claude's before/after diff are painted on every card. When the shape of the document
changes (an entry ticked, moved, added, deleted, another subset or language), it is painted again at the
same place; otherwise only the cards that changed are.

## 4. A fourth workspace: Plan (2026-10-19)

After Applying, Ctrl+4. It holds one Markdown document the user works out with Claude, with versions. It has
no fixed shape: empty, it says one line. The chat on the left is the same chat. Design:
`docs/PLAN.md`.

## Building it, in order

1. One chat (part 1) and no Applications tab (part 2).
2. The document and the outline rail (part 3).

Each step ends usable and tested.

## What stays the user's

- Every chat, in every folder, stays reachable; nothing is merged or deleted.
- Ticking, ordering and adding entries stay one click away, in the rail.
- The chat pane, the outline rail and the preview can each be shown or hidden; widths stay draggable.
