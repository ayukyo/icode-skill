---
name: icode
description: 端到端编码工作流（步骤 0~6，含可选需求初稿步骤与日志根因分析入口），支持分步手动调用：/icode help (帮助), /icode install (MCP 环境检查+一键安装), /icode init [<粗略需求>] (需求初稿), /icode log [零散信息...] (日志根因分析→转修复需求), /icode start <需求> (全流程), /icode fast <需求> (精简全流程), /icode plan <需求> (计划), /icode review [N] (审查), /icode merge (定稿), /icode code (编码), /icode deepcheck (复检), /icode audit (终审), /icode patch [问题或新需求] (追加修改：主流程后/中途继续改), /icode doc [自然语言] (工程级知识库生成), /icode limit [自然语言] (项目约束红线), /icode readme (交付报告+跨领域简报), /icode ppt [自然语言] (PPT生成：项目/模块/本次功能开发/本次BUG修复), /icode status (工单状态), /icode list [关键词] (跨工程工单查找)。**新建工单入口支持 `--worktree` opt-in**（init/log/start/plan/fast 加 `--worktree` 即用 git worktree 隔离；默认原地不弹问）
---

**版本**: v2.18.0

# ICode 全流程编码工作流（步骤 0 + 1~6）

端到端编码工作流，将需求到交付拆解为严格步骤，每步可单独调用，方便你自行切换模型。

- **步骤 0（可选）**：需求初稿对话，多轮迭代后落档为 `00_init.md`（含链路图：修改前/后链路 + 改动点，每轮动态更新），独立步骤、不自动串联到步骤1
- **步骤 1~6**：拟定计划 → 审查 → 定稿 → 编码 → 复检 → 终审

> **主流程步骤真源（防误用，唯一真源 = `steps/` 目录，启动强制 Read）**：步骤编号 / 产物文件名 / `completed_steps` 合法值**一律以 `steps/` 目录实时清单为准**（`ls steps/*.md` 完整列出，含主流程与辅助入口 log / doc / fast / limit / status / install / list）——**本块仅示意，steps/ 演进后以目录为准，勿依赖写死**。`/icode start` / `/icode fast` / `/icode plan` 进入第一步**先 `ls steps/*.md`** 核对，不按"编码→测试→部署"直觉推断
> - 当前主流程示意（以 `ls steps/*.md` 为准）：`00_init → 01_plan → 02_review → 03_merge → 04_code → 05_deepcheck → 06_audit → 07_readme → 08_patch`；**不存在 `03_code` / `04_test` / `05_deploy`**（测试验证在 04_code 子段，部署/回归归 07_readme / 08_patch）
> - 辅助独立步骤（doc / log / fast / limit / status / install / list）不参与 1~6 推进
> - **强制**：产物命名 + `completed_steps` 写号**对照 `ls steps/*.md` 实时结果**（如入口含 `log` → 可写 `"log"`），不在清单 → 停下核对，禁止自造产物占位；steps/ 目录与本文档不一致时**以 steps/ 目录为准**

## 通用约定（对话语言）

**AI 对用户的回复一律使用中文**（提示 / 解释 / 报告 / 追问 / 总结 / 决策说明 / 输出行）。**工程内容保持原样、不翻译**：代码、标识符、产物文件名、命令、日志原文、报错原文、设备输出、配置字段值。仅当用户明确要求英文回复时切换。

> 适用所有 `/icode` 命令的会话交互与步骤内对用户的询问/报告；产物文件正文遵循既有中文风格撰写。

## 调用命令

所有输出保存在 `.icode_output/.icode_output_N/`（N 自动递增）目录下——所有产物统一收纳在 `.icode_output/` 父目录内，避免工程根目录堆积大量 `.icode_output_*` 目录：

| 命令 | 功能 | 创建目录？ |
|------|------|-----------|
| `[辅助]` `/icode help` | **帮助**：输出使用流程示例 | 否 |
| `[辅助]` `/icode install` | **MCP 环境检查+一键安装（独立步骤）**：跑 `mcp/install.sh` 扫描所有 `mcp/*/install.sh`，每个子工程自检环境（venv/Node/npm）并缺啥补啥、注册到 `~/.claude.json`。新 clone 仓库 / 新机器 / CI 初始化时跑一次。**不创建工单目录、不写工单 metadata、不参与 1~6 推进**（详见 [steps/install.md](steps/install.md)） | 否 |
| `[入口]` `/icode log [零散信息...]` | **可选入口（日志根因分析）**：把"设备/服务日志+模糊症状"转为有对抗验证的根因报告，自动转修复需求 `00_init.md` 衔接步骤1。先基线检查（git diff/链路图）再日志侦察，对抗分析防确认偏误。**领域无关，每次调用都新建目录**（详见 [steps/log.md](steps/log.md)）。**支持 `--worktree`**（opt-in）；**支持 `--debug`**（独立孪生不入索引，详见 [references/debug_mode.md](references/debug_mode.md)） | ✅ 每次都新建 |
| `[入口]` `/icode init [<粗略需求>]` | **可选步骤0**：多轮对话产出 `00_init.md`（需求初稿，含链路图：before/after + 改动点，每轮动态更新）。**每次调用都新建目录，不复用、不续聊**（详见 [steps/00_init.md](steps/00_init.md)）。**支持 `--worktree`**（opt-in）；**支持 `--debug`**（独立孪生不入索引，详见 [references/debug_mode.md](references/debug_mode.md)） | ✅ 每次都新建 |
| `[流程]` `/icode start <需求>` | **全流程（full 模式）**：创建/复用目录 → 步骤1→6 串联。步骤2 review 默认 3 轮 + 对抗验证，步骤5 deepcheck 三阶段循环（**复用规则见下**）。**支持 `--worktree`**（opt-in） | ✅ 创建新目录 / 复用 |
| `[流程]` `/icode fast <需求>` | **精简全流程（fast 模式）**：plan → review(1轮无对抗) → merge → code → deepcheck(Reverse 单阶段) → audit。耗时约为全流程 65%，产物结构与 full 对齐（详见 [steps/fast.md](steps/fast.md)）。入口打印警告、用户自负其责。**支持 `--worktree`**（opt-in） | ✅ 创建新目录 / 复用 |
| `[流程]` `/icode plan <需求>` | **仅步骤1**：拟定项目计划（**复用规则见下**）。**支持 `--worktree`**（opt-in） | ✅ 创建新目录 / 复用 |

> **步骤0 init 状态转换时机**（避免状态机歧义）：
> - `init` 调用：建新目录，立即写 `status=init_in_progress` + `completed_steps=["0"]` 落盘
> - 多轮对话期间：状态保持 `init_in_progress`，`00_init.md` 每轮增量更新
> - 用户决定进入步骤1（调 `start`/`plan`）：**start/plan 调用时立即**把 status 从 `init_in_progress` 切换为 `plan_done`，`completed_steps` 追加 `"1"`（**步骤1计划已就绪等价于 plan_done 终态**，因为 start 调用前已读 00_init.md 作步骤1输入）
> - **不得在 init 阶段把 status 切换为 plan_done**——只有 start/plan 显式复用时才切换

> **log 入口状态转换时机**（方式D log→start 工单）：
> - `log` 调用：建新目录，写 `status=log_done` + `completed_steps=["log"]` 落盘
> - 用户决定进入步骤1（调 `start`/`plan`）：**start/plan 调用时立即**把 status 从 `log_done` 切换为 `plan_done`，`completed_steps` 追加 `"1"`（与 init→start 复用规则一致）
> - **不得在 log 阶段把 status 切换为 plan_done**——只有 start/plan 显式复用时才切换
| `[流程]` `/icode review [N]` | **仅步骤2**：多轮循环审查 + 独立质疑者对抗验证（N=软上限轮数，默认3；如最后一轮仍有新问题自动延长 +2 轮，最多扩展至 `max(10, N×2)`）。`mode=="fast"` 时强制 1 轮无对抗 | 用最新目录 |
| `[流程]` `/icode merge` | **仅步骤3**：合并审查意见定稿 | 用最新目录 |
| `[流程]` `/icode code` | **仅步骤4**：落地编码实施（含**末尾 1.5 子段"Code Review Fix" 4 维度复检**——核对实施是否与计划设计的 4 维度一致。复检失败轻/重度分流回代码修复或重设计，不强制阻断；详见 [steps/04_code.md](steps/04_code.md)）**支持用户自担验证豁免（O-6）**：需求/对话出现"我自己编译 / 不要 commit / 我自己验证 / 不宣称已修复"等表达时——编译/测试验证降为可选（产物标注"编译由用户执行"）、禁止 `git commit/push`（收尾只出改动清单）、结论用词降级为"已完成代码修改，待实机验证"（不宣称"已修复"）；与 `/icode limit` 项目约束职责分离（用户工作模式偏好走行为分支，不入 limit） | 用最新目录 |
| `[流程]` `/icode deepcheck` | **仅步骤5**：三阶段递进复检（Reverse → Fixed → Free）。`mode=="fast"` 时只跑 Reverse 阶段 | 用最新目录 |
| `[流程]` `/icode audit` | **仅步骤6**：终极终审 + 统一修复（产出 `{ICODE_OUT_DIR}/06_audit.md`） | 用最新目录 |
| `[流程]` `/icode readme` | **可选步骤7**：一次性生成两份——**交付报告**（**给自己看**：完整技术档案，自包含，智能识别功能/查BUG模板）+ **跨领域简报**（`_brief.md`，**给其它模块研发/测试/产品看**：含必要改动/修复代码，主要问题/需求/时间点/链路/修复，较简略）。步骤6完成后手动触发 | 用最新目录 |
| `[独立]` `/icode patch [问题或新需求...]` | **追加修改（独立步骤）**：主流程完成后（`completed`）或中途（步骤1~5任一状态）继续修改既有工单——测试发现问题 / 后续新需求，在既有工单上打补丁。**轻量四段式**（重审现状 → 增量计划 → 最小实施 → 反向复检，含**分析验证型分支**：纯分析无代码修改时豁免实施/编译、改「结论验证」，但**端到端代码追溯/证据方法可靠性/链路完整性结论验证不豁免），**不靠会话记忆靠磁盘产物重载上下文**（治"越问上下文越爆炸"）。产物 `08_patch.md` 追加式（每次**显式调用**追加 Patch N 段；**会话内追问/补充归入当前 Patch N，不新增段**）。**不改变 status/completed_steps**（completed 保持 completed），靠 `patch_count`/`patch_history` 记录；可选 `--listen`（自动监听）/ `--test`（显式触发验证）→ 阶段 4「1.5 实机部署验证」（连设备部署 + 持续轮询 + 实时链路分析；`--listen` 告知触发即监听、用户随时操作被捕获，`--test` 空转停下确认用户已操作再继续）；无 flag 跳过实机验证（详见 [steps/08_patch.md](steps/08_patch.md)） | 用最新目录 |
| `[工程]` `/icode doc [自然语言]` | **工程级知识库生成（独立步骤）**：扫描工程代码特征，生成/维护 `~/.claude/icode_data/project_docs/<project_id>/<branch>/` 下的工程知识库章节（架构/IPC/术语表/代码事实审计，**按分支分目录**，切分支跑 doc 不互相覆盖），**同时检测工程依赖的独立模块**（git submodule / `repo` 管理 / CMake FetchContent / monorepo / vendor / 用户配置，6 级优先级）并生成 `~/.claude/icode_data/module_docs/{key}/` 模块共享文档（**按仓库+分支 key 跨工程共享**，同一上游仓库同分支只一份），供 init/log/plan/start/fast 段零自动跨仓库检索注入。**去参数化**——目标工程与动作（全量/增量/新增）由自然语言识别。**v1 单级布局自动迁移**：检测到旧 `<project_id>/` 平铺布局时自动迁移到 `<project_id>/<branch>/`（保留所有字段 + 备份 `_meta.json.v1_migrated_from`，详见 doc.md 步骤 5）。**不创建工单目录、不写工单 metadata、不参与步骤1~6推进**（详见 [steps/doc.md](steps/doc.md)） | 否（写全局 `project_docs/` 和 `module_docs/`） |
| `[配置]` `/icode limit [自然语言]` | **项目约束红线（独立步骤）**：定义和维护本工程的红线/约束/禁区。**主存**：`~/.claude/icode_data/limits/<project_id>.md`（全局，跨 checkout 共享，团队私有不上传）；**覆盖**：`<project_root>/.icode_output/limit.local/<project_id>.md`（单 checkout，自动 gitignore）。**local 完全覆盖 main**。**追加式演进**——每次调用增量追加新红线条目（编号自增），不覆盖、不 diff。对齐 `/icode doc` 模式：无描述→全局扫描显示当前约束（合并视图）；有描述→针对操作生成/追加新条目。**plan 步骤硬基线**——plan §3/§4/§6 引用 limit 条目作为设计依据（柔性提示：plan 入口检测不到 limit 建议生成但不阻断）；plan 步骤1「前置 limit 硬基线」为 init/start/fast 三入口读 LIMIT 的唯一汇聚点，读留痕落盘 `{ICODE_OUT_DIR}/limit_checkpoint.md`「阶段块：plan前置硬基线」（进入强制思考/设计前，先读索引→精读命中，缺失按未读）。**log 步骤对照清单**——log 前置 limit 红线检查点读取（**步骤1 末尾，先于步骤2 历史检索/段零文档注入**，防对照滞后），**先读索引→精读命中条目**的直接记录在进入步骤2 前向 `limit_checkpoint.md` **追加**「阶段块：log前置检查点」（步骤8 §2.3 / 步骤9 `limit_refs` 是事后产物，不能替代该读留痕），逐条对照根因假设是否违反约定红线，报告 §2.3 必填 + 引用红线经步骤 9.5 机器自检留痕（柔性提示：log 入口检测不到 limit 不阻断）。`limit_checkpoint.md` 为**工单级追加式**留痕文件，log 前置与 plan 前置各自带**阶段块标题锚点**（`阶段块：log前置检查点` / `阶段块：plan前置硬基线`）、互不覆盖。**不创建工单目录、不写工单 metadata、不参与步骤1~6推进**（详见 [steps/limit.md](steps/limit.md)） | 否（写全局 `limits/` + 工程根 `.icode_output/limit.local/`，自动 gitignore） |
| `[交付]` `/icode ppt [自然语言]` | **PPT 生成（独立交付步骤）**：自然语言 → 真实 `.pptx`。**4 类场景**：**项目**（工程知识库/仓库结构）、**模块**（模块 doc 章节）、**本次功能开发**（最新工单产物：00_init/01_plan/03_final/04_code/06_audit）、**本次BUG修复**（log 根因 + 08_patch + 验证）。**内置 16 套模板**（`tools/ppt/templates/`，索引 INDEX.md，AI 先筛 2-3 个风格匹配候选、由用户挑选；也可直接点名模板），只替换文字不破坏排版，产出 `<project_root>/.icode_output/ppt/{工程简名}_{场景关键词}.pptx` + `edits.json` 可回溯（**不放进工单目录 `.icode_output_N/`**）。依赖 python-pptx（必需）；LibreOffice+poppler 仅渲染自检可选。**内容必须有来源（产物/知识库/git），禁止编造；无占位残留、禁止省略号截断**。内置模板**非商业授权**（仅供学习研究，见 tools/ppt/NOTICE）。**不创建工单目录、不写工单 metadata、不参与步骤1~6推进**（详见 [steps/ppt.md](steps/ppt.md)） | 否（写 `<工程根>/.icode_output/ppt/`，非工单产物） |
| `[查询]` `/icode status` | **状态查询/verdict 标注/产物集校验**：默认只读查当前工单状态（含 `mode`/`verdict` 字段 + 全局索引工单数）；`--verdict <ticket_id> <verified\|disproved\|superseded> "<reason>" [--correct "<正确方向>"] [--source <machine_test|review|user|auto_signal>]` 手动标注工单方向结论（双写 metadata+index，幂等覆盖刷新 `verdict_at`）；`--scan-verdict` 批量扫描 unknown 完成态工单的 00_init 末轮/06_audit 证伪信号并提示标注；`--validate [N]` 机器校验工单产物集完整性（6 主流程产物 + review_round_*.json 存在 + status 词表内 + code_files 非空，只读提示不自动改）（详见 [steps/status.md](steps/status.md)） | 否（默认只读；`--verdict`/`--scan-verdict` 写 metadata+全局索引，不写工程内源码文件） |
| `[查询]` `/icode list [关键词]` | **跨工程工单查找**：从全局索引 `~/.claude/icode_data/index.json` 全量读取，表格化展示所有工单（ticker-id/project/status/workload/last-used/verdict/summary），支持 `--project <path>` / `--status <status>` / `--since <duration>` / `--limit N` / `--no-color` / `--include-stale` 过滤。**纯查询不跳转**——不创建目录、不写 metadata、不改任何文件（详见 [steps/list.md](steps/list.md)） | 否（纯只读，跨工程） |

> **`/icode start` / `/icode plan` / `/icode fast` 的目录复用规则**：启动时检查最新 `.icode_output/.icode_output_N/` 目录：
> - **入口态有歧义 → 一律问用户**（无论是否带参）：最新目录 status 为 `init_in_progress` 或 `log_done`（即 init/log 产出了 `00_init.md` 但还没进步骤1，且无 `01_plan.md`）时，**必须问用户**："检测到最近有未完成的初稿/根因 `<摘要>`，是 ① 在此基础上继续（复用目录）/ ② 开全新需求（新建目录）？"——用户选①则复用（命令行参数作为需求补充输入，`00_init.md` 为主体），选②则新建
> - **为何带参也问**：带参可能是"补充旧需求"也可能是"新需求"，区分不了，故一律问。误复用（新需求被吞进旧 init、难拆分恢复）的代价高于误新建（旧 `00_init.md` 仍在磁盘、可恢复），故取保守可靠的"一律问"
> - **不得擅自复用**（会丢失新需求）也**不得擅自新建**（会丢失 init/log 上下文）
> - **非入口态带参 → 直接新建**：最新目录已进入步骤1+（有 `01_plan.md` 等，REUSE=0），`/icode start <需求>` / `/icode plan <需求>` / `/icode fast <需求>` 带参一律新建目录。
> - **无参且无入口态可复用 → 报错**：提示先 init/log 或带参新建。
> 详见下文「目录管理」段落（bash 脚本 `REUSE=2` 分支即"一律问"行为，散文与脚本逐行对齐）。

> **历史检索复用**：`/icode init`、`/icode plan`、`/icode start`、`/icode fast`、`/icode log` 启动时会自动检索全局索引中相似历史工单并按命令分流注入参考（init→需求要点 / plan/start/fast→ADR+风险 / log→根因结论+证据），详见下文「历史检索复用」段落。**`/icode fast` 的检索委托给紧随的 plan 步骤2**（slice 相同，`_inject_cache.json` 去重兜底，fast 不单独检索）。`/icode review`/`merge`/`code`/`deepcheck`/`audit`/`patch` 不触发检索。

### 帮助说明（`/icode help`）

在对话中输出使用流程示例和命令一览（不创建目录和文件）。

> **公共选项**（适用所有**新建工单入口**命令 `init/log/start/plan/fast`）：
> - **`--worktree`** — 新建工单时用 git worktree 隔离（独立分支 + 独立目录），opt-in 参数触发；不传默认原地建工单，不弹问；工程可写 limit「worktree 强制禁止」红线阻止触发（详见 [SKILL.md「目录管理·worktree 决策与创建」](../SKILL.md)+ [steps/limit.md §7](../steps/limit.md)）。例：`/icode start --worktree <需求>`
> - **`--debug`**（仅 `init`/`log`）— 独立孪生工单对照：目录建在 `.icode_output/.debug/`、不入索引、不参与主流程，**忽略 `--worktree`**（详见 [references/debug_mode.md](references/debug_mode.md)）。例：`/icode log --debug <症状>`

### 使用流程示例

```bash
# 方式A：全流程一步到位（自动串联所有步骤）
/icode start 实现MCU雨量传感器I2C驱动

# 方式A+：opt-in 用 worktree 隔离（与其它参数/flag 共存)
/icode start --worktree 实现MCU雨量传感器I2C驱动   # 同上，但产物在 ../<repo>-wt-<ticket-slug>/ 内独立分支
/icode fast --worktree 实现MCU雨量传感器I2C驱动    # fast 模式 + worktree 隔离
/icode plan --worktree 实现MCU雨量传感器I2C驱动    # 仅步骤1 + worktree 隔离
/icode init --worktree 录制传感器数据转包            # 步骤0 + worktree 隔离
/icode log --worktree ~/work/log/服务异常 "启动后无响应"  # log + worktree 隔离
# 默认不带 --worktree → 直接原地建工单，不弹问

# 方式B：分步执行
/icode plan 实现MCU雨量传感器I2C驱动   # 步骤1
/icode review                          # 步骤2（软上限3轮，仍有问题时自动延长）
/icode review 5                        # 步骤2（若上轮已 review_done 则重新审查5轮、覆盖历史；若 review_in_progress 中断态则续跑且改用5轮上限）
/icode merge                           # 步骤3
/icode code                            # 步骤4
/icode deepcheck                       # 步骤5
/icode audit                           # 步骤6

# 方式C：先讨论需求再进入流程（推荐用于需求不明确的场景）
/icode init 录制传感器数据转包              # 步骤0：起一稿，进入对话
# ... 多轮对话补充需求，文档 00_init.md 每轮都被增量更新 ...
/icode start                             # 无参→检测到 init 入口态，会询问"复用/新建"，选复用则把 00_init.md 作需求输入，进入步骤1→6
# 或：
/icode plan                            # 无参→同上询问，选复用则仅执行步骤1

# 方式D：从 bug 日志分析切入修复（先查根因，再修复）
/icode log ~/work/log/服务异常 "启动后无响应"      # 入口：分析日志根因，产出 log_analysis.md + 修复需求 00_init.md
# ... 对抗分析收敛后，根因确定；若质疑可继续对话重跑被质疑分支 ...
/icode start                             # 无参→检测到 log_done 入口态，会询问"复用/新建"，选复用则把 00_init.md（修复需求）作输入，进入步骤1→6
# 或：
/icode plan                            # 无参→同上询问，选复用则仅执行步骤1
/icode readme                          # 可选：步骤6完成后手动触发，生成交付报告 + 跨领域简报（两份）
/icode status                          # 可选：随时查当前工单状态（只读，不创建目录）
/icode list                             # 跨工程查找：列全索引所有工单
/icode list mcu                         # 关键词搜索（ticket_id/summary/keywords）
/icode list --project myproject --since 30d # 按工程+时间过滤
/icode list --status completed --no-color | less  # 禁用颜色便于管道
```

```bash
# 方式D2：从 Teambition 缺陷单拉日志分析（TB 单带日志附件时，远程拉取替代本地日志）
/icode log 分析 https://tb.example.com/project/<pid> DEMO-26 问题
# 入口：自动拉取缺陷单标题/描述/评论/日志附件到 .icode_output/.icode_output_N/tb_source/<ID>/，进对抗根因分析，产出 log_analysis.md + 00_init.md
# 配置（可选）：URL+单号用法不配 config 也行（AI 从 URL 抽 domain+pid 传 --domain --pid）；多项目快捷或固定 domain 时建 ~/.claude/skills/icode/tools/tb/config.json；cookie 用 ~/.claude/skills/icode/tools/tb/scripts/tb_cookie.py --domain <域名> 取
# 仅拉取分析，不回写 TB；无 TB 引用时 /icode log 走纯本地日志路径（见方式D）
# 同 TB 单再次分析（TB 上有新评论/附件）：再跑同单 -> 提示"复用旧工单/新建"，复用则重拉最新+增量对抗
```

```bash
# 方式H：从钉钉文档/钉盘拉资料（需求文档/参考资料在钉钉分享链接时，拉取替代手动导出）
/icode init 用户给了钉钉文档分享链接作为需求来源
/icode patch 用户测试时给了钉钉链接作为补充资料
# 入口：init|log|plan|start（阶段0 输入收敛）与 patch（阶段0，随时插入）——自动 auth→resolve→ls→download 拉取到 .icode_output/.icode_output_N/dingtalk_source/，作为需求/参考资料输入
# 前置：Chrome 已登录 alidocs.dingtalk.com + pip install browser_cookie3 + 桌面会话(keyring 解锁)；缺一按 tools/dingtalk/README.md 提示补
# 工具：~/.claude/skills/icode/tools/dingtalk/scripts/dingtalk.py（四命令 auth/resolve/ls/download，详见 tools/dingtalk/README.md）
# 仅拉取，不回写钉钉；原生格式(.axls/.doci)需用户在钉钉 UI 导出为 pdf/xlsx 后，再拉导出后的真实文件
# 区分：TB 工单日志用方式D2(tools/tb)，钉钉文档/钉盘用本方式(tools/dingtalk)
```

```bash
# 方式E：精简全流程（fast 模式，单文件/小改动场景）
/icode fast 给工具模块增加 clamp 函数                  # 一键串联：plan→review(1轮无对抗)→merge→code→deepcheck(Reverse)→audit
# 入口警告自动打印：
# ⚠️ /icode fast 模式：
#    - 步骤2 review 固定 1 轮无对抗验证
#    - 步骤5 deepcheck 只跑 Reverse 阶段（跳过 Fixed/Free）
#    - 依赖 plan+1 轮 review+Reverse 单阶段+audit 四道关卡
#    - 复杂需求（跨模块/新架构/安全敏感）建议改用 /icode start 全流程
# 产物：01_plan.md, 02_review.md, 03_plan_final.md, 04_code_review_fix.md, 05_deepcheck.md, 06_audit.md（与 full 模式结构对齐）
```

```bash
# 方式F：工程级知识库生成（doc 步骤，独立于 1~6 流程，任意时刻可跑）
/icode doc                              # 无描述→全局扫描：列各工程知识库 stale 状态 + 建议动作
/icode doc myproject                    # 检查该工程更新（增量优先：git diff 命中章节才重生成）
/icode doc 重新生成 myproject           # 全量重生成（触发确认门：检测手动编辑，警告后才覆盖）
/icode doc myproject 加 feature_xxx     # 新增章节（十位桶自动编号）
# 产物：~/.claude/icode_data/project_docs/<project_id>/<branch>/*.md（按分支分目录，章节自带身份证：前 50 行四块）
# 不创建工单目录、不写工单 metadata、不参与步骤1~6推进
# 生成后，后续 /icode init|log|plan|start|fast 启动时段零自动检索注入相关章节（无需手动告知参考文档）
```

```bash
# 方式I：PPT 生成（ppt 步骤，独立交付，任意时刻可跑；把 icode 产物/知识库转成 .pptx）
/icode ppt                                # 默认场景：最新工单→本次功能开发 PPT（有 log 入口→本次BUG修复）
/icode ppt 项目                            # 项目全景 PPT（内容源：project_docs 工程知识库 + 仓库结构）
/icode ppt 模块 传感器                        # 指定模块 PPT（内容源：模块 doc 章节）
/icode ppt 本次功能开发                      # 最新工单产物→功能开发 PPT
/icode ppt 本次BUG修复                      # log 根因 + 08_patch + 验证→查BUG PPT
/icode ppt "用数据可视化模板做项目PPT"      # 场景+模板风格都用自然语言指定
# 前置：pip install python-pptx（必需）；LibreOffice+poppler 可选（渲染 PNG 自检）
# 产出：<工程根>/.icode_output/ppt/{工程简名}_{场景关键词}.pptx + {场景关键词}_edits.json（可回溯改字；不放进工单目录）
# 内置模板非商业授权（tools/ppt/NOTICE），仅供学习研究；不创建工单目录、不写工单 metadata
```

```bash
# 方式G：主流程后的追加修改（patch 独立步骤，测试发现问题 / 继续迭代）
/icode start 实现MCU雨量传感器I2C驱动   # 主流程 1→6 走完（或走到任意步骤）
# ... 你测试后发现某个场景行为不对 ...
/icode patch 测试发现超时阈值场景下读数跳变    # 独立步骤：重审现状 → 增量计划 → 最小修改 → 反向复检
# 产出：08_patch.md（追加 Patch N 段）+ 06_audit.md 末尾补丁记录 + metadata.patch_history
/icode patch 还发现 I2C 复位时序有问题          # 连续多轮补丁：再追加 Patch N+1，不新建工单
# 特点：靠磁盘产物重载上下文（换会话/换模型可继续），不靠会话记忆，上下文不爆炸

# patch 实机部署验证（可选 flag，配 ~/.claude/icode_data/device_config/<project_id>.json，模板 templates/device_config.json.template，单文件多连接 adb/ssh/串口）
/icode patch --listen 测试发现超时阈值场景下读数跳变   # 自动监听：告知触发即监听，连设备部署 + 持续轮询 LOG + 实时链路分析，用户随时操作被捕获
/icode patch --test 测试发现超时阈值场景下读数跳变     # 显式触发验证：空转停下确认用户已操作再继续（防误触发）
# 无 flag → 跳过实机验证（只走四段式）；--listen/--test 进入阶段4「1.5 实机部署验证」（多设备按硬件唯一标识核指纹，防连错设备误判）
```

```bash
# 方式J：MCP 环境检查+一键安装（install 独立步骤，新 clone / 新机器 / CI 初始化时跑一次）
/icode install                           # 扫描所有 mcp/*/install.sh，每个子工程自检环境（venv/Node/npm）并缺啥补啥、注册到 ~/.claude.json
/icode install context7                  # 只装指定子工程（如 context7）
/icode install --no-auto-install         # 跳过自动装依赖（自己装）
# 可选 MCP 未装时工作流优雅降级（显式声明降级路径，不阻塞）；不创建工单目录、不参与步骤1~6推进
```

```bash
# 方式K：项目约束红线（limit 独立步骤，定义本工程的红线/约束/禁区）
/icode limit                           # 无描述→全局扫描显示当前约束（合并视图：主存+local 覆盖）
/icode limit 禁止修改 sdk 底层接口签名  # 有描述→针对操作生成/追加新红线条目（编号自增，追加式演进）
# 主存：~/.claude/icode_data/limits/<project_id>.md（全局跨 checkout 共享）；覆盖：<工程根>/.icode_output/limit.local/<project_id>.md（自动 gitignore，local 完全覆盖 main）
# 后续 plan 步骤1「前置 limit 硬基线」（统一覆盖 init/start/fast）与 log 前置 limit 红线检查点，各自「先读索引→精读命中」读留痕追加落盘工单级 limit_checkpoint.md 阶段块（plan前置硬基线 / log前置检查点），对照逐条核验红线；不创建工单目录、不参与步骤1~6推进
```

## 通用规则

### 产物命名 + status 词表速查表（硬性速查，防"发明命名/状态"）

> **产物文件名与 status 值一律以 `steps/XX_*.md` 各步骤规定为准，下表只是速查不是第二真源**（完整 status 语义见下方「status 字段枚举」段）。写产物 / 写 metadata 前对照下表核对，命名或状态不在表内 = 不合规（见下方「产物命名硬性条款」）。

**主流程产物文件名**（`{ICODE_OUT_DIR}/` 下，全部小写、不得自造近似名）：

| 步骤 | 产物文件名 | 备注 |
|------|-----------|------|
| 1 | `01_plan.md` | 计划全文 |
| 2 | `02_review.md` + `review_round_{N}.json` | 多轮审查；每轮有 new_issues / pending_verification / refuted_issues 任一非空才写 JSON |
| 3 | `03_plan_final.md` | **完整计划副本**（复制 `01_plan.md` 全文 + 审查采纳标记 + 末尾「实现偏差备忘」空段），不是元数据摘要 |
| 4 | `04_code_review_fix.md` | 步骤4 末尾 1.5「Code Review Fix」复检产物（所有工单都触发，不论入口） |
| 5 | `05_deepcheck.md` | 三阶段复检 |
| 6 | `06_audit.md` | 终审报告（含修复日志段） |
| 0/log | `00_init.md` / `log_analysis.md` | 入口产物 |

**status 词表**（写回 metadata 前逐字对照，禁止自定义）：`log_in_progress` / `log_done` → `init_in_progress` → `plan_done` → `review_in_progress` / `review_done` → `plan_finalized` → `code_in_progress` / `code_done` → `deepcheck_in_progress` / `deepcheck_done` → `completed`（终态）

**产物命名硬性条款**：产物必须按 `steps/XX_*.md` 规定的**文件名、目录、格式**产出；metadata 的 `status` 必须在本词表内。**自定义文件名 / 自定义格式 / 词表外状态值 = 不合规**，内容质量高也不能豁免——内容好 ≠ 机制合规。发现命名/状态不在上表，停下对照对应 `steps/XX_*.md` 修正，不得沿用自造近似（如用 `03_merge.md` 替代 `03_plan_final.md`、自造 `audit_done` 状态）。

### 强制阻断边界矩阵

按检查项的**严重级别**定义统一的"是否阻断流程"语义，避免规则散落在各 step 文件里：

| 级别 | 含义 | 触发后行为 | 典型场景 |
|---|---|---|---|
| **L1·致命** | 阻塞流程的前置条件不满足 | **报错退出**，流程不可继续 | cwd 不在 git 仓库 / 强制产物文件缺失 / MCP 完全不可用 |
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

### 目录管理

**worktree 决策与创建（新建工单入口 · opt-in 参数触发）**：
> **触发规则（opt-in）**：本段由以下两种触发形式之一触发，AI **不主动弹问**——识别到触发意图即执行，未识别则默认原地：
>   1. **flag 形式**：用户命令中含独立 token `--worktree`（**只接受双短横独立 token 形式**：`--worktree`，**不接受** `-worktree`（单短横）/ `--worktree=true` / `--worktree true` 等变体——避免 AI 自主解析变体导致误触；与其它独立 flag token 共存如 `--listen`）
>   2. **自然语言意图声明**：用户在消息正文里显式声明意图，常见措辞如「用 worktree 隔离做」「走 worktree」「独立分支做」「在 worktree 里做」
>   - **不触发（避免误触）**：仅在消息**正文叙述/引用**（反引号包裹、代码块、问题解释、文档片段）中提到 `--worktree` / `worktree` 等字眼 **≠ 触发**——语境属"讨论参数"而非"下达命令"，AI 必须做语境识别
>   - **反向声明（后置优先）**：若消息中同时出现**正向声明**（如「用 worktree」）与**反向声明**（如「别用 worktree」「不要 worktree 隔离」「普通做就行」），AI 取**后置声明**作为最终意图（最后一句即最终意图）
>   - **语境识别失败降级**：语境模糊难以判断时**不弹问**，按"未触发"处理（默认原地）+ L1 触发回显暴露给用户即时纠错（用户看到 `▶ worktree 隔离：未启用` 可主动澄清"用 worktree"补触发）
>   - **不弹问**：识别不触发即默认原地（不主动询问"要不要 worktree？"），符合 opt-in 默认语义；唯一例外 = limit 「worktree 强制禁止」红线命中时（见 [steps/limit.md](limit.md)「§7 worktree 强制禁止红线」）——这是**违规时阻止**，不同于 opt-in 弹问
>   - **触发回显（强制 L1，区分判定态与执行态）**：AI 必须先在回复顶部输出一行状态——
>     - **判定态·触发**：先输出 `▶ worktree 隔离：即将启用 → 准备创建 ../<repo>-wt-<ticket-slug>/（分支 icode/<ticket-slug>）`（`<ticket-slug>` 占位符**动态回填**为 AI 提炼的实际 ticket-slug 值，**勿直接输出尖括号字面**；占位符语义与冲突处理见下方 ⚠️ 段）
>     - **执行态·成功**：创建完成后输出 `▶ worktree 隔离：✓ 已创建 ../<repo>-wt-<ticket-slug>/（分支 icode/<ticket-slug>）`（同 `<ticket-slug>` 占位符回填为实际值）
>     - **判定态·未触发**：`▶ worktree 隔离：未启用（默认，原地建工单）`
>     - **执行态·失败**：`▶ worktree 隔离：⚠ 创建失败，降级原地（wt_degraded=true，原因：<错误>）`
>     - 让用户即时确认自己的意图是否被正确识别（防误触/漏触静默发生；连续两态让"判定→执行"过程透明）
> 
> **执行顺序**：`start`/`plan`/`fast` 先走下方「复用 / 创建新目录决策」**判定为「新建」后**，再判定参数（REUSE=2 复用歧义问「复用/新建」、答「新建」也走本段）；`init`/`log` 直接新建走本段判定。判定为「复用」→ **跳过本段**原地续跑（worktree 只在新建时建，不重复建）。
> **真源**：本段执行细节（创建/降级/字段族/回流/护栏全量规则）见 [references/worktree_isolation.md](references/worktree_isolation.md)，执行本段前 Read 之（本段是精简版，冲突以真源为准）。
```bash
# ① 参数识别：扫描用户当前消息，匹配以下两种触发形式之一
#    A. flag 形式：消息命令位置的独立 token `--worktree`（双短横独立 token；不允许变体）
#    B. 自然语言意图：识别"用 worktree 隔离"/"走 worktree"/"独立分支做" 等显式声明
#    - 反向声明后置优先：同一消息后置「别用 worktree」> 前置「用 worktree」
#    - 不触发：仅叙述/引用/反引号示例 ≠ 触发（语境判断，由 AI 判断）；模糊时按未触发处理
# ② 触发回显（L1 强制，区分判定态与执行态）：
#    - 判定态·触发（创建前）：echo "▶ worktree 隔离：即将启用 → 准备创建 ../<repo>-wt-<ticket-slug>/（分支 icode/<ticket-slug>）"（动态回填 ticket-slug）
#    - 判定态·未触发：echo "▶ worktree 隔离：未启用（默认，原地建工单）"
# ③ 未触发 → 默认原地，直接走下方「创建新目录」，不读 worktree 真源、不创建 worktree、不记 worktree metadata
# ④ 触发 → 执行创建前**告知**（非再次询问；用户触发意图即一次性同意，写操作前最后公示）：
#    - path = "../<repo>-wt-<ticket-slug>" / branch = "icode/<ticket-slug>"
#    - 执行前需 limit 红线检查（违规阻止契约；见 steps/limit.md §7）
# ⑤ 触发 + limit 命中「worktree 强制禁止」 → 提示一次"本工程 limit 禁止 worktree，本工单回退原地建"+ 走 ③ 默认原地，**不创建 worktree**
git rev-parse --is-inside-work-tree             # 前置：必须在 git 仓库（失败→原地降级）
git rev-parse --verify HEAD >/dev/null 2>&1     # 前置：仓库必须有提交（无 HEAD 不能建 worktree）
test -f "$(git rev-parse --show-toplevel)/.git" && { echo "已在 worktree 内→原地"; WT_SKIP=1; }  # 主仓才建
git worktree list                               # 只读：确认目标路径/分支名未占用
[ -z "$WT_SKIP" ] && git worktree add -b "icode/<ticket-slug>" "../<repo>-wt-<ticket-slug>"
# ⑥ 执行后回显：成功 → "▶ worktree 隔离：✓ 已创建 ../<repo>-wt-<ticket-slug>/（分支 icode/<ticket-slug>）"
# ⑦ 创建后：cd 进 worktree → 按下方「创建新目录」逻辑在 worktree 内生成 .icode_output/.icode_output_N
#    （worktree 内无旧产物 → 通常恒为 _1），本工单全部产物在 worktree 内；校验 worktree 内 .icode_output/ 应为空
#    → 非空 = 该工程 .icode_output 未 gitignore（worktree 带入了主仓旧产物）→ 提示「建议配置 .gitignore 排除 .icode_output/」，L3 不阻断
# ⑧ 创建失败（无 HEAD（仓库无提交）/路径冲突/无写权限/FS 不支持/命名冲突修正后仍失败）→ 原地降级 + metadata 记 wt_degraded=true（见「强制阻断边界矩阵」L3）
# ⑨ 业务子仓隔离（repo 多仓库工程，worktree 工单进入 code 前；真源见 worktree_isolation「⑤ 业务子仓隔离」）：
#    super-repo worktree 不覆盖业务子仓（子仓有自己的 .git 在原工程路径）——若需求要改业务子仓，
#    进入 code（步骤4）前必须为每个受影响子仓建隔离 checkout（git -C <原子仓> worktree add -b icode/<ticket-slug>-<子仓slug> <super-wt>/<子仓相对路径>，
#    写 metadata.sub_worktrees），禁止直接改原工程路径子仓；回流时先 commit+merge+remove 子仓再 remove super-worktree

# ⚠️ `<ticket-slug>` 占位符语义（回显与创建共用，必须明确）：
#    - 由 AI 在判定·触发之后、执行·创建之前**自行提炼**（基于当前需求文本，英文短横线、≤30 字符小写；命名规则见 [references/worktree_isolation.md §1「② 创建·命名」](../references/worktree_isolation.md)）
#    - 与 git worktree 命令 `git worktree add -b "icode/<ticket-slug>" "../<repo>-wt-<ticket-slug>"` 中的 `<ticket-slug>` **同一值**（一处定义两处用）
#    - **回显中动态回填**：先用 LLM 提炼的 slug 填占位符显示给用户 → 同一值再喂给 git worktree add 执行
#    - **与 ticket_id 不同**：ticket_id = `{工程名}-{N}`（步骤8 索引写入后回填），`<ticket-slug>` 是早于 ticket_id 的纯提炼 slug（不带工程名前缀、不带目录号 N）
#    - **冲突处理**：与 `git worktree list` 已存在的路径/分支冲突 → 追加 `-2` / `-3`（详见 worktree_isolation §1「② 创建·命名」）；slug 提炼后立即用 `git worktree list` 检查冲突，命中即重提炼
#    - ⚠️ LLM 必须自己提炼、勿向用户索取；勿用占位符字符串（如直接输出 `<ticket-slug>` 而未提炼）执行创建
```

**创建新目录**（原地路径：worktree opt-in **未触发**（默认）/ limit「worktree 强制禁止」红线阻止 / worktree 创建失败降级时；worktree 场景则在 worktree 内执行）：

完整脚本（含目录号递增 + 硬熔断①建前检查 + 硬熔断②建后验证）见 [references/dir_and_metadata.md「创建新目录」段](../references/dir_and_metadata.md)（**真源**：所有入口命令与 step 都引用本段，禁止独立定义或微改）。

**复用 / 创建新目录决策**（用于 `start` / `plan` / `fast`）：

完整脚本见 [references/dir_and_metadata.md「复用 / 创建新目录决策」段](../references/dir_and_metadata.md)（**真源**）。决策三档语义：

> **复用决策三档**：`REUSE=2`（入口态有歧义）→ 必须问用户"复用 / 新建"，按答复定；`REUSE=0`（非入口态）→ 带参新建、无参报错。复用时将 `00_init.md` 作为步骤1主要需求输入（命令行参数作补充）。

**检测最新目录**（用于 `review`/`merge`/`code`/`deepcheck`/`audit`/`patch`/`status`/`readme`）：

完整脚本（含 `LAST` 提取 + 错误提示 + `exit 1` 兜底）见 [references/dir_and_metadata.md「检测最新目录」段](../references/dir_and_metadata.md)（**真源**）。所有 step 文件共享本段，禁止独立微改。

### 前置文件校验

| 步骤 | 必须存在的文件 |
|------|---------------|
| review | `{ICODE_OUT_DIR}/01_plan.md` |
| merge | `{ICODE_OUT_DIR}/01_plan.md` + `{ICODE_OUT_DIR}/02_review.md` |
| code | `{ICODE_OUT_DIR}/03_plan_final.md` |
| deepcheck | `{ICODE_OUT_DIR}/03_plan_final.md` + 步骤4代码文件 |
| audit | `{ICODE_OUT_DIR}/03_plan_final.md` + 步骤4代码文件 |
| patch | `{ICODE_OUT_DIR}/.ico_metadata.json`（且 status 非入口态 `init_in_progress`/`log_done`） |

> **通用依赖**：所有步骤均依赖 `{ICODE_OUT_DIR}/.ico_metadata.json`（读取 status/completed_steps/续跑字段等），上表仅列出各步骤**额外**要求的产物文件。`init`/`plan`/`start` 因会创建 metadata，无前置校验。

缺失则报错并提示需要先执行哪一步。

### 元信息文件（`.ico_metadata.json`）

```json
{
  "requirement": "需求描述",
  "created_at": "创建时间",
  "status": "当前步骤状态",
  "completed_steps": ["1", "2"],
  "code_files": ["path/to/file"],
  "total_rounds": 1,
  "clean_rounds": 0,
  "max_rounds": 3,
  "absolute_cap": 10,
  "extended_rounds": 0,
  "unresolved_issues_at_cap": false,
  "pending_verification": [],
  "code_compile_failed": false,
  "deepcheck_total_rounds": 0,
  "deepcheck_clean_rounds": 0,
  "deepcheck_phase": "reverse",
  "requirement_summary": "",
  "requirement_points": [],
  "keywords": [],
  "indexed": false,
  "ticket_id": "",
  "code_deviations": [],
  "worktree_path": null,
  "worktree_branch": null,
  "wt_degraded": false,
  "cross_project_refs": [],
  "sub_worktrees": [],
  "archive_path": null
}
```

每步执行后必须更新 `status` 和 `completed_steps`。步骤4编码后必须记录 `code_files`。

**`code_files` 路径基准**：所有路径**相对于项目根目录**（即用户运行 `/icode` 命令的目录），不含前导 `./`。例：`src/foo.c`、`include/bar.h`。使用 Read 工具时须将相对路径拼接为绝对路径。

**可选字段**（按需写入，缺失视为默认值）：
- `total_rounds` / `clean_rounds` / `max_rounds`：步骤 2 续跑用（`max_rounds` 由 `/icode review [N]` 参数决定，默认 3；运行中如最后一轮仍有新问题会**自动延长 +2 轮**）
- `absolute_cap`：步骤 2 硬上限，`= max(10, max_rounds初始值 × 2)`，防止无限循环
- `extended_rounds`：步骤 2 自动延长次数（每次延长 +2 轮，触发条件：达到 `max_rounds` 但仍有新问题）
- `unresolved_issues_at_cap`：步骤 2 触达 `absolute_cap` 仍有新问题时置 `true`，提示用户回到步骤1修计划
- `pending_verification`：步骤 2 对抗验证中 `needs_more_evidence` 的 issue 清单（**完整 issue 对象数组**，含 `id`/`affected_sections`/`suggestion`/`rejection_risk`/`evidence_pointer`/`verification_status`，供步骤3定稿时直接复核无需回查 JSON），随轮次动态维护（新增追加、已证实/证伪移除），供步骤3定稿时重点复核
- `code_compile_failed`：步骤 4 编译失败标记（`true` 时步骤 5 入口输出警告）
- `deepcheck_total_rounds` / `deepcheck_clean_rounds` / `deepcheck_phase`：步骤 5 续跑用（`deepcheck_phase` 值：`reverse` / `fixed` / `free`），步骤5完成时最终记录
- `requirement_summary`：一句话需求摘要（≤100 token），跨工程历史检索的主依据。步骤0首轮基于粗略需求生成，步骤0每轮对话后更新，步骤1完成计划后基于完整计划刷新
- `requirement_points`：需求要点清单（≤8 条字符串，每条 ≤30 token），`/icode init` 检索命中时注入用。由步骤0从 `00_init.md`「3.新增需求点」自动提炼，用户无感
- `keywords`：技术关键词（≤8 个），辅助检索匹配
- `workload_estimate`（新增，可选，默认 `"medium"`）：工作量评估等级，枚举 `"small"`/`"medium"`/`"large"`（**字段缺失视为 `"medium"` 中性默认**，向后兼容旧 metadata）。由步骤 0 init 收尾时按 4 维度 max 算法（需求点数/涉及文件数/跨模块数/大改词命中）自动评估，写入 metadata 用于入口建议（small→fast，medium/large→start）。详见 [steps/00_init.md](steps/00_init.md)「步骤 9 工作量评估」段
- `workload_reason`（新增，可选，≤80 token）：工作量评估的简短理由，辅助用户理解"为什么是 large"等判断。**字段缺失视为空字符串**（向后兼容）
- `indexed`：是否已写入全局索引（防重复写入）
- `debug`（新增，可选，缺失 = 正常工单）：bool，仅 `/icode init --debug` / `/icode log --debug` 创建的 debug 孪生工单为 `true`。debug 工单**永不写入 index.json**（`indexed` 恒为 `false`、`ticket_id` 为空串）、**不参与主流程**（各主流程步骤 L1 检测段按 `metadata.debug == true` 阻断，见 [references/debug_mode.md](references/debug_mode.md)）
- `ticket_id`：本工单在全局索引中的唯一键（`{工程名}-{N}`，冲突时带 hash 后缀）。步骤0写索引时持久化到 metadata；**跳过步骤0直接 `/icode plan`/`/icode start` 的常规新建目录情况**，在步骤1首次写索引时生成并回填 metadata。供后续步骤检索时排除当前工单
- `code_deviations`：步骤4 编码时主动偏离定稿计划的记录数组（每条含 `plan_said`/`actual_done`/`reason`），供步骤6 终审汇总回写到 `03_plan_final.md` 的「实现偏差备忘」段；无偏离写空数组 `[]`
- `limit_refs`（默认 `[]`，**产物文本引用 limit 时须填写**）：plan / log 步骤引用的 limit 红线编号数组，每条 `{redline_no: int, source: "main"|"local", title: str, applied_in: [...]}`，`source` 区分主存全局约定 vs 单 checkout 覆盖，`applied_in` 为引用章节。**plan**：§3 架构设计 / §4 ADR / §6 异常处理**引用 limit 条目（计划文本出现「红线 N」/「红 N」）时必须记录**，完全未引用才可留空；audit 视角 B 以**先检测计划是否实际引用**再判定跳过/回补（见 [steps/06_audit.md](steps/06_audit.md) §6.7）。**log**：`log_analysis.md §2.3 limit 红线对照`（必填小节）/ §6 对抗分析记录引用红线时必须记录，经 log 步骤 9.5 机器自检校验（**读留痕 `limit_checkpoint.md` 存在性 + §2.3 存在性 + 引用完整性**，见 [steps/log.md](steps/log.md)）。⚠️ `limit_refs` 是**事后回补**，只证明"后来引用了哪些红线"；"**先读索引→精读命中**"确实读过的可审计留痕靠 `{ICODE_OUT_DIR}/limit_checkpoint.md` 的**工单级追加式阶段块**——log 前置检查点落「阶段块：log前置检查点」（log 9.5 维度④校验），plan 前置硬基线落「阶段块：plan前置硬基线」**统一覆盖 init/start/fast 三入口**（plan limit_refs 机器自检 维度④校验），缺失按未读处理。**字段缺失视为 `[]`（向后兼容旧 metadata）**。详见 [steps/limit.md](steps/limit.md)
- `code_review_fix_with_issues`（新增，可选，默认 `false`）：步骤4末尾 1.5「Code Review Fix」4 维度复检未通过标记（同事提示词 4 维度闭环在 04_code 末尾的工程化复检）。`true` 时步骤5/6 入口输出警告，audit 终审会看到此标记——**不阻断流程**，仅作可见性提示，让后续 reviewer/历史检索知道本工单 4 维度复检未通过。**字段缺失视为 `false`（向后兼容旧 metadata）**
- `test_cmd`/`test_outcome`/`test_failures`/`test_timeout`（测试集成字段）：`test_cmd`=探测/配置的测试命令字符串（null=无测试套件，步骤4 自动探测 Makefile/package.json/pytest.ini/go.mod/CMakeLists.txt/Cargo.toml/pom.xml）；`test_outcome`=枚举 `pass`/`fail`/`skipped`（默认 `skipped`）；`test_failures`=bool（步骤4 测试 3 次重试仍失败置 true，L3 警告不阻断，与 `code_compile_failed` 同级）；`test_timeout`=int 秒（默认 120）。借鉴 aider `auto_test` 机制（一手验证 Aider-AI/aider base_coder.py:1616），icode 增加自动探测。详见 [steps/04_code.md](steps/04_code.md)「编译验证 + 测试验证」段。**字段缺失视为 null/skipped/false/120（向后兼容旧 metadata）**
- `mode`（新增，可选，默认 `"full"`）：工单模式。`"full"` = `/icode start` 全流程（步骤2 默认 3 轮 + 对抗，步骤5 三阶段循环）；`"fast"` = `/icode fast` 精简全流程（步骤2 固定 1 轮无对抗，步骤5 只跑 Reverse）。**字段缺失视为 `"full"`（向后兼容旧 metadata）**。详见 [steps/fast.md](steps/fast.md)
- `max_rounds`（新增，可选，默认 3）：步骤2 review 软上限轮数。`mode="full"` 时由 `/icode review N` 参数决定（默认 3）；`mode="fast"` 时**自动串联下强制为 1**，但**单步命令（`/icode review N`）在 fast 工单上调用时 N 优先级最高**——用户用参数 N 显式表达 fast→full 升级意图时，按 N 轮跑（详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「步骤2/5 读 mode 字段的契约」段）。**字段缺失视为 3**
- `worktree_path`（worktree 字段族，缺省 `null` = 未进 worktree，向后兼容旧工单）：本工单所在 worktree 绝对路径。**非 null 是「本工单在 worktree 内」的判定依据**——status 显示 worktree 列 / 06_audit §6.4 回流提醒 / 07_readme worktree 状态段均读它。工单回流 `git worktree remove` 后随 worktree 消失（全局索引 `project_path` 由 stale 检测标 `path_gone` 留档，见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「过时校验」）。创建 worktree 时写入，见「目录管理·worktree 决策与创建」
- `worktree_branch`（worktree 字段族，缺省 `null`）：本工单分支 `icode/<ticket-slug>`（worktree 场景下与索引 `created_branch` 一致——**冗余存储**，便于 status 直接读 metadata 显示分支，不强制双写一致性）
- `wt_degraded`（worktree 字段族，缺省 `false`）：bool，worktree 创建失败降级原地标记（`true` = 未进 worktree 且已降级，见「强制阻断边界矩阵」L3）
- `cross_project_refs`（worktree 字段族，缺省 `[]`）：数组，跨工程 worktree 引用——本工单转工单到关联工程时追加 `{project_id, ticket_id, worktree_path}` 指向 B 工单及其 worktree；B 工单自身用 `worktree_path` 记录自己的。从 A 工单可完整追溯「本需求涉及的每个工程的 worktree」
- `sub_worktrees`（worktree 字段族，缺省 `[]`）：业务子仓隔离 checkout 记录（repo 多仓库工程，仅涉及子仓修改的 worktree 工单）：数组元素 `{sub_path, worktree_path, branch}`——`sub_path`=子仓相对 super-repo 路径、`worktree_path`=子仓隔离 checkout 绝对路径（在 super-worktree 内同名相对路径）、`branch`=`icode/<ticket-slug>-<子仓slug>`。首次建子仓隔离时追加，回流回收时清。见 [references/worktree_isolation.md](references/worktree_isolation.md)「⑤ 业务子仓隔离」
- `archive_path`（worktree 字段族，缺省 `null`）：本工单核心产物归档目录（`~/.claude/icode_data/worktree_archive/<project_id>/<ticket_id>/`）。**06_audit 终审**标记 `status=completed` 且 `worktree_path` 非 null 时自动归档写入（见 [references/worktree_isolation.md](references/worktree_isolation.md)「产物归档」）；同步写全局索引条目。`archive_path` 非 null 且 `test -d` 有效的工单为 **archived 活跃历史工单（不标 stale）**：检索照常命中，`project_path` 失效（worktree remove）时从归档读 ADR/根因走历史参考，命中正常续期 + 按 verdict 分流，待遇与主仓工单一致（见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「过时校验」归档工单）。缺省 `null` = 原地工单或未归档

- `fix_tiers`（新增，可选，默认 `null`）：修复方案三档分级（反偷懒第 26 条）。`{"A": ["A1..."], "B": ["B1..."], "C": ["C1..."]}` 供 review/code/audit 核对实施范围。**由步骤1 plan §4.5 落盘**（每档 1-2 条一句话摘要），步骤2/4/6 核对实施范围时读取；字段缺失视为 `null`，从 `03_plan_final.md` §4.5 文本读（向后兼容旧 metadata）
- `confirmed_B_fixes`（新增，可选，默认 `[]`）：步骤4 实施 B 档兜底修复前记录的**用户显式确认清单**（每条含 B 档内容简述）。**字段缺失视为 `[]`（向后兼容旧 metadata）**。仅当用户显式确认后才实施 B 档并记录；未确认的 B 档不实施
- `anchors_enabled`（可选，默认 `true`）：决策锚点机制开关。`true` 时各步骤完成后写 `.decision_anchors.json` + 下游启动读（传递关键决策摘要，省 token + 不丢上下文）；`false` 跳过。详见 [references/decision_anchors.md](references/decision_anchors.md)。**字段缺失视为 `true`（向后兼容旧 metadata）**
- `patch_count`（可选，默认 `0`）：追加修改次数（`/icode patch` 调用次数累计）。`/icode patch` 启动时读它确定本次 `Patch N` 序号（N = patch_count + 1），完成后写回新值。**不改变 `status`/`completed_steps`**——patch 是横向追加非纵向推进。**字段缺失视为 `0`（向后兼容旧 metadata）**
- `patch_history`（可选，默认 `[]`）：追加修改历史数组，每次 `/icode patch` 完成**追加**一条 `{"patch_no": N, "summary": "一句话（≤100 token）", "files": ["相对项目根路径..."], "at": "时间", "status": "done"|"issues"}`（`issues` = 阶段4 复检发现新引入问题且当场未修复，L2 警告不阻断）。供回读工单演进链 / 历史检索 / 06_audit 补丁记录对齐。**字段缺失视为 `[]`（向后兼容旧 metadata）**。详见 [steps/08_patch.md](steps/08_patch.md)

> **`verdict` 字段族（方向结论，v2 新增）**--与 `status` 流程态正交：`status` 表示"流程走到哪步"，`verdict` 表示"方案方向对不对"。用于历史检索注入分流，防止已被证伪/取代的工单误导新需求（详见下文「历史检索复用·注入分流」段）：
> - `verdict`（可选，默认 `"unknown"`）：枚举 `"unknown"`（未判定，旧工单默认）/`"verified"`（已验证有效，实机通过/终审高分/已上线无回退）/`"disproved"`（核心方案被证伪/已回退，如某"暂停数据流"方案实机发现语义是"重置状态"而非"冻结"，从根上不可行）/`"superseded"`（被替代方案取代，指向 `superseded_by`）。**字段缺失视为 `"unknown"`（向后兼容旧工单，走原注入逻辑 + 对抗质疑兜底）**
> - `verdict_reason`（可选，≤150 token）：为何这个 verdict。`disproved` 时填证伪原因
> - `correct_direction`（可选，≤150 token）：正确方向。`disproved`/`superseded` 时填，是反转注入避坑的核心载体
> - `verdict_source`（可选）：结论来源，枚举 `machine_test`/`review`/`user`/`auto_signal`，可信度递减
> - `verdict_at`（可选）：结论时间（运行时取系统时间，禁写死，同 `last_used_at` 约定）
> - `superseded_by`（可选）：`"superseded"` 时填替代工单 `ticket_id`
> - `verdict_premise_deps`（可选，数组，默认 `[]`）：证伪前提依赖的外部模块列表，支持"硬复活"。`disproved`/`superseded` 时填（`/icode status --verdict ... --premise-dep <module>:<commit>[:<path>]`，可多次）。每条 `{module, commit, path}`：证伪前提（如"某接口语义是重置非冻结"）依赖这些模块的当时行为；模块 commit 变了->证伪前提可能失效->须重新评估。**空数组/缺失->无硬复活能力，靠主解软复活**（不阻塞）
> - `verdict_review_needed`（可选，bool，默认 `false`）：证伪前提是否需重新评估。置 `true`：`verdict_premise_deps` 非空且任一 dep.commit != 该模块当前 HEAD（`--scan-verdict` 主动扫或检索命中被动检测）。注入分流：`false`->硬反转+证伪前提断言验证；`true`->降级走 unknown 对抗质疑（不硬避坑，防漏过后来又可行的方向）。**字段缺失视为 `false`**（向后兼容旧 disproved，走主解软复活）
> - **录入途径**：`/icode status --verdict` 手动标注（[steps/status.md](steps/status.md)）/ 步骤6 终审回填（[steps/06_audit.md](steps/06_audit.md)）/ 批量识别扫描提示（`/icode status --scan-verdict`，[steps/status.md](steps/status.md)）
> - **幂等保护**：verdict 一旦判定，后续 status 流转不重置；只有"步骤6 终审重新评估"或"用户显式改标"才覆盖（覆盖时刷新 `verdict_at`）

**`status` 字段枚举**（统一词表，所有步骤必须严格遵守，禁止自定义）：

| 步骤 | 状态值 | 含义 |
|------|--------|------|
| log | `log_in_progress` → `log_done` | 日志根因分析中 → 完成（入口命令，与步骤0并列，不参与步骤1~6推进） |
| 0 | `init_in_progress` | 步骤0需求初稿讨论中（多轮对话每轮更新文档，无显式"完成"态） |
| 1 | `plan_done` | 步骤1计划完成 |
| 2 | `review_in_progress` → `review_done` | 步骤2审查中 → 完成 |
| 3 | `plan_finalized` | 步骤3定稿完成 |
| 4 | `code_in_progress` → `code_done` | 步骤4编码中 → 完成（含末尾 1.5 "Code Review Fix" 4 维度复检；`code_review_fix_with_issues=true` 时审计可见，不阻断流程） |
| 5 | `deepcheck_in_progress` → `deepcheck_done` | 步骤5复检中 → 完成 |
| 6 | `completed` | 步骤6终审完成（终态） |

> **debug 工单例外**：`/icode init --debug` / `/icode log --debug` 创建的 debug 孪生工单使用**独立状态名** `debug_in_progress` / `debug_done`（不入上方主流程词表校验范围——debug 目录在 `.icode_output/.debug/` 下，天然被「检测最新目录」排除、不参与 `--validate`，无状态机冲突；见 [references/debug_mode.md](references/debug_mode.md)）

**步骤0说明**：步骤0产出 `00_init.md` 后 status 一直保持 `init_in_progress`，直到 `/icode start`/`/icode plan` 复用该目录进入步骤1时才被切换为 `plan_done`。`completed_steps` 含 `"0"` 表示走过步骤0。

**`in_progress` 状态的两种语义**：

- `init_in_progress`：步骤0**稳态**标记，文档每轮增量更新，等待 `/icode start`/`/icode plan` 复用并切换到 `plan_done`。**不参与崩溃续跑判定**。
- `log_in_progress` / `log_done`：`/icode log` 日志根因分析的**分析中→完成**标记。`log_done` 后用户质疑可切回 `log_in_progress` 只重跑被质疑的根因分支（详见 [steps/log.md](steps/log.md)）。**不参与步骤1~6 推进**（`completed_steps` 含 `"log"` 仅标记走过 log）。
- `review_in_progress` / `deepcheck_in_progress`：步骤 2/5 的**中断续跑标记**，每轮结束时实时落盘 `*_in_progress` + 续跑计数器，崩溃后重启可从断点恢复。步骤2落盘 `total_rounds`/`clean_rounds`/`max_rounds`/`absolute_cap`/`extended_rounds`/`pending_verification`；步骤5落盘 `deepcheck_total_rounds`/`deepcheck_clean_rounds`/`deepcheck_phase`。
- `code_in_progress`：步骤 4 的**执行中标记**（只在步骤4整体开始时落盘 `code_in_progress`，完成后切换为 `code_done`，不带轮次/阶段维度的断点续跑）。

**status 写回校验（强制，防词表外值落盘）**：每步写 `.ico_metadata.json` 前，必须先对照上方词表校验 `status` 在枚举内（`completed_steps` 中的步骤号须是 `steps/*.md` 清单里存在的合法值），词表外值直接判不合规、拒绝写回并修正。校验用一行命令：

```bash
python3 -c "import json,sys; d=json.load(open('{ICODE_OUT_DIR}/.ico_metadata.json')); valid={'init_in_progress','plan_done','review_in_progress','review_done','plan_finalized','code_in_progress','code_done','deepcheck_in_progress','deepcheck_done','completed','log_in_progress','log_done'}; s=d.get('status'); print('status:', s); sys.exit(0 if s in valid else 1)"
```

### 执行模式

所有步骤（含可选步骤0）在主会话中执行，使用当前会话模型。**不主动切换模型**，用户如需切换可手动 `/model`。

### 强制思考前置 + 反偷懒约束（所有步骤必须遵守，硬性总则）

完整规则见下表两真源（步骤执行前**必须** Read 完整内容，不得凭 SKILL.md 概述或记忆执行——见反偷懒第 15 条）：

| 主题 | 真源 | 核心要点 |
|------|------|---------|
| 强制思考前置 | [references/thinking_core.md](references/thinking_core.md)（每步必读）+ [references/thinking_detail.md](references/thinking_detail.md)（按需读） | 每步开始前先 `ultrathink`，首选 `sequential-thinking` MCP 至少 3 步；MCP 不可用降级 `### 结构化思考` 文字块；思考子项见各 step 文件 |
| 反偷懒约束 | [references/anti_laziness.md](references/anti_laziness.md) | 31 条典型偷懒行为 + 正面合规要求；引用 references 必须每步重新 Read 输出 `📖 已 Read` 确认行；思考块每子项 ≥2 句实质内容 |

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

### 历史检索复用（跨工程/跨工单借鉴）

> **检索复用两源**（init/log/plan/start/fast 启动时并行检索，候选合并排序注入，最相关者胜）：
>
> - **源1·历史工单**（本段）：跨工单借鉴相似需求的 ADR/风险/根因/要点，详见下文
> - **源2·工程文档（段零）**：当前工程 `~/.claude/icode_data/project_docs/<project_id>/` 知识库（`/icode doc` 生成），段零只读章节前 50 行粗筛、命中按 `[小节锚点]` 定点读小节。**过时章节降级注入**（stale 章节不注正文只注摘要+警告，与历史工单 stale 跳过注入同等防误导）+ **注入文档须 Read/Grep 实证不盲信**（文档是快照可能过时，不作代码事实依据）。**v2 模板质量信号**（v2.0.0 新增）：章节 `_meta.json.template_version` 与 [doc_template.md](references/doc_template.md) 顶部 `SCHEMA_VERSION` 比对，**v2 章节注入优先级 > v1 章节**（v1 章节降级注入摘要+升级提示），保证下游尽量拿到高质量上下文。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「段零·工程文档检索」「stale 章节降级注入」「不盲信约束」+「质量信号」+「双视角使用说明」段 + [references/doc_template.md](references/doc_template.md)
> - **防重复注入**（两源共用）：`{ICODE_OUT_DIR}/_inject_cache.json` 按 `(source, ref_id, slice)` 三元组去重，历史源 `hit_count` 同目录内同 ticket 只续期一次。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「注入缓存机制」段

**痛点**：每次 `/icode` 都是冷启动，过往相似需求的计划/决策/踩坑无法被新需求复用。本机制在不破坏工程隔离、不撑爆上下文的前提下，让新需求能主动检索历史相似工单并定点注入参考。

> **verdict 防误导（v2 新增）**：历史工单的核心方案可能已被实机证伪/取代，直接注入其 ADR 会把新需求带向错误方向。本机制给每个工单标 `verdict`（方向结论，与 `status` 流程态正交），注入按 verdict 分流：`disproved` **反转注入避坑**（不注 ADR，注证伪原因+正确方向）、`superseded` 注替代指针、`unknown`（含所有旧工单，**不依赖标注**）走 A 层强制强化（扩读 `00_init.md` 末轮+对抗质疑+⚠️警告）兜底。录入：`/icode status --verdict` 手动标 / 步骤6 终审回填 / `/icode status --scan-verdict` 批量识别提示。详见「注入形式·按 verdict 分流」+ [references/dir_and_metadata.md](references/dir_and_metadata.md)「过时校验·verdict 分流注入」+「续期·verdict 分流」段

**全局索引**（不污染任何工程，不放技能目录）：

- 索引文件：`~/.claude/icode_data/index.json`（首次运行自动创建）。**路径说明**：`~` 是当前用户主目录，由 Claude Code 工具层跨平台解析（Linux/macOS/Windows 通用），与技能目录 `~/.claude/skills/icode/` 同源。技能文件**禁止硬编码**任何具体用户路径（如 `/home/xxx`、`C:\Users\xxx`），所有全局路径必须用 `~` 表达，确保技能可移植
- 每条记录：`ticket_id`(`{工程名}-{N}`，工程名冲突时追加 `project_path` 短 hash 后缀保唯一)、`project_path`、`out_dir`、`requirement_summary`(≤100 token)、`requirement_points`(≤8 条)、`keywords`(≤8个)、`has_00_init`/`has_plan`、`status`、`created_at`、`last_used_at`(检索命中时更新，LRU淘汰依据)、`hit_count`(检索命中+1，达20永久保留)、`stale`(默认false，过时校验失败置true，软stale可复活)、`stale_reason`(失败原因`anchor_gone`/`checkout_mismatch`/`path_gone`/`semantic_deviation`/`timeout`)、`stale_checked_commit`(上次评估时HEAD，可复活判据)、`created_commit`(创建时HEAD，commit上下文判据，非git仓库为null)、`created_branch`(`--abbrev-ref HEAD`)、`tb_source`（可选，null 或 {lib,num,pid,label}：TB 缺陷单溯源，log 步骤按 lib+num+pid 检索同单复用）、`verdict`/`verdict_reason`/`correct_direction`/`verdict_source`/`verdict_at`/`superseded_by`/`verdict_premise_deps`/`verdict_review_needed`（方向结论字段族，默认 `verdict="unknown"` 其余 null/[]/false，详见上「verdict 字段族」；检索注入按 verdict 分流，`disproved`/`superseded` 不续期 hit_count、不享受永久保留、排序降权，详见「索引淘汰规则」）。**`has_00_init` 语义 = 该工单是否已产出 `00_init.md`（走过 init 或 log，log 也会产出 00_init.md），与"是否走过步骤0 init"解耦**——log 未走过 init 但产出 00_init.md 故也为 true
- **只存指针和摘要，不存产物正文**。产物仍在各工程 `.icode_output/`，工程隔离不破坏。
- **LRU 淘汰**（防 index.json 无限膨胀）：索引是检索缓存非档案。容量上限 200 条；`hit_count >= 20` 且 `verdict != "disproved"` 永久保留（被复用≥20 次的高价值工单；`disproved` 不永久保留，详见「索引淘汰规则」）；未完成态（init/log/review/deepcheck/code in_progress）默认不淘汰，但**超时降级**（`last_used_at` 超 30 天无更新->置 `stale=true`+`stale_reason=timeout` 解除保护、纳入可淘汰，不新增 status 值；timeout 为硬 stale 不复活）；超上限时在**可淘汰集**淘汰 `last_used_at` 最老的：① `hit_count < 20` 且 `stale=false` 完成态，或 ② `stale=true` 且 `stale_reason=timeout` 的超时降级僵尸（status 仍 in_progress）；**软 stale（非 timeout）保留不淘汰待复活**。另**主动 stale 扫描**：每次写索引顺带校验最旧 K 条锚点，失效置 `stale=true`。检索命中**原子同步**更新 `last_used_at`+`hit_count` 续期。淘汰只删索引条目，产物保留各工程。**排序**：tickets 数组按复合键 `(verdict_priority, hit_count)` 降序、同值按 `last_used_at` 降序（`verdict_priority`: verified>unknown>superseded>disproved，详见「索引淘汰规则」）（高价值近期项在前，段一粗筛扫 keywords 快+淘汰从末尾）。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「索引淘汰规则」
- **过时校验**（防注入过时信息）：索引存的是工单当时的摘要，工程迭代后老工单 ADR/需求可能已过时。**两处触发**：①检索命中准备注入前（被动）；②每次写索引触发淘汰后，主动 Grep 校验最旧 K 条代码锚点（主动）。锚点失效→置 `stale=true` 跳过注入（即使 hit_count 高也不注入）。stale 工单保留索引留追溯，不再续期，不再被段一粗筛命中。**project_docs 工程文档同样有过时校验**：段零注入前被动 stale 检测（命中 KEYS 文件位置或正文目录前缀即标 stale，降级注入不注正文）+ `/icode doc` 末尾主动 stale 扫描（全库锚点校验写 `_meta.json.stale_files`，见 [steps/doc.md](steps/doc.md) 步骤8）+ module_docs commit 一致性校验（同分支不同 commit 降级注入+警告）。**历史工单 stale 校验**：commit 上下文比对（`created_commit` vs 当前 HEAD：同提交高置信注入/后代跑锚点/祖先分叉软stale）+ top-N 注入前语义偏离 checklist（抓"锚点在但语义变"）+ stale 可复活（checkout 变化重评，解决临时旧提交误判）+ **Git 操作只读白名单**（禁 checkout/reset/commit 等写操作与网络操作，详见「过时校验·Git 操作安全白名单」）。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「检索命中续期 + 过时校验」「索引淘汰规则·主动 stale 扫描」「project_docs 主动 stale 扫描」「段零·工程文档检索·步骤 3 commit 一致性校验」

**写索引时机**（**`keywords` 是段一粗筛的检索索引，所有入口首次写索引时必须填 ≤8 个技术关键词、不得为空**——空 keywords 的工单无法被粗筛命中，等于检索盲区）：
- `/icode log` 产出 `log_analysis.md` + `00_init.md` 后：**首次生成 `ticket_id`**（`{工程名}-{N}`，冲突加 hash 后缀）并回填 metadata，写入 `requirement_summary`（根因摘要）+`requirement_points`（修复要点）+`keywords`（≤8个，从根因/症状提炼）+`has_00_init=true`+`status=log_done`+`last_used_at=当前`+`hit_count=0`+`stale=false`+`stale_reason=null`+`stale_checked_commit=null`+`created_commit`（`git rev-parse HEAD` 只读，非git仓库为null）+`created_branch`+`tb_source`（{lib,num,pid,label}，从 metadata 读，无 TB 源时 null），**写后执行LRU淘汰 + 主动 stale 扫描**
- 步骤0 首轮写 `00_init.md` 后：**首次生成 `ticket_id`**（`{工程名}-{N}`，冲突加 hash 后缀）并回填 metadata，写入 `requirement_summary`+空 `requirement_points`+`keywords`（≤8个，从粗略需求提炼）+`workload_estimate`（4 维度 max 算法首评）+`workload_reason`（≤80 token 理由）+`has_00_init=true`+`last_used_at=当前`+`hit_count=0`+`stale=false`+`stale_reason=null`+`stale_checked_commit=null`+`created_commit`（`git rev-parse HEAD` 只读，非git仓库为null）+`created_branch`，**写后执行LRU淘汰 + 主动 stale 扫描**
- 步骤0 每轮对话更新后：刷新 `requirement_summary`；`requirement_points` **仅在首次写索引时生成**（步骤0首轮），步骤0 每轮对话不重复刷新（步骤1 完成 `01_plan.md` 时再统一刷一次）
  - **`requirement_points` 提炼算法**（明确可执行）：
    1. 扫描 `00_init.md` 中 `## 3. 新增需求点` 章节下的 `- [ ]` / `- [x]` 列表项
    2. 每行去掉 checkbox 前缀（`- [ ] ` / `- [x] `），保留核心短语作为一条 `requirement_points`
    3. 若某行超 30 字符，截断到 30 字符 + `...`（避免索引体积爆）
    4. 最多保留 8 条，多余的丢弃
    5. 若「3. 新增需求点」章节缺失/为空，`requirement_points` 保持空数组
    6. 例：`- [x] calc_eval 函数签名` → `"calc_eval 函数签名"`
- 步骤1 写完 `01_plan.md` 后：刷新 `requirement_summary`（基于完整计划）+ `has_plan=true`；**常规新建目录首跑时**（跳过步骤0）在此首次生成 `ticket_id` 并回填 metadata、首次写入索引条目（`has_00_init=false`、`keywords`（≤8个，从计划技术栈提炼，不得为空）、`last_used_at=当前`、`hit_count=0`、`stale=false`、`stale_reason=null`、`stale_checked_commit=null`、`created_commit`（`git rev-parse HEAD` 只读，非git仓库为null）、`created_branch`），**写后执行LRU淘汰 + 主动 stale 扫描**
- 步骤6 终审完成后：刷新 `status=completed`，`requirement_summary` 若与最终交付显著偏差则基于最终成果刷新；**若 `stale=true` 重置 `stale=false`+`stale_reason=null`+`stale_checked_commit=null`**（旧 stale 判据失效，下次检索重评）；**确认 verdict**（默认保持 `unknown` 不阻塞流程；用户标 `verified`/`disproved`/`superseded` 时回填 `verdict`+`verdict_reason`+`correct_direction`+`verdict_source`+`verdict_at`，详见 [steps/06_audit.md](steps/06_audit.md)）

**检索注入流程**（`/icode init`、`/icode log`、`/icode plan`、`/icode start`、`/icode fast` 共用检索，分流注入；段零工程文档候选与本流程候选合并排序后统一注入，不分来源）：

1. **检索阶段·两段式**（强制思考**之前**；`/icode init`/`/icode log` 在建目录后检索，`/icode plan`/`/icode start` 在目录管理+确定需求来源后检索——确保用完整需求做相关性判断）：

   **段一·粗筛（不进 LLM，纯计算，零 token 消耗）**：从当前需求/症状提炼关键词集 `K_new`，**先过滤**当前 `ticket_id`（不自我参考）+ **可复活预扫**（对每条 stale 工单取 `H = git -C {project_path} rev-parse HEAD`（只读）；`stale=true` 且 `stale_reason != timeout` 且 `stale_checked_commit != H` 的临时置 `stale=false` 重入候选重评，见步骤2「可复活 stale」）。**归档工单天然不受影响**：`archive_path` 非 null 且 `test -d {archive_path}` 有效的工单为 **archived 活跃态，非 stale**，不被段一排除、照常进候选走归档读档（worktree 回流已归档工单虽 `project_path` 失效，但 ADR/根因已归档可读档复用，见 [dir_and_metadata.md](references/dir_and_metadata.md)「过时校验·归档工单」）——故段一无需对归档工单特判，它与其他活跃工单同等待遇后剩余的 `stale=true`--段一粗筛前**显式排除**而非粗筛后再过滤，降低计算量；再与**全量 `tickets` 数组中**剩余 ticket 的 `keywords` 做集合交集（index.json 是完整 JSON，必须 `json.load` 整体解析全量读，禁止只读前 N 行--「前 50 行」仅适用于 `project_docs/*.md` 章节，见 [dir_and_metadata.md](references/dir_and_metadata.md)「段零·工程文档检索」段），按 **Jaccard 相似度**（`|K_new ∩ K_ticket| / |K_new ∪ K_ticket|`）降序排列。取相似度 > 0 的前 **≤10 条**作为候选集（候选为 0 则直接零命中结束）。**关键词缺失的工单**（`keywords` 为空）在粗筛中无法被命中，故写索引时 `keywords` 不得为空（≤8 个技术词）。

   > **为何先粗筛**：index.json 到 200 条上限时全量进上下文 ≈ 3.5 万 token，纯靠 LLM 现场扫全部 summary 会撑爆 context 且判断质量随条数下降。粗筛把 O(全部) 降到 O(候选集)，实测能圈出 ≤10 条强相关候选。

   **段二·精读（调 `mcp__cheap-research__retrieve_similar`）**：只把候选集的 `keywords + requirement_points`（约 50-100 token/条，10 条 ≤1K token）喂给 cheap-research 的 `retrieve_similar` 工具，传入 `query`=当前需求/症状、`candidates`=候选集（每项含 `id`/`summary`/`keywords`/`status`）、`k`=候选集总数（确保所有候选都被评分，不截断）。返回带 `score` 的排序结果。按分数选 top-N 命中（N 由梯度规则定，见下）。**降级**（cheap-research 不可用）：退回主代理手动打分（`Agent(model="haiku")` 兜底，见 [references/mcp_integration.md](references/mcp_integration.md) ⑦ 段），不阻塞流程。

2. **过时校验 + 命中续期**（对 top-N 命中工单，注入前逐条；`H = git -C {project_path} rev-parse HEAD`（每候选一次）；详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「过时校验」）：
   - **项目路径校验**：`test -d {project_path}` 失败->`stale=true`+`stale_reason=path_gone`，跳过注入
   - **commit 上下文校验**（`created_commit` 非 null）：`H==created_commit`->高置信注入快路径；`git merge-base --is-ancestor {created_commit} {H}`：退出0->正常演进进锚点校验；退出1->`stale=true`+`stale_reason=checkout_mismatch`（软stale，checkout变化可复活）；退出128（commit不可达）->视同null进锚点校验
   - **代码锚点校验**：Grep 该工单工程 `{project_path}` 的 ADR 锚点是否仍存在；失效->`stale=true`+`stale_reason=anchor_gone`，跳过注入
   - **语义偏离校验**（仅对已决定注入的 top-N≤3 条）：Read 该工单工程 `{project_path}` 的锚点代码按偏离 checklist 判 ADR 前提（签名/返回值边界/调用关系）是否仍成立；偏离->`stale=true`+`stale_reason=semantic_deviation`，跳过注入（抓"锚点在但语义变"）

   **命中续期**：全部校验通过的工单，**按 verdict 分流续期**——`verified`/`unknown`（含旧工单）原子同步更新 `last_used_at`=当前时间、`hit_count`+=1 写回（两字段同一次写回，不得只更其一--详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「续期（校验通过才续期）·原子同步」）；`stale_checked_commit=H` 在评估时已更新（与 hit_count 解耦，续期去重不阻断）。`stale=true` 的工单不注入不续期（软stale可复活见下）。

   **可复活 stale**（解决 checkout 假阳性）：段一前对每条 stale 工单取其 `H = git -C {project_path} rev-parse HEAD`；`stale=true` 且 `stale_reason != timeout` 且 `stale_checked_commit != H` 的工单临时置 `stale=false` 重入段一重评（重评仍失败则更新 `stale_checked_commit=H`，同 HEAD 不再重评）。**所有 git 调用只读**，禁止 checkout/reset/commit 等写操作与网络操作（白名单见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「过时校验·Git 操作安全白名单」）。

   **续期去重**（防 hit_count 虚高）：续期前查 `{ICODE_OUT_DIR}/_inject_cache.json`，若本工单目录已续期过该历史工单（`source=history AND ref_id=该 ticket` 任一记录）则不再 `+1`（新 slice 仍注入）。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「注入缓存机制·续期去重」段

3. **注入阶段·top-N 动态梯度**（按命令分流，N 由相关性梯度决定；**注入前查 `{ICODE_OUT_DIR}/_inject_cache.json` 去重**——按 `(source, ref_id, slice)` 三元组查，已注入的 slice 跳过。历史源 `(history, ticket, <slice>)`、段零 `(project_doc, 章节文件, section:<file>)`。详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「注入缓存机制·去重规则」段）：

   - **强相关**（精读分数 ≥8）：全注入，上限 2 条
   - **弱相关**（精读分数 5~7）：在强相关之外再注 ≤1 条最相关的作"边缘参考"
   - **零强相关但有弱相关**：注 ≤2 条最相关
   - **全无相关**（分数 <5 或候选集为 0）：**零注入，不强凑参考**
   - 注入总量上限：强相关 2×1K + 弱相关 1×0.8K ≈ 2.8K token（plan 模式；init/log 按下表体积上限）

   | 命令 | 命中后注入内容 | 来源 | 体积上限 |
   |------|--------------|------|---------|
   | `/icode init` | 命中工单的 `requirement_points`（需求要点清单） | 读 metadata 或 `00_init.md`「3.新增需求点」 | ≤500 token/条 |
   | `/icode plan` / `/icode start`（`/icode fast` 委托 plan） | 命中工单的 **ADR 章节 + 风险评估章节** | 定点读 `01_plan.md` 对应章节（**不读全文**） | ≤1K token/条 |
   | `/icode log` | 命中工单的 **根因结论 + 决定性证据** | 定点读 `log_analysis.md`「核心结论 + 决定性证据」章节（**不读全文**） | ≤800 token/条 |

4. **注入形式·按 verdict 分流**（v2 新增，核心防误导机制）：命中工单经段二精读+过时校验后，**先读其 `verdict` 字段按值分流注入**（字段缺失视为 `"unknown"`，向后兼容旧工单）：

   | verdict | 注入内容 | 来源 | 体积上限 | 思考块标注 |
   |---------|---------|------|---------|-----------|
   | `verified` / `unknown`（含旧工单） | 上表对应命令的注入内容（ADR+风险/根因/要点） + **`unknown` 额外扩读 `00_init.md` 末轮对话摘要** | 上表来源 + `00_init.md` 末轮 | 上表上限 + ≤0.3K（末轮） | ✅借鉴 / `unknown` 时 ⚠️「结论未经验证标注，须甄别是否被后续实机推翻」 |
   | `disproved`（`verdict_review_needed=false`） | **不注 ADR**，注 `verdict_reason`（作可验证断言）+ `correct_direction` | metadata/index verdict 字段 | ≤0.7K/条 | ⛔「避坑+证伪前提断言：须 Grep/Read 验证前提是否仍成立，失效则提示 `--verdict` 标复活」 |
   | `disproved`/`superseded`（`verdict_review_needed=true`） | **降级对抗质疑**：不硬反转，走 unknown A 层（扩读末轮+三问）+ 证伪前提+依赖变化提示 | metadata verdict + 末轮 | ≤1.1K/条 | ⚠️「曾证伪但依赖已变化，原证伪可能失效，重新评估是否还成立」 |
   | `superseded` | 替代指针 `superseded_by` + `correct_direction` + 替代工单摘要 | metadata + 替代工单 | ≤0.8K/条 | 🔁「已被替代，参考新方案 {superseded_by}」 |

   - 历史参考作为主代理的**思考输入**，在强制思考文字块里加一节「历史参考」，按上表 verdict 标注 + 注入内容，影响后续产出质量
   - **`unknown` 强制 A 层强化**（旧工单防误导主防线，不依赖标注）：扩读末轮 + 对抗质疑三问（详见 [references/thinking_detail.md](references/thinking_detail.md)「历史参考小节」）
   - `disproved` 的 `correct_direction` 缺失时：降级注 ADR + ⛔ 警告（ADR 仅作避坑对照），提示用户用 `/icode status --verdict` 补标 `correct_direction`
   - **verdict_review_needed 复活降级**（防漏过后来又可行的方向）：`disproved`/`superseded` 工单若 `verdict_premise_deps` 非空且任一依赖 commit 已变化，则 `verdict_review_needed=true`，**不硬反转**，降级走 unknown A 层对抗质疑 + 证伪前提+依赖变化提示，让新需求重新评估证伪前提是否仍成立；前提失效则该方向或可重新考虑，提示 `/icode status --verdict` 标复活（unknown/verified）。检测：`--scan-verdict` 主动扫 + 检索命中被动检测（详见 [references/dir_and_metadata.md](references/dir_and_metadata.md)「过时校验·verdict 分流注入」+ [steps/status.md](steps/status.md)）
   - **零命中不注入，不强凑参考**

**防撑爆四道闸门**：
- **索引体积**：全局索引单条只存摘要+要点+关键词，整体 <2K token
- **粗筛控量**：两段式检索只把 ≤10 条候选集 keywords+requirement_points 喂 LLM（非全量），控 token
- **注入数**：top-N 动态梯度，强相关≤2 + 弱相关≤1，全相关时上限 3 条（与段零工程文档候选合并后总量≤3 条一致，见 [dir_and_metadata.md](references/dir_and_metadata.md)「段零·工程文档检索」段步骤 4）
- **注入体积**：init 注入要点 ≤500 token/条；plan 注入 ADR+风险 ≤1K token/条；log 注入根因+证据 ≤800 token/条；超大工单需深读时派子代理消化成摘要返回（隔离上下文）

**工程污染防护**（重要）：
- 历史参考**只进会话上下文，不写进产物文件**（`00_init.md` 完全不写历史引用；`01_plan.md` 不堆砌历史引用）
- **唯一最小留痕**：若某条 ADR 实质借鉴了历史工单决策，在该 ADR「理由」末尾加一句 `(参考相似工单 {ticket_id} 的同类决策)`——这是决策溯源而非污染，且可选
- 全局索引在 `~/.claude/icode_data/`，工程内无感知；产物路径不动

**边界处理**：
- 全局索引不存在 → 首次运行自动创建空索引，检索跳过
- 命中工单产物读不到（工程被删/移动）→ 跳过该条不报错，索引条目惰性保留
- `00_init.md` 无「3.新增需求点」→ `requirement_points` 为空，`/icode init` 命中时不注入该条
- `log_analysis.md` 无「核心结论 + 决定性证据」章节（如工单未走过 log）→ `/icode log` 命中时不注入该条

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

### MCP 调用覆盖强制化（强证据二元化）

> **核心问题**：AI 默认只调 sequential-thinking（必用项），其他 MCP 全部跳过--根因是"形式强制"（产物必含记录段）而非"执行强制"（流程必走调用）。**治本**：消除 🟡"应该调"模糊地带，二元化（🟢 必须调 / ⚪ 不必调），并用**双保险**把 🟢 MCP 写进执行流：

**强制规则**：

1. **产物文件不记录 MCP 调用信息**（消除 MCP 噪声对用户的干扰）：MCP 调用结果只进**思考块**「MCP 调用」段（按 [references/thinking_core.md](references/thinking_core.md) 通用流程第 3 步 gate + 各 step 执行步骤内嵌点），不写入 01_plan.md / 02_review.md / 03_plan_final.md / 04_code_review_fix.md / 05_deepcheck.md / 06_audit.md / log_analysis.md / 00_init.md 等产物文件。
2. **🟢 必须调的 MCP**：强证据场景满足 + MCP 可用 -> **必须实际调用一次**（双保险承载），失败/空才能降级。降级需在思考块「MCP 调用」段写明原因（MCP 不可用 / LSP server 缺失 / 调用返回空）

3. **⚪ 不必调的 MCP**：强证据场景不满足 -> 无需评估、无需声明、无需记录

4. **双保险承载**：
   - **A 层·执行步骤内嵌**：cheap-research 等在各 step 执行步骤主体里有独立的调用指令（非末尾推荐表），AI 顺序执行必然走到
   - **B 层·thinking_core MCP gate**：[references/thinking_core.md](references/thinking_core.md) 通用流程第 3 步--思考块先列本步 🟢 MCP（工具已在列表直接可见则直接调用，不可见才 ToolSearch 取 schema）-> 实际调用 -> 结果进思考块。覆盖 context7/memory/vision-bridge/playwright

**降级路径仍然合规**：MCP 真的不可用（tool unavailable / LSP server 缺失），用 Bash/Read/Write/Grep 等原生工具替代--降级不是错误，但**必须先实际调用一次，失败/空才能标降级**，且**必须显式声明**。

详见 [references/mcp_per_step.md](references/mcp_per_step.md)。

### 工具调用模式规范（连续执行约束）

解决"每次只发一个工具调用，等结果回来后就停，需要用户手动点继续"的交互问题。**两条硬性规则**：

1. **批量独立调用**：所有无依赖关系的工具调用必须在同一个回复中并行批量发出。包括但不限于：
   - 多个独立文件的 Read（如同时读 3 个 step 文件）
   - 多个独立目录的 ls 查询
   - 多个不相关的 grep/ripgrep 搜索
   - 多个独立 MCP 调用（如同时调 context7 和 memory）
   - 多个互不依赖的 Bash 命令
   - 多个独立子任务用 Agent 工具并行启动

   **判定**：A 的结果影响 B 的执行 → 串行；A 和 B 互不依赖 → 必须并行一个回复发出。

2. **连续执行不等待**：拿到工具结果后，在同一回复中立即继续后续步骤/决策，不等用户主动推进（不论用户说什么）。仅以下情况可以停下来问用户：
   - 需要用户做二元决策（如"复用/新建目录"）
   - 不可逆操作需用户确认（如删除/覆盖文件）
   - 信息不足以推进（如需要用户补充需求细节）

**违规示例**（禁止）：
```text
# ❌ 错误：分开发送，每次停
curl -s ...  # 发一个请求，停，等用户推进
# （用户说了点什么才继续）
curl -s ...  # 再发一个，再停
```

**合规示例**（必须）：
```text
# ✅ 正确：批量发送独立调用 + 拿到结果后继续
Read file_a
Read file_b
Bash ls dir_a
# （全部在同一个回复发出，拿到结果后立即继续，不等用户）
```

**本规则与 MCP 调用覆盖强制化的关系**：MCP 调用覆盖强制化决定"哪些 MCP 必须调"（内容维度），本规则决定"如何调"（模式维度）——两条规则配套使用，缺一不可。

## MCP 工具集（双保险承载）

icode 工作流可调用 6 个 MCP（`/icode install` 一键安装）。**双保险承载**：🟢 MCP 由执行步骤内嵌 + thinking_core gate 双驱动，确保真实触发--按 [references/mcp_per_step.md](references/mcp_per_step.md) 推荐级别（🟢 必须调 / ⚪ 不必调，**消除 🟡**）执行。

**核心文档**：
- [references/mcp_integration.md](references/mcp_integration.md)：**每个 MCP 的强证据 + 降级路径**（必读）
- [references/mcp_per_step.md](references/mcp_per_step.md)：**步骤 × MCP 推荐矩阵（强证据二元化）**
- [references/thinking_core.md](references/thinking_core.md)：**MCP gate（通用流程第 3 步）**
- 本文件「MCP 调用覆盖强制化」章节：强制规则

**判定逻辑**：AI 在每个步骤开始时，按 [references/mcp_per_step.md](references/mcp_per_step.md)「强证据场景判定」判定每个 MCP 是否 🟢：
- 证据 A：`Read ~/.claude.json` 的 `mcpServers.<name>` 段存在
- 证据 B：工具可在当前会话直接调用（工具列表直接可见——按语义识别，标准 `mcp__<name>__<tool>` 或代理前缀 `__<proxy>_<tool>` 形态——或 ToolSearch 可取 schema）
- **强证据场景满足**（如 context7 在 plan 步骤 + 需求涉及第三方库）+ 证据 A/B 任一 -> 🟢 必须调
- 强证据场景不满足 -> ⚪ 无需评估

**🟢 MCP 承载**：
- context7 / memory / vision-bridge / playwright -> B 层（thinking_core gate）
- sequential-thinking -> thinking_core 通用流程第 4 步（结构化思考载体）

**未调用合规处理**：🟢 需在思考块「MCP 调用」段写降级原因（先实际调用一次，失败/空才能降级）；⚪ 无需记录。

### 6 个 MCP 速览

| MCP | 用途 | 🟢 强证据场景 | 承载层 |
|---|---|---|---|
| **sequential-thinking** | 强制思考前置 | 所有步骤 | thinking_core 第 4 步 |
| **context7** | 库文档实时查询 | init/plan/code + 涉及第三方库 | B 层·thinking_core gate |
| **vision-bridge** | 图片/视频理解 | 任意步骤 + 用户给图(直接调) / TB 缺陷源附件含视频/图片时 **vision-bridge 可用则主动调**(视频先用 ffmpeg 本地抽帧省钱；不可用时仅提示不主动调，防纯文字模型报错)，详见 [steps/log.md](steps/log.md)「附件分析（含本地路径 + TB 源）与 ffmpeg 抽帧」 | B 层·thinking_core gate |
| **playwright** | 浏览器自动化 | deepcheck/audit + 前端工程 | B 层·thinking_core gate |
| **memory** | 跨工单记忆 | init/plan + 本工程有历史工单 | B 层·thinking_core gate |
| **cheap-research** | 便宜 LLM 推理（降本） | init/log/doc/plan/review/code/deepcheck/audit/readme + 单闸门入选的 23 个子任务（长上下文压缩/历史检索/模板填充/结构化提取/TB 评论预提取/代码事实审计/模式扫描/符号追溯/差异摘要等）；未装走 Agent(model="haiku") 兜底。**不接管决策**：3 质疑者对抗/架构决策/终审裁决/修复方案一律不走（零灰区原则） | B 层·thinking_core gate + 执行步骤内嵌 |

> cheap-research 跟 vision-bridge 模式完全对齐（用户自己配 URL/KEY/模型，不锁平台），详见 [mcp/cheap-research/README.md](mcp/cheap-research/README.md) + [references/mcp_integration.md](references/mcp_integration.md) ⑦ 段 + [references/mcp_per_step.md](references/mcp_per_step.md) 矩阵。

### 速用示例

```bash
# 一键安装所有 6 个 mcp
/icode install

# 只装一个
/icode install context7

# 跳过自动装依赖（自己装）
/icode install --no-auto-install

# 对应卸载
./mcp/uninstall.sh                  # 全部卸载
./mcp/uninstall.sh playwright       # 只卸 playwright
```

### 工具命名约定

实际工具名格式：`mcp__<server-name>__<tool-name>`

- 示例：`mcp__sequential-thinking__sequentialthinking` / `mcp__vision-bridge__analyze_media` / `mcp__memory__read_graph` / `mcp__playwright__browser_navigate`

### sequential-thinking（强制思考前置）

- **必装**。所有步骤必用，详见 [references/thinking_core.md](references/thinking_core.md)
- 强证据：`mcp__sequential-thinking__sequentialthinking`（至少 3 步）
- 降级：`### 结构化思考` 文字块（一项不可省）

### vision-bridge（图片/视频理解）

视觉理解是可选增强，**统一走 `mcp__vision-bridge__analyze_media` 工具**。

- **TB 缺陷源附件视频/图片**：log 步骤拉取 TB 缺陷源后,`tb_source/<ID>/` 下若含视频(`*.mp4`/`*.mov`/`*.avi` 等)/图片(`*.png`/`*.jpg`/`*.jpeg` 等),**vision-bridge 可用则主动调**——视频先用 ffmpeg 本地提取关键帧(免费),再传图片帧给 vision-bridge 分析(省 API 额度)。vision-bridge 不可用时仅提示附件清单不主动调(防纯文字模型报错)。详见 [steps/log.md](steps/log.md)「附件分析（含本地路径 + TB 源）与 ffmpeg 抽帧」与「TB 视频/图片附件研读」(反偷懒第 23 条含 vision-bridge 不可用豁免条款)
- **本地日志目录视频/图片**：`/icode log` 分析本地日志时,日志目录下若含视频/图片文件,vision-bridge 可用则主动调（扫目录枚举,视频同走 ffmpeg 抽帧,行为同 TB 源模式）。详见 [steps/log.md](steps/log.md)「附件分析（含本地路径 + TB 源）与 ffmpeg 抽帧」段
- **装好 vision-bridge 且 `config.json` 配好三件套（base_url/api_key/model）**：`mcp__vision-bridge__analyze_media` 可用，**优先用 MCP 工具**走统一接口
- **没装 vision-bridge，或装了但 `config.json` 三件套没填**：`analyze_media` 工具返回 fallback 提示字符串，**降级**——AI 不替用户判断原生能力
  - 原生支不支持图片/视频 **视具体 session 模型而定**（Opus/Sonnet 一般支持，Haiku 可能部分支持）
  - **用户自己把握**原生能力是否够用；AI 不假装"可以原生处理"
  - 不报错、不阻塞
- **vision-bridge 不绑任何平台**：任何 OpenAI Chat Completions 兼容端点都能用，**不推荐任何 provider 或模型名**——用户自己填 `base_url` / `api_key` / `model`
- 安装：`cd ~/.claude/skills/icode/mcp/vision-bridge && ./install.sh`，三件套在生成的 `config.json` 里配（不入 `~/.claude.json`，不污染环境）
- 详见 [mcp/vision-bridge/README.md](mcp/vision-bridge/README.md)

### 其他 3 个 MCP（memory / context7 / playwright）

详细说明（强证据 / 降级路径 / 工具签名 / 依赖）见 [references/mcp_integration.md](references/mcp_integration.md)。

**关键约定**：
- 每个 MCP 都标注**强证据**和**降级路径**
- **不阻塞**：MCP 不可用不是错误，降级操作**完全合规**
- **不假设即装**：session 模型上下文**不能假设**任一 MCP 已装，必须先判定

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
| doc | `doc` | [steps/doc.md](steps/doc.md) |
| limit | `limit` | [steps/limit.md](steps/limit.md)（独立步骤，不参与 1~6 流程推进；plan 步骤硬基线引用源） |
| ppt | `ppt` | [steps/ppt.md](steps/ppt.md)（独立交付步骤：项目/模块/本次功能开发/本次BUG修复 → .pptx） |
| - | `install` | [steps/install.md](steps/install.md)（独立 MCP 装/同步步骤）|
| - | `status` | [steps/status.md](steps/status.md) |
| - | `list` | [steps/list.md](steps/list.md)（跨工程工单查找，纯查询） |

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
| [references/anti_laziness.md](references/anti_laziness.md) | 反偷懒约束（31条偷懒行为+合规要求+references必读+确认行） | 所有 step |
| [references/adversarial.md](references/adversarial.md) | 对抗分析模式（3质疑者/裁决优先级/诚实降级/证据回指） | 02_review / log |
| [references/dir_and_metadata.md](references/dir_and_metadata.md) | 目录管理（创建新目录含**硬熔断**：建前 test -d + 建后 ls -A 验证，禁手写目录号/echo 伪确认）+ ticket_id 生成 + 全局索引写入（含LRU淘汰） + metadata 模板 + **过时校验（含 worktree 归档工单**：archive_path 有效→archived 活跃态读档历史参考，正常续期） + **注入缓存机制（防重复注入，两源共用）** + **project_docs 工程文档库 + 段零检索** | init / log / plan / start / fast / doc |
| [references/doc_template.md](references/doc_template.md) | icode doc 章节模板：前 50 行四块结构（项目元信息/KEYS/简要说明/目录）+ 十位桶编号 + 自适应 grep 关键词表 + 99 章审计策略 + **v2.0.0 双视角必含元素清单（14 项）+ 业务流独立成章 + 英文首次中文备注 + 链路中文说明 + 质量审视检查清单 + 模板版本自举迁移** | doc |
| [references/necessity_check.md](references/necessity_check.md) | **现有功能覆盖度检查（防重复实现机制）**：触发时机 + 执行命令（全工程检索 + Read 命中处行为链）+ 三类判定（已覆盖/部分/未覆盖）+ 各步骤落点（init §2.X/预筛列、plan 前置/断言/ADR/对抗、review 维度7、deepcheck Reverse 对比、audit 视角 C） | init / plan / review / deepcheck / audit |
| [references/first_activation_path.md](references/first_activation_path.md) | **首次激活路径一致性检查**：静态分析盲区（"写了从没实机执行过"的死路径既有 bug）+ 触发条件 + 检测法（软信号、不阻断）+ 双侧校验一致性核对清单 + 部署后验证建议下游输出 | plan（断言⑤）/ deepcheck（Reverse）/ audit（部署后建议）/ patch（部署后验证发现） |
| [references/worktree_isolation.md](references/worktree_isolation.md) | **git worktree 多需求隔离**：worktree 决策与创建（**opt-in 参数触发**：`--worktree` 才走创建，否则默认原地，不弹问；**预检/公示告知/失败降级**）+ cwd 契约 + metadata 字段族 + 回流指引（F2 二选一）+ **产物归档（自动，防 remove 丢档）** + 防误删护栏 + 空间自查 | 新建工单入口加 `--worktree` 时（init/log/start/plan/fast）/ 续跑与只读（review/code/deepcheck/audit/patch/status/readme） |
| [references/debug_mode.md](references/debug_mode.md) | **debug 模式（独立孪生工单）**：`/icode init --debug` / `/icode log --debug` 产出对照工单，不入全局索引、不参与主流程（各主流程步骤 L1 阻断）；目录在 `.icode_output/.debug/` 下、N 独立递增；`debug: true` 元数据标志 + 独立状态名；忽略 `--worktree` | init / log（`--debug` 时） |
