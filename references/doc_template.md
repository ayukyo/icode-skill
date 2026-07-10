# icode doc 章节模板与自适应规则

<!-- SCHEMA_VERSION: v2.0.0 -->
<!-- 模板版本号：steps/doc.md 步骤 5「模板版本迁移」子步骤读此版本号，与章节 _meta.json.template_version 比对 -->
<!-- 旧 v1 章节（无 template_version 字段）一律视为待升级；v2 章节享有段零注入优先级（见 dir_and_metadata.md「段零·工程文档检索·质量信号」段） -->
<!-- v2.0.0 相对 v1 的破坏性变化：双视角必含元素清单（14 项）/ 业务流独立成章 / 英文首次中文备注 / 链路中文说明 / 质量审视机械可验证 -->

> `/icode doc` 生成章节的模板规范，被 [steps/doc.md](../steps/doc.md) 引用。核心哲学见 [references/dir_and_metadata.md](dir_and_metadata.md)「project_docs 工程文档库」段：**零配置/零状态/零索引文件，章节前 50 行自带身份证**。

## 〇、模板版本与双视角设计（v2 新增）

**SCHEMA_VERSION = v2.0.0**——本版本对 v1 的核心破坏性变化：

1. **双视角设计**：DOC 文档**不只是给人看的**，还是 init/log/plan/start/fast 步骤**段零检索的上下文载体**。前 50 行 KEYS + H2 摘要 + 锚点表的设计同时服务于"人"（章节导览）和"AI"（注入上下文）
2. **14 项必含元素**：每类章节（overview / workflow / module）强制必含若干元素，AI 生成时机械可验证，缺项视为偷工
3. **业务流独立成章**：识别到 N 个业务流（如工序编排中枢常含 flow_x/flow_y/flow_z 等）时，每个流独立章节而非塞进 runtime 一章
4. **英文首次中文备注**：所有英文术语首次出现必含中文+全称+类比（如 `IPC = Inter-Process Communication（进程间通信）`）
5. **链路中文说明**：所有数字链路（如 topic 数量、线程数）必含一句因果中文，让读者理解"数字背后的故事"
6. **模板版本自举迁移**：旧 v1 章节被识别后自动重生成，无需用户手动触发（详见 [steps/doc.md](../steps/doc.md) 步骤 5「模板版本迁移」子步骤）

**质量信号机制**：章节 `_meta.json.template_version` 字段记录生成所用模板版本。段零检索时 v2 章节注入优先级 > v1（v1 降级注入摘要+升级提示），详见 [references/dir_and_metadata.md](dir_and_metadata.md)「段零·工程文档检索·质量信号」段。

## 一、章节前 50 行四块结构（强制）

每个章节前 50 行含四块，正文从 `---` 后开始。前 50 行是段零检索唯一载体（只读前 50 行判断相关性），必须自包含；短章节不足 50 行时前 50 行即全文，无副作用。

```markdown
# {章节中文标题}

> **项目元信息**
> - 工程名：myproject
> - 项目类型：git-root（或 repo-root，.repo/manifest.xml 管理）
> - Git 地址：git@example.com:user/myproject.git
> - 分支/提交：main @ a3f2b1c (2026-07-06 12:30)
> - 子模块（git submodule，v1 字段）：thirdparty/lib_algo → ...git @ c8d2f1a；thirdparty/lib_nav → ...git @ b9e4d3c
> - 依赖子模块（按仓库+分支，段零跨仓库覆盖用）：module_a → module:module_a@main@a3f2b1c（module_docs/{key}/）；module_b → module:module_b@dev@b8c3d4e（module_docs/{key}/）
> - 产品线/型号：{产品代号，无则填"未识别"}
> - 章节归属模块：module_a
> - 模板版本（v2 字段）：v2.0.0
> - 章节生成时间：2026-07-06T15:30:00Z

> **KEYS**（术语→小节锚点，便于段零定位）
> - 核心类：[架构] MyBaseNode, [节点框架] initParam/handleData
> - 关键技术：[IPC] <框架名>, [模块A] feature_x
> - 文件位置：src/module_a/feature_node.cpp
> - 关联模块：module_b, module_c
> - 关键 IPC topic：[IPC] feature_result, status_event

> **简要说明**（50~100 字）
> module_a 负责 feature_x，订阅上游数据、发布结果给 module_b。基于 thirdparty/lib_algo 适配。

> **本章节目录**（H2 锚点，段零定点读）
> - [1. 节点框架](#1-节点框架) | [2. 数据流](#2-数据流) | [3. IPC 契约](#3-ipc-契约)

---

## 1. 节点框架

{正文从这里开始}
```

**四块职责**：项目元信息块（身份证、stale/冲突/隔离用）、KEYS（检索词+小节锚点）、简要说明（语义判断）、本章节目录（定点读小节不灌全章）。

**元信息块字段取值**见 [steps/doc.md](../steps/doc.md)「元信息块字段取值约定」段，此处不重复。

**KEYS 双覆盖**：同时含客观词（类名/文件/topic）+ 主观词（H2 标题/加粗术语），按符号搜与按概念搜都能命中。

### 详尽度目标（强制，禁"列表式简述"风格）

> 反例（不合规）：只列"类名表 + 文件名表 + 一句话定位"就交差——这类章节虽然事实没错但**无法让新人 30 分钟内读懂一个模块**。"先讲是什么，再讲为什么"的教学风格必须保留。

**双视角设计原则**：DOC 文档**同时服务两类读者**：
- **人**（新人 / 维护者 / 改 bug 者 / 做新功能者）：要的是导览、类比、图、角色路径、故障索引
- **AI**（init/log/plan/start/fast 步骤的段零注入上下文）：要的是 H2 摘要、可 grep 锚点、跨章节引用、命令索引

两类读者的需求有重叠也有差异，必含元素清单双视角兼顾（见下「14 项必含元素」）。

### 14 项必含元素（按章节类型分级，机械可验证）

> 每项标注"视角"（人/AI/双）和"验证方式"（grep 验证/表格行数/LLM 阅读）。AI 生成时每项必含，缺项视为偷工（与 [anti_laziness.md](anti_laziness.md) 一致）。

#### overview 章节（6 项必含）

| # | 元素 | 视角 | 验证方式 |
|---|------|------|----------|
| 1 | **# 0. 新手 X 分钟导览**（≥ 500 字，含类比/比喻） | 人 | grep `## 0\..*新手` + 字数统计 |
| 2 | **ASCII / mermaid 全栈图**（≥ 15 个 box） | 双 | grep ` ``` ` code fence 数 |
| 3 | **H2 一句话摘要列表**（前 50 行"本章节目录"段每行附 ≤ 50 字摘要） | AI | grep `## \d` 计数 + 摘要长度 |
| 4 | **常见修改/问题 file:line 锚点表**（"想改 X → 看 `file.cpp:123`" ≥ 8 行） | AI | grep `file\.cpp:\d+` 行数 |
| 5 | **角色化阅读路径表**（≥ 5 种角色：新人/改 bug/做新功能/查 API/优化性能 等） | 双 | 表格行数 |
| 6 | **故障现象索引表**（≥ 5 行 "现象 → 排查点" 映射） | 双 | 表格行数 |

#### workflow 业务流章节（4 项必含）

| # | 元素 | 视角 | 验证方式 |
|---|------|------|----------|
| 1 | **# 0. 新手先看**（含"为什么这个流要这么复杂"问题铺垫） | 人 | grep `## 0\..*新手` |
| 2 | **业务流程 ASCII 时序图**（≥ 1 张） | 双 | grep 时序图 / sequenceDiagram / sequence 关键词 |
| 3 | **状态机入口枚举 + 关键状态转移表**（from_state × event × to_state，≥ 6 行） | AI | grep 状态名 + 转移表行数 |
| 4 | **故障排查表**（症状 × 根因 × 修复，≥ 5 行） | 双 | 表格行数 |

#### module 章节（4 项必含）

| # | 元素 | 视角 | 验证方式 |
|---|------|------|----------|
| 1 | **# 0. 新手先看**（"什么是 X，为什么用 X"概念前置） | 人 | grep `## 0\..*新手` |
| 2 | **关键类/文件表 + 调用链图** | 双 | grep 类名引用 + 框图 |
| 3 | **API 锚点速查表**（类名/方法名/文件:行号，≥ 6 行） | AI | grep `\.cpp:\d+` / `\.hpp:\d+` 行数 |
| 4 | **典型场景示例**（≥ 3 个，每个含"输入/调用链/输出"三段） | AI | 计数 `### 示例` 子段 |

### 教学性元素补充规范（推荐，非强约束）

- **英文术语首次出现**：含全名 + 中文翻译 + 类比（如 `**IPC** = **Inter-Process Communication**（进程间通信，进程间相互"说话"的机制）`）。后续重复出现可直接用英文术语
- **链路数字说明**：所有 topic 数量 / 线程数 / 子模块数等数字串，**必含一句因果中文**（如 `19 路订阅 + 13 路发布 + 9 路 client + 9 路 server = 50 个通信通道，所以契约文档不长不行`），避免纯数字堆砌
- **类比与生活化解释**：复杂概念用生活化类比（**用通用场景作类比，不绑定具体业务**——如"工厂+工头+工人"或"指挥家+乐团"或"流程树 vs if-else"等），新人 5 分钟内能听懂
- **跨版本差异点小节**（涉及代码演进时推荐）：标注"与上一版/上游工程的差异点"——维护者最关心

### 反偷懒红线（与 anti_laziness.md 一致）

- **禁"列表式简述"**：仅"类名+文件名+一句话定位"表格无任何展开 → 不合规
- **禁"只复制代码块"**：仅贴 XML/代码块无文字解读 → 不合规
- **禁"省略状态转移"**：状态机章节无转移图/转移表 → 不合规
- **禁"省略错误分支"**：workflow 章节无错误分支/重试策略 → 不合规
- **禁"省略新人视角"**：00_overview 无新手导览 / 类比 / 全栈图 → 不合规

## 二、章节编号：十位桶

```text
00_overview.md              # 永远首章（总览+导览+全栈图）
10_architecture.md           # 跨模块架构
20~29_<module>_<topic>.md   # 模块章节桶 1（留空隙，插入用 25_xxx 不重排）
30~39_<module>_<topic>.md   # 模块章节桶 2
...
90_glossary.md               # 术语表
99_code_facts_audit.md       # 永远末章（代码事实审计）
```

顺序编号（00-16）插入需全局重排；十位桶留空隙，插入取桶内 `max(NN)+1` 不动其他桶。

## 三、章节命名规范

- 文件名 `<NN>_<module>_<topic>.md`，小写 snake_case 英文；标题 `# {中文标题}`
- 跨模块章节省 `<module>`（`00_overview.md`、`10_architecture.md`、`90_glossary.md`）
- 模块章节必填 `<module>`（`20_module_a_overview.md`、`25_module_a_ipc.md`）

### 业务语义命名引导（v2 新增，强烈推荐）

> `topic` 命名直接决定章节"看起来像代码视角还是业务视角"。**业务/工作流类章节优先用业务语义命名**，避免纯代码结构词（如 `state_machine` / `ipc_and_topics` 这种过抽象、看不出"在讲什么业务"的命名）。

| 章节性质 | 推荐 `<topic>` 后缀 | 反例（不合规） | 正例 |
| --- | --- | --- | --- |
| 业务流程（端到端跑通一类任务） | `_workflow` / `_lifecycle` / `_flow` | `module_a_state_machine.md` | `module_a_flow_x_workflow.md`、`module_a_flow_y_lifecycle.md` |
| 任务准入与排队 | `_admission` / `_queue` / `_dispatch` | `module_a_task_lifecycle.md` | `module_a_mission_admission.md` |
| 安全/仲裁 | `_arbitration` / `_safety` / `_verdict` | `module_a_safety_and_arbiter.md` | `module_a_safety_arbitration.md` |
| 状态上报/对外契约 | `_reporting` / `_status_publishing` / `_api` | `module_a_ipc_and_topics.md` | `module_a_state_reporting.md` |
| 持久化/恢复 | `_persistence` / `_recovery` / `_resume` | `module_a_storage.md` | `module_a_persistence_and_recovery.md` |
| 定位/感知融合 | `_localization` / `_fusion` / `_sensing` | `module_a_sensor.md` | `module_a_localization_fusion.md` |
| 启动/重定位 | `_startup` / `_relocation` / `_boot` | `module_a_init.md` | `module_a_mowing_startup.md` |
| 模块文件索引 | `_module_index` | — | `14_module_index.md` |

**判定原则**：拿到一个候选 `<topic>` 时，问自己"读者只看文件名能不能猜出这一章在讲什么业务"——答不上来就改成业务语义名。**业务枚举名优先于抽象结构名**（如业务流名优先于 `state_machine` / `ipc`）。

## 四、固定章节

| 文件 | 内容 |
|------|------|
| `00_overview.md` | 新手导览 + 一句话定位 + 功能速览 + 全栈图 + 产品线差异。元信息块 generation_commit 是全库 stale 检测基准 |
| `90_glossary.md` | 术语表（缩写/专有名词） |
| `99_code_facts_audit.md` | 代码事实审计：并行子代理交叉验证全库 file:line 引用真实存在且描述属实。**永远生成，增量+并行**（见六） |

## 五、动态章节（按代码特征自适应）

**先扫描后生成**，不预置固定列表。扫描工程的代码特征类别，命中则追加对应章节：

| 代码特征类别 | 追加章节 | 桶 |
|------|------|------|
| 节点框架 + 进程间通信 | `architecture_and_modules.md` + `messages_and_ipc.md` | 10 / 20-29 |
| 数据记录/录制 | `data_recording.md` | 20-29 |
| 固件升级 | `build_and_ota.md` | 20-29 |
| 序列化协议 | 并入 `messages_and_ipc.md` 或独立 | 20-29 |
| 多产品线分支（编译宏/配置开关） | `bringup_and_launch.md` + 文档标差异 | 20-29 |
| 运行时编排（行为树/状态机/任务框架） | `runtime.md` + `arbitration.md` | 20-29 |
| 外部通信协议（无线/串口/总线等） | `protocol.md` + 生命周期章节 | 20-29 |
| 传感器桥接（定位/激光/惯性/视觉等） | `sensor.md` | 20-29 |
| 标定/校准流程 | `calibration.md` | 20-29 |
| **业务流程维度**（v2 新增，业务枚举 / 任务流 / 工作流） | **每个识别到的业务流独立章节**（命名 `_workflow` / `_lifecycle`，见 §三业务语义命名） | 20-39（按识别到的流数量顺位编号） |

**业务流维度扫描规则**（与代码特征扫描并列，**强制必跑**，避免"全部塞进 runtime 一章"的过粗粒度）：

1. **触发条件**：grep 命中以下任一模式 → 触发业务流维度扫描
   - `enum class\s+\w*(Type|Kind|Flow|Phase|State)\b` 业务枚举定义
   - 状态机入口类（**通用命名模式，非绑定真实类名**——如 `Try*` / `On*` / `Activate*` / `Start*` / `Handle*` 等典型命名习惯）
   - 行为树 XML 中存在多个 `SubTree ID="BT_*SuiteTree"` / `BT_*Flow`（多任务子树并行）
   - 配置文件含 `[section_*]` 多 section（每个 section 对应一个独立业务流）
2. **识别每个业务流**：枚举值/SubTree 名/section 名 → 每条生成独立 workflow/lifecycle 章节
3. **章节示例**（用通用占位，避免绑定真实项目）：
   - `20_module_a_flow_x_workflow.md`（业务流 X 端到端）
   - `30_module_a_flow_y_workflow.md`（业务流 Y 端到端）
   - `40_module_a_flow_z_workflow.md`（业务流 Z 端到端）
4. **与"运行时编排"章节互补**：runtime 章节讲"调度机制怎么转"（如行为树怎么 tick、命令队列怎么排队），workflow 章节讲"每个业务流端到端走什么步骤、状态怎么转移、错误怎么分支"——**禁止把多个业务流塞进一个 runtime 章节**（粒度过粗，新人看不懂"具体一种业务怎么跑"）
5. **桶位分配**：识别到 N 个业务流时，按 N 顺位占用 20/30/40... 桶（不与现有章节冲突），间隔留空隙便于插入

**AI 自主 grep**：表中"类别"是提示，AI 执行时根据工程实际技术栈选择具体 grep 模式，**不硬编码具体框架/库名**。未命中任何特征 → 只生成固定章节（00/10/90/99），不强行凑。

## 六、`99_code_facts_audit.md` 生成策略

**永远生成**（不因工程大跳过），增量+并行控成本：

- **首次**：并行子代理（general-purpose，禁用 Explore），每个验证一批 file:line 引用。从全库正文提取引用（grep `\w+\.\w+:\d+`），子代理实读返回 `{ref, exists, matches, issue}`，汇总失效项 + 修正建议
- **增量**：读 `00_overview.md` 的 generation_commit，`git diff <commit>..HEAD --name-only` 拿变更文件，只重验引用了变更文件的 file:line
  - **语义联动盲区（已知局限）**：增量只重验"引用了变更文件"的 file:line；未变更文件因他处变更而语义变化时不被重验（如 a.c 没变但 b.c 改了导致 a.c 中函数 A 的行为变，a.c:100 相关引用不重验 → 章节描述可能已过时但 99 章不发现）
  - **一跳联动缓解**：为缩小盲区，对变更文件直接调用/引用的未变更文件也纳入重验范围--grep 变更文件调用的函数符号 / 引用的类型，把被引用的未变更文件加入重验集（仅扩一跳，控 token）；仍无法覆盖"间接语义联动"（二跳及以上），残余过时由段零 stale 检测 + 不盲信约束兜底（见 [dir_and_metadata.md](dir_and_metadata.md)「stale 检测」「不盲信约束」）
- **局部失败**：单个子代理失败 → 该批标 `[未验证-子代理失败]`，不拖垮全局，末尾标红可针对性重跑

## 七、KEYS 与简要说明生成

**AI 自动生成**（首次+更新），用户可手动覆盖（直接改 md，下次段零立即生效）：

- **核心类**：grep `class\s+\w+`/`struct\s+\w+` 取前 10，锚点 = 所在 H2 小节
- **关键技术**：代码客观词（从 fenced code 块和 API 名提取）+ 叙事主观词（H2 标题/加粗术语）
- **文件位置**：正文出现的所有 .cpp/.h/.py/.c 等源码路径完整聚合去重（不得遗漏--遗漏会导致段零 stale 检测漏判假阴性，见 [dir_and_metadata.md](dir_and_metadata.md)「stale 检测」以 KEYS 文件位置为锚点）；含模块内相对路径
- **关联模块**：正文 `[xx章](xx.md)` 链接 + 提及的其他模块
- **关键 IPC topic**：grep 工程既有 IPC 发布/订阅/服务端 API 的模板参数
- **简要说明**：首段+尾段提炼，50~100 字，讲清"解决什么+关键设计+模块关系"，不复述标题

## 八、与段零检索的契约

段零检索流程见 [references/dir_and_metadata.md](dir_and_metadata.md)「段零·工程文档检索」段：`ls` 枚举章节 → 读前 50 行 KEYS 匹配 → 命中按 `[小节锚点]` 定点读小节 → 查 `_inject_cache.json` 防重复注入。

**章节作者责任**：保证前 50 行 KEYS 准确反映正文、锚点指向真实 H2 小节。AI 生成时自动保证；用户手动改正文后应同步更新 KEYS（不更新则段零匹配精度下降，不报错）。

## 九、模块章节模板（module_docs 共享文档）

> 与工程章节（"一"）结构一致（前 50 行四块），但元信息块字段差异反映"这是子模块的文档，不是工程的文档"。

### 职责划分（避免重复/错位）

> **判定标准**：模块**是否为独立 git 仓库**（git submodule / `repo` 子项目 / CMake FetchContent / monorepo 子目录 / vendor）是唯一分界，**与"是否工程核心"无关**。同一模块既可能是工程核心、又同时是独立仓库（如某任务控制中枢模块由 `repo` 管理为独立子仓库）——双重身份时两者都生成，互补不重复。

| 模块性质 | module_docs（仓库内部视角） | project_docs（工程视角） |
| --- | --- | --- |
| **独立仓库模块**（含工程核心同时也是独立仓库的双身份模块） | 生成：仓库内部架构 / API / 对外接口 / 使用约束，跨工程共享（按 url+branch key） | 生成该模块在工程 IPC 拓扑中的角色、与其他工程模块的关系、配置、启动（**不重复** module_docs 内部细节） |
| **工程内子目录模块**（非独立仓库） | 不生成 | 生成 project_docs 该模块章节 |
| **第三方预编译库**（thirdparty 无 git 仓库） | 不生成 | 只在 project_docs 依赖列表记录，不单开章节 |

**互补不重复原则**：独立仓库模块的"内部实现"写进 module_docs，"在本工程中的角色/拓扑位置"写进 project_docs。段零检索时两源合并排序（见 [dir_and_metadata.md](dir_and_metadata.md)「段零·工程文档检索」），读者从工程章节看角色定位、从模块章节看内部实现，不交叉。

### 模块章节与工程章节的差异

| 字段 | 工程章节 | 模块章节 |
| --- | --- | --- |
| 工程名 → **模块名** | myproject | module_a（子模块名） |
| 项目类型 → **模块类型** | git-root / repo-root | git-submodule / repo / cmake / monorepo / vendor / user |
| Git 地址 | 工程根 url | 子模块 url（如 `git@example.com:user/myproject/module_a.git`） |
| 依赖子模块 | 工程依赖的子模块列表 | **不填**（除非子模块本身依赖其他子模块，需在 _meta.json 嵌套记录） |
| 关联模块 → **对外接口** | 工程内其他模块名 | 子模块对外暴露的 API 类/方法 |
| 文件位置 | 工程内 src/ 路径 | 子模块内相对路径 |

### 模块章节完整示例（精简，元信息块+KEYS+简要说明，H2 模板用一行说明）

```markdown
# {子模块名 章节中文标题}

> **项目元信息**
> - 模块名：module_a
> - 模块类型：git-submodule（或 repo / cmake / monorepo / vendor / user）
> - Git 地址：git@example.com:user/myproject/module_a.git
> - 分支/提交：main @ a3f2b1c (2026-07-06 12:30)
> - 被工程引用（used_by）：myproject（git-root）, another_project（git-root）
> - 模板版本（v2 字段）：v2.0.0

> **KEYS**（术语→小节锚点）
> - 核心类：[模块A] ModuleAClass, [API] ModuleAClass.method1
> - 对外接口：[API] ModuleAClass.method1
> - 文件位置：src/module_a/feature_node.cpp

> **简要说明**（50~100 字）
> module_a 提供 feature_x 能力，对外暴露 ModuleAClass.method1 接口。被 myproject、another_project 调用。
```

> **H2 模板**（3 段，与工程章节模板同）：`## 1. 架构` / `## 2. API` / `## 3. 使用约束`（每段正文由 `## 1. 架构` H2 锚点定位，段零按锚点定点读，不灌全章）

### 模块章节特定字段

- **`used_by`**：列出引用此模块的工程，按 `_meta.json` 的 `project_type` 区分（`git-root` / `repo-root` / `user`）
- **段零来源标签**（段零命中模块章节时附，便于 AI 区分来源）：「来源：project:工程名」/「来源：module:module_a@branch@commit」
- **`<key>/` 一致性**：元信息块展示的 `<key>/` 从 `_meta.json` 读（不重算 URL+branch），确保与 `module_docs/<key>/` 目录名一致（避免 URL 字符串微变如大小写、尾部 `/`、协议前缀导致不同 key）。key 格式 `<url_basename_sanitized>_<sha256[:12]>`（含模块名前缀便于人眼辨认，详见 [dir_and_metadata.md](dir_and_metadata.md)「module_docs key 计算」）

## 十、质量审视检查清单（v2 新增，机械可验证）

> 本清单供 `/icode doc` 步骤 5「质量审视」子步骤调用。每次跑 doc 时，对现有每个章节执行本清单扫描，不达标项累计计数。**达标率 < 70% 的章节标记为"待升级"，触发全量重生成**（走确认门）。

### 10.1 硬性指标（grep 验证，无需 LLM）

| 检查项 | 验证方式 | 阈值 |
|--------|----------|------|
| 前 50 行四块齐全 | grep `项目元信息\|KEYS\|简要说明\|本章节目录` | 4 块均存在 |
| `template_version` 字段 | grep `template_version` in 元信息块 | = `v2.0.0` |
| 元信息块字段完整性 | grep `Git 地址\|分支/提交\|章节生成时间` | 必含字段齐全 |
| KEYS 双覆盖 | grep 客观词（类名/文件名）+ 主观词（H2 标题/加粗术语） | 双向均存在 |
| H2 锚点存在 | grep `^## \d+\.` | ≥ 2 个 H2 |
| 锚点链接有效 | grep `](#\d+` 与实际 H2 对应 | 链接不指向不存在锚点 |

### 10.2 必含元素检查（按章节类型）

#### overview 章节必含（6 项中至少 4 项达标即视为合格）

- [ ] # 0. 新手导览段（grep `## 0\..*新手` + 段内字数 ≥ 500）
- [ ] ASCII / mermaid 全栈图（grep ` ``` ` fenced code 数 ≥ 1 张大图）
- [ ] H2 一句话摘要列表（前 50 行内 grep `## \d+\..*\|.*` 每个目录项含 ≤ 50 字摘要）
- [ ] 常见修改/问题 file:line 锚点表（grep `file\.[ch]pp:\d+` 行数 ≥ 8）
- [ ] 角色化阅读路径表（grep 表格行数 ≥ 5）
- [ ] 故障现象索引表（grep 表格行数 ≥ 5）

#### workflow 业务流章节必含（4 项中至少 3 项达标即视为合格）

- [ ] # 0. 新手先看段（grep `## 0\..*新手`）
- [ ] 业务流程 ASCII 时序图（grep `sequenceDiagram\|->.*->\|时序`）
- [ ] 状态机入口枚举 + 关键状态转移表（grep 枚举 + 转移表行数 ≥ 6）
- [ ] 故障排查表（grep 表格行数 ≥ 5）

#### module 章节必含（4 项中至少 3 项达标即视为合格）

- [ ] # 0. 新手先看段（grep `## 0\..*新手`）
- [ ] 关键类/文件表 + 调用链图（grep 表格 + 框图）
- [ ] API 锚点速查表（grep `\.cpp:\d+\|\.hpp:\d+` 行数 ≥ 6）
- [ ] 典型场景示例（grep `### 示例\|### 场景` 段数 ≥ 3）

### 10.3 软性指标（需 LLM 阅读判断）

- **类比/比喻丰富度**：每千字至少 1 个类比（**用通用场景类比，不绑定具体业务**）
- **英文术语首次出现含中文备注**：抽查 5 个英文术语，每个都有 `**X** = **全名**（中文翻译）` 格式
- **链路数字说明完整度**：所有 topic 数量 / 线程数 / 子模块数等数字串后必含一句因果中文
- **跨版本差异点标注**：涉及代码演进时是否有"与上一版/上游工程差异"小节

### 10.4 升级触发逻辑

```
达标率 = 硬性指标通过数 / 硬性指标总数 * 0.4
       + 必含元素达标数 / 必含元素总数 * 0.4
       + 软性指标 LLM 评分 * 0.2

if 达标率 < 0.7:
    标记章节为"待升级" → 走确认门 → 全量重生成
elif 达标率 < 0.9:
    标记章节为"局部补全" → 增量补全缺失元素
else:
    跳过，无需重生成
```

**自举迁移机制**：`_meta.json.template_version < v2.0.0`（含缺失字段视为 v1）的章节**一律视为待升级**，强制触发全量重生成（不计算达标率），保证所有现有文档在首次升级时同步到新标准。

详细执行流程见 [steps/doc.md](../steps/doc.md) 步骤 5「质量审视与模板版本迁移」子步骤。
