"""The visas a profile's work record can name (resume.yaml profile.work, jobhunt/natures.py), picked from a list
instead of typed (2026-11-14: a visa's name is a fact that must not be misspelt).

Each is the issuing government's own name for it, as on its immigration site, with the name Chinese speakers
use for it and the words people search it by (its subclass, its letters). `status` is the record's status it
belongs under: "work" (a visa that lets one work) or "study" (a student visa). A country not listed, or a visa
not listed, is still typed by hand; what is kept is the name itself, so a record reads the same to Claude.

Country catalogs are data, so one can move into a country's plugin later as it is.
"""
from __future__ import annotations

# country, status, official name, Chinese name, search words
VISAS = [
    # Australia: immi.homeaffairs.gov.au
    ("AU", "work", "Temporary Graduate visa (subclass 485)", "485 毕业生工作签证", "485 tr post-study psw"),
    ("AU", "work", "Skills in Demand visa (subclass 482)", "482 紧缺技能签证（雇主担保）", "482 sid tss sponsor"),
    ("AU", "work", "Skilled Work Regional (Provisional) visa (subclass 491)", "491 偏远地区技术移民临时签证", "491 regional"),
    ("AU", "work", "Skilled Employer Sponsored Regional (Provisional) visa (subclass 494)", "494 偏远地区雇主担保临时签证", "494 regional"),
    ("AU", "work", "Work and Holiday visa (subclass 462)", "462 打工度假签证", "462 whv working holiday"),
    ("AU", "work", "Working Holiday visa (subclass 417)", "417 打工度假签证", "417 whv"),
    ("AU", "work", "Temporary Activity visa (subclass 408)", "408 临时活动签证", "408"),
    ("AU", "study", "Student visa (subclass 500)", "500 学生签证", "500 student"),
    # New Zealand: immigration.govt.nz
    ("NZ", "work", "Post Study Work Visa", "毕业后工作签证", "pswv post-study"),
    ("NZ", "work", "Accredited Employer Work Visa", "认证雇主工作签证", "aewv"),
    ("NZ", "work", "Working Holiday Visa", "打工度假签证", "whv"),
    ("NZ", "study", "Fee Paying Student Visa", "学生签证", "student"),
    # United Kingdom: gov.uk/browse/visas-immigration
    ("GB", "work", "Graduate visa", "毕业生签证（PSW）", "psw post-study"),
    ("GB", "work", "Skilled Worker visa", "技术工作签证", "tier 2 sponsor"),
    ("GB", "work", "Health and Care Worker visa", "医疗护理工作签证", "nhs"),
    ("GB", "work", "High Potential Individual visa", "高潜力人才签证", "hpi"),
    ("GB", "work", "Global Talent visa", "全球人才签证", "tier 1 exceptional"),
    ("GB", "work", "Youth Mobility Scheme visa", "青年交流计划签证", "yms tier 5"),
    ("GB", "study", "Student visa", "学生签证", "tier 4"),
    # United States: uscis.gov, travel.state.gov
    ("US", "work", "H-1B Specialty Occupation", "H-1B 专业人才工作签证", "h1b"),
    ("US", "work", "L-1 Intracompany Transferee", "L-1 跨国公司内部调派签证", "l1"),
    ("US", "work", "O-1 Individual with Extraordinary Ability", "O-1 杰出人才签证", "o1"),
    ("US", "work", "E-3 Specialty Occupation (Australian citizens)", "E-3 澳大利亚公民专业人才签证", "e3"),
    ("US", "work", "F-1 Optional Practical Training (OPT)", "F-1 学生毕业实习（OPT）", "opt stem ead"),
    ("US", "study", "F-1 Student", "F-1 学生签证", "f1"),
    ("US", "study", "J-1 Exchange Visitor", "J-1 交流访问学者签证", "j1"),
    # Canada: canada.ca/immigration
    ("CA", "work", "Post-Graduation Work Permit (PGWP)", "毕业后工作许可", "pgwp"),
    ("CA", "work", "Employer-specific work permit", "雇主指定工作许可（LMIA）", "lmia closed"),
    ("CA", "work", "Open work permit", "开放式工作许可", "owp spousal"),
    ("CA", "work", "International Experience Canada (Working Holiday)", "国际体验加拿大（打工度假）", "iec whv"),
    ("CA", "study", "Study permit", "学习许可", "student"),
    # Singapore: mom.gov.sg, ica.gov.sg
    ("SG", "work", "Employment Pass", "就业准证（EP）", "ep"),
    ("SG", "work", "S Pass", "S 准证", "spass"),
    ("SG", "work", "Overseas Networks & Expertise Pass", "海外网络与专业人才准证（ONE Pass）", "one pass"),
    ("SG", "work", "Training Employment Pass", "培训就业准证", "tep"),
    ("SG", "work", "Work Holiday Pass", "工作假期准证", "whp"),
    ("SG", "study", "Student's Pass", "学生准证", "student pass"),
    # Hong Kong: immd.gov.hk
    ("HK", "work", "Immigration Arrangements for Non-local Graduates (IANG)", "非本地毕业生留港 / 回港就业安排（IANG）", "iang"),
    ("HK", "work", "Admission Scheme for Mainland Talents and Professionals", "输入内地人才计划（专才）", "asmtp"),
    ("HK", "work", "General Employment Policy", "一般就业政策", "gep"),
    ("HK", "work", "Top Talent Pass Scheme", "高端人才通行证计划（高才通）", "ttps"),
    ("HK", "work", "Quality Migrant Admission Scheme", "优秀人才入境计划（优才）", "qmas"),
    ("HK", "work", "Technology Talent Admission Scheme", "科技人才入境计划", "techtas"),
    ("HK", "study", "Student visa / entry permit", "学生签证 / 进入许可", "student"),
    # Germany: make-it-in-germany.com, AufenthG
    ("DE", "work", "EU Blue Card (Blaue Karte EU)", "欧盟蓝卡", "blue card blaue karte"),
    ("DE", "work", "Residence permit for skilled workers (§ 18a / 18b AufenthG)", "技术工人居留许可", "fachkraft 18a 18b"),
    ("DE", "work", "Residence permit to seek a job after graduating (§ 20 AufenthG)", "毕业后求职居留许可", "18 months job search"),
    ("DE", "work", "Opportunity Card (Chancenkarte)", "机会卡", "chancenkarte"),
    ("DE", "study", "Residence permit for study (§ 16b AufenthG)", "学习居留许可", "student studium"),
    # Japan: isa.go.jp
    ("JP", "work", "Engineer / Specialist in Humanities / International Services (技術・人文知識・国際業務)", "技术・人文知识・国际业务签证", "gijinkoku 技人国"),
    ("JP", "work", "Highly Skilled Professional (高度専門職)", "高度专门职签证", "hsp 高度人才"),
    ("JP", "work", "Designated Activities, job hunting after graduation (特定活動)", "特定活动（毕业后求职）", "tokutei katsudo"),
    ("JP", "work", "Specified Skilled Worker (特定技能)", "特定技能签证", "ssw"),
    ("JP", "work", "Working Holiday (ワーキング・ホリデー)", "打工度假签证", "whv"),
    ("JP", "study", "Student (留学)", "留学签证", "ryugaku"),
    # South Korea: hikorea.go.kr
    ("KR", "work", "E-7 Specially Designated Activities", "E-7 特定活动签证", "e7"),
    ("KR", "work", "D-10 Job Seeking", "D-10 求职签证", "d10"),
    ("KR", "work", "H-1 Working Holiday", "H-1 打工度假签证", "h1 whv"),
    ("KR", "study", "D-2 Student", "D-2 留学签证", "d2"),
    # Ireland: irishimmigration.ie, enterprise.gov.ie
    ("IE", "work", "Stamp 1G (Third Level Graduate Programme)", "Stamp 1G 毕业生签证", "1g graduate"),
    ("IE", "work", "Critical Skills Employment Permit", "关键技能工作许可", "csep"),
    ("IE", "work", "General Employment Permit", "一般工作许可", "gep"),
    ("IE", "study", "Stamp 2 (student)", "Stamp 2 学生签证", "stamp 2"),
    # Netherlands: ind.nl
    ("NL", "work", "Highly skilled migrant (kennismigrant)", "高技术移民签证", "kennismigrant hsm"),
    ("NL", "work", "Orientation year for graduates (zoekjaar hoogopgeleiden)", "毕业生求职年签证", "zoekjaar orientation"),
    ("NL", "work", "EU Blue Card", "欧盟蓝卡", "blue card"),
    ("NL", "study", "Residence permit for study", "学习居留许可", "student"),
    # China, for a foreign national: nia.gov.cn, fwp.safea.gov.cn
    ("CN", "work", "Work permit and work-type residence permit (Z visa)", "外国人工作许可与工作类居留许可（Z 字签证）", "z visa"),
    ("CN", "work", "Talent visa (R visa)", "人才签证（R 字签证）", "r visa"),
    ("CN", "study", "Study-type residence permit (X1 visa)", "学习类居留许可（X1 字签证）", "x1 student"),
]


def catalog(lang: str = "") -> list[dict]:
    """[{country, status, name, desc, find}] for the window: `desc` is the Chinese name when the interface is
    Chinese, so a name a Chinese speaker knows the visa by shows under the government's own."""
    zh = (lang or "").startswith("zh")
    return [{"country": cc, "status": st, "name": name, "desc": zh_name if zh else "", "find": f"{zh_name} {words}"}
            for cc, st, name, zh_name, words in VISAS]
