# 工单存储细则（按需加载）

> **本文件由 [dir_and_metadata.md](dir_and_metadata.md) 外提的存储细则**，只在下列动作发生时才需要读取；目录创建/复用、索引写入、metadata 字段语义等核心契约仍在 [dir_and_metadata.md](dir_and_metadata.md)。
>
> | 何时读 | 段落 |
> |--------|------|
> | 执行 `/icode bak`、索引 LRU 淘汰、归档保留判定 | 「索引淘汰规则」 |
> | 写检索注入结果、排查重复注入 | 「注入缓存机制」 |
> | `/icode doc` 维护工程文档库 | 「project_docs 工程文档库」 |
> | 工程接入 / 模块族快照 | 「module_docs 工程模块库」 |

## 索引淘汰规则（LRU + 命中续期 + 永久保留）

**目的**：index.json 是检索缓存非档案，随工单增长会膨胀。靠 LRU 淘汰失去复用价值的老工单，保留高价值工单。淘汰只删索引条目，**不删各工程 `.icode_output/` 产物**（产物保留，索引只是指针）。

**触发时机**：工作流调用方在写索引前完成主动 stale 扫描并用 `index-update` 回写；每次受控写索引（首次写入/条目更新/命中续期）时，确定性内核执行僵尸超时降级、排序和 LRU 收缩。

**排序规则**（写入时重排 tickets 数组）：按复合键 `(verdict_priority, hit_count, last_used_at)` 降序。`verdict_priority`：`verified`=3（最高）> `unknown`=2 > `superseded`=1 > `disproved`=0（最低，沉底）。旧工单无 verdict 视为 unknown。

**淘汰规则**：
1. **目标容量 200 条**：tickets 数组超 200 时触发淘汰；若剩余条目全部受永久保留/未完成态保护，允许暂时超过 200，待出现可淘汰条目后再收缩，不为凑硬上限破坏保留契约
2. **永久保留**：`hit_count >= 20` **且 `verdict != "disproved"`** 的工单永久不淘汰（被复用≥20 次的高价值工单）。**例外**：`verdict="disproved"` 的工单即使 `hit_count >= 20` 也不永久保留--被证伪的方案不应作为高价值正面参考永久保留（仍可被反转注入避坑，但走正常淘汰流）
3. **未完成态保留**：`status` 为 `init_in_progress`/`log_in_progress`/`review_in_progress`/`deepcheck_in_progress`/`code_in_progress` 的工单**默认**不淘汰（流程未结束）——**但有超时降级例外**（见规则 5）。`log_done` 已完成日志分析，属可淘汰完成态。
4. **LRU 淘汰**：超目标容量时，在**可淘汰集**中淘汰 `last_used_at` 最老的，直到条目数 ≤ 200 或已无可淘汰条目。**可淘汰集** = `hit_count < 20` 且满足下列**任一**：① `stale=false` 且 `status = completed/code_done/review_done/deepcheck_done/plan_done/plan_finalized/log_done`（正常已完成/推进态）；② `stale=true` 且 `stale_reason = timeout`（规则 5 超时降级的僵尸未完成态，status 仍 in_progress 但已降级可淘汰）。**`stale=true` 且 `stale_reason != timeout` 的软 stale 工单不在可淘汰集**（保留待 checkout 变化后复活重评）
5. **僵尸未完成态超时降级**：未完成态工单（init/log/review/deepcheck/code in_progress）若 `last_used_at` 距当前**超过 30 天**无更新，视为"建了就扔"的僵尸工单——**不删 status，改置 `stale=true`+`stale_reason=timeout`**（复用 stale 机制，不污染 status 语义），降级后该条不再受规则 3 的未完成态保护，**纳入规则 4 可淘汰集②**（status 仍 in_progress，由可淘汰集②的 `stale_reason=timeout` 判定可淘汰）。**触发时机**：每次写索引触发淘汰扫描时，顺带检查所有未完成态工单是否超时。区分"正在用"（30 天内有更新）与"建了就扔"（超时）。**timeout 为硬 stale**：复活条件 `stale_reason != timeout` 排除它，超时降级不复活、直接走淘汰
6. **淘汰不报错**：静默移除，用户无感

**主动 stale 扫描**（防过时信息堆积，规则 5 之外的第二道清理）：

- **现状漏洞**：原设计 stale 校验只在"检索命中准备注入前"才做——没被命中的老条目永远 stale=false，永远不会被这个机制清理，stale 清理形同虚设。
- **新增主动扫描**：每次工作流写索引前，**调用方**对 `last_used_at` 最旧的 K 条（**K=min(30, 当前条目数)**）做一次代码锚点 Grep 校验（方法同「过时校验」段第3步），再通过 `index-update` 原子写回结果。`maintain_index_tickets()` 是无外部 I/O 的确定性内核，只负责超时降级、排序与 LRU，不会自行 Grep。锚点失效→置 `stale=true`+`stale_reason=anchor_gone`+`stale_checked_commit=H`（H=该工单 `git -C {project_path} rev-parse HEAD`）。
- **归档/备份豁免（防误标 anchor_gone）**：主动扫描对每条先做 `test -d {project_path}`——失效但 `backup_path` 或 `archive_path` 有效（活跃态）→ **跳过锚点校验**（历史快照无当前代码可 grep，不因 grep 失败误标 stale）；两源都失效才按锚点校验/`path_gone` 标 stale。对齐「过时校验」段第1步的并行判定。
- **控 token**：只扫最旧的 K 条（最可能过时），不扫全量；Grep 单条锚点 <1K token。
- stale 工单仍受排序影响，但**不注入不续期**；`timeout` 硬 stale 可按规则 4 淘汰，其他软 stale 保留等待 checkout 变化后复活重评。

## 注入缓存机制（防重复注入，两源共用）

> 本机制解决「同一开发链路内重复注入同一来源的同一信息切片」问题。**历史检索复用（现有）**和**段零工程文档检索（icode doc 新增）**共用此缓存。定义在此处一处，五入口（init/log/plan/start/fast）统一引用。

### 设计动机

五入口（init/log/plan/start/fast）启动时都会触发检索注入，但一次开发链路常跨多命令（如 init→start 复用同目录），同一历史工单/文档章节会被多次命中，导致 **token 浪费**（重复注入同一切片）+ **hit_count 扭曲**（同目录多次续期虚高，扭曲 `hit_count >= 20` 永久保留判定）。

### 缓存文件

`{ICODE_OUT_DIR}/_inject_cache.json`（工单目录内，与 `.ico_metadata.json` 平级，跟随工单生命周期）：

```json
{
  "ticket_id": "demo-5",
  "injections": [
    {
      "source": "history",
      "ref_id": "demo-3",
      "slice": "requirement_points",
      "by_cmd": "init",
      "at": "2026-07-06T15:30:00Z"
    },
    {
      "source": "project_doc",
      "ref_id": "07_messages_and_ipc.md",
      "slice": "section:07_messages_and_ipc.md",
      "by_cmd": "plan",
      "at": "2026-07-06T15:35:00Z"
    }
  ]
}
```

### slice 取值词表（两源统一）

| source | slice | 注入命令 | 含义 |
|--------|-------|---------|------|
| `history` | `requirement_points` | init | 需求要点清单 |
| `history` | `adr_risks` | plan / start / fast | ADR + 风险评估章节 |
| `history` | `root_cause_evidence` | log | 根因结论 + 决定性证据 |
| `history` | `verdict_lesson` | init/plan/start/fast/log | disproved/superseded 工单反转注入的避坑结论（`verdict_reason`+`correct_direction`，见「检索命中续期·过时校验」段 verdict 分流注入） |
| `project_doc` | `section:<file>` | init/log/plan/start/fast | 工程文档章节（可细化到小节锚点 `section:<file>#<anchor>`） |
| `project_doc` | `section:<file>#stale-summary` | init/log/plan/start/fast | stale 章节降级注入的简要说明（不读正文小节，见「stale 章节降级注入」） |

### 去重规则（核心）

**先校验版本，再查缓存**——历史源仍按`(source, ref_id, slice)`；DOC源的ref_id必须绑定族键+snapshot_id，或工程章节source_id+正文SHA，slice区分正文/摘要/锚点。实际source与正文SHA不匹配时不复用旧缓存，详见[DOC快照契约§4](doc_snapshot_contract.md#4-段零检索与缓存)。

- **三元组已存在** → **跳过注入**（不重复进思考输入）+ **跳过续期**（历史源不重复 `+1` hit_count）
- **三元组不存在** → 注入 + 追加一条缓存记录

**不同 slice 允许共存**（不是 bug，是特性）：

- init 注 `(history, demo-3, requirement_points)`，后续 start 注 `(history, demo-3, adr_risks)`——两个不同 slice，各自注入一次，合理
- 这是"一次开发链路里不同步骤注入同一工单的不同信息切片"的正常场景

### 续期去重（hit_count 防重复 +1）

**历史源**命中才续期 `hit_count`（工程文档源无续期概念）。同一工单目录内，对同一历史 `ticket_id` 的续期**只算一次**：

- **判定依据**：缓存 `injections` 中是否已有 `source == "history" AND ref_id == <该 ticket_id>` 的**任一**记录
- **已有** → 已续期过，本次命中不再 `+1`（但若 slice 是新的，仍允许注入该新 slice，只是不续期）
- **没有** → 首次命中，注入 + 续期（`hit_count += 1` + `last_used_at` 原子同步）+ 写缓存

**跨工单目录**命中同一历史工单 → 算新的续期（不同 `_inject_cache.json`，各自首次命中 `+1`）。符合"独立开发才算多次复用"。

### 生命周期与崩溃恢复

- **跟随工单目录 + append-only**：`.icode_output_N/` 删除→缓存消失；每条注入立即写盘，崩溃不丢、重启不重复注入
- **与续跑机制正交**：不读 `*_in_progress`，不影响步骤 2/5 断点续跑
- **向后兼容**：旧工单有 metadata 且 `ticket_id` 非空、但无缓存 → 首次检索可创建 `{"ticket_id":"<本工单>","injections":[]}`；无 metadata 或空 `ticket_id` 的 legacy 目录只读，不创建无身份缓存

### 工程污染防护

缓存只在 `.icode_output_N/` 内（不进工程根/git，建议 `.gitignore` 已含 `.icode_output/`），与 `.ico_metadata.json` 平级独立，不污染检索字段。

## project_docs 工程文档库（icode doc 步骤用）

> `/icode doc`生成工程知识库，无需用户维护工程专属配置。章节前50行用于检索；实际源码身份、正文SHA、审核及状态另由元信息和worklist核对。详见[DOC快照契约](doc_snapshot_contract.md)、[步骤](../steps/doc.md)与[模板](doc_template.md)。

### 目录布局

```text
~/.claude/icode_data/project_docs/
└── <project_id>/                    # project_id = basename(git rev-parse --show-toplevel)
    ├── main/                        # 分支目录：DOC_DIR 按 <project_id>/<branch> 分目录
    │   ├── 00_overview.md           # 永远首章，元信息块含 generation_commit/branch/submodules
    │   ├── 10_architecture.md
    │   ├── 20_<module>_<topic>.md   # 十位桶：20-29 模块章节，留空隙可插入
    │   ├── 30_<module>_<topic>.md   # 第二个模块桶
    │   ├── ...
    │   ├── 90_glossary.md
    │   ├── 99_code_facts_audit.md   # 永远末章
    │   └── _meta.json               # 含 branch + head_commit，跨分支不通用
    ├── feature/                     # 同一工程在 feature 分支跑 /icode doc 落这里
    │   ├── 00_overview.md
    │   └── ...
    └── (detached)/                  # detached HEAD 状态落此目录（罕见）
        └── ...
```

### project_id 与 branch 语义

- **git-root 模式**（cwd 在 git 仓库内）：`project_id` = `basename($(git rev-parse --show-toplevel))`，不区分子目录。**worktree 归一化（F1）**：worktree 内 `git rev-parse --show-toplevel` 返回 **worktree 自身根**（如 `<repo>-wt-<ticket-slug>`），直接 basename 会导致 project_id 漂移 → limit 主存 / project_docs / device_config 全部读不到主仓数据。**`.git` 为普通文件（内容以 `gitdir:` 开头）= git worktree 成员**，此时 `project_id` 归一到**主仓根**（`git worktree list --porcelain` 首行 `worktree <path>`），而「当前 checkout 根」（代码实际所在）仍为 worktree 根——两概念分离：**project_id 归主仓（跨 checkout 共享依据），checkout 根留当前（代码实证依据）**
- **unique-nested-git 模式**（请求路径不在 Git 仓但深度 2 内只有一个 Git 根）：先按 [project_intake.md](project_intake.md) 切到该实际根，再完全按 git-root 模式处理；请求路径只作入口证据，不得成为 `project_path` 或 `.icode_output` 落点。
- **`repo` 根模式**（cwd 不在 git 仓库但有 `.repo/manifest.xml`，如 Google `repo` 管理的多仓库项目）：`project_id` = `basename(从 cwd 向上找第一个含 .repo/ 的目录)`
- **branch 感知**：DOC_DIR = `~/.claude/icode_data/project_docs/<project_id>/<branch>/`，**按分支分目录存**（key 设计见下文「DOC_DIR 分支隔离」段）
  - 同工程不同分支（如 `myproject/main/` vs `myproject/feature/`）→ 天然隔离互不覆盖
  - **detached HEAD** → 落到 `(detached)` 子目录
  - **非 git 仓库** → 落到 `(no-git)` 子目录
  - 分支名sanitize后若与原始分支不同或命中Windows设备名，追加sha256(原始分支)前8字符，避免替换字符导致目录碰撞；旧目录只按原始branch精确匹配作为legacy材料，保留不删
- 子目录（如 `myrepo/module_a/`）是同一 project 下的**章节模块**（章节名带模块前缀 `20_module_a_overview.md`），不是独立 project
- **冲突处理**：若 `~/.claude/icode_data/project_docs/<project_id>/<branch>/` 已存在且其 `00_overview.md` 元信息块的 `project_path` 与当前 `git_root` 不同 → 自动追加 hash 后缀 `${PROJECT_ID}__${BRANCH_SAFE}__$(echo $GIT_ROOT|sha256sum|cut -c1-4)`，AI 靠元信息块的 git remote 区分同名工程同名分支
- **零配置**：不引入任何配置文件，工程名、分支等可配信息全在章节元信息块
- **`resolve_project_id(cwd)` 算法**：
  1. `git rev-parse --show-toplevel` 成功 → git-root 模式：**先做 worktree 归一化（F1）**——若 `<git_root>/.git` 是**普通文件**（内容以 `gitdir:` 开头）= git worktree 成员，则 `project_id = basename(主仓根)`（`git worktree list --porcelain` 首行 `worktree <path>`，**勿用 `awk '$1=="worktree"{print $2}'` 形式**：`$2` 在路径含空格时截断，须 `substr($0,10)` 取整行剥前缀）；`git_root` 保持 worktree 根**不动**（分支判定/代码实证用）。返回 `(project_id, "git-root", git_root, abbrev-ref HEAD)`
  2. 否则从 cwd 向上逐级 `test -d $d/.repo`，首个命中 → repo-root 模式，返回 `(basename(repo_root), "repo-root", repo_root, "(no-git)")`
  3. 都失败 → 报错"请在 git 仓库或 repo 管理的项目内运行 /icode doc 或段零检索"
  4. **多分支并存**：同 `project_id` 下可有多个 `<branch>/` 子目录，下游段零检索需枚举所有分支目录 + 按当前 cwd 分支过滤（详见「DOC_DIR 分支隔离」段）

### 章节前 50 行三合一（自带身份证）

每章前50行承载工程/源码版本、依赖、KEYS与简要说明，结构见[doc_template.md](doc_template.md)。无需用户维护工程专属配置；版本身份、正文SHA、审计和进度还须读_meta/worklist，不能只凭前50行或文件存在称完成。

### 段零·工程文档检索（init/log/plan/start/fast 共用）

> 步骤 1-5 与 3.5/3.6 已独立到 [references/project_docs_retrieval.md](project_docs_retrieval.md)；执行段零检索时按该文件完整执行，本文件不再重复。

### 检索结果缓存（可选）

缓存规则以[doc_snapshot_contract.md§4](doc_snapshot_contract.md#4-段零检索与缓存)为准。每次先验证当前工作区/依赖source_id、绑定snapshot_id和实际正文SHA，再查缓存；300秒TTL或index mtime都不能替代版本校验。

位置仍为~/.claude/icode_data/_search_cache.json。键包含cwd、排序关键词、当前source_id、精确依赖绑定与模板；值保存文档SHA和已校验来源。旧cwd+关键词键不复用。切提交、dirty变化、依赖版本、模板/正文修改即失效，哪怕发生在TTL内。可调用doc_snapshot.py的cache_key生成身份键；缓存仅省精读排序，不绕过版本与人工编辑检查。

### project_docs 主动 stale 扫描（`/icode doc` 末尾执行，防过时章节堆积）

对比 index.json 的主动 stale 扫描，project_docs 章节此前只有"段零命中前被动检测"一条路径--长期不跑 `/icode doc` 的工程，过时章节无机制标记，首次段零命中才被动检测（且只降级注入，不清理）。本机制补第二道清理（与 index.json「主动 stale 扫描」对齐，见上文「索引淘汰规则·主动 stale 扫描」）：

- **触发时机**：`/icode doc` 执行末尾（见 [doc.md](../steps/doc.md) 步骤8）
- **扫描范围**：当前工程分支目录章节；扫描其他工程时分别解析其实际源码根/分支/逐章身份，不把全局不同版本放在当前cwd下判定
- **校验方法**：逐章核其来源上下文，再在对应实际源码根检查KEYS文件路径/锚点。源码不可读列未解析，不编造stale结论；锚点存在仅证明存在性，不把源码身份不匹配或未审计的正文恢复为新鲜
- **结果写 `_meta.json.stale_files`**：失效的章节文件名写入工程 `_meta.json.stale_files` 数组；章节重生成（锚点恢复）后从此数组移除
- **段零消费**：段零检索时先读 `stale_files` 快速跳过（无需每章 git diff，控 token），再运行时 `git diff` 兜底（见上文「stale 检测」两源合一）

## module_docs 工程模块库（模块族与工作区快照）

模块族仍按URL+原始有效分支计算可读key；正文保存在snapshots/<snapshot_id>/。具体算法、元信息和发布/检索命令以[doc_snapshot_contract.md](doc_snapshot_contract.md)为唯一真源，实际辅助工具为[doc_snapshot.py](../tools/doc_snapshot.py)。

工程切分支与模块切分支互相独立，但复用还须核实际源码内容；同URL/分支不同commit或dirty内容分别保留。detached需完整HEAD及内容身份，不用“HEAD”标签合并所有版本。旧平铺正文和_meta不删除/覆盖，不自动转换成可信快照。

### 目录布局（module_docs 层）

```text
icode_data/
  project_docs/<project_id>/<branch_safe>/_meta.json  # 精确module_deps与逐章基线
  module_docs/<module_key>/
    *.md / _meta.json                              # 旧平铺库保持原样
    snapshots/<snapshot_id>/*.md / _meta.json       # storage_schema=2，不可变审稿修订
```

### `module_docs` key 计算

族键仍为<url_basename_sanitized>_sha256(url+":"+branch)[:12]，不含commit；**它是族身份，不是可直接复用正文的版本身份**。正文定位另用source_id和snapshot_id。两不同URL不擅自合并，已用旧key继续保留。

### `_meta.json` 与工程依赖

工程保留project_id/project_type/git_root/原始branch、真实模块path及未知字段。module_deps每条除key/commit外记录source_mode、source_fingerprint、source_id、snapshot_id、doc_path、generated。发布前新依赖只是计划；快照可见后用doc_snapshot.py bind更新源码外的父元信息，并回读成功后才算完成。已知但不可读依赖仍保留generated=false，同时列unresolved_modules及失败原因。

快照_meta记录storage_schema、完整source_identity、snapshot_id、source_id、commit、source_fingerprint、正文SHA、template_version、scope和review。partial不能充full，旧元信息无指纹不能直接强绑。模板升级/手改合并产生新文档修订；各工程明确绑定需要的snapshot_id。used_by按父工程实际绑定核对，仅是关联信息，不能替代版本选择。

### 工程元信息块"依赖子模块"字段规范

元信息写module_a@有效分支@完整HEAD/source_id，并指向module_docs/<key>/snapshots/<snapshot_id>/；使用当前实际checkout源码核事实。工程正文只写模块在工程拓扑的角色，内部实现放在对应版本模块正文。完整字段例见doc_snapshot_contract.md§3，避免重复维护模板。

### 6 级模块检测（按优先级，`/icode doc` 执行）

本表识别候选，不保证存在独立Git版本。实际Git根才能capture并发布module_docs快照；monorepo/vendor等无独立Git根的目录写工程章节，记录真实文件输入与SHA，不编造URL/HEAD。repo聚合根无HEAD时按实际子仓库身份及工程清单记录来源，不强造单仓库提交身份。

| # | 方式 | 识别 | 提取 URL + branch | commit 获取 |
| --- | --- | --- | --- | --- |
| 1 | git submodule | `.gitmodules` | url + `git config -f .gitmodules submodule.<name>.branch` | `git submodule status` |
| 2 | `repo` 管理 | `.repo/manifest.xml` | `<remote fetch>` + `<project name>` 拼 URL，`<project revision>` = branch | `cd <submodule_path> && git rev-parse HEAD`（**`<submodule_path>` 来自 manifest `<project path="...">` 字段，非字面 `${GIT_ROOT}/<project name>`**——见本表下嵌套路径推导说明） |
| 3 | CMake FetchContent | `CMakeLists.txt` 扫 `FetchContent_Declare` | `GIT_REPOSITORY` + `GIT_TAG` | 通常不可用（build 目录未下载），fallback 警告 |
| 4 | monorepo 启发式 | 无 .gitmodules + 多子目录各自有 README + 独立构建文件（CMakeLists/package.json/Cargo.toml/Makefile） | 子目录路径，branch 标 "unknown" | 不可用 |
| 5 | vendor 扫描 | `vendor/<lib>/` 子目录 | 子目录路径，type="vendor-no-git" | 不可用 |
| 6 | 用户配置 | `.icode_doc_modules`（工程根） | 显式列路径/URL | 显式 |

**去重**（两步，避免单一目录被多种方式识别导致重复生成）：
1. **先按"归一化绝对路径"合并**——归一化绝对路径 = `realpath <path>` 解析（处理符号链接 + 路径规范化）后**去尾部 `/`**；同一归一化路径被多种方式识别（如 vendor + monorepo 都识别某路径）→ 合并到 type 列表，type 字段记录多标签如 `"vendor,monorepo"`
2. 再按(module_key, source_id)去重，同族不同提交/dirty内容不合并；记录所有同身份的本地副本路径，逐副本核验。

### 子仓库代码获取

- **git submodule/repo子仓库**：解析真实checkout路径后capture实际工作区，再Read当前源码；不以archive HEAD丢掉未提交输入。缺失仓库记录unresolved，不自行同步/checkout。

**`repo` 嵌套子项目路径推导**：从manifest project的真实path取checkout，缺path按name默认值；用`.repo/project.list`与实际Git根交叉核对，`.repo/projects/*.git`是管理数据，不能当源码目录。include/extend-project解析不完整时保留unresolved，不猜字面module_name或任意find结果。实际生成与读取路径相同，不受目录嵌套层数限制；具体示例见[doc.md](../steps/doc.md)步骤2。

### 段零跨仓库自动覆盖（关键收益）

- 多工程仅在源码身份和已审正文快照匹配时复用；同族的不同版本分别绑定，各版本并存。
- 详见「段零·工程文档检索」段步骤 3（module_deps 检索）+ 步骤 3.5（反查父项目）

**生成时不省 token**（用户闲时跑，模块文档可详细完整生成），**检索时省 token**（段零只读前 50 行粗筛 + 命中按小节锚点读，不灌全章）。
