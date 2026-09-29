# Resume Studio

一个带图形界面的 Claude Code Skill：人在本地网页里编辑、挑选、预览简历，Claude Code 直接读写同一份 `resume.yaml`。

也是「带 UI 的 Skill」的第一次尝试：`core/` 是跟简历无关的公共层（文件存储、监听、SSE、HTTP、`claude -p`），以后做别的带界面的 Skill 可以直接复用。

## 启动

```bash
python scripts/studio.py --data E:/Resume/resume.yaml
```

需要 Python 3.10+、`xelatex`（TeX Live / MiKTeX），以及已登录的 Claude Code（只有 AI 功能需要）。`jinja2`、`ruamel.yaml` 缺失时会自动安装。

## 功能

- 经历库：每条经历分成展示层和事实层（`notes` / `evidence`）。事实层不会被渲染，只给 AI 看。
- 版本：勾选要导出的经历，调整顺序，切换模板（Classic / Modern / Timeline）和语言。
- 版面：字号、行距、边距、各种间距、强调色、纸张、字体都用滑块或输入框手动调，实时预览，超出一页时提示。
- AI：「润色这一条」或底部输入框下任意指令，都是调用本机的 Claude Code；改完显示对比，可以一键撤销。
- 协作：Claude 在终端里改文件，界面自动刷新；同一条同时被两边修改时，会提示冲突。
- 导出：PDF 和 .tex 源文件一起导出到 `settings.export_dir`。

数据格式和 Claude 的编辑规则见 [SKILL.md](SKILL.md)。
