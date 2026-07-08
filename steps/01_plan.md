# 步骤 1 — 拟定正式项目计划

**命令**: `/icode plan <需求>` 或 `/icode start <需求>`
**产出**: `{ICODE_OUT_DIR}/01_plan.md`
**会话**: 主会话

在**当前会话**中直接撰写计划，保留所有历史对话中的需求上下文。

## 执行步骤

1. **目录管理 + 需求来源决策**（必须严格按以下顺序；完整目录管理脚本见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)——**必须先 Read 该文件完整内容**（含 ticket_id 生成/索引写入/metadata 模板，不得凭概述执行））：

   a. 检查最新 `.icode_output/.icode_output_N/` 目录是否满足"入口态复用条件"：有 `.ico_metadata.json` + `00_init.md`，且**无 `01_plan.md`**（status 为 `init_in_progress` 或 `log_done`，即 init/log 产出 00_init.md 但未进步骤1）。**注**：log 目录除 00_init.md 外还有 `log_analysis.md`，仍满足复用条件。
   b. **满足复用条件**：
      - 复用该目录（不创建新目录），`ICODE_OUT_DIR` 指向该目录
      - **Read 该目录下的 `00_init.md`**，将其内容作为本次步骤1的**主要需求输入**（init 产出的是需求初稿；log 产出的是根因转成的修复需求）
      - 若目录含 `log_analysis.md`（即来自 `/icode log`），**Read 其「核心结论 + 建议修复方向」章节作背景参考**——步骤1计划应基于该根因展开修复方案，在 ADR/风险评估里呼应根因证据
      - 若 `/icode start` / `/icode plan` 命令行同时携带了需求字符串，仅作为**补充上下文**（次优先级），不覆盖 `00_init.md`
      - 在 `01_plan.md` 的"需求描述"章节中明确标注：本计划基于 `00_init.md` 展开（若来自 log，标注"基于根因报告 log_analysis.md 的修复需求"），并引用其关键章节
   c. **不满足复用条件**：执行常规「创建新目录」逻辑，确定 `ICODE_OUT_DIR`，需求输入采用命令行参数

2. **历史检索复用**（目录管理之后、强制思考之前，全局索引存在时必须执行，详见 SKILL.md「历史检索复用」段）。**置于目录管理之后**：此时需求来源已确定（复用情况已读 `00_init.md`，常规新建情况用命令行参数），可用完整需求做相关性判断：
   - Read `~/.claude/icode_data/index.json`（不存在则跳过检索）
   - **两段式检索**：段一从本次需求提炼关键词集，与各 ticket `keywords` 做 Jaccard 粗筛取 ≤10 候选（零 token，排除 stale/当前 `ticket_id`）；段二只把候选 `keywords + requirement_points` 喂主代理精读打分选 top-N 命中（N 由梯度决定，明确无关则 0 条）。**排除当前 `ticket_id`**，不自我参考——当前 ticket_id 读「最新 `.icode_output_N` 目录的 `.ico_metadata.json`」的 `ticket_id` 字段；**常规新建目录首跑时目录刚创建、尚未入索引，无需排除**；复用步骤0目录时 metadata 已有 ticket_id，按值排除
   - **`/icode plan`/`/icode start` 注入分支**：命中工单**定点读其 `01_plan.md` 的 ADR 章节 + 风险评估章节**（**不读全文**，≤1K token/条），作为本次计划的启发——参考其决策理由与踩坑。**只进会话上下文，不得在 `01_plan.md` 堆砌历史引用**（唯一例外：实质借鉴的 ADR 可在"理由"末尾加一句 `(参考相似工单 {ticket_id} 的同类决策)`）
   - 命中工单的 `01_plan.md` 读不到（工程被删/移动）→ 跳过该条不报错
   - **段零·工程文档检索**（与历史检索并行，候选合并排序）：`resolve_project_id(cwd)`（支持 git-root / repo-root 双模式，`repo` 根模式从 cwd 向上找 `.repo/manifest.xml`）→ `ls ~/.claude/icode_data/project_docs/<project_id>/`，逐章读前 50 行按 KEYS 匹配 → 读 `project_docs/<project_id>/_meta.json` 的 `module_deps` 列表，对每个 dep 查 `module_docs/<dep.key>/` → **反查父项目**（cwd 在子仓库 + path 匹配 manifest 的 `<project path>` → 父 repo-root 也纳入检索）；命中按 `[小节锚点]` 定点读小节；无知识库则零命中（ℹ️ 不阻塞）。详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「段零·工程文档检索」+「module_docs 工程模块库」段
   - **注入防重复**（两源共用 `_inject_cache.json`）：无缓存则创建空 `{"ticket_id":"<本工单>","injections":[]}`；注入前按 `(source, ref_id, slice)` 查缓存去重，已注入的跳过。历史源 slice=`adr_risks`；段零 slice=`section:<file>`。详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「注入缓存机制」段
   - 零命中不注入，不强凑参考

3. **强制思考前置**（不可跳过，缺证据视为不合规；**必须先 Read [references/thinking.md](../references/thinking.md) + [references/anti_laziness.md](../references/anti_laziness.md) 完整内容**（不得凭概述/记忆执行，否则产出不合规））：本步骤子项（至少3步）= 需求分解 → 方案分析 → 风险评估。**若步骤2有历史参考，在此处「历史参考」小节记录命中工单 id 与 ADR/风险要点，作为思考输入**
4. 撰写计划：
   a. **先了解现有工程**：阅读项目中现有的代码，了解目录结构、现有架构模式、可复用模块
   b. **撰写计划**：包含以下 9 个章节（缺一不可）：

需求描述（包含所有对话中讨论过的细节；若复用 `00_init.md`，需引用该文档关键章节并展开）：
{用户输入的原始需求 / 或 00_init.md 内容}

必须包含的章节（逐一输出，不得跳过）：
1. **项目概述** — 目标、范围、约束条件，需说明与现有工程的关系
2. **功能需求** — 所有功能点列表，含输入/输出/边界，标注哪些可复用现有模块
3. **架构设计** — 模块划分、数据流、接口定义，需说明如何在现有架构中扩展。**必须包含跨文件关联分析**：哪些文件需要新建、哪些现有文件需要修改、修改的文件被谁依赖。**架构优雅三要求**：①**复用决策**——新增功能涉及的工具/辅助函数，必须 grep 工程既有代码，有等价的必须复用（计划写明复用哪个既有函数）；②**模式一致**——新增代码的组织方式（handler 注册模式 / 属性中心 / RAII / 错误码返回 / switch-case 等）必须与工程既有模式一致，ADR 里记录"为何用此模式 + 与既有 XX 模式对齐"；③**接口克制**——新增导出接口只暴露必要符号（YAGNI），计划 §3 接口定义里标注每个 public 符号的必要性
4. **架构决策记录（ADR）** — 每个关键决策记录：上下文、候选选项、决策、理由。格式如下：

   ```markdown
   ### ADR-N: {决策标题}
   - **上下文**：为什么需要做此决策
   - **工程既有模式调研**（决策前必做，grep 实证）：当决策涉及"如何调用既有方法/接口/跨模块协同"时，必须先 grep 工程里同类调用的既有写法，把统计结果写在此处。统计字段：直调 N 处 / 间接调用（路由/事件/注册分发）M 处 / 其他 K 处；结论：工程既有主导模式是 X（若多种并存，说明各自适用场景）。**不得凭"内部触发更简单"等直觉选调用方式，必须以工程既有模式为准**。
   - **选项**：A) xxx  B) xxx  C) xxx
   - **决策**：选择 X
   - **理由**：为什么选 X 而非其他（须呼应「工程既有模式调研」结论——若选与主导模式不同的方案，必须给出强证据）
   ```

   需记录 ADR 的典型场景：方案选型、降级策略、接口取舍、兼容性权衡、**跨模块/跨端点调用方式选择**（直调 vs 路由/事件分发）。决策变更时只需更新此章节 + 受影响的引用点。

   > **历史溯源（可选）**：若本 ADR 的决策实质借鉴了历史检索命中的相似工单，在「理由」末尾追加一句 `(参考相似工单 {ticket_id} 的同类决策)`。这是决策溯源而非工程污染，仅限实质借鉴时使用，不得堆砌。
5. **模块详细设计** — 每个模块的职责、关键函数、数据结构，引用现有接口命名风格。**代码示例使用伪代码+关键行号引用**，禁止粘贴完整函数实现（完整实现留给步骤4编码阶段）。格式：`参考 src/foo.cpp:42-68 的 HandleXxx 模式`。**必须说明新增代码如何融入既有链路习惯**（命名模式/错误处理模式/调用链模式/日志模式），不得写出"功能对但风格突兀"的设计
6. **异常处理** — 错误码、异常场景、降级策略
7. **实现步骤** — 分阶段实施顺序、依赖关系
8. **校验项** — 可复核检查点列表（用于后续步骤核对）
9. **风险评估** — 技术风险、依赖风险、缓解措施

格式要求：
- 使用 Markdown 格式
- 条理分明，每章有编号
- 校验项以 [ ] checkbox 形式列出

   c. **断言验证**（必须执行，不可跳过）：
      从计划中提取所有涉及具体代码位置/行为的断言，逐一用 Read/Grep 工具实证验证。断言分三类：
      - **接口存在类**（原有）：如"某函数在文件X中且 public"、"某实例可通过 GetXxx() 取得"
      - **路径可达类**（原有）：如"某配置从Y加载"、"某接口签名是Z"
      - **调用模式一致性类**（新增）：对计划中每个"跨模块/跨端点/跨层"调用，grep 同文件/同模块既有同类调用的写法，核对新增调用是否对齐工程主导模式。若工程有明确主导模式（如同类调用 N 处都走路由、0 处直调），新增调用必须对齐，否则断言失败 `[未验证-调用模式不一致]`，计划必须修正。**同函数内既有同类调用是最强信号**——同函数已有调用走某模式，新增调用不得另选他法。

      - 已验证的断言标记为 `[已验证]`
      - 验证失败或未验证的断言标记为 `[未验证]`，并说明原因
      - 验证失败导致计划需要调整的，立即修正对应章节
      - 将验证结果追加到计划末尾的"断言验证记录"章节

   d. 使用 Write 工具将计划写入 `{ICODE_OUT_DIR}/01_plan.md`

## 强制操作（完成后必须执行）

5. **创建或更新 `{ICODE_OUT_DIR}/.ico_metadata.json`**：

   - **复用步骤0目录的情况**：metadata 已存在，需**更新**（而非覆盖）以下字段：
     - `status`: `init_in_progress` → `plan_done`
     - `completed_steps`: 在原有 `["0"]` 后追加 `"1"`，形成 `["0", "1"]`
     - 保留原有 `requirement`、`created_at`，可在 requirement 后追加命令行参数（若有）
     - **刷新检索字段**：基于完整计划刷新 `requirement_summary`（一句话摘要，≤100 token）；`requirement_points` 保持步骤0的值或补全；`keywords` 按计划涉及的技术栈补全；保留 `indexed=true`
   - **常规新建目录的情况**：创建新文件如下：

```json
{
  "requirement": "{用户输入的原始需求}",
  "created_at": "当前时间",
  "status": "plan_done",
  "completed_steps": ["1"],
  "code_files": [],
  "requirement_summary": "{基于完整计划的一句话摘要，≤100 token}",
  "requirement_points": [],
  "keywords": "{≤8个技术关键词数组}",
  "indexed": false,
  "ticket_id": "{刷新全局索引时回填，初始创建时为空字符串（步骤1 常规新建首跑时首次写索引生成，复用步骤0目录时已有 ticket_id）}",
  "mode": "full",
  "max_rounds": 3
}
```

   - **两种情况都要刷新全局索引**（步骤5之后）：Read `~/.claude/icode_data/index.json`，按 `ticket_id` 更新本工单条目——`requirement_summary` 用刷新后的值、`has_plan` = true、`status` = `plan_done`，写回 index.json，置 metadata `indexed = true`。
   - **常规新建目录情况**（此前未入索引）：此时需**首次生成并写入**条目。`ticket_id` 按 `{工程名}-{N}` 规则生成（工程名冲突时加 `project_path` 短 hash 后缀，规则同步骤0），`has_00_init` = false，`has_plan` = true，`project_path`/`out_dir`/`created_at`/`requirement_summary`/`keywords` 取自本步骤 metadata；写入索引后**回填 metadata 的 `ticket_id` 字段**。
   - **复用步骤0目录情况**：metadata 已有 `ticket_id`，按该 id 更新对应条目（`has_plan` 置 true，刷新 `requirement_summary`），不新建条目。

6. 如果是 `/icode start`（全流程模式）：

   - **立即继续执行步骤2**（不要等待用户确认）。**过渡提示不得写死轮数**——只输出 `▶ 步骤1 完成，进入步骤2 审查`，**不要**自行加"（3轮）""（默认3轮）"等轮数说明；轮数与延长机制由步骤2 启动时自行输出（见 [02_review.md](02_review.md)）
   - 如果会话断开后恢复，读取 `.ico_metadata.json` 的 `completed_steps`，从最后一个完成步骤的下一步继续。
   - **续跑判定规则**：以 `completed_steps` 中**编号 1~6 范围内最大的已完成步骤**为基准推进下一步。`"0"` 和 `"log"` 仅作为"已走过步骤0/log入口"的标记，**不影响**推进逻辑。例：
     - `["0"]`/`["log"]` → 下一步是步骤1
     - `["0","1"]` 或 `["1"]` 或 `["log","1"]` → 下一步是步骤2
     - `["0","1","2"]` 或 `["1","2"]` → 下一步是步骤3
