# 步骤 1 — 拟定正式项目计划

**命令**: `/icode plan <需求>` 或 `/icode start <需求>`
**产出**: `{ICODE_OUT_DIR}/01_plan.md`
**会话**: 主会话

在**当前会话**中直接撰写计划，保留所有历史对话中的需求上下文。

## 前置：schema 迁移（自动/幂等/原子）

> 自动识别 + 自动迁移：用户无需任何操作，进入步骤 1 时若检测到 `.ico_metadata.json.template_version` 缺失或旧于当前版本，**自动**在已存在的 `01_plan.md` 末尾追加 §1.5 工程结构快照、补写 metadata、原子落盘；幂等（已含 §1.5 则跳过），失败兜底静默（不阻塞主流程）。

**自动执行**（强制，写在「执行步骤」第 1 步前）：

1. Read `.ico_metadata.json` —— 读 `template_version` 字段（缺失视为 `"v0"`）
2. 与当前 `PLAN_TEMPLATE_VERSION = "v1.1"` 比对（含 §1.5 工程结构快照、模板演进机制），若 `template_version` 已经 ≥ `"v1.1"` → 跳过本次迁移
3. 否则执行迁移 **（原子，不破坏既有正文）**：
   a. 解析既有 `01_plan.md`，用 **`grep -F '工程结构快照（v1.1 自动迁移）'`** 检测是否已含（**必须用 `-F` 字面量模式，不能用 regex**，否则 marker 内的 `(` 与年份误判）；不存在则执行迁移
   b. 自动生成 §1.5 工程结构快照——来源两条（按以下优先顺序）：
      - **优先**：若 `~/.claude/icode_data/project_docs/<project_id>/<branch_safe>/` 有章节，从 `00_overview.md` 的「核心模块清单」+「全栈图」段截取关键列表（≤80 行）
      - **次选**：无知识库时，临时 grep（`ls -la` 顶层目录 + `grep -rn 'int main\|class \w` 找 entry 函数），输出 ≤30 行快照
      - **零退化**：两条都无（纯新工程、无任何源码）→ 输出 `[无工程结构快照-工程无可索引内容]`，不阻塞
   c. §1.5 段追加到 `01_plan.md` 末尾（在所有现有章节之后），原子写：先写 `.tmp` 再 `mv`
   d. 写回 metadata：增字段 `template_version = "v1.1"`、增 `migration_log` 数组追加 `{from, to, at, files=[01_plan.md]}` 条目、`at = date +%Y-%m-%dT%H:%M:%S` 取系统时间（不写死）
   e. 输出 `▶ 自动迁移 → v1.1：补全 §1.5 工程结构快照（迁移到 .ico_metadata.migration_log）`
4. 任一步失败（文件 I/O 错、grep 不可用、project_docs 不可达）→ 静默跳过迁移、记 `[迁移跳过-原因]` 到 metadata migration_log，主流程继续

**与步骤 4/5 迁移的关系**：三步各自独立、各自检测、各自写入 ——
- 步骤 1 迁移在 `01_plan.md` 写 §1.5 工程结构快照
- 步骤 4 迁移在 `03_plan_final.md` 写三链预扫段
- 步骤 5 迁移在 `05_deepcheck.md` 写 blast-radius 三链自检段
- 三段互补不重复写入同一文件、互不依赖；migration_log 数组会按顺序记录全部迁移

**向后兼容**：

- 旧工单（缺 `template_version`）一律视为 `v0`，自动升级到 v1.1
- 已迁移的 01_plan.md 再次进入步骤 1 不会重复追加（按 `grep -F '工程结构快照（v1.1 自动迁移）'` 字面量检测去重）
- 用户手改过的 `01_plan.md` 原文**不会被覆盖**——只在末尾追加新段
- metadata 字段新增（`template_version` / `migration_log`）一律非破坏性，旧 metadata 缺这字段视为默认（`v0` / `[]`），写回时整对象保留

## 执行步骤

1. **目录管理 + 需求来源决策**（必须严格按以下顺序；完整目录管理脚本见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)——**必须先 Read 该文件完整内容**（含 ticket_id 生成/索引写入/metadata 模板，不得凭概述执行））：

   a. 检查最新 `.icode_output/.icode_output_N/` 目录是否满足"入口态复用条件"：有 `.ico_metadata.json` + `00_init.md`，且**无 `01_plan.md`**（status 为 `init_in_progress` 或 `log_done`，即 init/log 产出 00_init.md 但未进步骤1）。**注**：log 目录除 00_init.md 外还有 `log_analysis.md`，仍满足复用条件。
   b. **满足复用条件**：
      - 复用该目录（不创建新目录），`ICODE_OUT_DIR` 指向该目录
      - **Read 该目录下的 `00_init.md`**，将其内容作为本次步骤1的**主要需求输入**（init 产出的是需求初稿；log 产出的是根因转成的修复需求）
      - 若目录含 `log_analysis.md`（即来自 `/icode log`），**Read 其「核心结论 + 修复设计 + 4 维度验证清单」章节作背景参考**——步骤1计划应基于该根因展开修复方案，**必须把 `log_analysis.md` §7 设计态的 4 维度验证清单固化到 `01_plan.md` 的「修复方案设计」段**（详见下方章节 4.5）；在 ADR/风险评估里呼应根因证据
      - **4 维度清单读取强制**：无论 init 工单（`00_init.md` §7）还是 log 工单（`00_init.md` §5 + `log_analysis.md` §7），步骤1必须 Read 并固化——**未固化 = 设计遗漏 = 04_code 末尾 "Code Review Fix" 复检必失败**
      - 若 `/icode start` / `/icode plan` 命令行同时携带了需求字符串，仅作为**补充上下文**（次优先级），不覆盖 `00_init.md`
      - 在 `01_plan.md` 的"需求描述"章节中明确标注：本计划基于 `00_init.md` 展开（若来自 log，标注"基于根因报告 log_analysis.md 的修复需求"），并引用其关键章节
   c. **不满足复用条件**：执行常规「创建新目录」逻辑，确定 `ICODE_OUT_DIR`，需求输入采用命令行参数

2. **历史检索复用**（目录管理之后、强制思考之前，全局索引存在时必须执行，详见 SKILL.md「历史检索复用」段）。**置于目录管理之后**：此时需求来源已确定（复用情况已读 `00_init.md`，常规新建情况用命令行参数），可用完整需求做相关性判断：
   - Read `~/.claude/icode_data/index.json`（不存在则跳过检索）
   - **两段式检索**：段一从本次需求提炼关键词集，与各 ticket `keywords` 做 Jaccard 粗筛取 ≤10 候选（零 token，可复活预扫后排除剩余 stale/当前 `ticket_id`）；段二只把候选 `keywords + requirement_points` 喂主代理精读打分选 top-N 命中（N 由梯度决定，明确无关则 0 条）。**排除当前 `ticket_id`**，不自我参考——当前 ticket_id 读「最新 `.icode_output_N` 目录的 `.ico_metadata.json`」的 `ticket_id` 字段；**常规新建目录首跑时目录刚创建、尚未入索引，无需排除**；复用步骤0目录时 metadata 已有 ticket_id，按值排除
   - **`/icode plan`/`/icode start` 注入分支**：命中工单经段二精读+过时校验后，**按 `verdict` 分流注入**（字段缺失视为 `unknown`，详见 SKILL.md「注入形式·按 verdict 分流」）：
     - `verified`/`unknown`（含旧工单）：定点读其 `01_plan.md` 的 ADR 章节 + 风险评估章节（**不读全文**，≤1K token/条）；**`unknown` 额外扩读 `00_init.md` 末轮对话摘要**（≤0.3K，捞最终结论/证伪信号）+ 思考块「历史参考」走对抗质疑三问 + ⚠️未验证警告（[../references/thinking_detail.md](../references/thinking_detail.md)「历史参考小节」）--旧工单防误导主防线，不依赖标注
     - `disproved`（`verdict_review_needed=false`）：**不读 ADR**（避免错误方向被借鉴），改读 `verdict_reason`（作可验证断言）+ `correct_direction` 作避坑参考（≤0.7K/条）；**强制 Grep/Read 验证证伪前提是否仍成立**（详见 [../references/thinking_detail.md](../references/thinking_detail.md)「历史参考小节」）；`correct_direction` 缺失则降级读 ADR + ⛔ 警告，提示用户 `/icode status --verdict` 补标
     - `disproved`/`superseded`（`verdict_review_needed=true`，证伪前提依赖已变化）：**降级对抗质疑**--不硬反转，走 unknown A 层（扩读末轮+三问）+ 证伪前提+依赖变化提示（详见 SKILL.md「注入形式·按 verdict 分流」），让新需求重新评估前提是否仍成立
     - `superseded`：读 `superseded_by` 指针 + `correct_direction` + 替代工单 ADR 摘要（≤0.8K/条）
     - 作为本次计划的启发——参考其决策理由与踩坑。**只进会话上下文，不得在 `01_plan.md` 堆砌历史引用**（唯一例外：实质借鉴的 ADR 可在"理由"末尾加一句 `(参考相似工单 {ticket_id} 的同类决策)`）
   - 命中工单的 `01_plan.md` 读不到（工程被删/移动）→ 跳过该条不报错
   - **段零·工程文档检索**（与历史检索并行，候选合并排序；本入口检索时机：目录管理+需求来源确定后）：完整流程以 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「段零·工程文档检索」+「module_docs 工程模块库」段为准（含步骤 1-5 + 3.5 反查父项目 + 3.6 关联工程检索 + 3.6 源码路径定位 [project_path+manifest+兜底]），**执行前必须 Read 该段全文（含顶部「段零步骤速查」导航），不得凭本行摘要执行**；stale 降级 / commit 校验 / 注入防重复等细节同该段
   - **注入防重复**（两源共用 `_inject_cache.json`）：无缓存则创建空 `{"ticket_id":"<本工单>","injections":[]}`；注入前按 `(source, ref_id, slice)` 查缓存去重，已注入的跳过。历史源 slice=`adr_risks`；段零 slice=`section:<file>`。详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「注入缓存机制」段
   - 零命中不注入，不强凑参考

3. **强制思考前置**（不可跳过，缺证据视为不合规；**必须先 Read [references/thinking_core.md](../references/thinking_core.md) 完整内容（核心规则每步必读）+ 按需 Read [references/thinking_detail.md](../references/thinking_detail.md) 对应小节（各步骤子项/历史参考）+ [references/anti_laziness.md](../references/anti_laziness.md) 完整内容**（不得凭概述/记忆执行，否则产出不合规））：本步骤子项（至少3步）= 需求分解 → 方案分析 → 风险评估。**若步骤2有历史参考，在此处「历史参考」小节记录命中工单 id 与 ADR/风险要点，作为思考输入**
4. 自动迁移（如上「## 前置：schema 迁移」段）—— 迁移到 v1.1
5. 撰写计划：
   a. **先了解现有工程**：阅读项目中现有的代码，了解目录结构、现有架构模式、可复用模块 **serena 优先（v2.2 执行步骤内嵌）**：若工程有可索引源码（.py/.ts/.js/.c/.cpp/.rs/.go/.java 等）且 serena 可用（`~/.claude.json` 注册 + deferred 列表有 `mcp__serena__find_symbol`），先 ToolSearch 取 schema -> `find_symbol` 找 entry/导出符号 -> `find_referencing_symbols` 摸清关键调用链，结果作为 §1.5 工程结构快照输入；serena 不可用/无 LSP -> 降级 Read+Grep，降级说明只进思考块，不写入产物文件。**未经实际调用 serena 就标降级 = 反偷懒第 21 条违规**。
   b. **撰写计划**：包含以下 10 个章节（缺一不可）：

需求描述（包含所有对话中讨论过的细节；若复用 `00_init.md`，需引用该文档关键章节并展开）：
{用户输入的原始需求 / 或 00_init.md 内容}

必须包含的章节（逐一输出，不得跳过）：
1. **项目概述** — 目标、范围、约束条件，需说明与现有工程的关系
1.5. **工程结构快照** — 来源：`~/.claude/icode_data/project_docs/<id>/<branch>/`（如存在）或临时 Grep（顶层目录 + entry 函数）。内容 ≤80 行：(a) 顶层目录结构 + 各目录一句话职责；(b) 已识别模块清单（按需，按代码特征自适应）；(c) 关键 entry 函数 / 类（≥ 3 个，file:line 锚点）；(d) 与本次需求直接相关的现有模块（按工程文档检索命中）；(e) 已知技术栈与构建工具。**如内容为 `[无工程结构快照-工程无可索引内容]`，照常写但不阻塞**——给后续步骤 4/5 "调用链预扫"作锚点基线（v1.0→v1.1 迁移会自动补这一节，见「## 前置：schema 迁移」）
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

   > **与 `00_init.md` 第5节待决策项的关系**：若 `00_init.md` 第5节列了某待决策项的初步倾向（步骤0「待决策倾向自审」产出），步骤1 ADR **必须独立评估**该决策，不得直接照搬 init 倾向作为 ADR 决策；若 ADR 决策与 init 倾向一致，「理由」字段须附独立 Read/Grep 调研证据（呼应上方「工程既有模式调研」），不得仅引用"init 已倾向 X"作为 ADR 理由。（注：init 第5节标"无代码证据-留步骤1 ADR"的纯设计/产品偏好项除外，其 ADR 理由记设计依据即可，不要求 grep 证据）

   > **历史溯源（可选）**：若本 ADR 的决策实质借鉴了历史检索命中的相似工单，在「理由」末尾追加一句 `(参考相似工单 {ticket_id} 的同类决策)`。这是决策溯源而非工程污染，仅限实质借鉴时使用，不得堆砌。
5. **模块详细设计** — 每个模块的职责、关键函数、数据结构，引用现有接口命名风格。**代码示例使用伪代码+关键行号引用**，禁止粘贴完整函数实现（完整实现留给步骤4编码阶段）。格式：`参考 src/foo.cpp:42-68 的 HandleXxx 模式`。**必须说明新增代码如何融入既有链路习惯**（命名模式/错误处理模式/调用链模式/日志模式），不得写出"功能对但风格突兀"的设计

### 4.5 修复方案设计 + 4 维度设计态固化（修 bug 工单与功能需求工单均必填）

> **目的**：同事提示词"开始修复，修复后做代码 review 修复，确保没有逻辑 bug 和副作用，确保没有竞态死锁问题，确保解决了日志反映的问题"4 维度，**在设计阶段就固化**到计划里。**不是事后复检，是前置设计**——plan 必须把 4 维度的设计态答案写清楚，04_code 末尾 "Code Review Fix" 才有的核对。

**来源读取**：

- **log 工单**（`00_init.md` 是修复需求版）：必须 Read `log_analysis.md` §7「修复设计 + 4 维度验证清单」+ `00_init.md` §5「4 维度验证清单」，把 H/P/V 链 + 维度 2/3/4 设计前置清单固化到本节
- **init 工单**（`00_init.md` 是功能需求版）：必须 Read `00_init.md` §7「4 维度验证清单」，把维度 2/3/4 设计前置清单固化到本节（维度 1 根因闭环标 N/A）

**本节必备内容**（**4 维度 × 设计态证据**，缺一不可）：

- **维度 1 根因闭环（H/P/V 链，log 工单）**：H（根因 file:line）→ P（计划修复点 file:line + 修复方案描述）→ V（验证路径：哪个日志/行为能证明修复生效 + 如何观测）
- **维度 2 逻辑+扩大修改设计**：本修复涉及的边界/状态/异常/时序/数值 5 类逻辑点预判 + 预期修改范围（最小侵入边界，列出 file:line 候选）+ 优雅度6条预判（复用/风格/调用链/最小侵入/接口克制/调用路径）
- **维度 3 竞态死锁设计**：本修复涉及的 10 条强制清单（按工程相关性裁剪不涉及的标 N/A）+ 设计态缓解方案（具体到锁/原子/内存序/超时等）
- **维度 4 日志反映设计**：根因-日志-修复对齐点（V 可观测性即来自维度 1）+ 关键路径日志候选 + 日志级别/风格（复用项目既有日志库）

**未固化的反模式**：plan 阶段只写"修复方案"不写"4 维度设计态" → 04_code 末尾复检无对照基线 → 4 维度被遗漏。**禁止"待步骤4 实施时再考虑 4 维度"——4 维度是设计要素，不是实施要素**

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

   - **两种情况都要刷新全局索引**（步骤5之后）：Read `~/.claude/icode_data/index.json`，按 `ticket_id` 更新本工单条目——`requirement_summary` 用刷新后的值、`has_plan` = true、`status` = `plan_done`，写回 index.json，置 metadata `indexed = true`；**写后执行唯一性验证**（见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「全局索引写入·写后唯一性验证」）。
   - **常规新建目录情况**（此前未入索引）：此时需**首次生成并写入**条目。`ticket_id` 按 `{工程名}-{N}` 规则生成（工程名冲突时加 `project_path` 短 hash 后缀，规则同步骤0），`has_00_init` = false，`has_plan` = true，`project_path`/`out_dir`/`created_at`/`requirement_summary`/`keywords` 取自本步骤 metadata；写入索引后**回填 metadata 的 `ticket_id` 字段**。
   - **复用步骤0目录情况**：metadata 已有 `ticket_id`，按该 id 更新对应条目（`has_plan` 置 true，刷新 `requirement_summary`），不新建条目。

6. 如果是 `/icode start`（全流程模式）：

   - **立即继续执行步骤2**（不要等待用户确认）。**过渡提示不得写死轮数**——只输出 `▶ 步骤1 完成，进入步骤2 审查`，**不要**自行加"（3轮）""（默认3轮）"等轮数说明；轮数与延长机制由步骤2 启动时自行输出（见 [02_review.md](02_review.md)）
   - 如果会话断开后恢复，读取 `.ico_metadata.json` 的 `completed_steps`，从最后一个完成步骤的下一步继续。
   - **续跑判定规则**：以 `completed_steps` 中**编号 1~6 范围内最大的已完成步骤**为基准推进下一步。`"0"` 和 `"log"` 仅作为"已走过步骤0/log入口"的标记，**不影响**推进逻辑。例：
     - `["0"]`/`["log"]` → 下一步是步骤1
     - `["0","1"]` 或 `["1"]` 或 `["log","1"]` → 下一步是步骤2
     - `["0","1","2"]` 或 `["1","2"]` → 下一步是步骤3
## MCP 推荐（v2.2 强证据二元化）

按 [references/mcp_per_step.md](../references/mcp_per_step.md)「强证据场景判定」，本步骤 MCP：

| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| sequential-thinking | 🟢 | 强制思考 |
| serena | 🟢* | 理解代码结构（哪些函数被谁调用）--有可索引源码时（执行步骤 5a 内嵌） |
| context7 | 🟢* | 库 API 核对（用 v3 还是 v2？）--涉及第三方库时 |
| vision-bridge | 🟢* | 识别截图--用户给图时 |
| memory | 🟢* | read_graph 查跨工单记忆--本工程有历史工单时 |
| playwright | ⚪ | 本步骤不推荐 |

**强制约束（v2.2）**：🟢 必须调（满足强证据场景）；🟢* 默认 🟢 但需满足强证据场景才必调（不满足降 ⚪，无需声明）；⚪ 无需评估。serena 由执行步骤内嵌点承载，其余 🟢/🟢* 由 [thinking_core.md](../references/thinking_core.md) MCP gate 承载。详见 [SKILL.md](../SKILL.md)「MCP 调用覆盖强制化」+ [mcp_per_step.md](../mcp_per_step.md)「双保险机制」。
