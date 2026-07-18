# 步骤 log — 日志根因分析（可选前置入口）

**命令**: `/icode log [零散信息...]`
**产出**: `{ICODE_OUT_DIR}/log_analysis.md` + `{ICODE_OUT_DIR}/00_init.md`（修复需求初稿，衔接步骤1）
**会话**: 主会话
**定位**: **与 init/start/fast/plan 并列的入口命令，非流程步骤编号**。把"一坨设备/服务日志 + 模糊症状"转化为"有证据、经对抗验证、可信"的根因报告，并自动转成修复需求衔接步骤1。**领域无关**——适用于任何能产生日志的系统（机器人/服务端/嵌入式/Web 等均不限）。完成后用户敲 `/icode plan`（仅步骤1）/ `/icode start`（全流程）/ `/icode fast`（精简全流程）（无参）复用同目录进入修复流程，详见 SKILL.md「目录复用规则」段。

## 设计借鉴（方法论，非绑定具体技能）

- **基线检查优先**：grep 日志前先做基线检查（git diff / 状态链路图 / 文档参考不盲信）——防"直接猜根因走弯路"
- **输入要素自主凑齐**：日志目录/问题描述/时间点三要素尽量推断、不挤牙膏追问
- **对抗分析防确认偏误**：独立质疑者反复攻击分析师假设，撑不住就降级，不凑虚假共识
- **诚实降级**：证据不足时说"未定论"，不编根因
- **icode 步骤2 对抗模式**：分析师+3质疑者、裁决优先级、证据回指

## 输入契约（三要素，自主凑齐）

| 要素 | 说明 | 缺失时自主推断 |
|------|------|------------|
| **日志目录** | 含节点日志的目录 | 用户零散输入给的路径；或扫 `~/work/log/`、`/var/log/`、工程内 `log/` 等常见位置；或从症状关键词找匹配目录 |
| **问题描述** | 一句话症状 | 目录名；目录内 README/最近的 `.md` 报告；用户零散语句提炼 |
| **问题时间点** | 症状发生时刻 | 日志文件 mtime；日志内容里最后一次重启/异常时间；多个候选全部分析 |

**自主性原则**：三要素尽量自己凑齐、自己推断、自己在报告里标注推断来源。**结论出来前的分析阶段不打断用户**。只有输入严重不足（既无路径又无任何症状线索）且无法推断时，才在输入收敛期一次性集中问。

## 执行步骤

1. **执行目录管理中的「创建新目录」逻辑**（强制新建，不做复用判定），确定 `ICODE_OUT_DIR`
2. **历史检索复用**（强制思考之前，全局索引存在时必须执行，详见 SKILL.md「历史检索复用」段）：
   - Read `~/.claude/icode_data/index.json`（不存在则跳过检索）
   - **两段式检索**：段一从本次症状/关键词提炼关键词集，与各 ticket `keywords` 做 Jaccard 粗筛取 ≤10 候选（零 token，可复活预扫后排除剩余 stale）；段二只把候选 `keywords + requirement_points` 喂主代理精读打分选 top-N 命中（N 由梯度决定，明确无关则 0 条）。`/icode log` 每次强制新建目录，本次工单尚未入索引，故无需排除当前 ticket_id
   - **`/icode log` 注入分支**：命中工单经段二精读+过时校验后，**按 `verdict` 分流注入**（字段缺失视为 `unknown`，详见 SKILL.md「注入形式·按 verdict 分流」）：
     - `verified`/`unknown`（含旧工单）：定点读其 `log_analysis.md` 的「根因结论 + 决定性证据」章节（**不读全文**，≤800 token/条）；**`unknown` 额外扩读 `00_init.md` 末轮对话摘要**（≤0.3K）+ 思考块「历史参考」走对抗质疑三问 + ⚠️未验证警告（[../references/thinking.md](../references/thinking.md)「历史参考小节」）——旧工单防误导主防线
     - `disproved`（`verdict_review_needed=false`）：**不读根因结论**（避免错误根因方向被借鉴），改读 `verdict_reason`（作可验证断言）+ `correct_direction` 作避坑参考（≤0.7K/条）；**强制 Grep/Read 验证证伪前提是否仍成立**（详见 [../references/thinking.md](../references/thinking.md)「历史参考小节」）；`correct_direction` 缺失则降级读根因 + ⛔ 警告，提示 `/icode status --verdict` 补标
     - `disproved`/`superseded`（`verdict_review_needed=true`，证伪前提依赖已变化）：**降级对抗质疑**--不硬反转，走 unknown A 层（扩读末轮+三问）+ 证伪前提+依赖变化提示（详见 SKILL.md「注入形式·按 verdict 分流」），让新需求重新评估前提是否仍成立
     - `superseded`：读 `superseded_by` 指针 + `correct_direction` + 替代工单根因摘要（≤0.8K/条）
     - 作为本次分析的启发——参考其根因方向与踩坑。**只进会话上下文，绝不写进 `log_analysis.md`**（唯一例外：实质借鉴可在该根因条目末尾加一句 `(参考相似工单 {ticket_id} 的同类根因)`）
   - **段零·工程文档检索**（与历史检索并行，候选合并排序）：`resolve_project_id(cwd)`（支持 git-root / repo-root 双模式，`repo` 根模式从 cwd 向上找 `.repo/manifest.xml`）→ `ls ~/.claude/icode_data/project_docs/<project_id>/`，逐章读前 50 行按 KEYS 匹配 → 读 `project_docs/<project_id>/_meta.json` 的 `module_deps` 列表，对每个 dep 查 `module_docs/<dep.key>/` → **反查父项目**（cwd 在子仓库 + path 匹配 manifest 的 `<project path>` → 父 repo-root 也纳入检索）；命中按 `[小节锚点]` 定点读小节；无知识库则零命中（ℹ️ 不阻塞）。详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「段零·工程文档检索」+「module_docs 工程模块库」段；**stale 章节降级注入摘要不注正文 + module commit 一致性校验**见「stale 章节降级注入」「步骤 3 commit 一致性校验」段
   - **注入防重复**（两源共用 `_inject_cache.json`）：无缓存则创建空 `{"ticket_id":"<本工单>","injections":[]}`；注入前按 `(source, ref_id, slice)` 查缓存去重，已注入的跳过。历史源 slice=`root_cause_evidence`；段零 slice=`section:<file>`。详见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「注入缓存机制」段
   - 零命中不注入，不强凑参考
3. **阶段0 输入要素收敛**（最多1-2轮，不拖成长讨论）：
   - 解析用户零散输入，提取已有信息
   - **能推断的推断**（日志目录/时间点/症状按上表推断），推断的标注"推断"
   - **推断不了的才问**，且一次性集中问（不挤牙膏）
   - 收敛出三要素，写入 `log_analysis.md` 的「输入要素」章节
4. **强制思考前置**（不可跳过，缺证据视为不合规；**必须先 Read [references/thinking.md](../references/thinking.md) + [references/anti_laziness.md](../references/anti_laziness.md) 完整内容**（不得凭概述/记忆执行，否则产出不合规））：本步骤子项（至少4步）= 症状分解 → 基线预判 → 链路假设 → 验证策略。**若步骤2有历史参考，在此处「历史参考」小节记录命中工单 id 与根因要点，作为思考输入**
5. **阶段1 基线检查**（grep 日志之前必做，借鉴 baseline-checklist）：
   - **git diff/status/log**：看相关代码改过没（含 submodule/subrepo）。若代码已被改过（AI/同事/其他分支 merge），问题可能在改动里
   - **画状态链路图**：从症状出发画完整数据流，标注每个状态的"谁写/谁读/谁清"。关键问题：这个状态谁写的？什么时候写？谁清的？清的时候有没有遗漏？
   - **找项目文档参考但不盲信**：grep 相关 `docs/`、`*.md`，快速了解设计意图。但**文档可能过时/错误**，看到后必须用 Read/Grep 验证文档描述的代码行为是否属实
   - **基线快通道**：若基线检查已直接定位根因（代码改动暴露问题 + 链路图显示清除者缺失），跳过阶段2深挖，直接进阶段3 对抗验证该根因
6. **阶段2 日志侦察 + 现场还原**：
   - **侦察**：日志目录有哪些节点/文件/多大/覆盖什么时间范围（不读 20MB 原文，用 `ls -la`/`wc -l`/`head`/`tail` 摸清）
   - **现场还原**：围绕问题时间点（默认前后各扩5-10分钟），跨节点抽时间线，按时间合并，每行打 `[节点名]` 前缀
   - **对照组**：捕捉重启前后/正常段vs异常段/同条件不同结果——对照组是定性"设备端 vs 环境"的铁证
   - **零号病人**：找首次异常出现的时刻，以及它之前的先兆
   - 产出「现场时间线」表（时间|节点|事件），写入 `log_analysis.md`
7. **阶段3 对抗根因分析**（复用 icode 步骤2 对抗模式：分析师+3质疑者+裁决优先级+诚实降级）：
   - **分析师提假设**：基于现场时间线+证据，提根因假设 H + 证据指针 E（具体日志行：节点+时间+原文）+ 置信度
   - **代码事实验证门（必须先于对抗）**：根因假设 H 涉及的代码行/函数，**必须先用 Read 工具实读实际代码验证**（如假设"mul_overflows 在1073741824*2边界失效"，则必须 Read calc.c 的 mul_overflows 函数实际实现，逐行核对分支逻辑）。**不得仅凭日志推断代码行为**——日志显示的是现象，代码才是事实。验证通过后才进入对抗分析
   - **对抗分析**：完整对抗模式（3质疑者/subagent_type=general-purpose+schema 强制结构化/裁决优先级/诚实降级/证据回指/子代理失败处理）——**必须先 Read [references/adversarial.md](../references/adversarial.md) 完整内容**（不得凭概述/记忆执行）。**必须独立 spawn 3 个质疑者子代理**（证据质疑者/替代解释者/充分性质疑者各一，不得合并 spawn，少任一视为不合规；**禁用 Explore**，用 general-purpose + schema 强制结构化输出防截断）。本步骤分析对象 = 根因假设 H（已通过代码事实验证），证据指针指向日志行（节点+时间+原文）和代码行（file:line），输入契约喂质疑者「日志目录路径 + 现场时间线 + 假设 H + 证据指针 E + 代码路径」。子代理失败时按 adversarial.md「子代理失败处理」重试1次→仍失败诚实降级为 `[未验证-子代理对抗失败]`，**绝不自演裁决**
   - **循环收敛**：分析师出假设 → 质疑者攻击 → 有实质反驳就修正/补证据/降级 → 重复直到收敛（默认最多4轮，可调）。收敛判据、诚实底线均在 [references/adversarial.md](../references/adversarial.md)（上方已 Read）
   - 对抗记录写入 `log_analysis.md` 的「对抗分析记录」章节，**每条记录必须注明该质疑者的 spawn Agent ID**（作为独立调用证据）
8. **阶段4 报告生成 + 衔接步骤1**：
   - 用 Write 写 `{ICODE_OUT_DIR}/log_analysis.md`，骨架：

     ```markdown
     # 日志根因分析报告：<问题描述>
     > 元信息：设备/版本/平台 | 分析日期 | 数据来源（日志目录+时间范围）
     ## 0. 核心结论 (TL;DR) —— 一句话定性 + 编号要点（是什么/不是什么/根因/置信度）
     ## 1. 输入要素 —— 三要素（日志目录/问题描述/时间点），标注推断来源
     ## 2. 基线检查 —— git diff 结论 + 状态链路图 + 文档验证结论
     ## 3. 现场还原（问题时间线）—— 阶段2事件表 + 问题时刻现场状态
     ## 4. 决定性证据 —— 对照组 + 关键日志片段（带节点/时间/原文）
     ## 5. 根因分析 —— 因果链分解 + 排除的假设（修正早期判断）
     ## 6. 对抗分析记录 —— 各轮 假设/质疑/修正 + 最终共识与残余不确定性
     ## 7. 建议修复方向 —— 方案 + 改动点 + 验证方法
     ```

     > **`log_analysis.md` 章节必填/可空说明**：
     > - **必填章节**（缺一不可）：0 核心结论、4 决定性证据、5 根因分析、6 对抗分析记录、7 建议修复方向
     > - **可空章节**：1 输入要素（如三要素明显可空）、2 基线检查（如无 git 可空）、3 现场还原（如无时间线数据可空）
     > - **章节引用强制**：步骤2 复用 log 阶段对抗验证结论时，必须读 6 对抗分析记录章节（不读全文）

   - 每条根因结论必须能回指到**具体日志片段**（节点+时间+原文）。做不到回指的结论不写进「核心结论」，只进「待验证假设」
   - **诚实降级**：若证据不足以支持任何高置信结论，报告写"现有日志不足以定论，已排除 X/Y/Z，建议补充 W"。**宁可说不知道，也不编一个根因**
   - **转修复需求**：把根因 + 建议修复方向提炼成 `00_init.md`（**log 修复需求版**，结构 = 症状/根因/新增需求点/链路图，详见下方「`00_init.md` 结构」；需求=修复该根因），其中**第 4 节链路图：before = 阶段 1 状态链路图所示的「带 bug 当前链路」（标注故障点 file:line）、after = 修复后链路（在修复点标 `[+]`/`[~]`/`[-]`）、改动点清单对齐第 3 节新增需求点**，供 `/icode plan`/`/icode start`（无参）复用进入步骤1

> **`00_init.md` 结构**（log 阶段产出 vs init 阶段产出的差异）：
> - **init 阶段产出**：`00_init.md` = 需求初稿（功能/性能/非功能需求）
> - **log 阶段产出**：`00_init.md` = **修复需求**（"症状描述 + 根因假设 + 新增需求点 + 链路图"）
>   - 1. 症状描述（"实际 X，预期 Y"）
>   - 2. 根因假设（指向 log_analysis.md 的「5. 根因分析」）
>   - 3. 新增需求点（"修复 bug X" + 验证方式）
>   - 4. 链路图（before = 带 bug 当前链路标故障点 file:line / after = 修复后链路标 `[+]`/`[~]`/`[-]` / 改动点清单对齐第 3 节）--before 取自阶段 1 状态链路图，一图流说明"修哪、怎么修"
>   - **不**包含功能规格/性能要求/非功能需求（这是 init 产出的特征）
> - 步骤1 plan 阶段读取 `00_init.md` 时，需识别是"修复需求"还是"功能需求"，对应不同的计划重点
9. **创建/更新 `.ico_metadata.json`**：

   ```json
   {
     "requirement": "{根因转成的修复需求描述}",
     "created_at": "当前时间",
     "status": "log_done",
     "completed_steps": ["log"],
     "code_files": [],
     "requirement_summary": "{根因一句话摘要，≤100 token}",
     "requirement_points": ["修复要点1", "修复要点2"],
     "keywords": "{≤8个技术关键词}",
     "indexed": false,
     "ticket_id": "{写入索引后回填}"
   }
   ```

10. **写入全局索引**（步骤9之后）：Read `~/.claude/icode_data/index.json`（不存在则创建），追加一条记录：
    - `ticket_id` = `{工程名}-{N}`（冲突时加 `project_path` 短 hash 后缀，规则同 init）
    - `project_path` = 当前工程根绝对路径；`out_dir` = `.icode_output/.icode_output_{N}`
    - `requirement_summary` / `requirement_points` / `keywords` 取自步骤9 metadata
    - `has_00_init` = true（log 已产出 `00_init.md`），`has_plan` = false，`status` = `log_done`，`created_at` = 当前时间，`last_used_at` = 当前时间（首次写入=created_at），`hit_count` = 0，`stale` = false，`stale_reason` = null，`stale_checked_commit` = null，`created_commit` = `git rev-parse HEAD`（只读，非 git 仓库为 null），`created_branch` = `git rev-parse --abbrev-ref HEAD`
    - 写回 index.json，置 metadata `indexed = true`、`ticket_id`；**写后执行唯一性验证**（见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「全局索引写入·写后唯一性验证」）
11. 提示用户：根因已定，可敲 `/icode plan` 或 `/icode start`（无参）复用本目录的 `00_init.md` 进入修复流程；若对根因有异议，继续对话即可重跑对抗分析

## 追问机制（与 init 的差异）

- **A 输入收敛期**（阶段0）：✅ 支持追问，快速集中问，凑齐就开干
- **B 分析过程中**（阶段1-3）：❌ 不主动打断，自主收敛。但**用户随时可插话**重新定向（如"别查A模块了，看B模块"），AI 接受新方向重定向，不主动追问
- **C 根因出来后**（log_done 后用户质疑"不是这个"）：✅ 支持，切回 `log_in_progress`，**只重跑被质疑的根因分支**（不重跑整个对抗，省 token）

**判定"是否还在 log 上下文"的依据**：
- 最新 `.icode_output/.icode_output_N/` 目录的 status 为 `log_in_progress` 或 `log_done`
- 当前用户消息是对该目录根因报告的质疑/补充/澄清
- 否则按正常对话处理

> 强制思考前置完整规则**必须先 Read [references/thinking.md](../references/thinking.md) + [references/anti_laziness.md](../references/anti_laziness.md)**（不得凭概述/记忆执行，否则产出不合规），本步骤子项见上方步骤4。

## 反偷懒机制

- **禁止跳过基线检查直接 grep 日志**——基线检查是防"猜根因走弯路"的前置，必须先做
- **禁止对抗分析走过场**——质疑者必须独立 spawn、独立读日志、返回具体反驳+日志行回指，不得"无问题/通过"空泛结论
- **禁止伪造根因共识**——证据不足时必须降级"未定论"，不得凑根因
- **每条根因结论必须能回指日志片段**——做不到回指的只进「待验证假设」
- **禁止照抄历史工单根因**——历史参考只作启发，当前症状与历史必有差异

## 与步骤1的衔接

`/icode plan` / `/icode start`（无参）启动时，如果检测到最新目录 status 为 `log_done`（或 `init_in_progress`，详见 SKILL.md 复用规则）且无 `01_plan.md`：
1. 复用该目录（不创建新目录）
2. 将 `00_init.md`（log 已生成修复需求）作为步骤1的主要需求输入
3. `log_analysis.md` 作为背景参考（步骤1可读其根因+建议修复方向）
4. 步骤1完成后切换 status 为 `plan_done`，`completed_steps` 追加 `"1"`

详细衔接规则见 [01_plan.md](01_plan.md)。
