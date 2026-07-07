# icode doc 章节模板与自适应规则

> `/icode doc` 生成章节的模板规范，被 [steps/doc.md](../steps/doc.md) 引用。核心哲学见 [references/dir_and_metadata.md](dir_and_metadata.md)「project_docs 工程文档库」段：**零配置/零状态/零索引文件，章节前 50 行自带身份证**。

## 一、章节前 50 行四块结构（强制）

每个章节前 50 行含四块，正文从 `---` 后开始。前 50 行是段零检索唯一载体（只读前 50 行判断相关性），必须自包含；短章节不足 50 行时前 50 行即全文，无副作用。

```markdown
# {章节中文标题}

> **项目元信息**
> - 工程名：myproject
> - Git 地址：git@example.com:user/myproject.git
> - 分支/提交：main @ a3f2b1c (2026-07-06 12:30)
> - 子模块：thirdparty/lib_algo → ...git @ c8d2f1a；thirdparty/lib_nav → ...git @ b9e4d3c
> - 产品线/型号：{产品代号，无则填"未识别"}
> - 章节归属模块：module_a
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

**四块职责**：项目元信息块（身份证，stale/冲突/隔离用）、KEYS（检索词+小节锚点）、简要说明（语义判断）、本章节目录（定点读小节不灌全章）。

**元信息块字段取值**见 [steps/doc.md](../steps/doc.md)「元信息块字段取值约定」段，此处不重复。

**KEYS 双覆盖**：同时含客观词（类名/文件/topic）+ 主观词（H2 标题/加粗术语），按符号搜与按概念搜都能命中。

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

**AI 自主 grep**：表中"类别"是提示，AI 执行时根据工程实际技术栈选择具体 grep 模式，**不硬编码具体框架/库名**。未命中任何特征 → 只生成固定章节（00/10/90/99），不强行凑。

## 六、`99_code_facts_audit.md` 生成策略

**永远生成**（不因工程大跳过），增量+并行控成本：

- **首次**：并行子代理（general-purpose，禁用 Explore），每个验证一批 file:line 引用。从全库正文提取引用（grep `\w+\.\w+:\d+`），子代理实读返回 `{ref, exists, matches, issue}`，汇总失效项 + 修正建议
- **增量**：读 `00_overview.md` 的 generation_commit，`git diff <commit>..HEAD --name-only` 拿变更文件，只重验引用了变更文件的 file:line
- **局部失败**：单个子代理失败 → 该批标 `[未验证-子代理失败]`，不拖垮全局，末尾标红可针对性重跑

## 七、KEYS 与简要说明生成

**AI 自动生成**（首次+更新），用户可手动覆盖（直接改 md，下次段零立即生效）：

- **核心类**：grep `class\s+\w+`/`struct\s+\w+` 取前 10，锚点 = 所在 H2 小节
- **关键技术**：代码客观词（从 fenced code 块和 API 名提取）+ 叙事主观词（H2 标题/加粗术语）
- **文件位置**：正文 `.cpp`/`.h`/`.py` 路径聚合
- **关联模块**：正文 `[xx章](xx.md)` 链接 + 提及的其他模块
- **关键 IPC topic**：grep 工程既有 IPC 发布/订阅/服务端 API 的模板参数
- **简要说明**：首段+尾段提炼，50~100 字，讲清"解决什么+关键设计+模块关系"，不复述标题

## 八、与段零检索的契约

段零检索流程见 [references/dir_and_metadata.md](dir_and_metadata.md)「段零·工程文档检索」段：`ls` 枚举章节 → 读前 50 行 KEYS 匹配 → 命中按 `[小节锚点]` 定点读小节 → 查 `_inject_cache.json` 防重复注入。

**章节作者责任**：保证前 50 行 KEYS 准确反映正文、锚点指向真实 H2 小节。AI 生成时自动保证；用户手动改正文后应同步更新 KEYS（不更新则段零匹配精度下降，不报错）。
