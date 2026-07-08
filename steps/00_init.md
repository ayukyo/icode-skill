# 步骤 0 — 需求初稿对话（可选前置步骤）

**命令**: `/icode init [<粗略需求>]`
**产出**: `{ICODE_OUT_DIR}/00_init.md`
**会话**: 主会话
**与后续步骤的关系**: **独立步骤，不自动串联到步骤1**。完成后用户须显式运行 `/icode start`（全流程）/ `/icode fast`（精简全流程）/ `/icode plan`（仅步骤1）才进入步骤1。复用规则详见 SKILL.md「目录复用规则」段。

## 关键约定（必读）

- **`/icode init` 即"新开一次需求初稿讨论"**：每次调用 `/icode init` 都**创建一个全新的 `.icode_output/.icode_output_N/` 目录**，**不复用**之前任何 `init_in_progress` 状态的目录、**不续聊**之前的讨论。如果用户想继续上一次讨论，就直接对话，**不要再敲 `/icode init`**。
- **后续讨论由 AI 自主识别并增量更新**：`/icode init` 之后，用户在同一会话里继续提问/补充需求，AI 必须自主判断"这是对当前 `00_init.md` 的补充"，并按"后续每轮对话"流程处理（先 Read，再讨论，最后 Write 更新文档）。无需用户每轮都敲命令。
- **每轮都更新文档**：每轮对话结束前都必须 Write 一次 `00_init.md`，文档始终保持完整结构、内容到当前为止最新。

## 设计目标

为开放式需求讨论提供一个落档载体。允许用户跟 AI 多轮迭代讨论需求，**每轮对话后 AI 都增量更新 `00_init.md`**，文档始终保持"格式完整、内容到当前为止最新"的状态。用户随时停止，文档都可直接作为步骤1的输入。

## 执行步骤

### 首次调用（即每次 `/icode init`）

1. **执行目录管理中的「创建新目录」逻辑**（**强制新建，不做任何复用判定**），确定 `ICODE_OUT_DIR`
2. **历史检索复用**（强制思考之前，全局索引存在时必须执行，详见 SKILL.md「历史检索复用」段）：
   - Read `~/.claude/icode_data/index.json`（不存在则跳过检索）
   - **两段式检索**：段一从本次粗略需求提炼关键词集，与各 ticket `keywords` 做 Jaccard 粗筛取 ≤10 候选（零 token，排除 stale）；段二只把候选 `keywords + requirement_points` 喂主代理精读打分选 top-N 命中（N 由梯度决定，明确无关则 0 条）。`/icode init` 每次强制新建目录，本次工单尚未入索引，故无需排除当前 ticket_id
   - **`/icode init` 注入分支**：命中工单只读其 `requirement_points`（需求要点清单，≤500 token/条），作为后续讨论的启发——提示用户"上次相似需求曾关注过这些点，本次是否也需要考虑"。**只进会话上下文，绝不写进 `00_init.md`**。
   - **段零·工程文档检索**（与历史检索并行，候选合并排序）：`resolve_project_id(cwd)`（支持 git-root / repo-root 双模式，`repo` 根模式从 cwd 向上找 `.repo/manifest.xml`）→ `ls ~/.claude/icode_data/project_docs/<project_id>/`，逐章读前 50 行按 KEYS 匹配 → 读 `project_docs/<project_id>/_meta.json` 的 `module_deps` 列表，对每个 dep 查 `module_docs/<dep.key>/` → **反查父项目**（cwd 在子仓库 + path 匹配 manifest 的 `<project path>` → 父 repo-root 也纳入检索）；命中按 `[小节锚点]` 定点读小节；无知识库则零命中（ℹ️ 不阻塞）。详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「段零·工程文档检索」+「module_docs 工程模块库」段
   - **注入防重复**（两源共用 `_inject_cache.json`）：无缓存则创建空 `{"ticket_id":"<本工单>","injections":[]}`（ticket_id 读 metadata，暂无填空串）；注入前按 `(source, ref_id, slice)` 查缓存去重，已注入的跳过。历史源 slice=`requirement_points`；段零 slice=`section:<file>`。详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「注入缓存机制」段
   - 零命中不注入，不强凑参考
3. 处理输入参数（**两种都支持**，本步骤只**构思内容框架**，实际 Write 在步骤6；深度读代码在步骤4）：
   - **有参数**（`/icode init <粗略需求>`）：将参数作为初始需求，结合步骤2历史参考（若有），**构思**第一版 `00_init.md` 各章节内容框架（此时不深入读代码，代码细节留给步骤4）
   - **无参数**（`/icode init`）：**构思**空模板版 `00_init.md`（各章节内容写"待补"），然后主动询问用户"这次想做什么？"开启对话
4. **了解现有工程**：阅读项目中相关代码，识别现状、可复用模块、相关接口（**先于思考**，为步骤5的"现状盘点/影响面分析"提供代码依据）
5. **强制思考前置**（不可跳过，缺证据视为不合规；**必须先 Read [references/thinking.md](../references/thinking.md) + [references/anti_laziness.md](../references/anti_laziness.md) 完整内容**（不得凭概述/记忆执行，否则产出不合规））：本步骤子项（至少4步）= 需求分解 → 现状盘点（基于步骤4读码结果） → 影响面分析（基于步骤4读码结果） → 待决策项识别。**若步骤2有历史参考，在此处「历史参考」小节记录命中工单 id 与要点，作为思考输入**
6. 使用 Write 工具写入 `{ICODE_OUT_DIR}/00_init.md`（模板见下文）
7. 创建 `{ICODE_OUT_DIR}/.ico_metadata.json`：

   ```json
   {
     "requirement": "{用户输入的原始需求，无参数时填空字符串}",
     "created_at": "当前时间",
     "status": "init_in_progress",
     "completed_steps": ["0"],
     "code_files": [],
     "requirement_summary": "{基于粗略需求的一句话摘要，≤100 token；无参数时填空字符串}",
     "requirement_points": [],
     "keywords": "{≤8个技术关键词数组，无参数时填空数组}",
     "indexed": false,
     "ticket_id": "{步骤8 写入索引后回填，初始创建时为空字符串}"
   }
   ```

8. **写入全局索引**（步骤7之后立即执行）：Read `~/.claude/icode_data/index.json`（不存在则创建 `{"version":"1","updated_at":"当前时间","tickets":[]}`），追加一条新记录：
   - `ticket_id` = `{工程名}-{N}`（工程名取 `project_path` 的 basename；N 为当前 `.icode_output_N` 的 N）。**工程名冲突处理**：若索引中已存在相同 `{工程名}-{N}` 但 `project_path` 不同的条目，ticket_id 追加 `project_path` 的短 hash 后缀（如 `myproject-1-a3f2`）以保唯一
   - `project_path` = 当前工程根绝对路径
   - `out_dir` = `.icode_output/.icode_output_{N}`
   - `requirement_summary` / `keywords` 取自步骤7 metadata；`requirement_points` 暂为空数组
   - `has_00_init` = true，`has_plan` = false，`status` = `init_in_progress`，`created_at` = 当前时间
   - 写回 index.json，同时置 metadata `indexed = true`、`ticket_id = {生成的 ticket_id}`（持久化 ticket_id，供后续步骤检索时排除当前工单，避免反推）
9. 提示用户：可继续对话补充需求，文档会随对话自动更新；讨论完成后运行 `/icode start` 或 `/icode plan` 进入步骤1。**若想另起炉灶讨论别的需求**，再敲 `/icode init`（会新建另一个目录）。

### 后续每轮对话（同一会话内，由 AI 自主识别，无需用户敲命令）

`/icode init` 创建目录后，用户在**同一会话内**继续对话补充/修改需求时，AI 必须**自主识别**这是在迭代当前需求初稿，并按以下流程处理：

1. **先 Read 现有 `00_init.md`**，理解当前文档状态
2. 跟用户讨论（回答疑问、提出反问、澄清歧义）
3. **本轮对话结束前，必须用 Write 工具更新 `00_init.md`**，把本轮新信息合并进对应章节，保持文档结构完整
4. **刷新全局索引条目**：从 `00_init.md`「3.新增需求点」提炼 `requirement_points`（≤8 条，每条 ≤30 token），结合本轮讨论刷新 `requirement_summary`。**注**：`requirement_points` 仅在步骤0首轮和步骤1完成时刷新，每轮对话不重复刷（防止索引条目膨胀），并**按 metadata 的 `ticket_id` 定位** `~/.claude/icode_data/index.json` 中本工单条目，更新其 `requirement_summary` / `requirement_points`。**用户无感，不写进 `00_init.md`**。
5. 不需要等待用户说"结束"才落档，**每轮都增量更新**

**判定"是否还在迭代当前 init"的依据**：

- 最新 `.icode_output/.icode_output_N/` 目录的 `status` 为 `init_in_progress`
- 当前用户消息是对该目录中 `00_init.md` 内容的补充/修改/澄清，而非另一个独立任务

不属于上述场景时（例如用户开始让你写代码、查别的 bug），就按正常对话处理，不要触发文档更新。

## `00_init.md` 模板（轻模板，每轮都填，信息不足处写"待补"）

```markdown
# 需求初稿：{标题，基于讨论内容自动生成}

> 本文档由 `/icode init` 生成，随对话增量更新。运行 `/icode start` 或 `/icode plan` 可基于本文档进入步骤1。

## 1. 背景与目标

{为什么要做这件事；业务/技术背景；预期效果}

## 2. 现状盘点

{涉及哪些现有代码/模块/已有功能；关键接口/数据流；参考文件路径}

- 涉及节点/模块：...
- 现有相关功能：...
- 参考路径：...

## 3. 新增需求点

{新增的功能/数据/能力；每条独立可验证}

- [ ] 需求点1：...
- [ ] 需求点2：...

## 4. 影响面与关联模块

{需修改/新建的文件；调用方/被调用方；跨节点协同点}

- 需新建：...
- 需修改：...
- 上下游依赖：...

## 5. 关键问题与待决策项

{当前讨论中尚未明确的点，显式列出；步骤1接力时知道哪些是已决、哪些是待定}

- [ ] 待决策1：...（候选方案 A/B/C）
- [ ] 待决策2：...

## 对话摘要（可选）

{按时间顺序简记每轮关键结论；可选，便于回顾本次讨论的演进}

- 轮次1：...
- 轮次2：...
```

## 强制规则

- **每轮都更新**：任何一轮对话结束前都必须 Write 一次 `00_init.md`，即使本轮只是细微调整，也要落档
- **不自动串联**：即使讨论已经非常充分，也不主动跳到步骤1。等用户显式运行 `/icode start` / `/icode plan`
- **保持模板结构**：无论信息多少，5 个章节都要存在，不足处写"待补"
- **status 维持 `init_in_progress`**：每次更新文档时，metadata 的 `status` 保持 `init_in_progress`（等步骤1启动消费时，由步骤1切换）

## 与步骤1的衔接

`/icode start` / `/icode plan` 启动时，如果检测到**当前最新目录只有 `00_init.md`（仅含 `.ico_metadata.json` + `00_init.md`，无 `01_plan.md` 等其他步骤产物）**，则：

1. **复用**该目录（不创建新的 N+1 目录）
2. 将 `00_init.md` 内容作为步骤1的需求输入（优先级高于命令行参数；命令行参数若有，仅作为补充上下文）
3. 步骤1完成后正常切换 `status` 为 `plan_done`，`completed_steps` 追加 `"1"`

详细衔接规则见 [01_plan.md](01_plan.md)。
