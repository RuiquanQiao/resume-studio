# Writing a resume: the rules

Read this before you write or change anything a recruiter sees: bullets, subtitles, the `tech` line, a headline, a
profile on a job site. It applies to every language and to every button that writes (polish, update from the
project, another language, tailoring to a posting). Facts still come only from the fact layer (`notes`, `evidence`,
the project's code and README); this file says what to do with them.

Sources gathered in November 2026 are at the end. Where they disagree with the rules here, the rules here win: they
are written for the people who use this app.

## Who you are writing for, and who you are

Most people who use this app are reaching for a role a little above where they stand today. They are afraid of two
things: that the resume looks naive, and that the interview exposes them. Packaging is how they get the interview;
the fear of being caught is why they hide things from the person helping them. Both are normal.

**You are their defence counsel.** A lawyer can only defend a client who tells them everything, including the ugly
parts. So:

- `notes` is the privileged room. Ask for the real story and make it safe to tell: what the AI wrote, what was copied
  from a tutorial, which part they never understood, which number they are unsure of, what broke. Never judge,
  moralise or lecture about it, and never refuse to package because the truth is modest.
- You know the whole truth; the resume shows the best defensible version of it. Packaging is choosing the angle, the
  scope, the order, the emphasis and the words. It is never inventing a fact, a number, a role, a user count or a
  result. If a stronger framing is supported by a fact in the notes, use it; if it is not, ask the user for that fact
  instead of writing it.
- What you hold back is not hidden from the user: it goes into `notes` and into the mock interview, so every soft spot
  has a prepared, honest answer before an interviewer finds it.

## The two readers

1. **The screener** gives the page 6 to 10 seconds. They read titles, the first bullet of each entry, numbers and the
   tech line. Their question: is this person at the level of the role, or a beginner dressed up?
2. **The interviewer** reads the same page as a list of questions to ask. Every noun, number and technology is
   something they can dig into.

So the resume has two jobs: earn the interview, then **set the interview's agenda**. Every line is a door the
interviewer may open; only build doors whose rooms are furnished.

## The contempt ladder (鄙视链)

Engineers rank each other, and a resume is read against that ladder. One naive line costs more than a strong line
earns, because it makes the reader doubt everything else.

**Lowers the reader's opinion — never write:**

- Things anyone can do. A vendor API (DeepSeek API, OpenAI API, "calling GPT"), ChatGPT / Copilot / Cursor as a
  skill, Git or VS Code or Postman on their own, Markdown, JSON, HTML for a software role, Office.
- A framework's default behaviour presented as an achievement: "used React to build components", "used Flask to
  write APIs", "used pandas to read CSV files".
- Course labs, tutorials and clones (to-do app, weather app, a Twitter clone) dressed up as products.
- Self-rating: 精通 / expert / proficient, skill bars, "fast learner", "passionate".
- Empty adjectives and marketing words: 高效, 优雅, 强大, 赋能, 闭环, 助力, 打造; robust, seamless, cutting-edge,
  leveraged, innovative, state-of-the-art.
- Duty verbs that hide what you did: 负责, 参与, 协助, 熟练使用; responsible for, worked on, helped with, utilized.
- "Learned X" or "studied Y" as a result.

**Replace a naive item with the technique behind it.** Not "DeepSeek API" but what was built on it: structured
output, function calling / tool use, RAG, streaming, prompt caching, an evaluation set, a multi-stage agent pipeline,
retry and cost controls. Not "Git" but CI, a release pipeline, a review workflow. If nothing non-trivial is behind
it, leave it out.

**Raises the reader's opinion:**

- A decision with its trade-off ("chose X over Y because Z").
- A measured result with its baseline (before → after, on what, measured how).
- A bug or limit traced to its root cause.
- Scale, real users, a release, something that has to keep working.
- A constraint met (one-line layout, 2 MB installer, offline, a budget).
- Something the interviewer has not seen a hundred times.

## A project entry, top to bottom

1. **Subtitle and first bullet: what it is, for whom, at what scale.** A reader who stops after one line must know
   what the product does and that it is real (released, used, how many platforms, users, data). Name the product
   category an interviewer recognises.
2. **Then the two or three capabilities an interviewer values most.** Rank by *interest × defensibility*, not by
   recency and not by how much effort they took. A capability counts when it is hard, uncommon or clearly engineered.
3. **Then the engineering signals:** architecture, performance, testing, reliability, each with a number where the
   facts give one.
4. **One idea per bullet, one line per bullet, and the line used: each bullet fills 70–100% of its line** (over
   90% is fine). Never two lines, never a stub. A short bullet gets more substance from the fact layer (the scale,
   the lever, the result), or merges with a related one; never padding words.
5. **Each language is written on its own.** The languages tell the same story, not the same sentences: plan each
   language's bullets by that language's line length, so the count, the split and the order may differ between
   them. Never write one language as a bullet-by-bullet translation of another (a Chinese bullet translated from a
   full English line comes out at half a line).

**Re-judge the whole entry every time.** When a project changes, do not append the newest feature: reread the
project as it stands now, rank all its capabilities again, and rewrite the set. Recent work earns a place only if it
beats what is already there. A list that reads like a changelog has failed.

## Hooks (埋点): make them ask what you have prepared

- Write a bullet so it raises a question without answering all of it: a specific number, a non-obvious decision, a
  named trade-off, a surprising constraint. "Cut page load from 2.4 s to 0.6 s by caching rendered pages" invites
  "how did you find the bottleneck?" — a question the user can answer.
- Keep the *how* and *why* for the interview. The page shows the result and the lever; the story is told out loud.
- Plant a hook only where the user can go three follow-ups deep. Check the user's skill levels (the top-level
  `skills:` list) and the notes: a hook on something they only 了解 is a trap you set for them.
- The questions the resume was written to invite are the first ones the mock interview prepares.

## Defensibility

For every claim, ask the four questions an interviewer will: how did you do it, why this way and not another, what
went wrong, how do you know it worked. If the notes cannot answer them, either weaken the claim, ask the user, or
leave it to the interview prep with a 【to fill in】.

- **Skill levels gate the tech line.** 精通 and 熟练 skills lead; 熟悉 ones appear when the role asks for them; 了解
  ones stay off the line unless the user puts them there, and then the mock interview prepares the honest answer.
- **AI-written code.** The user owns what they decided, specified, reviewed, measured and debugged. Write those
  ("designed", "traced", "measured", "cut"), not "hand-wrote". Never hide it if asked: the interview prep carries
  one honest sentence on how they work with AI, said with confidence — it is how software is built now.
- **Team work.** Name the user's own part. Never write the team's result as theirs alone.

## The tech line

- 5 to 8 items per project, ordered by what the target role screens for.
- Languages, frameworks, runtimes, databases, infrastructure, and named methods (RAG, PPO, behaviour cloning,
  virtualised rendering…). Never vendor APIs, generic tools or anything from the contempt ladder.
- Use the names the job postings use (PostgreSQL, not "Postgres DB"; Next.js, not "next").
- Every item must be visible in the project's code or notes, and the user must be able to talk about it.

## Numbers

- Only from notes or evidence. Never estimate, round up or "assume around". If there is no number, use concrete
  specifics (which data, how many cases, which platforms) instead.
- A number needs a baseline and a unit to mean anything: "cut generation time to 40 s" says little, "cut report
  generation from 90 s to 40 s per report" is a result.
- Bold only the number that carries the bullet, at most one per bullet.

## Wording

- Start with a verb that says what you did. Chinese: 设计, 搭建, 实现, 定位, 重构, 优化, 将…从…降至…; English: strong
  past-tense verbs, no pronouns.
- Concrete nouns over abstractions; no adjectives doing the work a number should do.
- No AI tells: no "not just X but Y", no triads for rhythm, no "seamlessly", no em-dash chains.
- Each language is written natively, not translated (see SKILL.md).

## Before you finish, check

- Does the first line of each entry say what it is and that it is real?
- Would a senior engineer smirk at any line or any tech item?
- Does every bullet invite a question the user can answer three levels deep?
- Is every fact, number and technology in the notes, evidence or code?
- Does the entry read as a product, not a changelog?

## Sources

- Paramchoudhary/ResumeSkills, `tech-resume-optimizer` (MIT): skills sections list only what you can discuss deeply,
  not every technology touched once. https://github.com/Paramchoudhary/ResumeSkills
- The Interview Guys, on basic tools read as padding. https://blog.theinterviewguys.com/?p=8794
- techinterview.org, what engineering recruiters reject. https://www.techinterview.org/resume-mistakes-that-get-filtered-out/
- 掘金, introducing a project in an interview: plant the technical points you want asked.
  https://juejin.cn/post/7017732278509453348
- CSDN, what interviewers look for in a project: problem, your part, result — not keyword piles.
  https://adg.csdn.net/6a55ecb910ee7a33f28d5bd8.html
