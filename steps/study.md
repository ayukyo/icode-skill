# 步骤 study — 从工单代码提炼通用技术文章

**命令**：`/icode study [--ticket <id>] [--library <绝对目录>] [主题]`

**定位**：独立、手动触发的学习步骤。从真实代码发现算法、语言语义、架构或工程方法，写成脱离原工程也能阅读的中文文章。它不创建工单，不参与步骤 0~6，不修改工程代码、`status/completed_steps/delivery_verdict`、全局工单索引或 Skill；命令正文不回写工单 metadata，从 UI 启动时宿主仍按现有控制面记录 Agent 执行回执。`/icode learn` 仍专门处理运行观测到 Skill 候选；`/icode doc` 仍维护工程专属知识库；`/icode readme` 仍生成交付报告。

## 强制思考前置

**强制思考前置**（不可跳过，缺证据视为不合规；按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行，先按 reasoning gate 分级再选载体）：本步骤默认等级 **L1**，决策字段 = 选题依据、旧章匹配、通用性证据、公开边界、剩余不确定性。L1 摘要放在库外私有来源 JSON 的 `decision_record`，不写目标工单；命中 L2/L3 升级触发器时照常执行对应思考，不因本步骤独立而豁免。+ Read [references/project_intake.md](../references/project_intake.md) 完整内容。

## 1. 确定输入和知识库

1. 按 `project_intake.md` 将请求路径解析为**实际工程根**，显示“请求路径 → 实际工程根”。用户指定工单时，用 `python3 tools/icode_control.py resolve-ticket --workspace <工程根> --ticket <id>`；否则优先用会话绑定工单，最后才从当前工程选择最新且有 `code_files` 的唯一工单。用户命名工单优先，不按目录时间覆盖。身份歧义必须停下列候选。
2. 工单须是 `code_done`、`deepcheck_in_progress`、`deepcheck_done` 或 `completed`，`code_files` 非空。阅读存在的计划、代码复检、终审、patch 产物理解“为何如此设计”，但以**当前源码与测试**核对事实。绑定实际 checkout/worktree、逐仓 Git root/branch/HEAD/dirty 和本次 diff；大仓只扫描相关代码及调用链，禁止无界全树灌入。
3. 知识库路径由 `python3 tools/knowledge_library.py resolve [--library <目录>]` 解析：显式参数优先，其次用户配置 `~/.claude/icode_data/knowledge_config.json`，否则使用 `~/.claude/icode_data/knowledge/`。这些是 ICODE 的全局配置与默认产物位置；配置中的 `library_root` 才是每个人自己的知识库位置，不得在开源 ICODE 中写入某位用户的路径。用户配置目录必须存在；Git 可选。输出“输入工程根 → 工单 → 知识库根”，禁止把文章误写到工程、ICODE 安装副本或源码仓。每人可用 `python3 tools/knowledge_library.py configure --root <已有目录>` 绑定自己的本地 Git 仓库；该命令不触发 clone/fetch/commit/push。

首次手工配置可参考 [templates/knowledge_config.json.template](../templates/knowledge_config.json.template)：复制到上述配置文件路径，把 `library_root` 改成**已存在的绝对目录**，再运行 `resolve` 校验。模板的路径仅是占位值，不能原样使用；已有配置文件不得用模板覆盖。使用 `configure --root` 时工具会直接生成同一格式的配置，无需手工复制模板。

## 2. 先查已有卷章，再决定动作

运行 `python3 tools/knowledge_library.py catalog [--library <目录>]`，并对标题、标签和章节正文做有界 `rg` 检索。语义相近时必须阅读旧章后决定：

| 判断 | 动作 |
|---|---|
| 已覆盖且本次没有新见解 | `reuse`：报告旧章路径，不重复写 |
| 同一主题有新边界、反例或更清楚的讲法 | `revise`：保留原章手工内容，精确修订 |
| 主题独立但属于旧卷 | `add`：旧卷新增一章，`related` 指向关联章 |
| 旧卷都不合适 | `add`：新卷首章 |

按“帮助读懂本次代码、跨项目复用、证据充分”排序，每次最多 3 章。用户指定主题仍需真实源码支撑；无通用内容就报告无候选，不凑文章。卷名/章名采用稳定的小写英文 slug，与某工程、产品、工单无关。阅读顺序由公开 `.meta.json` 的 `order` 控制，不用文件名序号；工具生成 `CATALOG.md` 和各卷 `INDEX.md`，目录存在人工内容时拒绝覆盖。

## 3. 写可独立分享的正文

每章先在临时目录写三份候选：`<chapter>.md`（公开正文）、`<chapter>.meta.json`（公开目录元数据）、`<chapter>.source.json`（私有来源）。正文必须具备：`问题`、`核心原理`、`流程图或对比表`、`最小示例`、`适用边界与误区`、`自测问题`；可增加必要章节。图表至少一张 Mermaid 流程/时序图或一张有解读的 Markdown 表。用原创的最小代码/数据例子，不贴工程原码；目标约 5–10 分钟读完。

算法复杂度说明输入与假设，语言语义用标准、官方文档或最小实验核对，架构取舍写替代方案何时更合适。文章不得出现真实工程路径、工单 ID、产品/客户名、专有协议/日志、设备地址、凭据或“本项目中”等依赖上下文的说法。外部引用给原始链接；未经验证的推断不得伪装为定论。可分享的文章**不会**由本步骤自动上传或发表。

公开元数据 schema v1：`volume, chapter, title, summary, order, tags, related, created_at, updated_at`；`related` 使用 `卷名/章节名`。私有来源 schema v1：`article_id, generated_at, project_root, ticket_id, baselines, source_refs, private_terms, decision_record`；每条 `source_ref` 带 `repo, path, symbol, start_line, end_line, sha256`，SHA-256 对该行段的原始字节计算。私有来源只保存在 `~/.claude/icode_data/knowledge_state/<知识库路径哈希>/`，**不进入用户知识库 Git 仓库**。`private_terms` 列出需要从正文排除的产品、客户、项目标识；机器扫描不能代替人工脱敏。

## 4. 校验并写入

1. `python3 tools/lint_knowledge_article.py <候选.md> --metadata <候选.meta.json> --source <候选.source.json>`。失败先修候选。人工通读确认：脱离工程仍可理解、示例成立、图表与文字一致、原理的适用边界可信、没有私有内容。
2. 新章：`python3 tools/knowledge_library.py publish <候选.md> --metadata <候选.meta.json> --source <候选.source.json> --mode add [--library <目录>]`。
3. 修订：先从 `catalog` 取得旧正文和元数据 SHA-256，阅读旧文并保留其 `volume/chapter/created_at`；调用 `publish ... --mode revise --expect-article-sha256 <hash> --expect-metadata-sha256 <hash>`。哈希漂移则停下重新读，不能覆盖别人的并发或手工编辑。工具把旧版备份到库外私有状态目录，原子写入正文/元数据并重建目录；不运行 `git commit/push`。
4. 回读新正文、公开元数据、私有来源、总目录和卷目录，核对链接与工具返回的哈希。若生成目录失败或内容冲突，按工具结果报告恢复/未完成范围，不把部分写入称为成功。

完成时列出每章的 `reuse/revise/add` 决策、文章和卷目录绝对路径、私有溯源位置、实际知识库根、验证结果。说明 Git 修改仍由用户决定何时提交/推送。知识库根无 Git 时同样能生成，报告“本地目录，无 Git 版本历史”。
