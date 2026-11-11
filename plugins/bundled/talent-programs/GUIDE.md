# Talent programmes (人才引进): the guide

Read once per chat before working on this plugin's records (`talent-programs` / `programs`).

## What counts

A talent programme is a published policy that gives a qualified person something for coming to work in a place
or an institution: money (安家费 settling-in allowance, 购房补贴 housing subsidy, 生活补贴 living allowance,
科研启动经费 research start-up funds), a status (落户 hukou, 编制 a permanent post, 人才认定 a talent grade that
unlocks schooling, housing and tax benefits), or a fast track (免笔试 no written exam, 直接考核 direct assessment).

Kinds, by who issues them (the record's `level`):

- **National** (国家级): national programmes for high-level and young talent, run by ministries or the national
  science foundation; for researchers with strong records. Rare for new graduates.
- **Provincial / city** (省级 / 市级): the most relevant for graduates. Cities compete for graduates with
  人才新政: hukou for degree holders, cash for bachelor's/master's/PhD graduates who start work and pay social
  insurance there, rental subsidies, talent apartments. Typical issuers: 人力资源和社会保障局 (HRSS bureau),
  人才工作领导小组办公室 / 人才办, 组织部.
- **Overseas graduates** (留学人员): many cities have rules just for them, e.g. Shanghai's 留学回国人员落户, Beijing's
  留学人员落户 and the 留学人员创业园 incentives; they usually require the degree to be verified (学历学位认证) by
  the 教育部留学服务中心 (CSCSE, cscse.edu.cn) and often a QS-ranked university, a time limit after graduation,
  and full-time study abroad for a minimum period.
- **University / institute** (高校 / 科研院所): 高层次人才引进 posts with 编制 and research funds; mostly for PhD
  holders; the notice is on the university's 人事处 (HR office) site.
- **Employer** (企事业单位): state-owned enterprises' and public institutions' 人才引进 recruitments (often
  "高层次人才" or "急需紧缺人才" with a simplified exam).

## Where to look (official sources first)

- the city's or province's HRSS bureau site and its 人才 portal; the 人才办's WeChat articles are often the
  first announcement, but find the matching page on a .gov.cn site for the record's `notice`;
- the university's 人事处 / 人才招聘 page; the institute's 招聘 page;
- for overseas graduates: the city's 留学人员 page and CSCSE's verification rules.

Third-party summaries (公众号, forums, 知乎) are leads, never the source: open the official text, check the year
and the version (policies are revised every year or two, and a 2023 rule may be gone).

## Checking eligibility against the user

Read the user's resume data (profile, education, experience) and their notes first. Check each condition in the
notice against it, condition by condition: degree level and whether it is full-time (全日制), the university's
ranking if one is required, the major if restricted, age limits (often 35 or 40 for 青年人才), how long after
graduation, whether they must sign a contract and pay social insurance in the city first, hukou or residence
status. Write what you found in `requirements` as the notice says it, and in `notes` how the user meets each one
(or which one is unclear and needs asking the issuer). When a condition can't be checked from the data, ask the
user, one question at a time.

## The calendar

There is no single season: city policies are open all year (applied for after starting work), while university
and enterprise recruitments run in rounds, often spring (March–May) and autumn (September–November), and national
programmes have one window a year. Always take the dates from the current year's notice; if the notice for this
year isn't out, say so and leave `apply_by` empty rather than guessing from last year.

## Filling the records

- One record per programme (not per city): `title` is the programme's own name, `issuer` the body that issues it,
  `region` the city or province.
- `benefits` and `requirements`: short, factual, with the amounts and conditions as stated; the notice's url in
  `notice`; anything uncertain marked as such in `notes` with today's date.
- `stage` follows the user: `watching` when found, `preparing` when they decide to apply, and so on; only move
  it when the user says what happened.
- `key_date`: the next date that matters after applying (an interview, a defence, a materials check).
