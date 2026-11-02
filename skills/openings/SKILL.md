---
name: finding-openings
description: How to find the real companies and roles a new graduate should go for, once their direction is set: which channels list graduate and intern roles in each market (China's 校招 sites, 牛客, 宣讲会, 国聘; Australia's Prosple, GradConnection, university career portals, APS Jobs), how to find employers the user has never heard of, how to reach the roles that are never posted (referrals, alumni, lecturers), and how to check each one is open to them before it goes into the tracker. Use when the user asks where to find jobs or internships, which companies to apply to, how to get a referral (内推), when the tracker has few companies matching their direction, or when the planning skill needs a target list.
---

# Finding openings for a new graduate

Most new graduates know a dozen famous names and apply where everyone applies. The openings that suit them
are often at employers they have never heard of, or are never posted at all. This skill turns a direction into
**a list of employers worth the user's time, each checked and in the tracker**, and keeps it fresh.

It starts from the direction in the plan (`plan.py show`): the routes, markets, cities and the user's
eligibility. If there is no direction yet, work one out first with the direction skill
(`../direction/SKILL.md`); searching without one fills the tracker with noise.

## 1. Channels, market by market

Use the channels that list **graduate and intern** roles, not only the general job boards. Check each is still
running before you send the user there.

- **China**
  - the employers' own 校招 sites: the source of truth for rounds, dates and roles; every other channel copies
    them, late or wrong;
  - 牛客: 校招 calendars, written-test and interview reports by company (笔经, 面经), and referral posts;
  - the user's university's 就业网 and 宣讲会 schedule, and the 就业质量年度报告 (which employers hired its
    graduates, and in what);
  - 国聘 and provincial 国资委 sites for state-owned employers; 国家公务员局 and provincial 人社 sites for the
    civil service and 事业单位; research institutes' own 人才招聘 pages;
  - 实习僧 and BOSS直聘 for internships and smaller companies; 脉脉 for referrals and how a team really is.
- **Australia**
  - Prosple (GradAustralia) and GradConnection for graduate programs and internships, with intake dates;
  - the user's university careers portal and careers fairs (employers that come to campus want its students);
  - APS Jobs for the public service (mostly citizens only: check before suggesting it);
  - SEEK and LinkedIn for single graduate or junior roles at smaller firms;
  - the employers' own careers pages for programs that close months ahead.
- **Anywhere**: the employers' own pages first, the national graduate boards, and the user's university.

## 2. Find employers the user doesn't know

Building the list is the part new graduates can't do alone. Work outward from what fits the direction:

- **Who hires people like them**: the destinations in their university's graduate reports for their degree;
  where alumni with the same degree work now (LinkedIn's alumni search, 脉脉).
- **Around the names they know**: competitors, suppliers and customers of a company they want; companies
  built on the tools in their projects; companies whose product they use.
- **Lists by kind**: an index's members (ASX 200, 沪深300) for established employers; 专精特新 and industry
  association member lists for strong smaller ones in China; recent funding rounds and investor portfolios for
  startups; government lists of research institutes.
- **The postings already scanned**: companies in the tracker list roles the user hasn't looked at; set
  `hunt.py keywords` from the direction so matching ones come forward.

Aim for **20 to 40 employers** in three bands: a few reaches, most a fair match on the evidence, and some
where an offer is likely. Band them by the user's fit (their evidence against the role's gate), not by fame.

## 3. Check each one before it goes in

An employer only goes into the tracker once you have checked, with sources:

- it hires graduates or interns **in the user's direction, this cycle** (a current posting, or a program page
  for this intake), not just "is a good company";
- the user is **eligible**: visa sponsorship or work-rights rules, citizenship, 届 window, degree or school
  requirements, language;
- it hires **in a city the user can work in** (or remote within their region);
- nothing about it is plainly wrong: it asks applicants to pay (training fees, deposits, "materials"), the
  "graduate role" is commission-only sales, or it can't be found outside its own ads. Leave such ones out and
  tell the user why. The risk skill (`../risks/SKILL.md`) has the full checks and the evidence to cite.

Then add it with `hunt.py add-company` and `fill` as the tracker's note says (its careers sites, logo, where it
hires, the About), so scans keep its roles current from then on. Say in the chat which ones you added, which
band each is in and why, and which you left out.

## 4. The roles that are never posted

Many graduate places, above all at small firms, in research and through referrals, are never advertised or are
filled before the posting goes up.

- **Referrals (内推)**: a referral usually skips the first screen. Find people at the company through alumni,
  牛客 and 脉脉 posts, LinkedIn, and the user's lecturers and classmates. Record a referral code on the role
  with `--referral`.
- **Asking well**: a short message to one person, after reading about their team: who the user is in one line,
  the specific role or team, one concrete reason they fit (from the library), and a small ask (a referral, or 15
  minutes to hear about the work). Draft it in the user's voice; the user sends it, never you.
- **Research and small firms**: ask the user's lecturers about RA work and the companies they work with;
  meetups, hackathons and university clubs where small companies recruit.
- One personal message beats twenty pasted ones. Never draft mass messages.

## 5. Keep it fresh, and feed back

- In a hiring season, look again every week or two: new rounds open, programs close, scans show new roles. The
  daily scan reads the sites already in the tracker; this skill adds the employers that aren't.
- Count what you find: how many open graduate roles match the direction in the user's cities. When the count
  is thin (a handful, or none the user is eligible for), say so with the numbers and suggest looking at the
  direction again with the direction skill, rather than lowering the bar on what goes in.
- Note in the plan (`plan.py note`) what was searched and what was found, so the next search starts from there.
