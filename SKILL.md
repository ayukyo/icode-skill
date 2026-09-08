---
name: icode
description: 端到端编码工作流（步骤 0~6，含可选需求初稿与日志根因分析入口），支持分步手动调用：/icode help, /icode install, /icode init [<粗略需求>] (需求初稿), /icode log [零散信息...] (日志根因分析→转修复需求), /icode start <需求> (全流程), /icode fast <需求> (精简全流程), /icode plan <需求> (计划), /icode review [N] (审查), /icode merge (定稿), /icode code (编码), /icode deepcheck (复检), /icode audit (终审), /icode patch [问题或新需求] (追加修改), /icode verify [--deploy|--listen|--device|--reuse-build] (实机验证), /icode doc [自然语言] (工程级知识库), /icode limit [自然语言] (项目约束红线), /icode readme (交付报告+跨领域简报), /icode ppt [自然语言] (PPT生成), /icode status (工单状态), /icode list [关键词] (跨工程工单查找), /icode bak [--project <path>] (工程工单备份), /icode worktree --update/--close/--reopen/--submit-check (git worktree 受控迁移/提交后收敛/显式恢复/交付前提交契约检查)。新建工单入口支持 --worktree opt-in
---

**版本**: v2.19.0

# ICode 全流程编码工作流（步骤 0 + 1~6）

端到端编码工作流，将需求到交付拆解为严格步骤，每步可单独调用，方便你自行切换模型。**本文档是路由器**：内核规则内联，详细规则一律放 `steps/*.md` 与 `references/*.md`，需要时按路由表 Read（懒加载，省 token）。

- **步骤 0（可选）**：需求初稿对话，多轮迭代后落档为 `00_init.md`（含链路图：修改前/后链路 + 改动点），独立步骤、不自动串联到步骤1
- **步骤 1~6**：拟定计划 → 审查 → 定稿 → 编码 → 复检 → 终审

> **主流程步骤真源（防误用，唯一真源 = `steps/` 目录，启动强制 Read）**：步骤编号 / 产物文件名 / `completed_steps` 合法值**一律以 `steps/*.md` 实时清单为准**（`ls steps/*.md` 完整列出，含主流程与辅助入口 log / doc / limit / status / install / list / bak / verify，及精简全流程入口 fast）——**本块仅示意，steps/ 演进后以目录为准，勿依赖写死**。`/icode start` / `/icode fast` / `/icode plan` 进入第一步**先 `ls steps/*.md`** 核对，不按"编码→测试→部署"直觉推断
> - 当前主流程示意（以 `ls steps/*.md` 为准）：`00_init → 01_plan → 02_review → 03_merge → 04_code → 05_deepcheck → 06_audit → 07_readme → 08_patch`；**不存在 `03_code` / `04_test` / `05_deploy`**（测试验证在 04_code 子段，部署/回归归 07_readme / 08_patch / verify）
> - 辅助独立步骤（doc / log / limit / status / install / list / bak / verify）不参与 1~6 推进；**fast 为精简全流程（非辅助独立步骤）**——参与 1~6 但各步缩略（见命令表 fast 行）
> - **强制**：产物命名 + `completed_steps` 写号**对照 `ls steps/*.md` 实时结果**（如入口含 `log` → 可写 `"log"`），不在清单 → 停下核对，禁止自造产物占位；steps/ 目录与本文档不一致时**以 steps/ 目录为准**

## 通用约定（对话语言）

**AI 对用户的回复一律使用中文**（提示 / 解释 / 报告 / 追问 / 总结 / 决策说明 / 输出行）。**工程内容保持原样、不翻译**：代码、标识符、产物文件名、命令、日志原文、报错原文、设备输出、配置字段值。仅当用户明确要求英文回复时切换。

> 适用所有 `/icode` 命令的会话交互与步骤内对用户的询问/报告；产物文件正文遵循既有中文风格撰写。

## 调用命令

所有输出保存在 `.icode_output/.icode_output_N/`（N 自动递增）目录下。**详细语义一律以 `steps/*.md` 为准，下表只给路由与关键 flag**：

| 命令 | 一句话用途 + 关键 flag | 创建目录？ |
|------|------|-----------|
| `[辅助]` `/icode help` | 输出使用流程示例与命令一览 | 否 |
| `[辅助]` `/icode install [--client codex\|all]` | MCP 环境检查+一键安装（扫描 `mcp/*/install.sh` 自检注册） | 否 |
| `[入口]` `/icode log [零散信息...]` | 日志根因分析→转修复需求；版本基线门；TB 复用/批量/`--debug`/`--worktree`；对外简报 | ✅ 每次都新建（同 TB 单复用除外） |
| `[入口]` `/icode init [<粗略需求>]` | 步骤0：多轮对话产出 `00_init.md`；`--worktree`/`--debug` | ✅ 每次都新建 |
| `[流程]` `/icode start <需求>` | 全流程：创建/复用目录 → 步骤1~6 串联；`--worktree` | ✅ 创建 / 复用 |
| `[流程]` `/icode fast <需求>` | 精简全流程：plan→review(1轮无对抗)→merge→code→deepcheck(Reverse)→audit；`--worktree` | ✅ 创建 / 复用 |
| `[流程]` `/icode plan <需求>` | 仅步骤1：拟定计划；`--worktree` | ✅ 创建 / 复用 |
| `[流程]` `/icode review [N]` | 仅步骤2：多轮循环审查 + 独立质疑者对抗（N=软上限轮数，默认3） | 用最新目录 |
| `[流程]` `/icode merge` | 仅步骤3：合并审查意见定稿 | 用最新目录 |
| `[流程]` `/icode code` | 仅步骤4：落地编码实施（含 Code Review Fix 4 维度复检；O-6 用户自担验证豁免） | 用最新目录 |
| `[流程]` `/icode deepcheck` | 仅步骤5：三阶段递进复检（Reverse→Fixed→Free；fast 只跑 Reverse） | 用最新目录 |
| `[流程]` `/icode audit` | 仅步骤6：终极终审 + 统一修复 | 用最新目录 |
| `[可选步骤7]` `/icode readme` | 一次性生成交付报告（自己看）+ `_brief.md` 跨领域简报（给其它模块/测试/产品） | 用最新目录 |
| `[独立]` `/icode patch [问题或新需求...]` | 追加修改：轻量四段式；不改变 status；`--listen`/`--test`（兼容别名→纯验证走 `/icode verify`） | 用最新目录 |
| `[独立]` `/icode verify [--deploy\|--listen\|--device\|--reuse-build]` | **实机验证（纯验证不改代码）**：结果记 `verification_runs`（与 patch_history 分离），不自动升级 delivery_verdict（[steps/verify.md](steps/verify.md)） | 否（写工单 metadata） |
| `[工程]` `/icode doc [自然语言]` | 工程级知识库生成/维护（`project_docs/`+`module_docs/`）；doc_worklist 防中断丢进度 | 否（写全局） |
| `[配置]` `/icode limit [自然语言]` | 项目约束红线（主存+单 checkout 覆盖）；plan/log 前置硬基线 + `limit_checkpoint.md` 读留痕 | 否（写全局 limits/ + 工程根 limit.local/） |
| `[交付]` `/icode ppt [自然语言]` | PPT 生成（4 类场景），16 套模板只换文字 | 否（写 `<工程根>/.icode_output/ppt/`） |
| `[查询]` `/icode status` | 只读查状态；`--verdict` 标注方向结论（双写 metadata+index）；`--scan-verdict` 批量扫证伪信号；`--validate` 产物集机器校验 | 否（`--verdict` 写 metadata+索引） |
| `[查询]` `/icode list [关键词]` | 跨工程工单查找（归档/备份活跃态展示） | 否（纯只读） |
| `[备份]` `/icode bak [--project <path>]` | 工程工单手动备份到全局快照（删工程前安全网） | 否（写全局） |
| `[生命周期]` `/icode worktree --update [--to-ref <ref>]` | 受控迁移活动实现根到新基线（11 阶段状态机） | 否 |
| `[生命周期]` `/icode worktree --close [--ticket <id>]` | 提交后收敛：G4 在线证据核验 → 分阶段 close_state 幂等推进 → 安全清理（`--ticket` 显式解析） | 否 |
| `[生命周期]` `/icode worktree --reopen [--ticket <id>] [--to-ref <ref>]` | 显式恢复已 close 工单（归档控制根受控解冻，先 reopen 再 patch） | 否 |
| `[生命周期]` `/icode worktree --submit-check` | 交付前提交契约检查（G3，逐仓枚举，只读不 push） | 否（只读） |

> **目录复用规则**（start/plan/fast 启动时）：检查最新 `.icode_output/.icode_output_N/`——入口态（`init_in_progress`/`log_done`）**有歧义一律问用户**（带参可能是补充旧需求也可能是新需求）；非入口态带参 → 直接新建；无参且无入口态可复用 → 报错提示。完整脚本与 `REUSE=2/0` 语义见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「复用 / 创建新目录决策」。

> **init/log 入口状态转换时机**：新工单先生成非空 ID 并经 `create` 原子出生；`init` 为 `init_in_progress`+`["0"]`，`log` 为 `log_in_progress`+`[]`，收尾经 `transition` 到 `log_done` 并追加 `"log"`；plan 产物/合同完成后经 `transition` 到 `plan_done` 并追加 `"1"`。禁止直写完成态。

> **公共选项**（所有新建工单入口 `init/log/start/plan/fast`）：`--worktree` 用 git worktree 隔离（独立分支+目录，opt-in）；`--debug`（仅 init/log）独立孪生对照（目录建 `.icode_output/.debug/`、不入全局索引）。

## 控制面（工单 schema v3，vNext）

**新工单一律 schema v3**，经 `tools/icode_control.py create` 原子创建 metadata+出生事件；状态流转、普通 metadata、事件链、验证记录、索引、迁移、归档校验、关闭与重开由控制面统一执行，**禁止绕过直写**。文中凡称“写/更新/追加 metadata”，除已明确指定专用命令的控制字段外，均指调用 `metadata-update --set-json/--append-json`，不是自行读改写 JSON。机器真源：状态机 = [mcp/workflow-gate/gates.json](mcp/workflow-gate/gates.json)；数据 schema = [schemas/](schemas/)；执行器子命令 = `create/resolve-ticket/validate/event/transition/metadata-update/index-write/index-update/migration/record-verification/archive-manifest/close-phase/reopen/snapshot`。完整契约见 [references/control_plane.md](references/control_plane.md)。legacy 工单只读，变更前必须显式迁移。

## 使用流程示例

> 完整示例见 [README.md](README.md)「快速开始」与各步骤文件；此处只给最小骨架。

```text
/icode init 粗略需求 → /icode start 需求        # 全流程（步骤0 可选 + 1~6 串联）
/icode plan → review → merge → code → deepcheck → audit   # 分步手动
/icode readme / patch / verify --listen          # 交付报告 / 追加修改 / 纯实机验证
```

- 日志根因分析入口：`/icode log 设备日志+症状` → 根因报告 + `00_init.md` → `/icode start` 衔接
- 全流程精简：`/icode fast 需求`；工程知识库：`/icode doc`；约束红线：`/icode limit`
- **中断/跨会话恢复**：按 `completed_steps` 中 1~6 范围内最大已完成步骤续跑（见「全流程串联规则」）；重新执行某步骤可覆盖该步骤输出

> **锚点保留：方式 D2 / D3 / H**——`/icode log` 的 TB 缺陷单拉取（方式 D2：单 `LIB-NUM` 复用+增量对抗；方式 D3：批量"打开/未完成"分析）——锚点兼容写法「方式H」/「方式 H」等价；钉钉文档拉取（方式 H）详见 [steps/log.md](steps/log.md)「TB 缺陷源拉取」+「批量 TB 分析」 / `tools/dingtalk/README.md`。

## 通用规则

### 产物命名 + status 词表速查表（硬性速查，防"发明命名/状态"）

> **产物文件名与 status 值一律以 `steps/XX_*.md` 各步骤规定为准，下表只是速查不是第二真源**（完整 status 语义见下方「status 字段枚举」段）。写产物 / 写 metadata 前对照下表核对，命名或状态不在表内 = 不合规（见下方「产物命名硬性条款」）。

**主流程产物文件名**（`{ICODE_OUT_DIR}/` 下，全部小写、不得自造近似名）：

| 步骤 | 产物文件名 | 备注 |
|------|-----------|------|
| 1 | `01_plan.md` | 计划全文 |
| 2 | `02_review.md` + `review_round_{N}.json` + `_review_summary.md` | 多轮审查；每轮有 new_issues / pending_verification / refuted_issues 任一非空才写 JSON；`_review_summary.md` 为步骤2 末尾压缩摘要（供 merge 消费，cheap-research 不可用时跳过，详见 [steps/02_review.md](steps/02_review.md) 步骤6） |
| 3 | `03_plan_final.md` | **完整计划副本**（复制 `01_plan.md` 全文 + 审查采纳标记 + 末尾「实现偏差备忘」空段），不是元数据摘要 |
| 4 | `04_code_review_fix.md` | 步骤4 末尾 1.5「Code Review Fix」复检产物（所有工单都触发，不论入口） |
| 5 | `05_deepcheck.md` | 三阶段复检 |
| 6 | `06_audit.md` | 终审报告（含修复日志段） |
| 0/log | `00_init.md` / `log_analysis.md` | 入口产物 |

**status 词表**（写回 metadata 前逐字对照，禁止自定义）：正常流 `log_in_progress` / `log_done` → `init_in_progress` → `plan_done` → `review_in_progress` / `review_done` → `plan_finalized` → `code_in_progress` / `code_done` → `deepcheck_in_progress` / `deepcheck_done` → `completed`（终态）；debug 隔离流 `debug_in_progress` → `debug_done`。

**产物命名硬性条款**：产物必须按 `steps/XX_*.md` 规定的**文件名、目录、格式**产出；metadata 的 `status` 必须在本词表内。**自定义文件名 / 自定义格式 / 词表外状态值 = 不合规**，内容质量高也不能豁免——内容好 ≠ 机制合规。发现命名/状态不在上表，停下对照对应 `steps/XX_*.md` 修正，不得沿用自造近似（如用 `03_merge.md` 替代 `03_plan_final.md`、自造 `audit_done` 状态）。

### 强制阻断边界矩阵

按检查项的**严重级别**定义统一的"是否阻断流程"语义，避免规则散落在各 step 文件里：

| 级别 | 含义 | 触发后行为 | 典型场景 |
|---|---|---|---|
| **L1·致命** | 阻塞流程的前置条件不满足 | **报错退出**，流程不可继续 | cwd 不在 git 仓库 / 强制产物文件缺失 / MCP 完全不可用 / **双活动实现根**（同一工单存在两个 `state=active` 的 checkout，见「目录管理·worktree 生命周期」） |
| **L2·关键** | 重要约束未满足 | **警告 + 记入 metadata + 流程继续**（不阻塞等用户；用户事后审阅产物/audit 报告时可见，可手动回退）。icode 调性是 AI 自治 + 用户审阅，L2 不强制阻塞（避免 `/icode start` 串联时卡死）；02_review `absolute_cap` 触达同理，不再设例外 | plan §3 架构设计完全缺失 / review 触达 `absolute_cap` 仍有新问题 |
| **L3·重要** | 重要检查项未通过 | **警告**，记入 metadata，**流程继续**（user 后续可手动回看） | plan §10 checklist ❌ > 3 条 / audit §6.7 视角 A 失败 / 步骤 4 编译失败（带 `code_compile_failed=true`）/ **worktree 创建失败**（降级原地 + metadata 记 `wt_degraded=true`，见「目录管理·worktree 决策与创建」④） |
| **L4·参考** | 软性建议 | **柔性提示**，不影响流程 | limit 不存在 / cheap-research 未装 / vision-bridge 未装 / init 末轮理解核对清单用户不回复 |

**各步骤声明的 L1/L2 检查项**：详见对应 step 文件头部的「本步骤 L1/L2 检查项声明」段（已声明 7 个：plan / review / merge / code / deepcheck / audit / patch）。
- `steps/01_plan.md` 头部 → L1（前置产物缺失）/ L2（§3 缺失 + §10 ❌ > 3）
- `steps/02_review.md` 头部 → L1（前置产物缺失）/ L2（触达 `absolute_cap`，警告+记 metadata+继续）
- `steps/03_merge.md` 头部 → L1（`03_plan_final.md` 不是完整计划副本——定稿机器硬校验不通过，禁止进入步骤4）
- `steps/04_code.md` 头部 → L1（前置产物缺失）/ L2（Code Review Fix 全失败）
- `steps/05_deepcheck.md` 头部 → L1（前置产物缺失）
- `steps/06_audit.md` 头部 → L1（前置产物缺失）
- `steps/08_patch.md` 头部 → L1（无最新工单目录 / 入口态）/ L2（复检发现新引入问题，警告+记 metadata+继续）

**L3·重要** 检查项（不强制阻断，警告后流程继续）也已在各 step 头部声明段标注。

> **新增/修改检查项时**：明确标注其 L 级别，写在 step 文件头部声明段。不明确的不算 L1-L4（默认按现有流程行为）。

## 目录管理（锚点保留，内容已迁移）

> 目录创建/复用/迁移/worktree 隔离规则已收敛到 [references/dir_and_metadata.md](references/dir_and_metadata.md) 与 [references/worktree_isolation.md](references/worktree_isolation.md)（真源）。标题保留为锚点（文档可能用「SKILL.md「目录管理」段」指回）。

> 锚点保留小节：`目录管理` / `目录管理·worktree 决策与创建` / `创建新目录` / `复用 / 创建新目录决策` / `检测最新目录`

## 元信息文件（锚点保留，内容已迁移）

> `.ico_metadata.json` 完整字段定义 / 模板 / verdict 字段族 / delivery_verdict / scope_escalations 等全部收敛到 [references/dir_and_metadata.md](references/dir_and_metadata.md)（真源）+ [schemas/ticket-metadata.schema.json](schemas/ticket-metadata.schema.json)。字段族标题保留为锚点（文档可能用「SKILL.md「verdict 字段族」」「SKILL.md「可选字段」」指回）。

> 锚点保留小节：`元信息文件` / `可选字段` / `verdict 字段族` / `workload_estimate 字段族` / `status 字段枚举` / `status 写回校验` / `全局索引` / `模板`

**status 写回校验（强制，防词表外值落盘）**：每步写 `.ico_metadata.json` 前，必须先对照上方词表校验 `status` 在枚举内（`completed_steps` 中的步骤号须是 `steps/*.md` 清单里存在的合法值），词表外值直接判不合规、拒绝写回并修正。校验用一行命令：

```bash
python3 -c "import json,sys; d=json.load(open('{ICODE_OUT_DIR}/.ico_metadata.json')); valid={'init_in_progress','plan_done','review_in_progress','review_done','plan_finalized','code_in_progress','code_done','deepcheck_in_progress','deepcheck_done','completed','log_in_progress','log_done','debug_in_progress','debug_done'}; s=d.get('status'); print('status:', s); sys.exit(0 if s in valid else 1)"
```



### 执行模式

所有步骤（含可选步骤0）在主会话中执行，使用当前会话模型。**不主动切换模型**，用户如需切换可手动 `/model`。

### 强制思考前置 + 反偷懒约束（所有步骤必须遵守，硬性总则）

完整规则见下表两真源（步骤执行前**必须** Read 完整内容，不得凭 SKILL.md 概述或记忆执行——见反偷懒第 15 条）：

| 主题 | 真源 | 核心要点 |
|------|------|---------|
| 强制思考前置（分级 reasoning gate） | [references/thinking_core.md](references/thinking_core.md)（每步必读）+ [references/thinking_detail.md](references/thinking_detail.md)（按需读） | 每步开始前先按 **reasoning gate 分级 L0～L3**：L0（status/list/install/bak）只执行机器门禁；L1（readme/ppt/close/reopen/worktree/init/doc/limit/merge）写 `.decision_anchors.json` 决策记录；**L2/L3（plan/review/code/patch/log/deepcheck/audit）才首选 `sequential-thinking` MCP 3～5 步**，MCP 不可用降级 `### 结构化思考` 文字块；思考子项见各 step 文件。分级判定机器真源 = `mcp/reasoning-gate/gates.json`，运行痕迹 = `{ICODE_OUT_DIR}/.thinking_gate_trace.jsonl`，校验器 = `python3 tools/lint_thinking_gate.py` |
| 反偷懒约束 | [references/anti_laziness.md](references/anti_laziness.md) | 39 条典型偷懒行为 + 正面合规要求；引用 references 必须每步重新 Read 输出 `📖 已 Read` 确认行；思考块每子项 ≥2 句实质内容 |

### 根因优先决策准则（修复缺陷逻辑本身，优先于规避/绕过/补丁/开关）

> 多方案并存时，**第一优先 = 修正缺陷逻辑本身（root cause）**；规避 / 重试 / 打补丁 / 加配置开关 = 降级选项，仅在根因不可行（须给出**可验证**的不可行论证）时选用。「保持安全门控」与「修正错误逻辑」**不是互斥**——根因方案在红线内部保持安全属性，而非因红线直接排除根因选项。细则落点：[steps/01_plan.md](steps/01_plan.md) §4 ADR（选型排序 + 强制判定问题）、[steps/log.md](steps/log.md)（根因多候选 → 诊断先行）、[steps/08_patch.md](steps/08_patch.md)（旁路修复后强制回主验收闭环）。

### 全流程串联规则

`/icode start` 执行步骤1后，如果会话断开，恢复时必须读取 `.ico_metadata.json` 的 `completed_steps`，从最后一个完成步骤的下一步继续。不可跳过未完成的步骤。

**续跑判定规则**：以 `completed_steps` 中**编号 1~6 范围内最大的已完成步骤**为基准推进下一步。`"0"` 和 `"log"` 仅作为"已走过步骤0/log入口"的标记，**不影响**推进逻辑。例：`["0"]`/`["log"]` → 下一步是步骤1；`["0","1"]`/`["log","1"]` → 下一步是步骤2。


**转换点门禁（自动串联硬门禁，防"前一步产物缺失/状态异常仍自说自话推进"）**：`/icode start` / `/icode fast` 串联推进到下一步前，必须机器校验**上一步产物存在 + status 已到对应完成态**，任一项不满足即**停止串联**，输出"前一步产物缺失/状态异常，停止串联；请先补跑上一步或对照 `steps/XX_*.md` 修正"：

| 推进到步骤 | 前置产物（须存在） | 上一步 status（须是） |
|-----------|-------------------|----------------------|
| 2 (review) | `01_plan.md` | `plan_done` |
| 3 (merge) | `01_plan.md` + `02_review.md` | `review_done` |
| 4 (code) | `03_plan_final.md` | `plan_finalized` |
| 5 (deepcheck) | `03_plan_final.md` + 步骤4代码文件 + `code_files` 非空 | `code_done` |
| 6 (audit) | `03_plan_final.md` + 步骤4代码文件 | `deepcheck_done` |

校验命令示例（推进到步骤4 前）：

```bash
test -f "{ICODE_OUT_DIR}/03_plan_final.md" && python3 -c "import json,sys; d=json.load(open('{ICODE_OUT_DIR}/.ico_metadata.json')); sys.exit(0 if d.get('status')=='plan_finalized' else 1)" || echo "❌ 前一步产物缺失/状态异常，停止串联"
```

> 与「前置文件校验」表（本段下方）的关系：前置校验表是**单步命令**入口的 L1 检查，本门禁是 **start/fast 自动串联**时每步转换点的强制复查——两者共用同一产物判据，自动串联下不因"上一步刚跑完"而跳过复查（本轮实测教训：自动串联下 `03_plan_final.md` 缺失仍推进到步骤6）。


**patch 不参与推进判定**：`/icode patch` 是横向追加修改，**不改** `status`/`completed_steps`，不影响续跑判定——`completed` 工单 patch 后仍是 `completed`，`code_done` 工单 patch 后仍是 `code_done`（补丁记录在 `patch_count`/`patch_history` 字段 + `08_patch.md` 产物，详见 [steps/08_patch.md](steps/08_patch.md)）。

**patch 与主流程步骤的配合**：patch 插在不同步骤之间时，后续代码相关步骤的**计划侧基准须纳入补丁**（补丁的增量计划/实施是已落地的设计依据），否则会覆盖 patch 修改或误判为偏离：

| 后续步骤 | patch 的影响 | 配合规则（各步骤文件已声明） |
|---|---|---|
| 步骤2 review / 步骤3 merge | 无（只动计划文档，不碰代码） | 不需要配合 |
| **步骤4 code** | Write 按 `03_plan_final.md` 实施会**覆盖 patch 已改的代码** | 启动 Read `08_patch.md` → **在 patch 基础上实施**（保留 patch 修改，只叠加本步骤改动）；patch 与计划设计冲突 → 记 `code_deviations` + 提示用户（见 [steps/04_code.md](steps/04_code.md)「前置：patch 配合」） |
| **步骤5 deepcheck** | Reverse 逆推对比计划时，patch 修改**误判为"偏离/冗余"** | 启动 Read `08_patch.md` → Reverse 对比基准扩展：patch 已记录修改**视为已计划**不标偏离；追溯矩阵纳入 Patch 功能点（见 [steps/05_deepcheck.md](steps/05_deepcheck.md)「前置：patch 配合」） |
| **步骤6 audit** | 追溯矩阵不含 patch 功能点；重跑 audit 可能**覆盖补丁记录段** | 启动 Read `08_patch.md` → 追溯矩阵纳入 Patch 功能点；`diff_summary` 对比文本含补丁计划；已含「补丁记录」段则重跑后保留（见 [steps/06_audit.md](steps/06_audit.md)「前置：patch 配合」） |

统一规则：**步骤 4/5/6（代码相关步骤）启动时 Read `{ICODE_OUT_DIR}/08_patch.md`**（存在且有 Patch 段才需配合；不存在走原流程）。步骤 2/3 只动计划文档、不碰代码，无需读补丁。决策锚点 `patch_summary` 已随 patch 刷新，下游读锚点时可感知补丁存在（[references/decision_anchors.md](references/decision_anchors.md)）。

步骤 2/5 的 `*_in_progress` 状态 + 轮次计数器支持**断点续跑**（步骤0的 `init_in_progress` 不参与，详见上节"两种语义"）；步骤 4 的 `code_in_progress`（编译失败时保留）支持**整体续跑**——重跑步骤4时**在已写入的代码基础上继续修复**（编译失败时代码文件和 `code_files` 已保留落盘，不丢弃、不从计划重新编码），不带轮次断点。



### 工作流硬门禁（workflow gate，P0 四类 + P1 生命周期验收）

> 机器可判定硬门禁。真源 = [mcp/workflow-gate/gates.json](mcp/workflow-gate/gates.json)（触发条件/阻断步骤/必填单元/状态机只从这里读），校验器 = `python3 tools/lint_workflow_contract.py <out_dir> [--step <step>] [--strict] [--json]`（0=通过/1=有阻断/2=参数错）。旧工单缺 `workflow_gate_schema_version` → legacy-untracked 提示（`--strict` 判失败）。历史出处 [docs/adr/ADR-0001-optimization-proposal-provenance.md](docs/adr/ADR-0001-optimization-proposal-provenance.md)。

**四类硬门禁 + 生命周期验收（机器判定，步骤转换前跑校验器）**：
- **语义决策门禁**：`semantic_decisions` 存在 `status != resolved` 且非 `diagnosis_only` → `plan/code/patch` 阻断；诊断可结束但须显式 `diagnosis_only=true`
- **身份变化影响合同**：`identity_change=true` 且 `completeness != complete` → `code/deploy/audit-verified` 阻断；9 维必答必带证据
- **需求增量回流**：`requirement_deltas` 分类 `needs_user_confirm`/`needs_replan` → 冻结点，未分流不得继续扩大设计/验收矩阵
- **快速模式自动升级**：`mode=fast` 命中 `fast_risk_triggers` 且 `effective_mode != full` 且 `override != true` → 违规
- **生命周期验收**：涉及生命周期/身份变化时 `acceptance_contract.matrix` 必须覆盖全部 `phases × consumers` 必填单元，只验证直接查询不能 `delivery_verdict=verified`

字段族定义与写入点见 [references/dir_and_metadata.md](references/dir_and_metadata.md)（scope_contract / requirement_deltas / semantic_decisions / impact_contract / acceptance_contract / risk_profile / delivery_verdict）。

## 历史检索复用（锚点保留，内容已迁移）

> 两段式检索 + 命中续期 + 过时校验（5 步）+ verdict 分流注入 + `_inject_cache.json`/`patterns.json`/`project_docs` 契约全部收敛到 [references/dir_and_metadata.md](references/dir_and_metadata.md) + [references/thinking_detail.md](references/thinking_detail.md)。标题保留为锚点。

> 锚点保留小节：`历史检索复用` / `历史检索复用·注入分流` / `注入形式·按 verdict 分流` / `检索注入流程` / `零命中不注入，不强凑参考`


### 注意事项

- **Git 安全**：禁止执行任何 Git 危险操作（`git reset --hard`、`git push --force` 等），**也禁止 `git commit` 和 `git push`**。**`git worktree add` / `git worktree remove` 允许执行**（不在禁止列）：创建/清理须用户确认（写操作影响 `.git`），**永不自动 `--force` remove**（未提交改动时 remove 失败是保护，见「目录管理·worktree 决策与创建」）
- **`.icode_output/` 父目录及其下的 `.icode_output_N/` 目录无需用户确认**：该目录下创建/写入/修改 `.md`/`.json`/`.log` 文件均为安全操作
- **工程污染防护**：`.icode_output/` 是 icode 产物目录，建议在工程 `.gitignore` 中加入 `.icode_output/`，避免产物误提交；icode 本身**不自动修改工程的 `.gitignore`**（工程配置由用户掌控）。历史检索的全局索引位于 `~/.claude/icode_data/`，不在任何工程内，无污染风险；`/icode doc` 的工程文档库位于 `~/.claude/icode_data/project_docs/`，同样不在任何工程内、不写工程内任何文件（用户工程内已有 `doc/workflows/` 等历史文档时，忽略不读取不迁移不删除，从零生成到全局）

> **`.icode_output/` 父目录语义（本次新增，含 limit.local 子目录）**：
> `.icode_output/` 父目录下含两类内容——
> 1. `.icode_output/.icode_output_N/` 子目录 = 工单产物（每工单一目录，跟随工单生命周期，详见各步骤执行步骤）
> 2. `.icode_output/limit.local/` 子目录 = **项目约束红线的单 checkout 覆盖**（`/icode limit` 步骤产物，团队私有约定，自动 gitignore。limit 主存始终在全局 `~/.claude/icode_data/limits/<project_id>.md`，跨 checkout 共享；local 完全覆盖 main。详见 [steps/limit.md](steps/limit.md)）
>
> `.icode_output/` 父目录默认 gitignore 不上传——这同时承担"产物容器 + 项目配置"两种角色，**与上方「工程污染防护」建议的 gitignore 策略一致，无需特殊白名单配置**
- **跨会话恢复**：运行 `ls -d .icode_output/.icode_output_*` 确认目录后，直接调用对应步骤即可
- **中断恢复**：重新执行某步骤可覆盖该步骤输出


## MCP 调用覆盖强制化（锚点保留，内容已迁移）

> MCP 分级语义（🟢/🟢*/⚪）、双保险机制、每步骤推荐表、分级思考 L0-L3、cheap-research 覆盖门 全部收敛到 [references/mcp_per_step.md](references/mcp_per_step.md) + [references/mcp_integration.md](references/mcp_integration.md) + [references/thinking_core.md](references/thinking_core.md)（真源）。标题保留为锚点。

> 锚点保留小节：`MCP 调用覆盖强制化` / `MCP 工具集` / `cheap-research 14 工具会话内缓存` / `降级标签格式规范` / `工具调用模式规范`

---

> **关于外部工具调研**：对于"是否值得引入第三方代码工具以优化 iCode"的判断结论（如 Tree-sitter 图谱、blast-radius 思路等），**非 SKILL 集成、零必装依赖**——iCode 主流程不依赖、不推荐、不安装任何外部工具。


## 各步骤详细规则

各步骤的详细 prompt、维度要求、执行流程请读取对应文件：

| 步骤 | 命令 | 详细文件 |
|------|------|----------|
| log | `log` | [steps/log.md](steps/log.md) |
| 0 | `init` | [steps/00_init.md](steps/00_init.md) |
| 1 | `plan` / `start` | [steps/01_plan.md](steps/01_plan.md) |
| 1~6 | `fast` | [steps/fast.md](steps/fast.md)（编排）+ 各步骤文件（带 fast 降级分支） |
| 2 | `review` | [steps/02_review.md](steps/02_review.md) |
| 3 | `merge` | [steps/03_merge.md](steps/03_merge.md) |
| 4 | `code` | [steps/04_code.md](steps/04_code.md) |
| 5 | `deepcheck` | [steps/05_deepcheck.md](steps/05_deepcheck.md) |
| 6 | `audit` | [steps/06_audit.md](steps/06_audit.md) |
| 7 | `readme` | [steps/07_readme.md](steps/07_readme.md) |
| patch | `patch` | [steps/08_patch.md](steps/08_patch.md)（独立步骤，主流程后/中途追加修改，不参与 1~6 推进） |
| verify | `verify` | [steps/verify.md](steps/verify.md)（独立实机验证，不改代码；结果记 `verification_runs`，不自动升级 delivery_verdict） |
| doc | `doc` | [steps/doc.md](steps/doc.md) |
| limit | `limit` | [steps/limit.md](steps/limit.md)（独立步骤，不参与 1~6 流程推进；plan 步骤硬基线引用源） |
| ppt | `ppt` | [steps/ppt.md](steps/ppt.md)（独立交付步骤：项目/模块/本次功能开发/本次BUG修复 → .pptx） |
| - | `install` | [steps/install.md](steps/install.md)（开源统一安装步骤：ICODE + 共享技能 + MCP）|
| - | `status` | [steps/status.md](steps/status.md) |
| - | `list` | [steps/list.md](steps/list.md)（跨工程工单查找，纯查询） |
| - | `bak` | [steps/bak.md](steps/bak.md)（工程工单手动备份到全局，删工程前安全网；写索引 `backup_path`） |

**执行步骤时，必须先读取对应的 `steps/XX_*.md` 文件，按其中的详细指令执行。**

## 决策锚点机制（步骤间思考传递）

解决「步骤间只传产物文件，AI 思考推理不传」痛点。各步骤完成后写 `.decision_anchors.json`（关键决策摘要），下游启动时读，**不用重读产物全文**。锚点是精炼摘要非产物备份。

- **文件**：`{ICODE_OUT_DIR}/.decision_anchors.json`（工单目录内，与 `.ico_metadata.json` 平级）
- **写时机**：init/plan/code/deepcheck/audit 完成后（L3 自动，AI 主动提炼）；patch 完成后追加 `patch_summary` + 刷新 `open_risks`（增量刷新，不覆盖主流程字段）
- **读时机**：plan/review/code/deepcheck/audit/patch 启动时（L4 自动）
- **开关**：`metadata.anchors_enabled`（默认 `true`，`false` 跳过，向后兼容旧工单）
- **完整规则**：[references/decision_anchors.md](references/decision_anchors.md)

## 共享规则文件（references/）

各 step 文件不再重复定义跨步骤共享的规则，统一引用 `references/` 下的共享文件。执行某步骤时若该步骤引用了共享文件，**必须先用 Read 工具实读该文件完整内容**（不得凭 SKILL.md 概述或记忆执行，否则产出不合规——见反偷懒第15条）：

| 共享文件 | 内容 | 引用方 |
|---------|------|--------|
| [references/thinking_core.md](references/thinking_core.md) | 强制思考前置核心（每步必读：MCP+降级文字块/结构化思考/Read references） | 所有 step |
| [references/thinking_detail.md](references/thinking_detail.md) | 强制思考前置细节（按需读：各步骤子项速查/历史参考小节） | 所有 step |
| [references/anti_laziness.md](references/anti_laziness.md) | 反偷懒约束（39条偷懒行为+合规要求+references必读+确认行） | 所有 step |
| [references/adversarial.md](references/adversarial.md) | 对抗分析模式（3质疑者/裁决优先级/诚实降级/证据回指） | 02_review / log |
| [references/skill_routing.md](references/skill_routing.md) | **共享 SKILL 懒路由**：按机器路由表判触发、准备输入合同、消费输出合同；无命中不加载 | log / plan / code / deepcheck / audit / verify |
| [references/evidence_and_verification.md](references/evidence_and_verification.md) | **证据与验证习惯真源**：现场事实、主代理复核、无日志反查、多 Git 根、诊断/实现/验证分层 | log / plan / deepcheck / audit / verify |
| [references/host_adapters.md](references/host_adapters.md) | Claude Code / Codex 宿主工具适配；共享技能正文禁止绑定具体工具语法 | 共享 SKILL 被路由时 |
| [references/control_plane.md](references/control_plane.md) | **工单控制面（schema v3）**：状态机/事件链/索引单一 writer/迁移/关闭分阶段/降级路径；执行器 `tools/icode_control.py`，真源 `mcp/workflow-gate/gates.json`「state_machine」 | 所有 step（状态写回点 / index-write / close / verify） |
| [references/dir_and_metadata.md](references/dir_and_metadata.md) | 目录管理（创建新目录含**硬熔断①②**：建前 test -d + 建后 ls -A 验证 + **硬熔断③工作区根校验**，禁手写目录号/echo 伪确认）+ ticket_id 生成 + 全局索引写入（含LRU淘汰） + metadata 模板 + **过时校验（含 worktree 归档工单**：archive_path 有效→archived 活跃态读档历史参考，正常续期；**含 `/icode bak` 备份工单**：backup_path 有效→backup 活跃态读档历史参考，工程优先→备份兜底） + **注入缓存机制（防重复注入，两源共用）** + **project_docs 工程文档库 + 段零检索** | init / log / plan / start / fast / doc / bak |
| [references/doc_template.md](references/doc_template.md) | icode doc 章节模板：前 50 行四块结构（项目元信息/KEYS/简要说明/目录）+ 十位桶编号 + 自适应 grep 关键词表 + 99 章审计策略 + **v2.0.0 双视角必含元素清单（14 项）+ 业务流独立成章 + 英文首次中文备注 + 链路中文说明 + 质量审视检查清单 + 模板版本自举迁移** | doc |
| [references/necessity_check.md](references/necessity_check.md) | **现有功能覆盖度检查（防重复实现机制）**：触发时机 + 执行命令（全工程检索 + Read 命中处行为链）+ 三类判定（已覆盖/部分/未覆盖）+ 各步骤落点（init §2.X/预筛列、plan 前置/断言/ADR/对抗、review 维度7、deepcheck Reverse 对比、audit 视角 C） | init / plan / review / deepcheck / audit |
| [references/first_activation_path.md](references/first_activation_path.md) | **首次激活路径一致性检查**：静态分析盲区（"写了从没实机执行过"的死路径既有 bug）+ 触发条件 + 检测法（软信号、不阻断）+ 双侧校验一致性核对清单 + 部署后验证建议下游输出 | plan（断言⑤）/ deepcheck（Reverse）/ audit（部署后建议）/ patch（部署后验证发现） |
| [references/worktree_isolation.md](references/worktree_isolation.md) | **git worktree 多需求隔离**：worktree 决策与创建（**opt-in 参数触发**：`--worktree` 才走创建，否则默认原地，不弹问；**预检/公示告知/失败降级**）+ cwd 契约 + metadata 字段族 + 回流指引（F2 二选一）+ **产物归档（自动，防 remove 丢档）** + 防误删护栏 + 空间自查 | 新建工单入口加 `--worktree` 时（init/log/start/plan/fast）/ 续跑与只读（review/code/deepcheck/audit/patch/status/readme） |
| [references/debug_mode.md](references/debug_mode.md) | **debug 模式（独立孪生工单）**：`/icode init --debug` / `/icode log --debug` 产出对照工单，不入全局索引、不参与主流程（各主流程步骤 L1 阻断）；目录在 `.icode_output/.debug/` 下、N 独立递增；`debug: true` 元数据标志 + 独立状态名；忽略 `--worktree` | init / log（`--debug` 时） |
| [mcp/workflow-gate/gates.json](mcp/workflow-gate/gates.json) + [tools/lint_workflow_contract.py](tools/lint_workflow_contract.py) | **workflow gate 工作流硬门禁（P0 四类 + P1 生命周期验收）**：语义决策 / 身份变化影响 / 需求增量强制升级 / 快速模式风险自动升级 / 生命周期验收矩阵；机器真源 + 运行时校验器（`--step plan/code/patch/merge/deploy/audit-verified/fast`，`--strict` 强制模式）；两阶段兼容迁移（legacy-untracked 提示 → 强制） | plan（写合同 + 7.5 自检）/ review（影响清单审查）/ merge（11.5 定稿硬校验）/ fast（risk_profile 自动升级）/ code（前置门禁 + 验收矩阵测试清单）/ deepcheck（生命周期一致性复检）/ audit（验收门）/ patch（2.7 重大增量回流） |
