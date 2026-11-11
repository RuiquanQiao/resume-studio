# Civil service exams (考公考编): the guide

Read once per chat before working on this plugin's records (`civil-service` / `positions`).

## The exams

- **国考** (national civil service exam, 中央机关及其直属机构公务员招考): announced by the 国家公务员局
  (scs.gov.cn) usually in October, registration online for about ten days, the written exam (行测 + 申论)
  usually late November or early December, interviews in the following spring.
- **省考** (provincial exams): most provinces sit a joint written exam (省考联考) in spring (often March), some
  have their own dates; announced by the provincial 公务员局 / 人社厅 and the 组织部.
- **选调生**: graduates selected for grassroots posts that lead into the civil service; 定向选调 is limited to
  named universities (often 双一流 and some overseas ones), 普通选调 is broader. Rounds in autumn and spring,
  announced by the province's 组织部; often they come to campuses and require 中共党员 or student-leader roles.
- **事业单位** (public institutions): joint exams (分类考试, A–E 类 by kind of post) in spring and autumn in many
  provinces, plus each institution's own recruitments; announced by the 人社局.
- **教师编** (teaching posts): by city or district education bureaus, often spring; the 教师资格证 is required or
  must be obtained by a date.
- **军队文职**: the armed forces' civilian posts, a national exam once or twice a year.

Dates move every year: always take them from the current year's announcement. If this year's isn't out, say so
and leave the date empty; the previous year's dates are only a hint for when to look again.

## Position tables (职位表)

Each announcement comes with a table (an Excel file or a search page) of thousands of positions. Read it and filter
by the user's own data, column by column:

- 学历 / 学位: the degree level and whether a 学位 is required; 全日制 or not.
- 专业: the majors allowed, matched against the national catalogue (专业目录); an overseas degree's major is
  matched by its content, and many tables accept "相近专业" only when the notice says so. When it's unclear, the
  hiring body's phone number in the table is the way to ask; tell the user.
- 政治面貌 (中共党员, 共青团员), 基层工作经历 (often 2 years, which new graduates lack), 应届毕业生 only,
  age (usually under 35), gender if stated, 户籍 for some local posts, 英语 levels (CET-4/6; for an overseas degree
  check whether IELTS / TOEFL counts).
- 招录人数 and, after registration opens, the published 报名人数 / 竞争比 (applicants per place) for the user's
  shortlist.

Add only the positions the user can apply for (one record each; `code` the position code, `unit` the hiring body,
`requirements` the table's conditions as written). Tell the user how many positions matched and why the near
misses failed.

## Overseas graduates

- **学历认证**: an overseas degree needs the 教育部留学服务中心 (CSCSE, cscse.edu.cn) verification; it takes weeks,
  so start early. Without it, eligibility checks (资格审查) usually fail.
- **应届生 status**: the rules for when an overseas graduate counts as 应届生 (often: back in China and not yet
  employed within a set period after graduation, by the date on the degree certificate) are set in each year's
  announcement and differ between exams: read the current one and quote it.
- Graduation dates abroad (e.g. December or July) decide which round can be taken: check the announcement's
  deadline for having the degree.

## Filling the records

- `exam` the kind of exam, `apply_by` the registration deadline, `written` / `interview` the dates as announced,
  `notice` the official announcement (a .gov.cn page), `quota` the places.
- `stage` follows the user; only move it when they say what happened. After an exam, a note of the score and rank
  goes into `notes` with the date.
- Facts are dated and sourced; a rule from memory is worse than an empty field.
