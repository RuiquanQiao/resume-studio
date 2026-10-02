---
name: resume-studio
description: 带图形界面的简历工具。经历库、事实层备注、按条目挑选、LaTeX 模板切换、一页预览、导出 PDF，数据全部在一个 resume.yaml 里，人在网页里微调，Claude 直接读写同一份文件。用户提到做简历、改简历、打开简历界面、resume studio、resume.yaml、经历库、导出简历 PDF、压到一页、润色某条经历时使用。Resume builder with a local web UI backed by resume.yaml.
---

# Resume Studio

一个带界面的 Skill：人在本地网页里编辑和预览，Claude 直接改 `resume.yaml`。两边改的是同一个文件，界面会监听文件变化并自动刷新和重新渲染。

Skill 根目录记为 `<skill>`，也就是这份 SKILL.md 所在的目录。

## 被调用时先做什么

- **`/resume-studio` 不带参数**，或用户说「打开简历界面」：不要先提问，直接打开窗口（见下一节），告诉用户窗口已经打开，然后停下来等用户下一步。
- **`/resume-studio <任务>`**，比如 `/resume-studio 润色 Paper2Exam` 或 `/resume-studio 把 JOB-001 压到一页`：直接按下面的编辑规则去改 `resume.yaml`。界面如果开着，会自动刷新；没开也不用特意去开。
- 数据文件路径：用户明确指定了就用用户的（加 `--data`）；否则一律用 `<skill>/resume.yaml`（已被 git 忽略）。不要因为当前工作目录里碰巧有个 `resume.yaml` 就去改它。

## 打开界面

用户平时双击 `<skill>/Resume Studio.exe`（或固定到「开始」的它）打开独立窗口。你替用户打开时，用 Bash 工具以 `run_in_background: true` 运行：

```bash
python "<skill>/scripts/studio.py" --window
```

- 窗口是独立进程，不依附于当前会话：会话结束、用户切换账号，窗口都还在。
- 同一个数据文件的窗口已经开着时，脚本只会把它调到前面，不会再开第二个。重复调用是安全的。
- 数据文件不存在时会自动创建一个空骨架。用户指定了别的文件时，加 `--data <绝对路径>`；这只对这一次生效，不会被记住。
- 没有图形界面的环境（或用户要求用浏览器）去掉 `--window`，会改为在浏览器里打开，并打印 `UI running at http://...`。
- 所有依赖都装在 `<skill>/.venv`，pip 缓存在 `<skill>/.cache`；第一次运行会自动创建，不碰全局环境。LaTeX 编译需要系统里有 `xelatex`（TeX Live 或 MiKTeX）。

## 窗口里的对话就是 Claude Code

窗口左栏的「对话」页签，每段对话都是一个真正的 Claude Code 会话：`claude -p --resume <session>`，在数据文件所在目录运行，加载用户的 CLAUDE.md 和已装的 Skill，没有替换系统提示词，也不指定回复语言。窗口只通过 `--append-system-prompt-file` 附上一段说明：用户正在看 ALL 还是哪个岗位、哪个条目，以及数据文件和这份 SKILL.md 的位置。所以在窗口里被调用时，照常按下面的规则工作即可。

## 不开界面时自己检查结果

```bash
python <skill>/scripts/render.py --data <path>                 # 渲染 ALL 和所有岗位，报告页数
python <skill>/scripts/render.py JOB-001 --data <path>         # 只渲染一个岗位
python <skill>/scripts/render.py JOB-001 --export              # 同时把 PDF 和 .tex 复制到导出目录
python <skill>/scripts/render.py --check                       # 只校验数据
```

输出里 `OVER ONE PAGE` 表示超过一页。改完内容想确认能放进一页时，就用这个命令。

## 数据格式（resume.yaml）

核心是「全集 → 子集」：`entries` 是经历库（全集，用户的全部真实经历）；`versions` 里除了 `ALL`，每一项都是一个**岗位**（AI Agent 工程师、高性能计算、管培生……），从全集里挑一部分做成那个方向的简历。

```yaml
schema_version: 1
settings:
  languages: [en, zh]        # 内容语言；多于一种时，文本字段按语言分开写
  export_dir: exports        # 导出目录，相对于 resume.yaml
profile:
  name: {en: Alex Chen, zh: 陈亚历}
  contacts:                  # 全部联系方式；岗位可以隐藏其中几项
    - {label: email, value: a@b.com, url: 'mailto:a@b.com'}
  notes: ...                 # 事实层
sections:                    # 板块的默认顺序
  - {id: projects, title: {en: Projects, zh: 项目经历}, kind: timeline}   # timeline | list
entries:                     # 经历库：全部真实经历，不受一页纸限制
  - id: EXP-002              # 内部 ID：稳定、和板块无关，界面上不显示（界面按板块各自编号）
    section: projects
    title: ...               # 公司 / 学校 / 项目名；list 板块里是类别名
    subtitle: ...            # 职位 / 学位 / 角色
    location: ...
    start: 2025-08           # YYYY-MM；结束可以写 present
    end: present
    date: ...                # 可选的自由文本，会覆盖 start/end
    link: https://...
    tech: [Next.js, Prisma]  # list 板块里是具体条目
    bullets:
      - {en: 'Built a **multi-stage** pipeline ...', zh: 搭建**多阶段**流水线……}
    notes: |                 # 事实层：真实情况，永远不会渲染
      ...
    evidence: [E:/Forge/Paper2Exam]   # 事实层：可以去读的本地路径
versions:
  - id: ALL                  # 保留项：全集预览，固定排第一，不能删、不能改 ID
    label: 全部经历
    lang: en
    template: classic
    headline: ''             # 没有 entries：永远显示全部经历（按经历库顺序）和全部联系方式，不受一页限制
    layout: {}
  - id: JOB-001              # 岗位：只引用条目，不复制内容
    label: AI Agent 工程师
    lang: en
    template: classic        # classic | modern | timeline（见 resume/templates/latex）
    headline: {en: 'AI Agent Engineer · LLM tooling', zh: AI Agent 工程师}   # 这个岗位的一句话介绍
    entries: [EXP-001, EXP-002]   # 选中的条目；同一板块内按这里的顺序排列
    sections: [education, projects]   # 可选：这个岗位的板块顺序
    hide_sections: [awards]  # 可选
    hide_contacts: [phone]   # 可选：按 label 隐藏联系方式
    layout: {font_size: 10, margin_x: 1.4, margin_y: 1.2, line_spread: 1.0,
             section_sep: 6, entry_sep: 3, item_sep: 1, accent: 1F4E79, paper: a4,
             font: '', cjk_font: ''}   # 不写的键用默认值
```

- **文本字段**可以是普通字符串（所有语言通用），也可以是 `{en: ..., zh: ...}`。只改用户要求的那种语言，其他语言原样保留。
- **行内标记**：`**加粗**`、`*斜体*`、`[文字](链接)`。其他字符按字面输出，LaTeX 转义由渲染器负责，不要在 YAML 里写 LaTeX 命令。

## 编辑规则（Claude 必须遵守）

1. **直接改 resume.yaml**，只动任务相关的部分。保留其他条目、注释、字段顺序和写法风格（行内 `{}` 或块状都行）。每次保存前，上一版会存到数据文件旁边的 `.studio/history/`，需要回退时从那里找。
2. **事实边界**：展示层（title、subtitle、bullets……）写出的任何事实、数字、职责、规模，都必须能被 `notes` 或 `evidence` 支撑。拿不准就不写，或者保守表述，并在回复里告诉用户缺什么信息。这是用户对「律师」说的实话，拿来包装，但不编造。
3. **不要改事实层**：除非用户明确要求，不改 `notes` 和 `evidence`；也永远不要把 notes 的内容原样搬进 bullets。
4. **ID 要稳定**：不要改已有条目的 `id`，换板块也不改。新增条目时用全局下一个 `EXP-xxx`。删除时要同步更新各岗位的 `entries`。ID 只给数据引用用，用户看不到：回复里用经历的名称（或「项目经历第 2 条」这种板块内序号）来称呼，不要说 EXP-xxx。
5. **全集与子集**：新经历只进经历库（`ALL` 自动包含）；要不要放进某个岗位，由用户决定。不要给 `ALL` 写 `entries`，也不要把求职方向写进 `profile`，它属于各岗位的 `headline`。
6. **一页纸**只针对岗位：用户要求压到一页时，先调岗位的 `layout`（字号、间距、边距），再精简 bullets 的措辞，最后才建议少选条目。选哪些条目由用户决定，不要擅自从岗位的 `entries` 里删掉经历。`ALL` 多页是正常的。
7. 通过界面触发的任务，不需要自己编译 PDF，界面会自动重新渲染。在终端里工作时，可以用 `render.py` 检查页数。

## 常见任务

- **录入新经历**：按「一次只问一个问题」的访谈方式，先问清真实情况，写进 `notes`，再根据 notes 起草 bullets。可以借鉴本机已装的 offer-toolkit-skill、resume-tailoring 里的追问方法和写作规则。
- **润色某一条**：读这一条的 notes 和 evidence（需要时去读代码仓库和 README），改写 bullets。要点用动词开头，突出本人做的决策和结果，每条尽量一行。
- **做一个新岗位**：在 `versions` 末尾追加一项，ID 用下一个 `JOB-xxx`，写好 `label` 和 `headline`，`entries` 列出要选的条目 ID。

## 目录结构

- `Resume Studio.exe`：双击打开窗口（源码在 `launcher/`，图标在 `assets/`，用 `scripts/build_launcher.py` 重新生成）。
- `resume.yaml`、`.studio/`、`exports/`：用户自己的数据、对话记录和历史版本、导出的 PDF，都被 git 忽略。
- `core/`：和简历无关的「Skill + 界面」公共层，包括 YAML 存储、文件监听、SSE 推送、HTTP 服务、Claude Code 会话（`chat.py`）。
- `resume/`：简历业务，包括数据模型、渲染器（`render/`，目前是 LaTeX，接口上预留了 Typst）、模板（`templates/latex/*.tex.j2`）、API、服务组装（`app.py`）。
- `web/`：无需构建的前端。
- `scripts/`：`studio.py`（启动，`--window` 为独立窗口）、`render.py`（命令行渲染）。
- `tests/` 与 `TESTING.md`：自动化测试和试用清单。改代码后按 TESTING.md 跑一遍再交付。
