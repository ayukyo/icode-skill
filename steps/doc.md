# 步骤 doc — 工程级知识库生成（独立步骤，不参与 1~6 流程推进）

**命令**: `/icode doc [自然语言]`
**产出**: `~/.claude/icode_data/project_docs/<project_id>/*.md`（章节自带身份证）
**会话**: 主会话
**定位**: **工程级知识库生成与维护，独立步骤**。不创建 `.icode_output_N/`、不写 `.ico_metadata.json`、不更新工单 `completed_steps`/`status`。知识库供 `/icode init`/`log`/`plan`/`start`/`fast` 启动时**段零检索**自动注入。

> **核心设计哲学**（必须先 Read [references/dir_and_metadata.md](../references/dir_and_metadata.md)「project_docs 工程文档库」段 + [references/doc_template.md](../references/doc_template.md)）：**零配置/零状态/零索引文件**——只有章节 .md，前 50 行四块自带身份证，文件系统即数据库。

## 前置校验

1. cwd 必须在 git 仓库或 `repo` 管理的项目内：
   - `git rev-parse --show-toplevel` 成功 → git-root 模式
   - 否则从 cwd 向上逐级 `test -d $d/.repo`，首个命中 → repo-root 模式（Google `repo` 工具管理的多仓库项目如独立子仓库组成的超级项目）
   - 都失败 → 报错"请在 git 仓库或 `repo` 管理的项目内运行 /icode doc"
2. 全局目录 `~/.claude/icode_data/project_docs/` 和 `~/.claude/icode_data/module_docs/`（首次自动创建）

## project_id 解析

```bash
# git-root 模式（cwd 在 git 仓库内）
GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
PROJECT_TYPE="git-root"
if [ -z "$GIT_ROOT" ]; then
  # repo-root 模式（cwd 向上找 .repo/）
  REPO_ROOT=""
  D="$PWD"
  while [ "$D" != "/" ]; do
    [ -d "$D/.repo" ] && REPO_ROOT="$D" && break
    D=$(dirname "$D")
  done
  if [ -n "$REPO_ROOT" ]; then
    GIT_ROOT="$REPO_ROOT"
    PROJECT_TYPE="repo-root"
  else
    echo "错误：cwd 不在 git 仓库或 repo 管理的项目内"
    exit 1
  fi
fi
PROJECT_ID=$(basename "$GIT_ROOT")
DOC_DIR="$HOME/.claude/icode_data/project_docs/$PROJECT_ID"
```

**冲突检测**：`$DOC_DIR` 已存在时读其 `00_overview.md` 元信息块的 `project_path`——与当前 `GIT_ROOT` 一致则复用；不一致（同名不同工程）则追加短 hash 后缀 `${PROJECT_ID}__$(echo $GIT_ROOT|sha256sum|cut -c1-4)`，输出 ℹ️ 一行提示。`PROJECT_TYPE`（`git-root`/`repo-root`）写进工程 _meta.json。

## 意图识别（去参数化）

AI 解析自然语言的「目标工程」+「动作」：

- **目标**：无描述→全局扫描（列各工程 stale 状态 + 建议动作，用户选）；路径→`git rev-parse` 解析 git 根（路径相对根部分仅过滤模块，不改 project_id）；工程名→`project_docs/` basename + `00_overview.md` 工程名匹配（多命中问、零命中用 cwd）；模块名→按 cwd 定 project_id 后，**先判定该模块是否为独立 git 仓库**（查 `.repo/projects/<name>.git`、`.gitmodules`、`CMakeLists.txt` 的 `FetchContent_Declare`，方法同步骤 2 模块检测）：**是独立仓库**→生成/更新该模块 `module_docs/<key>/`（聚焦），工程 `project_docs` 仅补"该模块在工程 IPC 拓扑中的角色"上下文章节（不重复 module_docs 内部细节，职责划分见 [doc_template.md](../references/doc_template.md)「九 · 职责划分」）；**非独立仓库**（工程内普通子目录）→生成/更新 `project_docs` 中该模块章节。两者均按 KEYS 匹配已有章节做增量判定（首次无章节可匹配时见步骤 3「首次全量 + 模块名参数」）
- **动作**：全量词（重新生成/全量/从头）→全量重生成（**确认门**）；增量词（检查/刷新/更新）→增量；新增词（加/补充）→新章节；只读词（只看/列一下）→扫描不写；无动作词→默认增量
- **歧义必问**：目标明确无动作词但 git diff 命中 ≥50% 章节→问"全量/增量N个/仅列"；"加 X"但 X 已存在→问"覆盖/新建/合并"；完全模糊→列候选工程让选
- **独立仓库模块默认不问**：模块名命中独立 git 仓库时，默认按 "module_docs + 工程上下文章节" 互补生成（见「目标」+ 步骤 5），**不强制三选一问询**（判定规则已定死 "独立仓库→两者互补"，问询徒增交互成本）；**仅当用户动作词明确表达 "只要模块本身"**（如 "只看/仅模块/不要工程上下文"）时→跳过工程上下文章节，只生成该模块 module_docs

## 确认门（全量重生成必经）

全量覆盖前**必须先检测手动编辑**（读每章元信息块"章节生成时间" T_gen，对比文件 mtime；mtime 晚于 T_gen → 被手动改过）：

- 无手动编辑 → 确认"将全量重生成 N 个章节"→ 执行
- 有手动编辑 → **禁止静默覆盖**，提示询问：

  ```text
  ⚠️ 检测到以下章节有手动编辑（将丢失）：
    - <章节名>（生成后 N 处修改）
  选择：① 全量覆盖 ② diff 后人工合并 ③ 跳过这些章只更新其余
  ```

## 执行流程

### 1. 解析 project_id + 意图（见上）

### 2. 模块检测 + 代码特征扫描（先扫描后生成）

**模块检测**（按 6 级优先级识别工程依赖的独立模块）：详见 [dir_and_metadata.md](../references/dir_and_metadata.md)「module_docs 工程模块库」段「6 级模块检测」表（git submodule / `repo` 管理 / CMake FetchContent / monorepo 启发式 / vendor 扫描 / 用户配置 + `.icode_doc_modules` JSON 格式）。**"独立模块"判定 = 该模块本身是独立 git 仓库，与"是否工程核心"无关**——工程核心模块同时是独立仓库时两者都生成，互补不重复（职责划分见 [doc_template.md](../references/doc_template.md)「九 · 职责划分」）。

**monorepo 启发式补充判别条件**（dir_and_metadata 表未含，doc.md 步骤 2 独有）：子目录**不在工程根 `.gitignore` 中**（避免误把工程内辅助目录当独立模块）+ **子目录有自己的 README**（含 `## ` 等 markdown 标题）。

**去重**：详见 dir_and_metadata.md 同段「去重」两步（先按归一化绝对路径合并 + 再按 `key = sha256(url+":"+branch)[:12]` 去重）。输出 modules 列表（每个含 url+branch+key+commit+path+type），写进工程 _meta.json 的 `module_deps` 字段。

**代码特征扫描**：用 Grep 扫描工程代码特征，按本表「动态章节」段（doc_template.md「五」）决定追加哪些章节（AI 根据工程实际技术栈选 grep 模式，**不硬编码框架名**）。汇总「章节规划清单」：固定（00/10/90/99）+ 命中的动态章节。

### 3. 增量判定（非全量时）

- `$DOC_DIR` 不存在 → 首次全量
- 存在 → 读 `00_overview.md` 的 `generation_commit`：HEAD 相同→"已是最新"；不同→`git diff <commit>..HEAD --name-only` 拿变更文件，命中某章 KEYS"文件位置"的章增量重生成，其余跳过；变更文件在未覆盖目录→提示"是否生成新章节"
- **模块依赖变化检测**（关键，否则新增/删除依赖漏生成）：比较旧 `_meta.json.module_deps` 与步骤 2 检测的新 modules 列表 → 差异：
  - 新加的 dep（key 不在旧列表）→ 触发对应 module_docs 生成
  - 删除的 dep（旧列表有但新列表无）→ 提示「工程不再依赖 X，module_docs/{key}/ 是否保留（默认保留，需手动清理）」
  - key 变化的 dep（url 或 branch 变）→ 触发新 key 的 module_docs 全量重生成 + 旧 key 提示是否清理

- **首次全量 + 模块名参数**（`$DOC_DIR` 不存在但用户给了模块名）-> 该模块名作为"聚焦生成目标"，而非"匹配已有章节"（首次无章节可匹配，原匹配语义悬空）：
  - 该模块是独立仓库 -> 生成该模块 module_docs + 工程固定章节（00/90/99）+ 命中动态章节，其中 `10_architecture` 描述该模块在工程拓扑中的角色（不重复 module_docs 内部）
  - 该模块非独立仓库 -> 生成 `project_docs` 该模块章节 + 固定章节（00/90/99）
  - 其余章节按代码特征自适应生成；**不因用户只点一个模块而省略固定章节**（00/90/99 永远生成）

### 4. 强制思考前置（不可跳过）

**必须先 Read [references/thinking.md](../references/thinking.md) + [references/anti_laziness.md](../references/anti_laziness.md) + [references/doc_template.md](../references/doc_template.md) 完整内容**（不得凭概述执行）。子项（≥4步）= 扫描结果分析 → 章节规划 → 元信息字段准备 → 风险评估（手动编辑/冲突）。

### 5. 生成 module_docs（依赖）+ 工程章节

**先生成 module_docs**（依赖先于依赖者，用户闲时跑不省 token，模块文档可详细完整生成）：

**module_docs 生成范围（按用户意图聚焦，避免全量 token 爆炸）**：

- **用户指定模块名** -> 聚焦该模块（其余检测到的独立仓库模块标 `generated: false` 写入 `module_deps`，不生成文档，末尾汇总提示"其余 N 个模块未生成 module_docs，可 `/icode doc <name>` 按需生成"）：该模块是独立仓库 -> 生成该模块 module_docs；该模块非独立仓库 -> 不生成 module_docs（该模块走下方「再生成工程自身章节」生成 project_docs 章节）
- **无模块名（全量）**→ 检测到的所有模块写入 `module_deps`（可读模块 `generated: true`）；module_docs 只生成"代码已本地化可读"的（git submodule / `repo` 子项目 / monorepo / vendor），不可读的（如 CMake FetchContent build 目录未下载）标 `unresolved_modules`
- **反例（禁止）**：**不得因数量大而全部降级、把模块内容塞进 `project_docs` 章节替代 module_docs**（历史 bug：某工程 34 个 `repo` 子项目时 AI 把模块塞进 project_docs 章节，module_docs 一个没生成）；全量时可读模块数量过多（如超过 10 个）时，应**分批生成或提示用户分次 `/icode doc <name>` 聚焦**，而非降级塞 project_docs

- 对每个**待生成**的 module（按上述范围筛后的列表，非全量 modules）：
  - 克隆/读取该 module 的代码到临时目录（git submodule 用 `git submodule foreach 'git archive HEAD | tar -x -C $tmp/<name>'`；repo 子仓库用 `cd <submodule_path> && git archive HEAD | tar -x -C $tmp`；monorepo/vendor 直接读子目录；CMake FetchContent 通常 build 目录未下载，fallback 警告）
  - 按 [doc_template.md](../references/doc_template.md)「九、模块章节模板」生成章节（前 50 行四块 + 模板自适应 grep 表；KEYS 按 doc_template.md「七」提取）
  - Write 到 `$HOME/.claude/icode_data/module_docs/<key>/<NN>_<topic>.md`（十位桶）
  - **读 `module_docs/<key>/_meta.json`（如存在）→ 提取现有 `used_by` → 与本工程（按 `project_id` 标识）合并去重**（避免 B 工程生成时覆盖 A 工程的引用）
  - 写 `_meta.json`（`repo_url` / `branch` / `current_commit` / `used_by` = 合并去重后的列表）
  - 检查 `<key>/_meta.json` 的 `current_commit` 与当前模块 commit 是否一致：一致跳过（已是最新），不一致→**该 module 全量重生成**（单 module 文档量小，全量合理；不需增量 diff，简化逻辑）
- 拉取失败→**不写**该 dep 到工程 `_meta.json.module_deps`（避免段零检索时查不到对应 `module_docs/<key>/` 漏匹配）；在工程 `_meta.json.unresolved_modules` 数组里记录 `{name, type, reason, attempt_at}`（`reason` 如"CMake FetchContent build 目录未下载"）；不拖垮（其他 module 继续）

**再生成工程自身章节**（依赖者引用依赖者，含"依赖子模块"字段）：

- 对每个待生成章节：读相关代码（Read/Grep）→ 按模板写正文 → 生成前 50 行四块（含「依赖子模块」字段）→ Write 到 `$DOC_DIR/<NN>_<module>_<topic>.md`（十位桶，新增取 `max(NN)+1`）
- 写 `_meta.json`（`project_id` / `project_type` / `git_root` / `module_deps` 含所有检测到的可读模块（已生成 `generated: true`、按需未生成 `generated: false`，见上「module_docs 生成范围」）/ `unresolved_modules` 含拉取失败的模块）
- 单章失败→标记不拖垮

### 6. 代码事实审计（`99_code_facts_audit.md`，永远生成）

增量+并行（见 [doc_template.md](../references/doc_template.md)「六」）：首次并行子代理验证全库 file:line；增量只重验变更文件相关引用（**含一跳联动**：grep 变更文件调用的符号，把被引用的未变更文件也纳入重验，缩小语义联动盲区）。**已知盲区**：未变更文件因他处变更而语义变化时（二跳及以上联动）增量审计不重验，残余过时由段零 stale 检测 + 不盲信约束兜底（见 [dir_and_metadata.md](../references/dir_and_metadata.md)）。子代理失败→该批标 `[未验证-子代理失败]`。

### 7. 进度输出（阶段级 + 末尾汇总）

```text
▶ /icode doc myproject
[1/3] 扫描代码特征... ✓ 识别 N 个章节候选
[2/3] 生成章节 [8/12]... (当前 <章节名>)
[3/3] 代码事实审计 [验证 45/120 引用]...

✓ 完成。汇总：
| 章节 | 状态 | 备注 |
|------|------|------|
| 00_overview.md | 新增 | - |
| <章节名> | 增量更新 | 3 处 file:line 重验 |
| 99_code_facts_audit.md | 失败 | 子代理超时，重跑 |
未生成模块 N 个（unresolved_modules）：<name1> (<reason1>), <name2> (<reason2>)... — 重跑 /icode doc 时自动恢复
```

### 8. 主动 stale 扫描（project_docs 章节锚点校验，防过时堆积）

对比 index.json 的主动 stale 扫描（见 [dir_and_metadata.md](../references/dir_and_metadata.md)「索引淘汰规则·主动 stale 扫描」），project_docs 章节此前只有段零命中前被动检测，长期不跑 /icode doc 的工程过时章节无机制标记。本步骤补第二道清理（详见 [dir_and_metadata.md](../references/dir_and_metadata.md)「project_docs 主动 stale 扫描」段）：

- **时机**：步骤7 进度输出后（章节已生成 + 99 章审计完成）
- **范围**：全库章节（project_docs 章节量可控，每章 Grep 锚点 <1K token，全量可控）
- **方法**：逐章读其前 50 行 KEYS「文件位置」列出的源码路径，用 Grep 确认锚点代码仍存在（方法同 99 章审计的 exists 校验，但只验存在性不验描述属实，更轻）
- **结果写工程 _meta.json.stale_files**：锚点失效（文件已删/路径已改/符号已重命名）→ 章节文件名加入 stale_files；锚点恢复存在（无论本次是否重生成，如代码恢复或重生成）→ 从 stale_files 移除；**stale_files 每次全量重算**（步骤8 执行后反映当前所有锚点失效章节，非增量累加）
- **输出**：步骤7 汇总表之后独立输出 stale_files 结果（不并入步骤7 表，避免时序冲突；N=0 写"无过时章节"；N>0 列出章节名 + 锚点失效原因，建议重跑 /icode doc 或确认锚点）
- **段零消费**：段零检索时先读 stale_files 快速跳过过时章节正文（降级注入摘要，见 [dir_and_metadata.md](../references/dir_and_metadata.md)「stale 章节降级注入」）

> 不校验"描述是否属实"（那是 99 章审计职责），本步骤只做存在性快检控 token；99 章带 [未验证-子代理失败] 的章节不自动标 stale（未验证≠过时）。


## 元信息块字段取值约定

- **工程名 / 模块名**：默认 `basename($GIT_ROOT)`，可在章节文件手动改
- **Git 地址**：`git -C "$GIT_ROOT" remote get-url origin`（无 remote 填"无 remote"）
- **分支/提交**：`git rev-parse --abbrev-ref HEAD` + `git rev-parse --short HEAD` + `git log -1 --format=%ci`
- **项目类型 / 模块类型**：`PROJECT_TYPE`（`git-root` 或 `repo-root`），或模块的 `MODULE_TYPE`（`git-submodule`/`repo`/`cmake`/`monorepo`/`vendor`/`user`）
- **子模块（git submodule，v1 字段）**：读 `.gitmodules`（无则"无子模块"）+ `git submodule status`，每行 `path → url @ commit`
- **依赖子模块（按仓库+分支）**：从工程 _meta.json 的 `module_deps` 列表取，章节元信息块格式 `module_a → module:module_a@main@a3f2b1c（module_docs/<key>/）`
- **被工程引用（模块章节 used_by）**：从模块 _meta.json 的 `used_by` 列表取（如 `myproject（git-root）, another_project（repo-root）`）
- **产品线/型号**：grep 工程的产品型号宏/配置，推断不出填"未识别"
- **章节归属模块**：与文件名 `<module>` 一致；跨模块章节填 `null`
- **章节生成时间**：运行时 `date +%Y-%m-%dT%H:%M:%SZ`，**禁止写死**

## 反偷懒

- 动态章节必须基于 grep 实证，不得"猜工程有某技术就硬塞章节"
- 正文每个代码引用必须真实存在（99 章审计兜底），禁止编造 file:line
- 每章前 50 行四块齐全（缺块段零无法检索）
- 全量覆盖必经确认门，禁止静默覆盖手动编辑
- KEYS 双覆盖（客观词+主观词），不得只有一类

## 工程污染防护

产物全在 `~/.claude/icode_data/project_docs/`，**不写工程内任何文件**（不动 `doc/workflows/`/`.gitignore`/源码）。用户工程内已有历史文档**忽略、从零生成**到全局，不读取不迁移不删除。

## 完成标志

- 规划章节已生成（固定 00/90/99 + 命中的动态章节）
- 每章前 50 行四块齐全
- `00_overview.md` 元信息块含当前 HEAD 的 `generation_commit` + 完整子模块列表
- `99_code_facts_audit.md` 已生成（部分失败也标明）
- 工程 _meta.json.stale_files 已刷新（步骤8 主动 stale 扫描，段零检索据此跳过过时章节）
- 末尾汇总表输出

## 衔接与可重复

- **段零消费**：`/icode init`/`log`/`plan`/`start`/`fast` 启动时段零自动检索（见 [dir_and_metadata.md](../references/dir_and_metadata.md)「段零·工程文档检索」段）；**doc 自身不写 `_inject_cache.json`**（工单目录缓存，doc 不创建工单）
- **可重复**：多次 `/icode doc` 覆盖更新，手动编辑受确认门保护
