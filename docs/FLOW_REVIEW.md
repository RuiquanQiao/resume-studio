# The whole flow, from a job seeker's chair (2026-11-18)

Question asked: from making a resume to submitting a 网申, is there anything worth improving, compared with
career-ops? Is this a mediocre product? Judged only from the job seeker's side: does it get them more offers, sooner,
for less time.

## Verdict

Not mediocre as engineering, and ahead of career-ops for a Chinese 校招 + Australia new graduate in about half the
flow. But the investment is in the wrong half. The app is very good at **finding and keeping track of** openings and
weak at **turning a picked role into a submitted application and the submitted application into an offer**. A job
seeker is paid for the second half only.

## Stage by stage

| Stage | Resume Studio | career-ops | Who is ahead for this user |
|---|---|---|---|
| Choosing a direction | `skills/direction`, `skills/planning` with the hiring calendars, the Plan workspace | none (assumes you know) | Resume Studio |
| Building the resume | experience library + fact layer, subsets per kind of role, versions, live LaTeX preview, writing rules, skill levels | one `cv.md`, rewritten per posting | Resume Studio, clearly |
| Finding employers | `skills/openings`, AI fills each company, logos, nature, map | 100+ preset Western tech companies, funded-company feed | Resume Studio for China; career-ops for US/EU tech |
| Reading openings | built-in 北森, Moka, 飞书, HotJob, 阿里, 国聘, routes for any other site, read counts checked against the site's total | Greenhouse / Lever / Ashby APIs | Resume Studio for China, by far |
| Screening | tracks by title (no AI), coverage + mastery scores, AI score after 想投 | A–H evaluation report, an explicit **do not apply below 4.0**, liveness and repost checks, work-auth blocker | **career-ops**: it gives a decision, Resume Studio gives numbers |
| Hard constraints (届, degree, graduation window, city, 留学生 rules, 志愿 limits) | listed as still to do | work-auth only | neither; this is the gap that matters most in 校招 |
| Tailoring per posting | link the best subset; adjust only when none fits | a tailored CV per posting | Resume Studio's rule is better (fewer, stronger versions) |
| The 网申 form | keep a copy of the company profile, three-way diff, recipes push without AI after the first time | prefill answers, never submit | Resume Studio for the structured fields |
| Open questions in the form (为什么选择我们, 职业规划, 最有挑战的项目 ≤500字) | **none** | `application-answers.mjs`, cover letters | **career-ops** |
| Referrals (内推) | a channel value only | `contacto`: finds the person, drafts the note | **career-ops** |
| After sending: 测评, 笔试, 面试 | statuses, booking, calendar, Gantt; mock interview per resume entry | interview prep per company, STAR+R story bank, practice, debriefs, red flags | **career-ops** for prep; Resume Studio for the calendar |
| Learning from outcomes | the direction skill says no interviews reopen the direction; no data view | `analyze-patterns`, funnel stats, rejection latency | **career-ops** |
| Offer | `skills/risks` grounded in 劳动合同法, offer comparison table | `offer-prep`, salary gap, negotiation scripts | about even; Resume Studio's is better for China |

## The problem in one picture

The funnel is: openings read → roles picked → applications submitted → tests passed → interviews → offer. Most of
the work since 2026-11-05 (by the commit log) went into the first arrow: sites that hang, sites that refuse foreign
addresses, routes rewritten to call APIs, faster scans. On a real season's data the first arrow already yields far
more picked roles than get submitted; the funnel narrows between **picked** and **submitted**, and nothing after
**submitted** helps the user pass the next round. Each further hour on scanning now buys the job seeker almost
nothing; an hour on the last mile buys applications.

## What to build, most valuable first

1. **A submit queue for 网申 (the last mile).** Today submitting one role takes: open the company, pick the subset,
   keep a copy of the profile, read it, review the diff, push, open the site, submit, come back, mark as sent. Make it
   one screen: every role at 想投, grouped by company, ordered by deadline, each with one state (ready / needs your
   input / needs sign-in) and one next button. Prepared ahead, in the background: the profile pushed from the subset,
   the PDF uploaded, the open questions drafted (item 2). The user's part shrinks to reading, fixing and pressing the
   site's own submit. Coming back to the window after the site's submit, the role asks "Submitted?" once and marks it
   sent with the version. This is item 6 of the 2026-11-04 flow review, and it should come first, not last.
2. **Answers to open questions.** Almost every Chinese 网申 has two to five free-text questions with a character limit.
   Draft each from the fact layer and the role's description, at the limit, in the writing rules' voice, and keep an
   answer bank: the same question at the next company starts from the last answer, adapted. This is what students
   spend most of their 网申 time on, and nothing helps with it today.
3. **A decision per picked role, with hard constraints first.** Before any score: does the user meet the posting's
   届 and graduation window, degree, 留学生 conditions, city, language? Then the scores. Then one line: "Apply" /
   "Apply, but …" / "Not worth it, because …", with the reason quoted from the description. And the 校招 rule
   career-ops cannot know: many companies allow one or two 志愿 per season and a rejection locks the season, so the
   page says how many 志愿 the company allows and which role is the best use of them.
4. **After submit: one prep page per round.** 测评 (what the company uses: 北森, SHL, 智鼎; what it measures),
   笔试 (the kind of test this company gives this kind of role, with the user's own practice record), 面试 (questions
   drawn from the description and the company, answered from the fact layer like the mock interview, not only per
   resume entry). After each round, a short debrief whose good answers become stories in a bank, reused for the next
   company. The calendar already knows when each round is.
5. **The daily brief** (item 2 of the 2026-11-04 review): one screen on opening: deadlines this week, roles ready to
   submit, rounds booked, new matching roles, sites that read short. Each line one click.
6. **Referrals.** Per company: a place for a 内推码 and who gave it, and a drafted message to an alumnus. In 校招 a
   referral often skips the resume screen.
7. **Outcomes back into the resume and the direction.** Once there are a few dozen results: which subset and which
   track pass the resume screen, which fail at the test, which at interviews. Shown as a small table on the Plan,
   with the direction skill's checkpoint read from it.

## What to stop or hold

- **New site coverage**, unless a company the user wants cannot be read at all. Coverage is ahead of need.
- **More tables and maps.** They describe the search; they don't move it.
- **Comparing with career-ops as the bar.** It is built for English-language tech hiring through Western ATS. The real
  alternatives for this user are 牛客 (面经, 笔试题库, 内推, the 校招 calendar), 超级简历's 网申 helper (form
  autofill across Chinese recruiting systems) and a spreadsheet. Item 1 and item 2 are where the app beats all of them;
  items 3 and 4 are where 牛客 is still the place students go.

## Not checked in this review

- Whether the exported PDF reads as clean text in a recruiting system's resume parser (北森 and Moka parse the
  uploaded PDF into fields). A LaTeX CJK PDF can lose its text layer or split words; worth one automated check.
- First-run cost for anyone but the developer (Python, xelatex and a signed-in agent CLI are all required).
