# 工程文档检索与时效校验（按需加载）

> 从 [dir_and_metadata.md](dir_and_metadata.md) 拆出的两节：只在 init/log/plan/start 执行段零检索、
> 或需要判定历史工单/结论是否过时时读取。目录创建、ticket_id、索引写入、metadata 模板仍见原文件。

### 段零·工程文档检索（init/log/plan/start/fast 共用）

五入口启动时与历史检索并行；先读[doc_snapshot_contract.md](doc_snapshot_contract.md)全文核当前工作区、依赖绑定及正文SHA，再把合格候选排序。工程章节按当前原始分支隔离，模块正文按族键+精确snapshot_id读取，相关度不能覆盖版本不匹配。

**段零步骤速查**（执行前必读导航；入口摘要仅索引到此 7 步，**不得凭摘要执行**，须读下文各步骤全文）：
1. `resolve_project_id(cwd)` -> project_id + branch（步骤 1）
2. 读 `project_docs/<id>/<branch>/*.md` 前 50 行 KEYS 匹配 -> project 候选（步骤 2，含 legacy 回退）
3. 读 `_meta.json` `module_deps` -> `module_docs` 检索 -> module 候选（步骤 3，含 commit 校验）
4. **3.5 反查父项目**（子仓库时，步骤 3.5）
5. **3.6 关联工程检索**（读 00_overview「关联工程」字段 -> 跨工程读关联工程 00_overview 简要说明 + **源码路径定位** [project_path + manifest + 兜底] -> 关联工程候选，步骤 3.6）
6. 合并排序 top-N（步骤 4）
7. 注入（project/module/关联工程各策略 + stale 降级 + 防重复，步骤 5）

```text
1. cwd → resolve_project_id(cwd) → (project_id, project_type)  # git-root 或 repo-root
2. **DOC_DIR 分支过滤**（关键，多分支并存不交叉污染）：按 `resolve_project_id(cwd)` 算法算出 `<project_id>` + 当前分支 `<branch>`（sanitize 后），只读 `<DOC_DIR>=~/.claude/icode_data/project_docs/<project_id>/<branch_safe>/*.md`，**不交叉读其他分支子目录**（如当前在 `feature` 分支时不读 `main` 子目录，反之亦然）。理由：分支间代码差异大，跨分支工程文档互相借鉴必然失真。
   - 列出其他分支子目录（仅枚举用于 ℹ️ 提示，不读章节）：`ls ~/.claude/icode_data/project_docs/<project_id>/` 各子目录 → 输出"本工程已有 N 个分支的知识库：main (a3f2b1c)、feature (b8c3d4e)、dev (c1e2f3a) — 当前分支 `<branch>` 已建则用，未建则提示按需 `/icode doc`"
   - **legacy 兼容回退**（v1 升级到 v2 时的过渡关键，已存在但还没跑过 `/icode doc` 的工程）：若 `<DOC_DIR>` 不存在但 `<project_id>/` 目录下直接有 `00_overview.md` 等章节（v1 单级布局）→ 输出 ⚠️ 警告「该工程 project_docs 还在 v1 单级布局（旧版，未按分支分子目录）—— 本次段零检索**回退按 legacy 方式读**章节（不会判为零命中），但建议下次 `/icode doc` 时**自动迁移**到 `<id>/<branch>/` 多分支布局（迁移逻辑见 doc.md 步骤 5，保留旧章节 + 写到正确分支子目录）」→ 读 `<project_id>/00_overview.md` 元信息块的 `generation_commit` + `分支/提交` 字段 → 同上做分支 + 祖先 stale 校验（**强制分支校验**：从元信息块提取旧 branch 与当前 `git rev-parse --abbrev-ref HEAD` 比对，不一致→该工程所有章节 stale，按 stale 章节降级注入只注摘要 + 警告），KEYS 匹配 + 语义打分 → project 候选集 → **整段检索末尾汇总追加一行 ℹ️「本工程 v1→v2 迁移未完成，建议下个稳定时机跑 `/icode doc` 触发自动迁移，旧数据保留直到迁移成功」**（避免段零每次都重复警告）
   - ls `<DOC_DIR>/*.md`：
     ├─ 不存在（且无 legacy 回退命中）→ 段零零命中（输出一行 ℹ️ 提示"本工程当前分支 `<branch>` 尚未生成知识库，可运行 `/icode doc`，或切换到已建分支"，不阻塞，不写缓存）
     └─ 存在 → 逐章读前 50 行 → KEYS 匹配 + 简要说明语义打分 → project 候选集
   - **粗筛 0 命中早返回**：`project` 或 `module` 候选集任一为 0 条 → 不调 LLM 精读打分，直接合并入总候选集（节省 1-3K token，语义等价——粗筛 0 命中 LLM 精读也是 0）
3. 读当前分支<DOC_DIR>/_meta.json的module_deps，保留原始分支和真实path。
   对每个dep重新capture当前本地模块及声明依赖，用doc_snapshot.py resolve核dep.snapshot_id、当前source_id和模板。
   - status=match：读返回目录前50行KEYS再精读相关小节，source_id/snapshot_id写来源标签与注入缓存。
   - source_mismatch/modified/partial/template_mismatch/missing/ambiguous：不注正文，只给不含file:line的概览、原因和更新提示；不存在的正文不编造概览。
   - 旧dep无快照绑定或旧平铺库无内容指纹：按legacy_summary_only处理，不凭相同commit自动认作工作区匹配。
   - generated=false区分按需未生成/partial/失败；保留依赖事实，汇总待办，不能只看族目录存在就认为generated=true。
   快照路径和校验细则见doc_snapshot_contract.md；旧库和不同工程的版本继续保留，不由当前工程覆盖。
3.5 **反查父项目**（子仓库内工作时）：如果 cwd 在 git-root 模式 → 计算 `cwd_relative = realpath(cwd) 相对 realpath(.repo 所在目录)`（**注意：是相对 .repo 根，不是相对 git_root**——例如 cwd 在 `myproject/module_a/`、.repo 在 `myproject/.repo/` 时，cwd_relative = `module_a/`）→ 若 `.repo/manifest.xml` 存在且 `cwd_relative` 精确匹配或为某 `<project path>` 的子路径 → 该 manifest project 的"父 repo 根"=`.repo/` 所在目录；把父 project（repo-root）也纳入检索（读 `project_docs/<父 project_id>/<父 branch>/_meta.json` + 章节，**按父 repo 自身分支子目录读**，勿读错的分支），候选合并排序时一并参与打分（来源标签标「来源：project:父 project_id」）
3.6 **关联工程检索（跨工程参考，只读 overview）**：读当前00_overview「关联工程」字段；优先按声明的project_id与其明确来源分支定位overview。关联工程分支不要求与当前工程同名；没有明确分支时仅枚举其元信息作概览候选，不混入当前工程正文，来源标其真实project/branch/source_id。多个关联版本只给不含file:line的说明及版本标签，不擅选一个当当前代码；始终不注关联工程正文。以该关联工程自己的实际源码根/分支核stale，无法定位则明示未经当前源码验证。无命中提示未生成，不因当前分支名不同抹去关联工程线索。
   - **3.6 源码路径定位**（为下游 Read 实证关联工程源码提供线索；仅给工程根路径不注正文 file:line，防跨工程失真）：对每个命中的关联工程，读其 00_overview 元信息块的 `project_path` + `Git 地址`，三级定位本机源码根：
     (a) **project_path 校验**：`project_path` 缺失/空（旧 v1 章节无此字段）则跳过本项进 (b)；`test -d <关联 project_path>` 有效 -> 取为源码线索（同机有效；绝对路径可能因换机器/换 clone 位置失效，故必校验）
     (b) **manifest 匹配**（当前工程 repo-root 模式 + `.repo/manifest.xml` 存在时）：按关联工程 `Git 地址` 精确匹配 manifest `<project name="<git 地址>" path="<本地路径>" />`，命中取 `path`（相对 .repo 根，拼绝对路径）-- 同 repo 关联工程最可靠定位（manifest 是本机源码权威映射）
     (c) **兜底**：(a)(b) 都失败 -> 标「源码不在本机/路径失效」，仅保留简要说明作方向参考，附提示「关联工程 `<id>` 源码未在本机定位到，如需 Read 实证请用户提供路径」
     定位结果（源码根路径 or 「未定位」）随关联工程候选一并传递至步骤 5 注入
   - **需求→工程锚定校验（防"凭相似性猜错工程"，O-1）**：需求/症状文本指向某个工程时，候选工程定位**必须锚定校验**，禁止仅凭路径相似度自行选定。三步（在 init 建目录 / plan 确定工程 / 段零检索给到多个候选时执行）：
     1. **候选枚举**：从需求文本提取工程特征词（模块名/符号名/目录名，如 `<lidar>`、`<mission>`、`<controller>`），在**已知工程候选集**（步骤 2 本工程 project_docs / 步骤 3 module_deps / 3.5 反查父项目 / 3.6 关联工程 + 历史索引里同特征词工程）中筛出所有候选（**可多个**，不预设唯一）
     2. **仓内锚定**：对每个候选，用需求提到的**模块/符号/目录名做存在性校验**（`ls`/`glob`/`rg` 命中如 `<特征目录>/<子模块>/src/<关键符号>_controller.cpp` 这类具体路径或符号）；锚定失败即**排除**该候选
     3. **多候选裁决**：锚定后仍 >1 候选，**必须向用户确认**（列出候选路径 + 各自锚点），**禁止凭路径相似度自行选一个**——形近目录（父目录名相近、都以同一词收尾）是高频错选源，纯相似度判断必错
     锚定结论（唯一命中 / 多候选待用户裁决 / 无命中）写进产物，供下游 Read/Grep/修改锚定在正确工程上
4. 合并排序：project 候选 + module 候选 + 反查父项目章节 + 关联工程候选 + 历史检索段一/二候选 → 统一按相关度排序 → top-N（强相关≤2 + 弱相关≤1，总量≤3 条）
5. 对每个 top-N 项（注入策略按来源区分）：
   - **project 章节**：stale 判定（查 `_meta.json.stale_files` + 运行时 `git diff` 兜底，见「stale 检测」）-- stale → slice=`section:<file>#stale-summary`，按「stale 章节降级注入」只注简要说明+警告（不读正文小节）；非 stale → 检查 template_version（见下文「质量信号」）：v2 → slice=`section:<file>#<anchor>` 正常注入；v1 → slice=`section:<file>#v1-summary` 降级注入摘要+升级提示不注正文（v1 章节模板过旧，注入质量不可控）。按 KEYS [小节锚点] 定点读对应小节（不读全章，≤1K token/条）→ 注入思考输入「历史参考」节
   - **module章节**：仅步骤3精确快照与当前工作区、正文SHA、模板、审计范围均匹配时注正文；任何不匹配只概览/警告。ref_id含族键与snapshot_id，不复用仅文件名旧缓存。
   - **关联工程章节**：注 00_overview 的「简要说明」+ ⚠️ 跨工程警告 + **本地源码路径线索**（步骤 3.6 源码定位结果：`project_path` 或 manifest path，`test -d` 校验有效才给；源码不在本机则注明「未定位」+ 提示用户给路径）（**不注正文小节**，防跨工程代码分叉失真），slice=`section:<file>#cross-project-summary`；stale 则按「stale 章节降级注入」只注摘要+警告+源码路径线索
   - 查 _inject_cache.json → 已注入的同 slice 跳过；否则注入并写缓存
   - 命中附来源标签：「来源：project:myproject」或「来源：module:module_a@main@a3f2b1c」
```

**防重复注入**：与历史源共用 `_inject_cache.json`（见上节），跨命令不重复注入同一章节。

**stale 检测**（注入前被动校验，控 token；与历史工单 stale「跳过注入」对齐防误导目标）：读 `00_overview.md` 元信息块的 `generation_commit` + 工程 `_meta.json.branch` + submodule commits，与 `git rev-parse --abbrev-ref HEAD` + `git rev-parse --short HEAD` + `git submodule status` 对比；**先做分支 + 祖先双校验**（防跨分支误用 git diff + 防分支切换文档复用误导）：

- **分支校验（最优先，主防线）**：
  - 工程 `_meta.json.branch` == `git rev-parse --abbrev-ref HEAD`（含 detachable 后 `HEAD` 字面量相等）→ 继续祖先校验
  - **分支不一致**（如 `_meta.json.branch = main` 但 cwd HEAD 在 `feature`）→ **该工程所有章节直接判定 stale**（不只看 diff 命中，跨分支文档整体不可信），slice=`section:<file>#stale-branch-mismatch`，按「stale 章节降级注入」只注简要说明 + 警告行「⚠️ 文档基于分支 `<branch>`，当前 `<current-branch>`，跨分支差异可能很大，重跑 `/icode doc` 重生成」
  - 工程本身非 git 仓库（`_meta.json.branch = null`）→ 跳过分支校验，走祖先 + diff 路径
- **祖先校验**（分支一致后）：
  - `git merge-base --is-ancestor <prev> HEAD` 退出 0（正常前向演进）→ HEAD 变更且 `git diff <commit>..HEAD --name-only` 命中该章 KEYS「文件位置」**或命中章节正文涉及的目录前缀**（应对 KEYS 文件位置不完整 / 新增文件 / 重构改路径导致漏判的假阴性）→ 判定该章 stale
  - 退出 1（HEAD 是 prev 的祖先/分叉 / prev 与 HEAD 在不同分支，如工程切了分支或重置）→ **`git diff` 不可信**（merge-base 可能很远导致 diff 失真/巨变），**直接判定该章 stale**（不留余地跨分支增量判定，避免误把"几乎全章变更"当增量）
  - 退出 128（prev 不可达，如 GC/换库）→ 同上直接判定 stale

**工作区补充校验**：先比较当前capture的source_id/声明依赖与章节基线、实际正文SHA。HEAD相同也可能有dirty变化；旧无内容指纹记录只能概览，不按HEAD相同判新鲜。工程章节按实际输入清单差异和关联调用核验，部分刷新保留各章基线；详情见doc_snapshot_contract.md。

**两源合一**：先查工程 `_meta.json.stale_files`（主动扫描持久化结果，见下文「project_docs 主动 stale 扫描」）快速识别 stale 章节（降级注入摘要不注正文，见下文「stale 章节降级注入」），再运行时 `git diff` 兜底（`_meta.json` 可能比最新 HEAD 旧）。判定宁可放宽（假阳性仅触发降级，假阴性才会误导）。

**stale 章节降级注入（不注正文，防误导）**：stale 章节**绝不注入正文小节**（避免过时 file:line / IPC 契约 / 调用链误导新工作流），改为**只注入「简要说明」（50~100 字概览，不含 file:line，误导风险低）+ 警告行**「⚠️ 章节 `<文件>` 基于旧 commit `<generation_commit>`，当前 HEAD `<HEAD>`，已过时仅注入摘要，建议重跑 `/icode doc` 更新」。与历史工单 stale「跳过注入」同等防误导强度--过时的正文小节不进新工作流思考输入；降级保留「简要说明」仅给新工作流方向性参考，不构成事实依据（配合下文「不盲信约束」）。 注入缓存 slice 记为 `section:<file>#stale-summary`（与正文小节 `section:<file>#<anchor>` 区分，避免去重混淆；章节重跑变新鲜后注入正文小节不被误跳过）。

**不盲信约束（段零注入的工程/模块文档仅作参考）**：段零注入的 project_docs / module_docs 章节是 `/icode doc` 生成时的**快照**，可能因工程迭代而过时（即使未标 stale 也只是「未检测到过时」，非「已验证最新」）。下游 init/plan/start/fast/log 步骤**不得将注入的文档描述当作事实直接采信**：凡涉及代码行为 / 位置 / 接口契约 / 调用链 / 错误码的断言，**必须用 Read/Grep 实证当前代码**后再纳入决策（与 [anti_laziness.md](anti_laziness.md)「段零文档不盲信」条 + [01_plan.md](../steps/01_plan.md) 计划断言实证一致）。文档只作「设计意图与模块关系」的启发，不作「代码事实」的依据。

**质量信号（v2 新增，模板版本驱动的注入优先级）**：章节 `_meta.json.template_version` 与 [doc_template.md](../references/doc_template.md) 顶部 `SCHEMA_VERSION` 比对：

| 章节状态 | 注入策略 | 注入缓存 slice |
|----------|---------|---------------|
| `template_version == SCHEMA_VERSION`（v2 章节，如 v2.0.0）**且非 stale** | 正常注入（既新鲜又高质量） | `section:<file>#<anchor>` |
| `template_version == SCHEMA_VERSION`（v2 章节）**但 stale** | 降级注入（stale 优先于模板版本——避免注入过时内容） | `section:<file>#stale-summary` |
| `template_version < SCHEMA_VERSION`（v1 章节，含缺失字段）**且非 stale** | **降级注入**：只注「简要说明」+ 警告「⚠️ 章节基于旧模板版本 `<旧版本>`，当前 SCHEMA_VERSION `<SCHEMA_VERSION>`，建议重跑 `/icode doc` 触发模板迁移」+ 建议运行 `/icode doc` 升级；**不注正文小节**（旧模板未含 14 项必含元素 + 业务流独立章节 + 英文中文备注 + 链路中文说明，注入质量不可控） | `section:<file>#v1-summary` |
| v1 章节 + stale | 降级注入（取 stale-summary，stale 优先级 > 模板版本） | `section:<file>#stale-summary` |
| 缺失 template_version 字段 | 视为 v1，走上一档降级路径 | 同上 |

**优先级排序**：v2 章节在候选排序中权重 > v1 章节（同相关度下 v2 优先），保证下游尽量拿到高质量上下文。注入缓存 slice 区分：正常注入 `section:<file>#<anchor>` + 升级后重注 `section:<file>#<anchor>`（v1→v2 升级后同一锚点覆盖旧缓存）、降级注入 `section:<file>#v1-summary` / `section:<file>#stale-summary`。**stale 优先级 > 模板版本**（避免给下游注入过时正文，哪怕章节是 v2 高质量版本）。去重不混淆：同一章节 v1→v2 升级后，下游重新拿到 `section:<file>#<anchor>`（不被旧 `v1-summary` 缓存跳过，因为 slice 名不同）。

**双视角使用说明（v2 新增）**：v2 章节**同时服务两类读者**，段零注入策略因下游角色不同而不同：

- **init/plan/start/fast 步骤**：偏重 AI 视角的 14 项必含元素（H2 摘要 + 锚点表 + API 速查表 + 状态转移表），用于让 AI 在写代码/做计划时直接 grep 定位
- **log 步骤**：偏重人/AI 双视角的故障现象索引表 + 故障排查表 + 状态转移表，用于日志根因分析时直接对照
- **readme 步骤**：偏重人视角的 00_overview 6 项必含元素（新手导览 + 全栈图 + 角色路径），用于生成交付报告时引用

下游步骤消费注入上下文时，应先识别自身角色，按上表选择最相关的必含元素。详见 [doc_template.md](../references/doc_template.md)「〇、模板版本与双视角设计」+「14 项必含元素」段。

**附加输出**（段零末尾汇总）：
- ⚠️ **缺失模块警告**（若步骤 3 收集到「缺失模块」列表）：输出「工程引用了以下 module_docs 但不存在：\`{dep1.name}\`(\`{dep1.key}\`), \`{dep2.name}\`(\`{dep2.key}\`)... — 请检查是否已 /icode doc 生成，或工程 _meta.json.module_deps 是否写错 key」
- ⚠️ **未生成模块警告**（若工程 `_meta.json.unresolved_modules` 非空）：输出「工程有 N 个未生成模块（拉取失败）：\`{name1}\`(\`{reason1}\`), \`{name2}\`(\`{reason2}\`)... — 子仓库代码本地化后重跑 /icode doc 时自动恢复」
- ℹ️ **按需未生成模块提示**（若步骤 3 收集到 `generated == false` 的模块）：输出「工程有 N 个模块未生成 module_docs（按需未生成，上次 /icode doc 指定了其他模块聚焦）：\`{name1}\`, \`{name2}\`... - 可 \`/icode doc <name>\` 按需生成」

**工程隔离**：工程正文只查当前project与原始分支；模块依赖只查父工程精确快照绑定。同族仅当source_id/正文SHA/模板与审计范围匹配才复用，多提交与dirty版本可以并存。**关联工程**（姊妹/同族）通过 00_overview 元信息块「关联工程」字段显式声明，段零只检索其 00_overview「简要说明」+ ⚠️ 跨工程警告 + **源码路径线索**（步骤 3.6 源码路径定位：project_path/manifest/兜底）作为参考候选（**不注正文小节**，防跨工程代码分叉失真），不自动跨库注入全章。

### 过时校验（防注入过时信息）

索引存的是工单**当时的摘要**，但工程会迭代，老工单的 ADR/需求可能已被后续工单推翻。命中过时工单注入会误导。

> **⚠️ Git 操作安全白名单（强制，违反即不合规）**：本段所有 git 调用必须**只读**，仅允许：`git rev-parse HEAD` / `git rev-parse --abbrev-ref HEAD` / `git rev-parse --git-dir` / `git merge-base --is-ancestor <A> <B>`（仅取退出码 0/1）/ `git status --porcelain` / `git log --oneline -1` / `git log --oneline --since=<N>.days -- <路径>`（只读；重复模式确定性佐证统计模块提交频次用，`--since`+路径过滤限定范围防大仓慢扫；**相对日期须用 `<N>.days` 格式**——`<N>d`/中文单位在此类 git 静默失效为不过滤，禁用；见 [thinking_detail.md](thinking_detail.md)「重复模式检测」段第 3 步） / `git log --oneline <commit>..HEAD -- <file>`（只读；结论级时效校验第 5 项列出某文件在 `created_commit` 之后的演进 commit，见「过时校验」段第 5 步；`<commit>..HEAD` 限定范围防大仓慢扫） / `git log -S <string> -- <file>`（只读；pickaxe 值演进追踪——按"某行关键值/哨兵值被增删"定位演进 commit，结论级时效校验第 5 步用） / `git cat-file -e <sha>` / `git show <hash>:<path>`（只读；现场运行版本基线门按现场 Hash 读取历史源码用，见「metadata 三基线字段」段；仅 blob 只读展示，不改工作树/索引） / `git diff <commit>..HEAD -- <path>`（只读；现场 Hash→当前 HEAD 相关文件演进对照用，`<commit>..HEAD`+路径限定范围防大仓慢扫） / `git blame <commit> -- <path>`（只读；对指定历史版本做行归因，设计意图证据采集与版本演进对照用） / `git worktree list --porcelain`（只读；resolve_project_id F1 worktree 归一化用，见「project_id 与 branch 语义」段）。**禁止** `checkout`/`switch`/`reset`/`stash`/`clean`/`commit`/`add`/`rm`/`rebase`/`merge`/`cherry-pick`/`push`/`fetch`/`pull`/`branch -D`/`tag -d` 等一切写操作与网络操作。stale 检测**只读工作树现状，绝不改工作树/索引/提交**--"checkout 变化时重评"指检测到**用户外部**改了 HEAD 后只读重评，**绝不由技能主动 checkout**。`-C {project_path}` 前缀用于跨工程工单切换目录执行只读命令，必需且允许；全部本地完成，离线可用。

**DOC工作区身份捕获补充白名单**：允许固定只读参数`rev-parse --verify HEAD`、`rev-parse --show-toplevel`、`symbolic-ref --short HEAD`、`ls-files --stage -z`、`ls-files --others --exclude-standard -z`；工具加`--no-optional-locks -c core.fsmonitor=false -C <实际根>`，不写索引、不运行用户fsmonitor。此补充不允许网络或写类Git操作。

**校验方法**（对 top-N 命中工单，注入前逐条；`H = git -C {project_path} rev-parse HEAD`（该工单工程的当前 HEAD，每候选取一次；非 git 仓库/失败→`null` 走纯锚点兜底））：
1. **项目路径校验**：`test -d {project_path}` 失败→置 `stale=true`+`stale_reason=path_gone`+`stale_checked_commit=H`，**跳过注入**（即使 hit_count 高也不注入），避免对已删除工程的引用注入。**worktree 场景（预期行为，非故障）**：`project_path` 指向 worktree 路径时，工单回流 `git worktree remove` 后 `test -d` 失败 → `path_gone` 属预期（工单已交付，索引留档即可，别当工程被删排查）；**复活路径** = 重新 `git worktree add` 同分支 → `project_path` 恢复存在 → 自动重入「可复活规则」再走锚点校验。**归档工单（archived 活跃态，worktree 工单）**：`archive_path` 非 null 时**跳过本步 project_path 校验**，改查 `test -d {archive_path}`（如 `~/.claude/icode_data/worktree_archive/<project_id>/<ticket_id>/`）——有效→该工单为 archived 活跃历史工单，**不标 stale**，产物源=archive_path（读 `01_plan.md` ADR/风险或 `log_analysis.md` 根因/结论），**跳过第 2/3/4/5 步**（无代码可校验，注入走历史参考语义——作启发、非当前代码事实，须 Grep/Read 实证，见「不盲信约束」），命中**正常续期 + 按 verdict 分流**（归档工单 `disproved`/`superseded` **降级**：`project_path` 失效无法 Grep/Read 当前代码验证证伪前提、`verdict_review_needed` 主动检测亦不可执行 → 改注 `verdict_reason`+`correct_direction` 作历史避坑提示 + ⚠️ 标注『基于已交付历史代码，未经当前代码实证，须用户判断前提是否仍适用』），待遇与主仓工单一致；archive_path 失效（归档也被删）→ 才置 `stale=true`+`stale_reason=path_gone`+跳过注入（真病态）。归档见 [worktree_isolation.md](worktree_isolation.md)「产物归档」。**备份工单（backup 活跃态，`/icode bak` 产物）**：`backup_path` 非 null 且 `test -d {backup_path}` 有效（如 `~/.claude/icode_data/project_backup/<project_id>/<快照>/.icode_output_N/`）的工单，`project_path` 失效（工程被删）时**重置 `stale=false`+`stale_reason=null`+`stale_checked_commit=null`**（backup 活跃态——即使此前标过 stale，备份是唯一可读快照，旧判据作废），产物源=backup_path（读完整工单产物：`01_plan.md` ADR/风险、`log_analysis.md` 根因/结论、`06_audit.md` 终审等），**跳过第 2/3/4/5 步**（历史快照，无当前代码可校验，注入走历史参考语义——作启发、非当前代码事实，须 Grep/Read 实证，见「不盲信约束」），命中**正常续期 + 按 verdict 分流**（`disproved`/`superseded` 降级注避坑提示 + ⚠️『基于备份历史代码，未经当前代码实证，须用户判断前提是否仍适用』），待遇与主仓工单一致；**工程优先**：`test -d {project_path}` 有效（工程恢复/重克隆）永远优先走工程，备份仅兜底。backup_path 失效（备份也被删）→ 才置 `stale=true`+`stale_reason=path_gone`+跳过注入（真病态）。**与归档工单并行**：`archive_path`（worktree 归档）与 `backup_path`（手动备份）任一有效即活跃态，互不排斥。备份命令见 [steps/bak.md](../steps/bak.md)
2. **commit 上下文校验**（`created_commit` 非 null 时；为 null 跳过本步进第3步纯锚点兜底）：
   - `H == created_commit`→工作树恰为工单出生提交，完全匹配，**not stale，高置信注入**（快路径，跳过第3步锚点校验）
   - `git merge-base --is-ancestor {created_commit} {H}`：退出 0→正常前向演进，进第3步；退出 1（H 是 `created_commit` 的祖先/分叉）→置 `stale=true`+`stale_reason=checkout_mismatch`+`stale_checked_commit=H`，**软 stale 跳过注入**（checkout 变化时可复活，见「stale 字段·可复活」）；退出 128（`created_commit` 不可达，如 GC/换库）→视同 `created_commit=null` 进第3步纯锚点兜底
3. **代码锚点校验**（廉价，现有）：读该工单 `01_plan.md` 的 ADR 章节提取**代码锚点**（如"calc.c mul_overflows"），Grep 该工单工程（`{project_path}`）确认是否仍存在；不存在（删除/重构/重命名）→置 `stale=true`+`stale_reason=anchor_gone`+`stale_checked_commit=H`，**跳过注入**
4. **语义偏离校验**（新，贵层，仅对已决定注入的 top-N≤3 条）：Read 该工单工程（`{project_path}`）锚点处当前代码，按 **ADR 偏离 checklist** 判 ADR 前提是否仍成立：①ADR 涉及的函数/类签名是否仍匹配 ②返回值/边界值/错误码是否仍如 ADR 所述 ③ADR 推理依赖的调用关系/数据结构是否仍存在。任一不成立→置 `stale=true`+`stale_reason=semantic_deviation`+`stale_checked_commit=H`，**跳过注入**（专抓"锚点符号在但语义已变"，锚点校验抓不到）
5. **结论级时效校验**（新，仅当"当前代码行为与某条历史工单结论冲突"时触发；抓"锚点在且语义看似在、但结论已被 `created_commit` 之后的 commit **有意推翻/演进**"）：前 4 项管"工单引用的代码还在不在/变没变"（**代码事实**），本项管"结论针对的行为是否被后续 commit 有意推翻"（**结论语义**）——**锚点存在 ≠ 结论仍成立**（实战案例：历史工单门禁结论的锚点代码一直在、ADR 文本原样可读，但结论针对的行为已被后续 commit 有意演进为兼容哨兵设计；若以旧结论为基准去"证伪"最新代码，方向颠倒、会提出回归既有修复的错误方案）。**触发条件**（避免全量开销）：仅当基线检查/ADR 评估发现"当前代码行为与某条历史工单结论冲突"时，对该工单执行；无冲突走原有注入路径。**步骤**：
   - ① 从该工单产物（`log_analysis.md` 根因/证据 或 `01_plan.md` ADR）抽取其"核心结论涉及的代码行"（根因/证据里的 file:line）；
   - ② 对该行执行 `git -C {project_path} log --oneline <created_commit>..HEAD -- <file>` 或 `git -C {project_path} log -S <哨兵/关键值> -- <file>`（只读，白名单内），列出该工单 `created_commit` 之后的所有演进 commit；
   - ③ 逐条 Read commit message，判"缺陷回归"（fix/bugfix/regression/回退）还是"有意设计演进"（feat/preserve/sentinel/compat/兼容/哨兵/防…误判/避免…）；
   - ④ 判定为"有意设计演进" → 该工单该条结论**降级为"历史快照"**，注入时附 ⚠️ 警告「该结论已被 `<hash>` 有意演进，当前行为以最新代码为准」，**不再作为根因判定/ADR 决策基准**；判定为"缺陷回归"或"无关演进" → 结论仍有效，正常注入。
   - **归档/备份工单豁免**：第 1 步 `archive_path`/`backup_path` 有效走读档的工单（`project_path` 失效，无当前代码可查演进史）跳过本步，注入按原有历史参考语义（作启发 + ⚠️ 标注"基于历史代码，未经当前代码实证"，见第 1 步归档/备份分支）。
   - **与第 4 项分工**：第 4 项管"当前代码现状是否符合结论描述"（抓"锚点在但语义已变"→ 置 stale 跳过）；本项管"演进史是否有意推翻结论"（抓"锚点在且语义看似在、但结论已被 commit 有意演进"→ **不置 stale**，降级为历史快照 + 警告继续注入，因代码本身没过时、结论仍可作历史参考）。两者互补，覆盖"过时"的两个维度。

通过全部校验（归档工单 = 第1步 `archive_path` 有效即通过，跳过 2/3/4/5 步）→工单有效，**按 verdict 分流注入**（详见 SKILL.md「历史检索复用·注入分流」段）+ 续期（`stale_checked_commit=H` 在评估时已更新）。**第 5 步结论级时效校验命中"有意设计演进"的工单**：不置 stale、正常续期，但注入时附 ⚠️ 警告「该结论已被 `<hash>` 有意演进，当前行为以最新代码为准」（见上第 5 步④）：

- `verdict="verified"`/`"unknown"`（含所有旧工单）：正常注入 ADR+风险章节（现状不变）；**`unknown` 强制走 unknown 强化层**（扩读 `00_init.md` 末轮对话摘要 + 对抗质疑三问 + ⚠️未验证警告，见 [thinking_detail.md](thinking_detail.md)「历史参考小节」）--这是旧工单（无 verdict）防误导的主防线，不依赖标注
- `verdict="disproved"`（`verdict_review_needed=false`）：**反转注入避坑 + 证伪前提断言**--不注 ADR，改注 `verdict_reason`（作可验证断言）+ `correct_direction`，标 ⛔ 避坑；**强制新需求 Grep/Read 验证证伪前提是否仍成立**（如"某接口语义是重置"，须 Read 当前实现确认是否仍重置）：仍成立则确实避坑；已失效则方向或可重新考虑，提示 `/icode status --verdict` 标复活（unknown/verified）；`correct_direction` 缺失时降级注 ADR + ⛔ 警告（提示补标）
- `verdict="disproved"`/`"superseded"`（`verdict_review_needed=true`，证伪前提依赖已变化）：**降级对抗质疑**--不硬反转，走 unknown 强化层（扩读末轮+对抗质疑三问）+ 注"曾证伪 + 证伪前提 + 依赖从旧 commit 到新 commit 已变化"提示，让新需求重新评估证伪前提是否仍成立；前提失效则该方向或可重新考虑，提示标复活。**防漏过后来又可行的方向**
- `verdict="superseded"`：注替代指针 `superseded_by` + `correct_direction` + 替代工单摘要，标 🔁 已替代

> **stale 与 verdict 正交**：`stale=true` 优先跳过注入（技术过时，锚点都没了，连避坑都不用）；`stale=false` 才按 verdict 分流。verdict 抓"方向证伪"（锚点在但方向错），stale 抓"技术过时"（锚点没了），互补不重叠。

> **verdict_review_needed 被动检测**（检索命中 disproved/superseded 注入前）：若 `verdict_premise_deps` 非空，逐 dep 取 `git -C {dep.path} rev-parse HEAD` 比对 dep.commit；变化时经 `index-update --ticket-id <id> --set-json '{"verdict_review_needed":true}'` 写回，未变则同入口写 false。本次分流语义不变。

