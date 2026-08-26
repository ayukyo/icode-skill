# 步骤 ppt — PPT 生成（独立交付步骤，不参与 1~6 流程推进）

**命令**: `/icode ppt [自然语言]`
**产出**: `<project_root>/.icode_output/ppt/{工程简名}_{场景关键词}.pptx`（**统一落工程根的 `.icode_output/ppt/`，不放进任何工单目录 `.icode_output_N/`**，与 `/icode limit` 的 `.icode_output/limit.local/` 同一「非工单产物」哲学；`项目`/`模块` 无工单目录，固定落此。示例：`<root>/.icode_output/ppt/{工程简名}_项目.pptx`；附带 `{场景关键词}_edits.json` 可回溯；可选 `ppt/preview/*.png` 渲染自检图）
**会话**: 主会话
**定位**: **独立交付步骤**——用自然语言把 icode 的既有产物/知识库转成真实 `.pptx`。**不创建工单目录、不写 `.ico_metadata.json`、不更新 `completed_steps`/`status`、不参与步骤1~6推进**。4 类场景：**项目 / 模块 / 本次功能开发 / 本次BUG修复**。

> **本质**：本步骤是「内容组织 + 模板替换」两步。内容**必须**来自 icode 已有产物/知识库（先收集再组织），模板**只替换文字不破坏排版**（内置 16 套模板，见 [tools/ppt/](../tools/ppt/README.md)）。

## 0. 前置校验

1. **python-pptx 必需**：`python3 -c "import pptx"` 失败 → 提示 `pip install python-pptx`，缺则本步骤无法产出 .pptx。
2. **渲染自检可选**：LibreOffice（`soffice --version`）+ poppler（`pdftoppm`）存在才做 PNG 预览自检；缺失则跳过渲染，**不阻断**（只出 .pptx，并提示无法自检）。
3. **模板完整性**：`tools/ppt/templates/` 下应有 `INDEX.md` + ≥1 套模板（每套 4 文件：`template.pptx`/`intro.md`/`detail.json`/`preview.png`）。缺失 → 报错提示模板目录不完整。
4. **中文字体**：模板用「微软雅黑」，机器无此字体时渲染预览会缺字；`fc-list | grep -i "yahei\|noto.*cjk\|wenquanyi"` 无命中 → 提示配 fontconfig alias（微软雅黑 → Noto Sans CJK SC），不阻断产出。

## 1. 场景识别（自然语言 → 4 类）

从命令参数自然语言识别目标场景与主题；无参数时按以下默认规则探测。**命名**：`工程简名` = `git rev-parse --show-toplevel` 的 basename；`场景关键词` = 简短标识（`_project`/`_模块`/`_<功能>`/`_<修复>`）。

| 场景 | 识别关键词（示例） | 内容源（**证据来源，禁止编造**） | 页面骨架 |
|------|--------------------|----------------------------------|----------|
| **项目** | 项目 / 工程 / 总体 / 全景 / 介绍 | `~/.claude/icode_data/project_docs/<project_id>/<branch>/` 工程知识库章节 + `git ls-files` 仓库结构 + README（若有） | 封面 / 项目概述 / 总体架构 / 模块地图 / IPC与数据流 / 关键机制 / 现状与路线 |
| **模块** | 模块 / 子系统 / 组件 / <模块名> | `~/.claude/icode_data/project_docs/<project_id>/<branch>/` 中该模块章节（或指定源码目录） | 模块职责 / 对外接口 / 依赖关系 / 边界与约束 / 使用示例 |
| **本次功能开发** | 本次功能 / 开发 / 工单 / 需求（默认，无关键词时） | **最新工单** `.icode_output/.icode_output_N/`（N 最大）产物：`00_init.md` / `01_plan.md` / `03_plan_final.md` / `04_code_review_fix.md` / `06_audit.md` + `git diff` | 需求 / 设计方案 / 核心接口 / 验证与测试 / 交付与后续 |
| **本次BUG修复** | 查BUG / 修复 / 根因 / 缺陷 | `log` 入口产物 `log_analysis.md` + `00_init.md`（根因）+ `08_patch.md`（修复与验证）+ 最新工单终审 | 现象 / 根因分析 / 修复方案 / 回归验证 / 结论与遗留 |

**无参数默认探测**：找最新工单 `.icode_output/.icode_output_N/`——若 `completed_steps` 含 `"log"` → **本次BUG修复**；否则 → **本次功能开发**；无任何工单 → 问用户选「项目/模块/先 init 一个需求」。

**内容源降级**：`项目`/`模块` 若 `project_docs/<project_id>/<branch>/` 对应章节不存在（未跑过 `/icode doc`）→ 降级用 `git ls-files` 仓库结构 + README + 源码探读取证，不阻断；并提示「建议先 /icode doc 生成知识库可获得更全内容」。

**场景判定失败或歧义**（如同时命中多类）→ 问用户选一类，不擅自假设。

## 2. 模板选择（给 2-3 个候选，让用户挑）

1. 读 [tools/ppt/templates/INDEX.md](../tools/ppt/templates/INDEX.md)（16 套模板的风格/主色/适用场景/页数，每套配 `preview.png`）。
2. 按「场景 + 风格」从 16 套里筛 **2-3 个候选**：候选必须与本次场景匹配，且彼此风格/主色互不相同（避免同风同款）。参考：汇报/复盘类 `minimal-business-summary`、`report-massive-models`、`competition-speech`；数据密集 `data-viz-deck`、`report-massive-charts`；架构讲解 `architecture-deck`、`mckinsey-style`、`premium-corp`；学术 `thesis-*`。把每套的一句话特点 + 推荐理由一并列出。
3. **让用户在候选中挑一套**（用户直接指定模板名 → 跳过候选、直接用）。选定后才进入 §3；不要把选择结果锁死在某一套上。

## 3. 生成工作流

> **PPT 内容预压缩（新增）**：在收集材料（§3 第 1 步）之前，对**本次功能开发/本次BUG修复**两类场景的内容源做 cheap-research `summarize` 预压缩——
> - 本次功能开发：5 产物 `00_init.md` / `01_plan.md` / `03_plan_final.md` / `04_code_review_fix.md` / `06_audit.md` + `git diff` 累计常达 30K+ token，先对**每个产物单独**调 `mcp__cheap-research__summarize(text=<产物全文>, max_tokens=800, focus="保留结构：需求要点/ADR/修复方案/验证结论/遗留")` 压到 ≤1K，主代理读摘要 + 关键章节定位即可，节省 ~20K token
> - 本次BUG修复：`log_analysis.md` + `00_init.md`（根因）+ `08_patch.md`（修复）+ 终审 同理对每个产物单独 summarize（focus="保留结构：现象/根因/修复方案/回归验证/遗留"）
> - **⚠️ 截断硬信号（truncation L1）**：summarize 只消费输入前 8000 字符（`truncate_with_meta`），产物超过 8000 字符时返回 `truncated=true` + `consumed_chars`/`source_digest`/`chunk_count`。**`truncated=true` 时禁止把摘要当完整结果消费**——两种合规处理（选一）：① 分块 map-reduce（按 `chunk_count` 分批 summarize 再合并，保序 + 保留结构锚点）；② 主代理对该产物 Read 原文（跳过预压缩）。**不得静默继续**（否则 PPT 内容基于截断摘要，丢章节）。`truncated=false` 才允许直接读摘要做 PPT 组织
> - **项目/模块**两类场景：内容源是 project_docs 章节（已是结构化文档，每章前 50 行自带身份证），**不预压缩**（避免结构损失 + 不污染章节布局），主代理按章节直接用
> - **降级**：cheap-research 不可用 → 跳过预压缩，走原流程（主代理 Read 全文）；写 `[降级-PPT 预压缩 summarize 不可用]`
> - **缓存复用**：若本 ticket 已通过其他步骤调过相同产物的 summarize（如 02_review.md 第 453 行末尾 `_review_summary.md` 的输入），主代理**优先复用 SKILL.md「cheap-research 14 工具会话内缓存」段的 `.cheap_research_cache.json` 命中条目**，避免重复调 summarize
> - **前提契约（产物缺失降级）**：5 个产物中部分不存在 → 主代理**只对存在的产物**调 summarize，缺失的产物主代理直接 Read 原文（不假装预压缩）；写 `▶ PPT 预压缩部分跳过：{产物名} 不存在`，最终节省按实际存在产物计算

```bash
# 1) 按「内容源」列收集材料（Read/Grep 实证，摘录带来源标注）
# 2) 选模板 + 读 $TPL/intro.md + $TPL/detail.json（每页角色/槽位/容量）
# 3) mkdir -p .icode_output/ppt（build 不会自动建目录）
#    AI 组织内容 → 写 .icode_output/ppt/{场景关键词}_edits.json：
#    { "template_slug": "...", "selected_slides": [1,2,3,...],
#      "edits": [ {"slide": N, "slot_id": "...", "new_text": "..."} ] }
# 4) 构建 .pptx（不加 --strict，出框只提示不阻断）
mkdir -p .icode_output/ppt
python3 tools/ppt/scripts/build_pptx.py \
    tools/ppt/templates/$TPL/template.pptx \
    .icode_output/ppt/{场景关键词}_edits.json \
    .icode_output/ppt/{工程简名}_{场景关键词}.pptx \
    --detail tools/ppt/templates/$TPL/detail.json
# 5) （可选）渲染自检：每页一张 PNG，肉眼过一遍排版/超框/占位残留
python3 tools/ppt/scripts/render_slides.py .icode_output/ppt/{工程简名}_{场景关键词}.pptx .icode_output/ppt/preview --dpi 144
```

**页面组织规则**：
- 按 `detail.json` 的 `page_roles` 用角色页：`cover` 空 → 从第一张内容页开始；`agenda` 空 → 不强加目录；`ending` 空 → 以最后内容页收尾。**模板有什么角色就用什么角色，不硬造页面。**
- 章节名前后呼应：目录改了的章节名，分章扉页/面包屑同步改。
- 图形/图表**通常无法同步**：模板里的装饰性进度条/圆环/流程箭头是固定形状，`build_pptx.py` 只改文字、不改图形数据。若必须改数据，用模板自带占位数字替换为真实数值文字（仍是文字替换）；装饰图形不改。（build 只支持 `--detail`/`--strict`/`--no-lint`/`--dry-run`，无图表数据接口）

## 4. 内容铁律（对齐 icode 反偷懒哲学；违反一条即不合规）

1. **来源真实**——每页内容必须有来源（产物/知识库/git/验证输出），**禁止编造数据、禁止凭印象写数字**；关键数字给来源标注（如 `06_audit.md` 的测试结果、`log_analysis.md` 的根因结论）。
2. **无占位残留**——成品 .pptx 不得出现任何模板占位词（`Question 1`/`Vivamus`/`项目名称`/`Key Words` 等）。抽检（.pptx 是 zip，对解压内容 grep）：`unzip -p {产出}.pptx "ppt/slides/slide*.xml" \| grep -c "Vivamus\|项目名称\|Key Words"`，命中 >0 → 该页占位未替换，补 edits。**注意：产物 .pptx 内部 slide 部件保留模板原始编号（`selected_slides` 非连续时如 `slide1/2/15/35/36.xml`），不按成品序号重排**；抽检/按页核对必须用 `slide*.xml` 通配或对照渲染预览图（预览图 `slide-N.png` 才按成品顺序），不能按成品序号猜文件名（如猜 `slide3.xml` 会落空）。
3. **禁止省略号截断**——内容过长用**精炼重写**（真正概括），绝不 `...`/`等等` 硬凑；`max_chars` 是软参考，轻微超出可接受（模板容量已留 20% 余量）。
4. **不破坏排版**——只改文字，不改形状位置/大小/颜色/字号；**同级标题字号保持一致**，不为塞字单独改小某处字号。
5. **产物不是代码事实**——引用 `project_docs` 知识库内容前须 Read/Grep 实证，文档是快照可能过时。
6. **可回溯**——`edits.json` 与 .pptx 同存，用户要改文字重跑即可，不重复收集材料。

## 5. 产出与收尾

- 完成后输出：产物路径 + 用了哪套模板 + 页数 + 内容来源清单 + （若跳过渲染）未自检提示。
- **不写任何工单 metadata**，不创建 `.icode_output_N/` 目录（`ppt/` 子目录直接建在工程根的 `.icode_output/ppt/`）。
- 若用户后续说「改 PPT」→ 直接改既有 `edits.json` 重跑构建，不重新收集。

## 6. 许可声明

内置 16 套模板来自第三方设计师作品，**仅供个人学习与研究，严禁商业用途**（含商业演示、企业内部营利性使用、再分发/并入付费产品），详见 [tools/ppt/NOTICE](../tools/ppt/NOTICE)。产出 PPT 仅限本工程内部使用。生成脚本（`tools/ppt/scripts/`）为 MIT，可自由使用。
