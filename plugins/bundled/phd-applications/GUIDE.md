# PhD applications (博士申请): the guide

Read once per chat before working on this plugin's records (`phd-applications`).

## The tables, and how they link

A PhD application is several things at once, so it is six collections, linked (a relation takes a record's id or
its exact title):

| Collection | One record is | Links to |
|---|---|---|
| `universities` | a university the user considers: country, ranking (which one, which year), English requirement, application fee, portal, rounds | — |
| `programs` | a programme, a doctoral school's call or an advertised position: model, priority, intake, funding, deadlines, stage | `university`, `supervisors` (several) |
| `supervisors` | a possible supervisor: group, research, whether they take students, contact (套磁) and its dates, why they fit | `university` |
| `funding` | a scholarship: provider, what it covers, amount, deadline, result date, stage | `programs` (several) |
| `referees` | a person writing a reference: relationship, which programmes, status, when asked, when needed | `programs` (several) |
| `materials` | a document: CV, statement of purpose, research proposal, transcript, an English test with its score and expiry… | `programs` (several) |

Order of work: add the university first (once), then link programmes and supervisors to it; never write a
university's name into a text field instead. Before adding anything, `show` the collection and reuse a record that
is already there (the same university, the same supervisor).

`stage` (programmes, scholarships), `contact` (supervisors) and the referee and document statuses follow the user:
move them only when the user says what happened.

## How admission works, by model (a programme's `kind`)

- **Programme (cohort)** (项目制): apply to a department's programme by its deadline; a committee admits; funding
  usually comes with the offer.
- **Supervisor first** (导师制): find a supervisor who agrees to take the user, then apply with a research proposal;
  funding is often a separate scholarship competition with its own, earlier deadline.
- **Advertised position** (职位制): a PhD is a paid job on a project; each position has its own deadline and interview.
- **Application and assessment, China** (申请考核制): application, review of materials, then a written test and an
  interview; a supervisor's agreement beforehand matters a lot.
- **Joint or sponsored** (联合培养 / 公派): e.g. a China Scholarship Council (CSC) programme, which needs an
  invitation letter from the foreign supervisor before its yearly window.

## By country: the usual pattern

These are patterns, not this year's facts. Take every date from the official page for the intake the user wants,
write in `rounds` or `notes` when it was checked, and leave a date empty rather than guess.

- **United States**: programmes (cohort). Deadlines mostly December 1 – January 15 for autumn entry; statement of
  purpose, 3 letters, transcripts, English scores, GRE now often optional; funding (stipend and tuition) usually comes
  with the offer; a common reply date is April 15 (`decision_by`). Contacting faculty helps but isn't required; some
  programmes have rotations, so `supervisors` may stay empty at first.
- **Canada**: mixed: many departments want a supervisor to agree first. Deadlines often December – February.
- **United Kingdom**: supervisor first or an advertised studentship. Funded places come from doctoral training
  partnerships (UKRI), university studentships and project funding, deadlines mostly December – February; many
  international students need a separate scholarship. A research proposal for self-proposed projects; usually an
  interview. A PhD is typically 3–4 years.
- **Australia and New Zealand**: supervisor first. Contact a supervisor, often send an expression of interest, then
  apply; the scholarship (Australia: the university's RTP-funded scholarships) has rounds, often twice a year, each
  university with its own dates. A research proposal is required. Intakes are usually more than one a year.
- **Germany**: two routes: an advertised position (a salaried job, often 65–100 %) or individual doctorate with a
  professor who agrees, plus a scholarship (DAAD, foundations); also structured graduate schools with calls.
- **Netherlands, Nordics, Switzerland, Belgium**: positions, salaried, advertised on the university's vacancies page
  (also EURAXESS, AcademicPositions); each has its own deadline, often 4–8 weeks after it is posted.
- **France**: a funded thesis (contrat doctoral, CIFRE with a company, or an ED call) with a supervisor; doctoral
  schools run their calls mostly in spring.
- **Hong Kong**: the Hong Kong PhD Fellowship Scheme (HKPFS) asks for a registration with the Research Grants
  Council first and then an application to the university, by a deadline around early December; universities also
  have their own studentships with later rounds. Contact a supervisor first.
- **Singapore**: NUS, NTU and others run rounds (often two a year) with university scholarships; contacting a
  supervisor helps.
- **Japan**: contact a professor first (often required before applying); MEXT scholarship by embassy or university
  recommendation; many programmes start in April or October; entrance exams in some.
- **South Korea**: contact a professor first; government scholarship (GKS) or the lab's funding.
- **Mainland China**: 申请考核制 for most PhDs, applications mostly November – March, then 材料审核, 笔试 and 面试;
  直博 and 硕博连读 are internal routes (直博 often through summer camps, 夏令营, and 预推免). The supervisor's
  agreement beforehand matters most.
- **CSC (China Scholarship Council, csc.edu.cn)**: for a degree abroad, a yearly window (usually in spring) that
  needs the foreign university's offer or invitation letter, so the supervisor contact has to start months earlier.

## Finding supervisors

Start from the user's own projects and interests (the experience library, their notes). For a candidate, read the
group page, their papers of the last 2–3 years (Google Scholar, DBLP for computing, Semantic Scholar), whether they
take students this intake (the page often says), current students, and funding (an advertised project or a grant).
A good fit is specific: write in `fit` one or two of their recent papers the user's work connects to, with links.
`taking` says only what a source says (their page, their reply); otherwise `unknown`.

## Contacting a supervisor (套磁)

A first email is short (under 200 words): who the user is in one line, the specific paper or project of theirs and
how the user's work connects, what the user wants (a PhD place, which intake, which funding route), CV attached.
Draft it for the user to send; never send anything yourself. When the user says it went out, set `contact` to
emailed, `first_email` to that day and `follow_up` about two weeks later; follow up once if there is no reply.

## Scholarships

Fully funded offers are the norm in US/Canadian programmes, European positions (a salary) and with a won
scholarship in the UK/Australia; a self-funded PhD is rarely a good idea, so say so when that is all a programme
offers. One record per scholarship, linked to the programmes it goes with; `amount` per year with its currency and
source; `apply_by` from its own page (often earlier than the programme's).

## Referees

Usually 2–3. Ask about a month before the earliest deadline (`needed_by`: the earliest `apply_by` among the
programmes linked to that referee). In `notes`, what to remind them of: the work the user did with them, grades,
what each programme asks. Each programme's portal sends the request; the status is `submitted` only once every
linked programme has the letter.

## Documents

The research proposal (supervisor-first and Chinese admissions), the statement of purpose (programmes), the
academic CV, transcripts and degree certificates (and certified translations), English scores (check the minimum
per band, and `valid_until`: IELTS and TOEFL are usually valid for two years). The user's resume subsets make the
CV: an academic CV leads with education, research, publications and skills; suggest a subset for it if there isn't
one. Link each document to the programmes that need it, and note each programme's word limit and format.
