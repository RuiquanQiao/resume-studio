---
name: career-direction
description: How to help a student or new graduate choose a career direction by weighing who they are against the market they would enter, at once: their evidence (what they have really done, not self-ratings), their constraints (visa, money, city, family, 届), and each candidate route's real work, entry gate, demand for new graduates, pay, trend and where it leads in 3–5 years, all from dated sources. Ends in one recommended route plus a fallback, with checkpoints that would change the decision, written into the user's plan. Use when a user doesn't know what to aim for, is torn between paths (大厂 / 国企 / 外企 / 创业 / 考公 / 读博, 回国 or stay abroad, one function or another), asks what suits them, what an industry or role is really like, whether a direction is worth it, or when their applications keep failing and the target itself may be wrong.
---

# Choosing a direction with a new graduate

New graduates rarely lack effort. They lack a map: what the jobs really are, which of them want someone like
them right now, and where each leads. Advice that looks only at the person ("you like coding, be a developer")
or only at the market ("AI is hot") is wrong half the time. **Every judgement here is made at the crossing of
the two: this person's evidence, against this market's demand, today.**

This skill decides *where to aim*. Once that is settled, the planning skill (`../planning/SKILL.md`) works out
*how to get there by when*, the openings skill (`../openings/SKILL.md`) finds *the actual companies and
roles*, and the risk skill (`../risks/SKILL.md`) checks a role, a company or an offer for traps. Results from those two come back here: a direction that produces no interviews, or has no openings
where the user can work, gets looked at again.

## 1. Start from what is already known

Before asking anything, read what Resume Studio already holds:

- the experience library in the data file (`resume.yaml`): every entry, its bullets, `tech`, and its **private
  notes** (the fact layer: what really happened, what the user did and didn't do, what they enjoyed). Notes
  are for your judgement only; never quote them into anything others see;
- the skill catalog (the top-level `skills:` list): how well the user really has each skill (`level`, by how an
  interview on it would go: expert, strong = holds up normally, working = does not hold up, none = not started; with `why`). A name in `tech` alone is what the resume claims, not what they can do;
- the profile's notes (education, 届, visa, goals the user stated before);
- the plan (`plan.py show`): a direction may already be written there, with its reasons;
- the tracker (`hunt.py list`): what they applied to and how it went. Outcomes are evidence too: ten
  applications with no answer say something about the target, the resume or the timing.

Then ask **only what the decision depends on and isn't there**, two or three questions at a time, never a
questionnaire:

- which countries and cities they can and would work in, and their status there (届 and degree date for
  China; visa, work rights and how long they last abroad);
- money: do they need an income soon, carry debt, support family? This alone can rule routes in or out;
- hard limits from family, partner, health, 户口 or anything else they name;
- what they have actually enjoyed or hated doing, as **episodes** ("the week I debugged the data pipeline
  I lost track of time"), not adjectives ("I like challenges"). Ask for the last time they lost track of
  time, and the task they kept putting off;
- what they want the next 3–5 years to give them, made concrete by trade-offs (below).

## 2. The person: evidence, not ratings

Self-ratings ("Python 4/5") measure confidence, not ability. Build the picture from evidence:

- **What they can show.** For each skill a route needs, the proof in the library: an entry id, what was built,
  a result, a grade, an award. "Python: EXP-002, built the scraping and scoring pipeline; can't yet explain the
  async part without help" is useful; "good at Python" is not. Where there is no proof, say so.
- **Rare combinations.** The strongest asset of a new graduate is often a crossing few classmates have:
  a geography or biology degree plus ML coursework, a language plus a technical skill, a domain (medicine, finance, law)
  plus code. Name it, and look for routes that pay for it. A crowded route where they have no edge is
  usually worse than a narrower one where they do.
- **How employers read them.** School, degree, major, grades and the 届 / visa position are filters before
  anyone reads a project. Say plainly which routes filter on what (a 学历门槛, target-school lists,
  citizenship) and where the user stands against each filter.
- **Real gaps**, as the distance to what the route's entry test asks (section 3), in weeks of honest work,
  not as a list of weaknesses.
- **Values by forced choice.** Ask them to pick between concrete pairs, not to rank words: "8k more a month in
  a city you don't like, or less in one you do?", "a stable job with a slow first three years, or a fast one
  that may not last?", "a famous name doing support work, or an unknown company where you own a product?".
  Their answers are the weights; write them down in their words.

## 3. The market: what each route really is, today

For every route in play, find out the following. **Look each one up for the user's market and this year, with
a source and the date you read it.** Reputation is often years out of date; the examples below show how fast
the ground moves.

- **The work itself.** What someone in the role does on an ordinary Tuesday, in the user's first year: the
  tasks, the tools, who they answer to. The same title means different work at different companies (an
  "算法工程师" may train models, tune business rules or label data; a "consultant" may do research, slides or
  sales). Read 5–10 real postings for the title and say what they have in common.
- **The entry gate.** How new graduates get in: the round and when it opens next (the planning skill's
  calendars), what is tested (笔试, OA, case interviews, portfolio, 行测/申论), the filters above, and how many
  places there are for graduates.
- **Demand for new graduates, not for the occupation.** Campus and graduate postings this cycle; whether
  companies are expanding or freezing graduate intake; national data where it exists. A shortage of
  experienced engineers says little about juniors. Where the tracker has scans, count matching postings in
  the user's cities: that is evidence about *their* market.
- **Pay for a graduate**, as a range with its source: the employer's own posting first, then graduate surveys,
  then self-reported sites (offershow, Levels.fyi, Glassdoor), which skew high and are a few dozen people.
  Separate base, bonus and one-off amounts, and the cost of the city.
- **The trend.** Is the field growing, flat or shrinking for entry-level people, and why (policy, a
  technology replacing junior work, a downturn)? Give the evidence, not a forecast.
- **Where it leads.** What people in it do after 3–5 years: the next titles, and the **exits** (which other
  routes they can still move to). A route that keeps many doors open is worth more early in a career than
  its first salary shows; one that closes doors (a narrow niche, a long training contract, an age-limited
  exam missed for it) needs a good reason.
- **Eligibility and status.** Visa sponsorship, citizenship rules, degree verification (留服认证), and the
  one-time value of 应届 status in China, which many employers and exams accept only within a set time after
  graduation: check the rule for each route rather than assume it.

Examples of why this must be looked up, read 2026-11-03:

- In Australia's 2025 Occupation Shortage List, software engineers were not in shortage in any state or
  territory, for the first time since the list began in 2021, while cybersecurity engineers still were
  ([Information Age, 2025-10-20](https://ia.acs.org.au/article/2025/there-s-no-shortage-of-software-engineers-in-australia-anymore.html)).
- In MyCOS's 2026 report on China's 2025届 bachelor graduates, average pay six months after graduation was
  6,435 yuan a month; microelectronics led the majors at 7,814, and computer science and software engineering,
  long near the top, had mostly dropped out of the top ten
  ([辽沈晚报 via 新浪, 2026-06-25](https://finance.sina.cn/2026-06-25/detail-inieqnay1052136.d.html)).

Neither says "don't do software": they say the easy years are over for juniors in those markets, so the user
needs an edge there or a reason to accept the competition.

Primary sources to try first (check they still publish, and use the latest edition):

- China: employers' own 校招 pages; MyCOS (麦可思) annual graduate employment reports; 教育部 and each
  university's 就业质量年度报告 (by major, with destinations and pay); 国家公务员局 and provincial 人社 sites
  for exams; 国聘 for state-owned employers.
- Australia: QILT's Graduate Outcomes Survey (employment and pay by field of study); Jobs and Skills
  Australia (Occupation Shortage List, occupation profiles); employers' graduate pages, Prosple and
  GradConnection for intake sizes and dates; Home Affairs for visas.
- Elsewhere: the national statistics office, the main graduate survey, and the employers' own pages.

Never invent a figure. When you can't find one, write "not found" and what you searched.

## 4. Lay out the routes

Offer **three to five routes**, and make sure they include:

- the one the user came in with, judged fairly;
- one that uses their rare combination, which they may not have thought of;
- one safe route: lower ceiling, high chance of an offer this cycle.

Think across kinds of employer and paths, not only job titles: big tech, foreign firms, state-owned firms and
public institutions, the civil service (考公, 选调), small companies and startups, research (RA, a PhD), and
going home or staying where they study. Each kind has its own gate, rhythm and trade-offs.

For each route, one block with facts, sources and dates:

- the work, in the first year;
- the gate: when it next opens, what is tested, the filters, and where the user stands against each;
- **fit**: the evidence for it from the library (entry ids) and from their episodes and values;
- **gap**: what is missing, and how many weeks of real work it takes to close, given their hours;
- market: demand for graduates, pay range, trend;
- 3–5 years: where it leads, the exits, and what it closes off;
- the main risk.

A table across the routes helps the user compare, but keep facts in it, not invented scores. A weighted
score looks precise and hides the judgement; make the judgement in words instead.

## 5. Recommend, with what would change your mind

- **Find the constraint that decides.** Usually one dominates: they need income within three months, they
  can't get a work visa, the only window this year closes in five weeks, or they hate the work. Say which,
  and let it decide before anything else.
- **Prefer doors that stay open** when the user is unsure: early on, a route that builds a skill many routes
  pay for beats a slightly better offer in a dead end. Say when a choice is hard to reverse.
- **Recommend one primary route and one fallback** that can run alongside it (shared preparation, a
  different gate), and say why that one, against the others, in two or three sentences.
- **Set checkpoints**: what result, by when, would mean the direction is wrong or the approach is.
  "By 2026-12-15: at least 3 first-round interviews from about 30 applications to graduate data roles. Fewer
  than that: look again at the resume first, then the target." A checkpoint is a number and a date.
- **Cheap tests before big commitments.** Before months of preparation, check the route for a week: talk to
  two people doing the job (alumni, LinkedIn, 脉脉), do one task the job really involves, read ten postings,
  send five applications and see what comes back. Put these first.
- Be plain. If the target they came in with doesn't fit their evidence or their market this year, say so,
  once, with the facts, and give the route that is open. Don't flatter, and don't talk them out of a route
  they want only because it is hard: give the cost and let them choose. The choice is theirs.

## 6. Write it into the plan

The decision belongs in the plan, so it lasts and its changes are kept. With the user's agreement, rewrite the
plan (`plan.py write --stdin --why "<what was decided and why>"`) so it holds, in the shape that suits it:

- the direction and the fallback, in a sentence each, with the reasons;
- the evidence that decided it, with sources and dates (so the next look can tell what changed);
- the checkpoints, as dated `- [ ]` items;
- what was ruled out, and why, in a line each: a short "not now" list stops the same doubts coming back.

Then hand over: the planning skill turns the direction into weeks and commitments; the openings skill fills
the tracker with the companies to go for.

## 7. When to look again

Rework the direction when a checkpoint fails, when the market moves (a freeze, a new rule, a visa change),
when the user's evidence changes (a new internship, a project they loved or hated), when an offer forces a
choice, or when they ask. Read the plan's versions first: what was decided, on what evidence, and what is
different now. A changed direction is a new version with its reason, never a silent rewrite.
