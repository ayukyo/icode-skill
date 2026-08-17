# 步骤 6 — 终极终审 + 出具报告 + 统一修复

**命令**: `/icode audit`
**产出**: `{ICODE_OUT_DIR}/06_audit.md`（含修复日志段）+ 回写 `{ICODE_OUT_DIR}/03_plan_final.md` 的「实现偏差备忘」段（6.2 第5步）
**会话**: 主会话

## 本步骤 L1/L2 检查项声明

按 SKILL.md「强制阻断边界矩阵」定义，本步骤触发的检查项：

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 前置产物缺失（`03_plan_final.md` 或步骤 4 代码文件不存在） | 报错退出，提示先跑 `/icode merge` 或 `/icode code` |
| **L1·致命** | 当前工单是 debug 工单（`metadata.debug == true`） | 报错退出，提示：`/icode audit` 不接受 debug 工单（debug 工单不入索引、不参与主流程，纯作为正常工单的对照；详情见 [references/debug_mode.md](../references/debug_mode.md)） |

**L3·重要**（矩阵段定义）：
- §6.7 视角 A（原始需求）失败 → 走 §6.2 强制修复流程（user 决定）
- §6.7 视角 B（limit）失败 → 同上（`limit_refs` 空数组/缺失时**先检测 03_plan_final 是否引用 limit，未引用才跳过**，引用则回补后实际核对——见 §6.7「空数组处理」）
- §6.7 视角 C（必要性）失败 → 同上（删除重复实现或改为复用现有实现）
- 6 维度评分有 ❌ → 走 §6.2 修复，**不阻断流程**

## 前置校验

> **读决策锚点**（启动时）：若 `metadata.anchors_enabled != false`，Read `{ICODE_OUT_DIR}/.decision_anchors.json`（不存在则跳过），获取上游关键决策摘要（requirement_digest/key_decisions/design_4dims/deviations/open_risks）作本步骤上下文，不替代产物。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

检查 `{ICODE_OUT_DIR}/03_plan_final.md` 和步骤4创建的代码文件是否存在，缺失则报错。

### 前置：worktree 验证基线落后检测（worktree 工单）

读取 metadata `worktree_path` 非 null 时：检测本工单分支是否落后主仓（验证基于过时基线）——

```bash
# 落后检测：结果 > 0 = 落后。⚠️ 勿用 `git merge-base --is-ancestor <worktree_branch> <目标基分支>`
# —— worktree 分支零提交时（icode 从不 commit 的常态）该命令恒返回「是祖先」= 不落后，检测完全失效漏报
git rev-list --count <worktree_branch>..<目标基分支>     # 目标基分支 = 回流 §4 的 master/主分支
```

结果 > 0 → 输出 L3 提示：「验证前建议先 `git merge <目标基分支>` 进 worktree 分支再终审」（不阻断）。目标基分支未知时跳过（提示用户自查）。

## 前置：patch 配合

> 工单可能已走过 `/icode patch` 追加修改（`{ICODE_OUT_DIR}/08_patch.md` 存在且有 Patch 段，或 `metadata.patch_count > 0`）。本步骤启动时 **Read `08_patch.md`**（不存在则跳过本段，走原流程），按以下规则配合：

1. **追溯矩阵扩展**：计划功能点 → 代码位置映射 = `03_plan_final.md` 功能点 + `08_patch.md` Patch 功能点（补丁功能点标注"补丁来源"），逐一给出代码证据位置
2. **计划 vs 代码差异摘要扩展**（6.1 第7步）：`diff_summary` 的 `text_a`（计划文本）= `03_plan_final.md` + `08_patch.md` 补丁计划合并文本（手动对比降级路径同理）；patch 修改不再显示为"计划外偏离"
3. **§6.7 三视角覆盖补丁**：视角 A（原始需求）核对补丁是否满足用户需求；视角 C（必要性）对补丁新增功能点同样做全工程等价实现检索
4. **重跑保护**：若 `06_audit.md` 已含「补丁记录（patch 追加）」段（之前 patch 追加过），**重跑审计后必须保留该段**——新报告出具后重新追加或合并，不得丢弃（回读需区分主流程结论与补丁演进）
5. **verdict 评估纳入补丁**：终审 verdict 判断把补丁影响纳入（补丁可能改变核心方案有效性，需一并评估）

## 代码新鲜度

**开始前必须重新读取所有代码文件**（基于步骤5结论/记忆写=偷懒=不合规）。步骤 5 的修复已落盘，审计必须基于最新代码。**必须输出 Read 确认行**：`📖 已 Read 代码文件（最新版）：<file1>, <file2>, ...`（无确认行=没读=不合规）。

## 6.1 出具终审报告

1. 检测最新目录，确定 `ICODE_OUT_DIR`
2. 读取 `03_plan_final.md` 和 `.ico_metadata.json` 的 `code_files` 列表 + `code_deviations`（步骤4主动偏离记录，供6.2偏差备忘汇总）
3. 额外读取 `05_deepcheck.md`（若存在）
4. **强制思考前置**（不可跳过，缺证据视为不合规；按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行）：本步骤子项（至少3步）= 构建追溯矩阵（计划功能点→代码位置）→ 汇步骤历史 → 规划 6 维度审计策略
5. 输出：`▶ 步骤6 终审开始`
6. **重新读取所有代码文件**
7. **计划vs代码差异摘要**：用已读取的 `03_plan_final.md` 内容（步骤2）和代码文件内容（步骤6），调 `mcp__cheap-research__diff_summary(text_a=计划文本, text_b=实现文本, focus="计划vs代码偏离")`，输出差异摘要（偏离项 + 未实现功能点 + 新增功能点，≤500 token）。**降级**（cheap-research 不可用）：主代理手动对比，不阻塞。**结果供 6.1 维度 2「执行精准度」+ 维度 3「方案偏离度」直接引用**。
8. **执行终审**

### 前置强制执行门（防"复用步骤5结论跳过审计"）

**在写入 06_audit.md 任何内容之前，必须依次完成以下动作。未完成即写产物 = 跳过步骤 = 严重违规。**

1. **Read 所有代码文件**：Read 步骤4 产出的每个代码文件（最新版，不是步骤5 的记忆版），输出 `📖 已 Read 代码文件（最新版）：<file1>, <file2>, ...` 确认行
2. **Read 计划与 metadata**：Read `03_plan_final.md` + `.ico_metadata.json`（`code_files`、`code_deviations`、`fix_tiers`、`limit_refs` 等字段）
3. **Read 步骤5 复检产物**：Read `05_deepcheck.md`（存在时），记录 Reverse/Fixed/Free 已查角度 + 残留风险
4. **独立列出审计角度**：在思考块列出"步骤5 未覆盖/更深层的 N 个角度"（至少 3 个，如架构合理性、跨模块耦合、长期维护性、与工程既有模式一致性等），**不得从步骤5 直接抄角度列表**
5. **构建追溯矩阵草稿**：在思考块列出计划功能点 → 代码位置映射（至少 3 个功能点）

**只有以上 5 步全部完成，才能进入 7 维度评分**。特别注意第 4 条——"步骤5 已查无新问题→步骤6 也无新问题"=跳过审计=不合规，禁止直接复用步骤5 结论。

### 审核维度（7个，全部覆盖）

1. **实施完整度** — 计划所有功能点 100% 落地，每个功能点必须给出代码证据位置。**三档实施范围核对**（反偷懒第 26 条）：Read `metadata.fix_tiers`（plan 落盘的三档分级）→ A 档必做项全部落地、B 档仅实施 `confirmed_B_fixes` 确认项、C 档未混入；字段缺失视为 null，从 `03_plan_final.md` §4.5 文本读。**测试核对**：Read `metadata.test_cmd`/`test_outcome`/`test_failures`--`test_outcome=pass` 强化完整度证据；`test_outcome=fail` -> 测试未通过的功能点不算 100% 落地，扣分并记入问题清单（按 6.2 强制修复流程）；`test_outcome=skipped` -> 标注"本工程无测试套件，完整度仅靠静态审查"，不扣分
2. **执行精准度** — 实现与计划一致，偏差处必须指出（文件+行号）
3. **方案偏离度** — 偏离项必须明确列出
4. **代码质量** — 可读性、性能、安全性、**注释完备性**（导出函数/接口/关键分支/数据结构注释是否齐全，对照步骤4 第6条）、**日志覆盖**（关键路径错误返回/状态跳转/外部交互/决策分支/降级重试是否有日志可排查，对照步骤4 第7条）、**优雅度6条**（①复用优先 ②风格对齐 ③调用链模式一致 ④最小侵入 ⑤接口克制 ⑥调用路径选择（架构一致性）——新增跨模块/跨端点调用 grep 工程既有同类调用对齐主导模式，不得绕过已注册路由/接收器直调，同函数内既有同类调用必须一致；对照步骤4 第9条）、**事务性/非事务性步骤分离**（多步骤业务流程须区分**事务性步骤**（主流程结果，失败应失败/回滚）与**非事务性/展示增强步骤**（APP/DP 展示数据上报、底图刷新、通知）——主事务已提交后执行的非事务性步骤，其失败**不得推翻/阻断已提交事务**，降级告警保留事务成功，识别「展示失败→误判整体失败→伪失败」；详见 08_patch 阶段2）
5. **跨文件一致性** — 接口变更全链路同步，上下游数据结构对齐
6. **残留风险** — 已知或潜在问题
7. **原始需求收敛**（本次新增·第 7 维度） — 对照 `metadata.requirement`（user 原始需求，逐条拆解）vs 步骤 4 代码 + 步骤 6 报告，**逐条核对 user 原始需求是否被满足**。**遗漏项强制追加为修复任务**（不阻断流程，但记入 `06_audit.md` 问题清单，按 6.2 强制修复流程处理）。

   **数据流闭环**（与 plan 步骤的 limit_refs 字段配合）：
   - **plan 阶段**：plan §3/§4/§6 引用 limit 条目时，写入 metadata `limit_refs` 数组（每条 `{redline_no, source: "main"|"local", title, applied_in: [...]}`）
   - **本步骤（audit §6.7）**：**Read `metadata.limit_refs` 数组**作为对照基线，知道 plan 引用了哪些红线 → 对照实际代码是否真的遵循
   - **未读取后果**：audit 不知道 plan 引用了哪些 limit，无法做"plan 引用 → 实施遵循 → audit 收敛"的完整闭环
   - **空数组处理**（**向后兼容 + 防掩盖**）：`limit_refs` 为空数组或字段缺失时，**先 grep `03_plan_final.md` 是否实际引用 limit**（模式「红线 N」/「红 N」）：
     - 计划**确实引用了** limit 但 `limit_refs` 缺失/不全 → **回补 `limit_refs` 后执行视角 B 实际核对**（不静默跳过），并在报告中标注「plan 引用 limit 但 `limit_refs` 缺失 → 已回补」
     - 计划**确实未引用** limit → 视角 B 跳过（维持原向后兼容行为），仅做视角 A（需求角度）+ 视角 C（必要性角度）对照。**避免在旧工程/无 limit 的工程上输出无意义的"视角 B 通过"**

   **§6.7 三视角对照**：
   - **视角 A（需求角度）**：user 原始需求 vs 实际产物（防"user 想要的没做"）
   - **视角 B（limit 角度）**：plan 引用的 limit vs 实际产物（防"plan 说遵循 limit 但代码违反"）
   - **视角 C（必要性角度，防重复实现）**：实际产物 vs 现有实现（防"实现了不该实现的功能"）——对最终产物的每个新增功能点，按 [references/necessity_check.md](../references/necessity_check.md) 全工程检索等价实现（`rg -in '<需求关键词>'` + Read 命中处行为链），检查新实现是否与现有实现重复（**含"新实现写出来也不会执行到（被已有入口/拦截先返回挡掉）"的情形**——比"功能近似"更确凿的重复）。格式见下方视角 C 表
   - 三视角都通过 → §6.7 收敛；任一未通过 → 走 §6.2 修复流程

   **视角 C 必要性收敛表**：
   ```markdown
   | 功能点 | 现有实现（file:line） | 新实现（file:line） | 关系 | 收敛判定 |
   |--------|---------------------|--------------------|------|---------|
   | 某功能点 | 已有模块入口拦截（src/foo.c:42） | 新增 per-item 门控（src/bar.c:514） | 重复（新实现永不执行） | ❌ 重复实现，追加修复（删除新实现或改为复用） |
   | 某功能点 | 无等价实现（全工程检索无命中） | 新增判断逻辑（src/baz.c:88） | 独立 | ✅ 无重复 |
   ```

   **与 §6.1 实施完整度区分**：
   - §6.1 = **计划角度**对照（计划功能点 → 代码位置）—— 检查"代码做了没"
   - §6.7 = **需求角度**对照（user 原始需求 → 最终产物）—— 检查"user 想要的做了没"

   **与 §6.2 强制修复的关系**：
   - 6.2 修复的是 §6.1-§6.6 发现的问题
   - §6.7 遗漏项**也走 6.2 流程**（强制修复），但优先级：user 原始需求遗漏 > 计划偏差

   **格式**（逐条对照）：
   ```markdown
   | user 原始需求（拆解） | 计划 §X.Y | 步骤 4 代码位置 | §6.1 实施状态 | §6.7 收敛判定 |
   |---------------------|-----------|----------------|--------------|--------------|
   | <需求点 1> | §2.1 / §3.1 | src/foo.c:42 | ✅ 已实现 | ✅ 满足 |
   | <需求点 2> | §2.2 / §3.2 | src/bar.c:88 | ⚠️ 部分实现 | ❌ 边缘 case 未覆盖（追加修复） |
   | <需求点 3> | — | — | — | ❌ 计划遗漏（追加修复） |
   ```

   **实战背景**：icode 多次踩过"计划实现完整但 user 原始需求没满足"的坑——例如某 limit 红线最初只约束"代码层 RAII"，遗漏了"日志必须带原始值（双值日志）"这一 user 关心的需求。§6.7 就是为防止这类"计划角度 OK 但需求角度漏了"的问题。

   **协同关系**：本段是本次新增的 audit 步骤第 7 维度（对照原始需求评估最终产物，追加未完成项为新任务），与现有 6 维度（实施完整度/执行精准度/方案偏离度/代码质量/跨文件一致性/残留风险）形成互补。

   **与 limit 红线协同**：如果本工程有 limit（`~/.claude/icode_data/limits/<id>.md`），§6.7 还要额外核对**实际产物是否与 limit 红线一致**（与 plan §10 #6 + §3/§4/§6 引用契约形成三层验证：plan 引用 → 实施遵循 → audit 收敛）。

### 部署后验证建议（audit 附加输出）

> **目的**：audit 是静态分析，无法实机验证。当检测到静态分析覆盖不到的运行时风险时，输出**部署后验证建议**（非阻断，只给用户预期，不新增步骤、不强制实机流程）。

当 audit 检测到以下情况时，在 `06_audit.md` 输出对应部署后验证建议：

1. **首次激活路径**：plan / deepcheck 涉及的跨层接口在历史日志中**无成功调用记录**（判定见 [references/first_activation_path.md](../references/first_activation_path.md)）→ 建议：「本工单涉及首次激活路径，部署后请跑日志验证确认该接口被成功接受。」
2. **状态机续接**：plan 涉及状态机的**非常规路径**（续接 / 嵌套 / 严格前置守卫）→ 建议：「本工单涉及状态机续接，部署后请确认状态转换链完整。」
3. **多仓库联动**：plan 涉及 **2+ 仓库**的联动修改 → 建议：「本工单涉及 N 个仓库联动，部署时需同时更新所有仓库。」

> 建议仅作提示，不改变评分、不阻断流程；若命中 1+ 条，可在结论处追加一行「部署后验证建议」摘要。

### 执行流程

建立追溯矩阵（逐条对照计划功能点/接口/约束，标记代码对应位置）→ 基于矩阵逐维度评分 → 对照校验项逐条勾对 → 汇总问题清单 → 给结论

### 报告格式

总体评分（百分制）+ 每维度独立评分评语（**每维度必须列 file:line 证据 + 评分理由 ≥2 句实质，不得只概括**）+ 问题汇总清单（含位置、严重程度）+ 结论（通过/有条件通过/不通过）

写入 `{ICODE_OUT_DIR}/06_audit.md`。

参考步骤 5 深检历史，避免重复报告已修复问题。**不得直接复用步骤5结论**（如"步骤5 已查无新问题→步骤6 也无新问题"=偷懒=不合规）。**必须独立深查**：列出步骤5 Free 15 角度之外/更深层的 N 个角度（如架构合理性、跨模块耦合、长期维护性、与工程既有模式一致性等），逐个独立查并给 file:line 证据。

### 反偷懒机制

必须先建立计划-代码追溯矩阵，再逐维度评分。禁止跳过追溯直接写"全部通过"。

## 6.2 强制修复

1. 读取 `06_audit.md` 中的问题清单
2. 按严重程度排序（高 → 中 → 低）逐个修复
   - 每个问题用 Edit 工具修复，**禁止删除现有注释**
   - 修复后追加记录到 `06_audit.md` 的「修复日志」段
3. 全部修复后做全局编译验证，最多 3 次
4. 更新 `.ico_metadata.json`：`status = completed`
5. **回写实现偏差备忘到 `03_plan_final.md`**（不可跳过，详见下方「实现偏差备忘」规范）
6. **worktree 工单产物归档（若 `metadata.worktree_path` 非 null）**：把核心产物（`.ico_metadata.json`/`00_init.md`/`01_plan.md`/`03_plan_final.md`/`log_analysis.md`）复制到 `~/.claude/icode_data/worktree_archive/<project_id>/<ticket_id>/`，写 `metadata.archive_path`（详见 [references/worktree_isolation.md](../references/worktree_isolation.md)「产物归档」）——防 worktree remove 后产物丢失、保后续检索可读档复用完整 ADR/根因
7. **刷新全局索引最终状态**：Read `~/.claude/icode_data/index.json`，**按 metadata 的 `ticket_id` 定位**本工单条目，更新 `status` = `completed`，`requirement_summary` 若与最终交付有显著偏差则基于 `03_plan_final.md`+交付成果刷新一次（确保未来检索命中的摘要准确反映最终成果而非中途状态）；**若该工单当前 `stale=true`，重置 `stale=false`+`stale_reason=null`+`stale_checked_commit=null`**（产物可能经本轮更新，旧 stale 判据失效；下次检索注入前由过时校验按当前 `01_plan` 锚点重评，盲重置安全不致误注入）；若已归档（步骤6），同步写 index 条目 `archive_path`；**确认 verdict（方向结论，v2 新增）**：向用户确认本工单核心方案最终方向结论--默认保持 `unknown` 不阻塞流程；若方案已实机验证有效标 `verified`，若核心方案被证伪/已回退标 `disproved`（填 `verdict_reason`+`correct_direction`；可选 `--premise-dep` 填证伪依赖的外部模块，支持硬复活检测），若被替代方案取代标 `superseded`（填 `superseded_by`）；标注时回填 `verdict`+`verdict_reason`+`correct_direction`+`verdict_source`（`machine_test`/`review`/`user`）+`verdict_at`（运行时取系统时间）；详见 SKILL.md「verdict 字段族」。写回 index.json（metadata + index 同步，不得只写其一）。
8. 输出交付总结

### 实现偏差备忘（回溯标注，防回读误解）

**目的**：步骤4/5/6 实施过程中，实际实现可能与步骤3定稿计划有实质偏差。若不回溯标注，未来回读 `03_plan_final.md` 时会看到"计划说 A、代码做 B"而误解。本步骤在步骤6终审收敛时，把所有实质偏差汇总回写到 `03_plan_final.md` 末尾，**不改计划正文**（保留计划 vs 代码的对照价值，步骤5逆推复检依赖此差异）。

**偏差来源汇总**（步骤6 统一回写，步骤4/5 不各自回写以免多次改动计划）：
- 步骤4 编码时主动偏离（发现计划不可行而调整）：记录在 `.ico_metadata.json` 的 `code_deviations` 字段（数组）
- 步骤5 逆推复检发现的欠实现/偏离：记录在 `05_deepcheck.md`
- 步骤6 终审的方案偏离度：记录在 `06_audit.md`

**测试期望偏差特别说明**（针对步骤4编码时发现测试期望写错的情况）：
- 步骤4 实际运行后，若发现计划/校验项中的"预期结果"数学不严谨或写错（如 `lcm(46341,46341)→OVERFLOW` 实际 46341²<INT_MAX 不溢出），代码本身正确但测试期望错误
- 这类偏差不是"代码偏离计划"，而是"计划预期 vs 实际行为"差异
- 处理方式：在 06_audit.md 的「修复日志」段独立记录"测试期望偏差"+ 修正后的期望值；同步在 03_plan_final.md 的「实现偏差备忘」用 `### 测试期望偏差-N: {简述}` 子章节记录
- 不计入"代码实质偏差"（代码本身正确），但需留痕防止回读误解

**回溯标注门槛**（只标"实质不一致、回读会误解"的偏差，细节差异不标以免噪音）：
- ✅ 标注：接口签名变更、数据结构变更、功能未实现/换实现方式、关键逻辑分支变更、异常处理策略变更
- ❌ 不标：变量名/注释/格式差异、纯内部实现细节、不影响对外行为的等价改写

**写入格式**：用 Edit **填充步骤3 定稿时预留的 `## 实现偏差备忘（步骤6 终审回写）` 空段**（`03_plan_final.md` 定稿自检时已预留占位标题；若该段已被前次终审填充过，则覆盖更新为最新内容）：

```markdown
## 实现偏差备忘（步骤6 终审回写）

> 本段由 `/icode audit` 步骤6 在终审后回写，记录实际实现与定稿计划的实质偏差。计划正文保持原样不动，本段仅供回读对照，避免"计划说 X、代码做 Y"的误解。

### 偏差-1: {简述}
- **计划说法**：{03_plan_final.md 中的原设计，引用章节/行号}
- **实际实现**：{代码实际做法，引用 file:line}
- **偏差原因**：{为何偏离——计划不可行/发现更优方案/约束变化等}
- **影响评估**：{对外行为是否变化、是否破坏兼容性、是否需告知使用者}

### 偏差-2: ...
```

**无偏差处理**：若汇总后确认无实质偏差，仍必须写入 `## 实现偏差备忘` 章节并注明"本次实施与定稿计划无实质偏差"，作为已回溯的留痕（不得跳过）。

**反偷懒**：禁止以"无偏差"为由跳过本章节；禁止把细节差异堆砌进备忘（抬高回读噪音）；每条偏差必须能回指到计划章节 + 代码行。

## 6.3 最终交付

```text
=== ICode 工作流完成 ===
[✓] 步骤1: 计划拟定 — 完成
[✓] 步骤2: 计划审查 — 完成
[✓] 步骤3: 计划定稿 — 完成
[✓] 步骤4: 编码实施 — 完成
[✓] 步骤5: 三阶段复检 — 总 {deepcheck_total_rounds} 轮
[✓] 步骤6: 终极终审 — 评分{分数}, {结论}
产出目录: {ICODE_OUT_DIR}/
```

## 6.4 交付报告提示

步骤6 完成后，提示用户：`▶ 步骤6 终审完成。可选：运行 /icode readme 生成交付报告 + 跨领域简报（两份）`

> 交付报告（原 6.4 文档化）已拆为独立步骤7 `/icode readme`，用户按需手动触发。步骤6 不再自动生成报告。详见 [07_readme.md](07_readme.md)。

### 6.4 worktree 回流提醒（worktree 工单完成时）

读取 metadata `worktree_path` 字段：**非 null**（本工单在 worktree 内完成）→ 交付总结后追加以下提醒（未进 worktree 的工单跳过本段）：

```
▶ worktree 回流提醒：本工单改动仍在 worktree（icode 不 commit），请按二选一方案手动回流——
  ①（推荐）在 worktree 内审阅改动后自行 commit → 主仓 switch 目标分支 → git merge icode/<ticket-slug>（或 PR）
           → git worktree remove <worktree路径> → git branch -d icode/<ticket-slug>（先 remove 再删分支！）
  ②（不提交）手动带出 worktree 内改动 → 确认已保存后 git worktree remove --force <worktree路径>
  ⚠️ 未回流前勿删 worktree：未提交改动时 git worktree remove 失败是保护（不是故障）。
  ⚠️ 回流前产物留档：交付报告与全部产物都在 worktree 内，remove 后随之消失，需留档先复制出来。
  ⚠️ 若有业务子仓隔离（metadata.sub_worktrees 非空）：先对每个子仓隔离 checkout commit + merge 回原子仓，
     再 git -C <原子仓> worktree remove <子仓隔离路径>，最后才 remove super-worktree（见 worktree_isolation「⑤ 业务子仓隔离」）。
  完整指引见 [references/worktree_isolation.md](../references/worktree_isolation.md) §4。
```

### 6.4 worktree 空间自查（每工单收尾例行提示）

防僵尸 worktree 堆积（每忘清一个 ≈ 一份完整源码 + 独立构建产物，C++ 工程磁盘近似翻 N 倍）。附命令：

```bash
git worktree list                                          # 看所有 worktree 与分支（首行是主仓自身）
git worktree list --porcelain | grep -c '^worktree '       # 数量；实际额外 worktree 数 = 计数 − 1（勿把主仓算进去）
du -sh <各 worktree 路径>                                  # 空间占用
```

提醒：「做完即 remove，勿堆积僵尸 worktree；`git worktree prune` 对目录仍在的僵尸无效（只清目录已删的残留），真正清理只能 `git worktree remove <path>`」。

## 补丁记录（/icode patch 追加）

> 本段**不是**步骤6 的正文内容，而是后续 `/icode patch` 调用时**运行时追加**的说明——供回读区分主流程结论与补丁演进：

- `/icode patch` 完成后，在本文件**末尾追加** `## 补丁记录（patch 追加）` 段（含 Patch N 摘要 + 终审结论是否需要修正；终审结论被补丁改变时**明确标注"原结论已过时，以补丁为准"**）
- **不覆盖原正文**——6.1 终审报告 / 6.2 修复 / 实现偏差备忘保持原样，补丁影响单独成段
- 追加式演进：多次 patch 多次追加，每段带 Patch N 编号，回读即得完整演进链
- 补丁的完整记录（增量计划/实施/验证）在 `08_patch.md` 的对应 Patch N 段，本段仅摘要
- **追问补充行**（与「patch 会话语义」配套）：同一 Patch N 内用户追问导致**代码修改或结论变化**时，在原补丁记录段内追加一行 `- Patch N 追问补充：<变化摘要>`（不新增段、不改原行）——与 `08_patch.md` 的「追问补充」小节对应；纯补充信息不改变结论 → 不追加

## 完成前自检（必须填，未填项标 ❌=不合规）

- □ 输出了 `📖 已 Read` 确认行（列出实际 Read 的代码文件）
- □ **产物集完整性机器终检通过**（防"过程文档缺件但 audit 照常放行"）：写 `06_audit.md` 结论前运行下方「产物集完整性终检」命令，任何产物缺失 / `status` 词表外 / `code_files` 为空均标 **L2 记入问题清单**（缺失产物 = 上游步骤漏产出，按 6.2 强制修复流程补齐，**不得以"内容已讨论过"豁免文件缺失**）
- □ 未复用步骤5结论，独立列了"步骤5未覆盖/更深层角度"并逐个查
- □ 7 维度每维度有 file:line 证据 + 评分理由 ≥2 句实质（含 §6.7 原始需求收敛）
- □ 测试结果已核对（§6.1 测试核对：`test_outcome` 值 + 失败项是否记入问题清单）
- □ 无"无新问题""整体通过"等空泛结论（每条结论有具体证据）
- □ 终审时确认了 verdict（默认 `unknown` 不阻塞流程；标注 `verified`/`disproved`/`superseded` 时回填 `verdict_reason`/`correct_direction`/`verdict_source`/`verdict_at`，双写 metadata + index 同步）

### 产物集完整性终检（完成前自检的机器命令）

> **落点约束**：终检必须在**本工单所在 checkout 内**执行——`worktree_path` 非 null 时先 `cd` 进对应 worktree 再跑；在主仓跑会找不到 worktree 内产物 → 误报缺失（cwd 契约的机器校验延伸，§9.5-⑤）。

```bash
python3 -c "
import json,sys,os
d=os.path.join('{ICODE_OUT_DIR}')
req=['01_plan.md','02_review.md','03_plan_final.md','04_code_review_fix.md','05_deepcheck.md','06_audit.md']
missing=[f for f in req if not os.path.exists(os.path.join(d,f))]
import glob
json_cnt=len(glob.glob(os.path.join(d,'review_round_*.json')))
m=json.load(open(os.path.join(d,'.ico_metadata.json')))
valid={'init_in_progress','plan_done','review_in_progress','review_done','plan_finalized','code_in_progress','code_done','deepcheck_in_progress','deepcheck_done','completed','log_in_progress','log_done'}
st=m.get('status')
miss_txt='无' if not missing else ','.join(missing)
status_txt='OK' if st in valid else '词表外:'+str(st)
cf_ok='True' if (m.get('code_files') or []) else 'False'
print(f'缺失产物: {miss_txt}; review_round JSON: {json_cnt}; status: {st} {status_txt}; code_files 非空: {cf_ok}')
sys.exit(1 if (missing or (st not in valid) or not (m.get('code_files') or [])) else 0)
"
```

- **缺失产物 / `status` 词表外 / `code_files` 为空 → 退出码非 0**：逐项按 L2 记入 `06_audit.md` 问题清单，走 6.2 强制修复流程补齐后再出结论（`04_code_review_fix.md` 缺失 = 步骤4 未产出 1.5 复检，须回补；`review_round_*.json` 全缺 = 步骤2 审查无结构化记录，须回查）。**不得以"这些内容我在会话里讨论过"豁免文件缺失**——产物集是下游步骤与回读的唯一磁盘依据
- `code_files` 为空（S8）：即使代码已写，也标 L2，回步骤4 补记 `code_files`（相对项目根路径数组），否则步骤5/6 的前置校验无代码证据对象

## 6.5 schema 状态汇总（自动写入，可缺省）

> **自动写入，非交互**：本段由步骤 6 启动时自动读取 `.ico_metadata.json.template_version` + `migration_log` 数组输出；不阻塞流程、不询问用户；缺字段零退化（视为 v0 / 0 条迁移）。

**触发位置**：步骤 6 启动时（前置校验之后，「6.1 出具终审报告」之前），自动追加本段到 `06_audit.md` 末尾。

**输出模板**（按字段可缺省）：

```markdown
## schema 状态汇总

- 当前 schema 版本: {template_version | "v0（待迁移）" | "未知（field 缺失）"}
- schema 迁移次数: {len(migration_log) | 0 | "未知"}
- 最近一次迁移: {migration_log[-1].at | "无"}
- 涉及产物文件: {migration_log[*].files 聚合去重 | "无"}
- 字段缺失兼容: 旧工单缺 `template_version` / `migration_log` 时视为 v0 / []，不报错
```

**字段缺失行为**（明确写明，便于审计追溯）：

| metadata 字段 | 实际值 | 终端输出 |
|--------------|--------|----------|
| `template_version` | `"v1.1"` | "当前 schema 版本: v1.1" |
| `template_version` | 缺失或 `null` | "当前 schema 版本: 未知（field 缺失）" |
| `template_version` | `"v0"`（极老工单） | "当前 schema 版本: v0（待迁移）" |
| `migration_log` | 数组长度 3 | "schema 迁移次数: 3" |
| `migration_log` | 缺失或 `null` | "schema 迁移次数: 0" |
| `migration_log[-1].at` | `"2026-07-25T12:34:56"` | "最近一次迁移: 2026-07-25 12:34:56"（取前 19 字符，去 T） |
| `migration_log[-1].at` | 缺失 | "最近一次迁移: 无" |
| `migration_log[*].files` | 数组聚合 | "涉及产物文件: 01_plan.md, 03_plan_final.md, 05_deepcheck.md"（去重 + 排序） |

**反偷懒**：

- **禁止无字段硬编**：缺 `template_version` 时必须明确标"未知"而非盲目写"v1.1"（误标会让 audit 误以为已迁移）
- **禁止忽略 migration_log 长度**：0 与 1 与 3 是不同信号，必须如实输出数字
- **禁止 try/except 静默**：Read JSON 失败时输出"[schema 读取失败-原因 X]"，绝不含糊
- **禁止与其他段合并**：本段独立 H2 标题，便于将来 grep 工具检索"## schema 状态汇总"

**自动化要求**：实现可用 Bash + python 一行（例如 `python3 -c "import json,sys; d=json.load(open(sys.argv[1])); ..."` 嵌入式调用）；如失败则降级为手工填写模板 + 标 `[未自动化]`。
## 决策锚点（步骤6 完成后写）

步骤6 终审后，若 `metadata.anchors_enabled != false`，最终刷新 `.decision_anchors.json`：刷新 `deviations` + `open_risks`（终审汇总）。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

## MCP 推荐（强证据二元化）
| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| vision-bridge | 🟢* | UI 截图分析--用户给图时 |
| **cheap-research** | 🟢* | **降本**：diff_summary（计划vs代码差异摘要）+ fill_template（6.4 交付报告提示+偏差备忘）+ summarize（schema 状态汇总）。不接管决策：6.1 终审裁决/6.2 强制修复走主会话 |
| playwright | 🟢* | 真实 UI 验证（截图、交互）--前端工程时 |
| memory | ⚪ | 本步骤不推荐 |
| context7 | ⚪ | 本步骤不推荐 |

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
