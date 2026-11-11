# PhD applications (博士申请): the guide

Read once per chat before working on this plugin's records (`phd-applications` / `programs`).

## How admission works, by model (the record's `kind`)

- **Programme (cohort)** (项目制; the US, Canada, much of Asia): apply to a department's programme by its deadline
  (US: mostly December 1 – January 15 for autumn entry); admission is by committee, funding usually comes with the
  offer (stipend + tuition as an RA/TA). Statement of purpose, 3 references, transcripts, sometimes GRE, English
  scores (TOEFL/IELTS) for non-native speakers. Contacting faculty helps but isn't required.
- **Supervisor first** (导师制; the UK, Australia, New Zealand, much of Europe for doctoral schools): find a
  supervisor who agrees to take you, then apply with a research proposal (RP); funding is often a separate
  scholarship competition with its own, earlier deadline (Australia: the university's RTP / government scholarship
  rounds, often two a year; the UK: doctoral training partnerships and university studentships, often
  December–February). The supervisor's support decides most of it, so contact comes first.
- **Advertised position** (职位制; Germany, the Netherlands, Scandinavia, Switzerland, many EU-funded projects):
  a PhD is a paid job on a project; positions are advertised (the university's vacancies page, EURAXESS,
  academicpositions, findaphd), each with its own deadline and an interview.
- **Application and assessment, China** (申请考核制): most universities admit PhDs by application plus an
  assessment (材料审核, 笔试 and 面试), mostly in spring (applications around November–March); a supervisor's
  agreement beforehand matters a lot. 硕博连读 and 直博 are internal routes.
- **Joint or sponsored** (联合培养 / 公派): e.g. the China Scholarship Council's (CSC, csc.edu.cn) programmes for
  study abroad, with an annual application window (often March–April) that needs an invitation letter from the
  foreign supervisor first.

Every date above is a pattern, not a fact for this year: take deadlines from the programme's own page for the
intake the user wants, and leave a date empty rather than guess.

## Finding supervisors

Start from the user's own projects and interests (the experience library, their notes). For a candidate
supervisor, look at the group page, their papers of the last 2–3 years (Google Scholar, DBLP for computing,
Semantic Scholar), whether they take students this year (the page often says), current students, and funding
(an advertised project or grant). A good fit is specific: one of their recent papers the user's project connects
to. Put the supervisor's name in `supervisor`, the area in `field`, and in `notes` why they fit, with the papers.

## Contacting a supervisor (套磁)

A first email is short (under 200 words): who the user is in one line, the specific paper or project of theirs
and how the user's work connects, what the user wants (a PhD place, which intake, funding route), CV attached.
Draft it for the user to send; never send anything yourself. Follow up once after about two weeks without reply.
`contact` follows what happens.

## Funding and money

Fully funded offers are the norm in US/Canadian programmes, European positions (a salary) and with a won
scholarship in the UK/Australia; self-funded PhDs are rarely a good idea. Note the stipend amount and its source
in `notes`. `funding_by` is the scholarship's deadline when it differs from `apply_by`.

## Materials

The research proposal (for supervisor-first and Chinese admissions), the statement of purpose (programmes),
references (ask referees a month ahead), transcripts and degree certificates (and their translations), English
scores (check the minimum per band). The user's resume subsets make the CV: an academic CV leads with education,
research, publications and skills; suggest a subset for it if there isn't one.

## Filling the records

- One record per programme or per supervisor (`title` "University – Programme" or "University – Supervisor").
- `link` the programme's or the supervisor's page; deadlines from the official page, dated in `notes` when checked.
- `stage` and `contact` follow the user; move them only when the user says what happened.
