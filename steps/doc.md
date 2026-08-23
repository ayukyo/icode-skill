# 步骤 doc — 工程级知识库生成（独立步骤，不参与 1~6 流程推进）

**命令**: `/icode doc [自然语言]`
**产出**: `~/.claude/icode_data/project_docs/<project_id>/<branch>/*.md`（分支子目录隔离，**切换分支跑 doc 不互相覆盖**；章节自带身份证）
**会话**: 主会话
**定位**: **工程级知识库生成与维护，独立步骤**。不创建 `.icode_output_N/`、不写 `.ico_metadata.json`、不更新工单 `completed_steps`/`status`。知识库供 `/icode init`/`log`/`plan`/`start`/`fast` 启动时**段零检索**自动注入。

> **核心设计哲学**（必须先 Read [references/dir_and_metadata.md](../references/dir_and_metadata.md)「project_docs 工程文档库」段 + [references/doc_template.md](../references/doc_template.md)）：**零配置/零状态/零索引文件**——只有章节 .md，前 50 行四块自带身份证，文件系统即数据库。

## ⚠️ 多分支设计 · 反偷懒强约束（必读，防止误判"覆盖"）

> **本段是 icode-skill doc 步骤对"多分支机制"的统一设计语义说明**——任何 AI 在执行 `/icode doc` 或解释用户"被覆盖"反馈前，**必须先 Read 本段全文**；只读概要不算。后续正文会把每个机制在某一行展开，本段只做**总览 + 索引**。

### 1. 设计目标（为什么按分支隔离）

工程代码在多分支并行开发时差异巨大。文档不按分支隔离会引发三种灾难：

1. **跨分支借鉴失真**——`main` 分支文档里说有模块 X，但当前在 `feature` 分支代码里 X 不存在或根本不一样，AI 借鉴工程文档会生成错误代码
2. **跨分支增量判错**——`git diff` 上次 doc commit 与本次 commit 时，若没按分支过滤，diff 会把"跨分支差异"误报为"代码变了"，触发 doc 频繁全量重生成浪费 token
3. **跨分支手动编辑丢失**——一个分支改了文档，另一分支 doc 全量重生成会把改动冲掉

**核心契约**：`project_docs/<id>/<BRANCH_SAFE>/` 子目录天然隔离每个分支的工程文档；`module_docs/<url+branch> key/` 子目录天然隔离每个上游仓库+分支的模块文档；**段零检索只在当前 cwd HEAD 分支对应的子目录内读，不交叉**。

### 2. 机制总览（3 层隔离，写作时必须知道在哪一层）

| 维度 | 隔离粒度 | 存储位置 | 跨分支是否交集 |
|------|----------|----------|----------------|
| **工程文档** | `<project_id>/<BRANCH_SAFE>/` 两层目录 | `~/.claude/icode_data/project_docs/<id>/<branch>/*.md` | **不交集**（子目录天然隔离） |
| **模块文档** | `<url_basename_sanitized>_<sha256(url+":"+branch)[:12]>/` 复合 key | `~/.claude/icode_data/module_docs/<key>/*.md` | **不交集**（同 url 不同 branch → 不同 key；同 url 同 branch → 同一 key 但 commit 可变） |
| **段零检索** | 只读当前 cwd HEAD 分支对应的子目录 | `dir_and_metadata.md`「DOC_DIR 分支过滤」段实现 | **不交集**（不交叉读其他分支子目录正文，避免跨分支借鉴失真） |

### 3. 五大边界（用户/AI 高频踩坑）

| 边界 | 实际行为 | 详见 |
|------|----------|------|
| **v1 → v2 旧布局迁移** | v1（平铺 `project_docs/<id>/00_overview.md`）**确实会互相覆盖**（v1 无分支隔离）；v2 起天然隔离。**v1 → v2 仅在跑一次 `/icode doc` 时自动触发** | 正文 §5.0.6 / §5.1 |
| **detached HEAD 工程** | 落到 `(detached)` 子目录单独记录，**不会与任何分支子目录混在一起** | 正文 §「project_id 解析」段 BRANCH 推导 |
| **同名 fork 不同 URL** | `<url_basename_sanitized>` 相同但 hash(URL+branch)不同 → **不同 key**（自动区分，不要手动合并） | `dir_and_metadata.md`「module_docs 工程模块库」段 |
| **同 url 同 branch 不同 commit** | key 不含 commit；`current_commit` 不一致时 doc 全量重生成覆盖（`dir_and_metadata.md`「module_docs key 计算」段：key 不含 commit；`dir_and_metadata.md`「段零·工程文档检索」段：commit 不匹配触发降级注入） | **有意行为**——"同一份代码不同 commit 只保留最新一份 doc"；要看历史 commit 外部 git 查看即可 |
| **branch 字符串 sanitize 撞名** | branch 名含 sanitize 字符集（9 种字符：反斜杠/斜杠/冒号/星号/问号/双引号/小于号/大于号/竖线）任一字符时会被 sanitize 函数抹平，可能与同 sanitized 后的其他分支撞名（后者写覆盖前者） | 用户创建分支避免用这 9 种字符（git 常用斜杠 `/` 但 icode sanitize 时抹平） |

### 4. 三大常见误解 × 正确理解（逐条对照，禁止误判）

| 误解（用户/AI 反馈常见说法） | 实际行为 | 正确理解 |
|------------------------------|----------|----------|
| "切分支跑 doc，原分支的工程文档被覆盖成新分支的" | doc 写到 `<id>/<新分支>/` 子目录，**原分支子目录里章节仍在** | 工程按分支分子目录天然隔离，原分支 doc 是"看不到"而非"被覆盖"。`ls project_docs/<id>/` 应看到多个 `*/` 子目录 |
| "切分支后段零检索只列新分支章节，原分支的不见了" | 段零按当前 cwd 分支过滤，**不交叉读其他分支子目录正文**（避免跨分支借鉴失真） | 这是**显式反交叉污染设计**，不是 bug。需要看其他分支章节，临时切换分支或 `ls project_docs/<id>/*/` 列举子目录 |
| "同 url+同 branch 的模块 doc 被新 commit 覆盖了"（详见 §3 第 4 行同边界） | key 不含 commit；`current_commit` 不一致时 doc 全量重生成覆盖 | **有意行为**，语义是"同一份代码不同 commit 只保留最新一份 doc"。要看历史版本，外部 git 查看对应 commit 即可 |

### 5. 反偷懒强制条款（违反一条即不合规）

1. **执行前必读本段**——执行 doc 任何子步骤（尤其是"全量重生成""主动 stale 扫描""模板迁移""模块全量重生成"）前，**必须先 Read 本段全文**；不读就直接复述"被覆盖了"作为 bug → 反偷懒第 21 条违规
2. **遇"被覆盖"反馈的处置**——永远先按本段"三大误解 × 正确理解"表 + "五大边界"表逐条排查，给出根因 + 诊断命令（`ls project_docs/<id>/` 看分支子目录布局、`cat project_docs/<id>/<branch>/_meta.json` 看工程元信息、`cat module_docs/<key>/_meta.json` 看模块元信息），**禁止默认就是 bug**
3. **跨工程/跨模块上下文**——跨工程 doc 借鉴仅看 `00_overview.md`（`dir_and_metadata.md`「段零·工程文档检索」段），**不交叉读其他章节**，防止跨工程/跨分支借鉴失真
4. **分支名边界两类单独走**：detached HEAD → `(detached)` 子目录；branch 名含 sanitize 字符（`\ / : * ? " < > |` 9 种）→ 被 `tr '/\\:*?"<>|' '_'` 抹平成同名 key，两个 git 分支合并到同一 BRANCH_SAFE 子目录，**后者写会覆盖前者**。**两种都落到独立子目录名，不会与正常分支子目录混**；用户反馈"看不到分支文档"时，先 `git -C "$GIT_ROOT" rev-parse --abbrev-ref HEAD` 看实际分支名 + 用 §3 表第 5 行的 sanitize 规则重算 BRANCH_SAFE 是否撞了别分支
5. **commit / 祖先双源硬约束**（两场景语义独立，合并理解）：

   - **`/icode doc` 生成时**：
     - **模块层** — `module_docs/<key>/_meta.json.current_commit` 不等于当前模块 commit → 全量重生成覆盖（`doc.md` 步骤 5「模块全量重生成」）
     - **工程层** — `git merge-base --is-ancestor <prev> HEAD` 退出 1（跨分支/分叉/fork）→ 按全量重生成处理（`<prev>` 与 `HEAD` 不在同一祖先链，`git diff` 不可信）
   - **段零检索时**（`dir_and_metadata.md`「段零·工程文档检索」段，含 stale 检测 + 分支校验 + 祖先校验）：
     - **分支不一致** — 整个工程文档 stale，降级注入只注摘要 + 警告，不读章节正文
     - **commit 不匹配** — 模块层降级注入；工程层走"祖先 + diff"校验，不直接判 stale（`dir_and_metadata.md`「段零·工程文档检索」段）

### 6. 正文索引（各机制在哪一行展开，需要时直接 Read）

| 机制 | 所在位置 | 关键句 |
|------|----------|--------|
| 工程 doc 分支子目录公式 | 正文 §「project_id 解析」第 1 步 BRANCH_SAFE 推导 | `DOC_DIR = ~/.claude/icode_data/project_docs/<id>/<BRANCH_SAFE>/` |
| 冲突检测（同名工程/分支实为不同 git_root） | 正文 §「project_id 解析」末尾 | 追加 hash 后缀，`PROJECT_ID` + `BRANCH_SAFE` + GIT_ROOT sha256 前 4 字符 |
| v1 → v2 自动迁移 | 正文 §5.0.6、§5.1 | 7 步迁移流程 + `_meta.json.v1_migrated_from` 备份 |
| 增量判定（跨分支按全量） | 正文 §3「增量判定」中祖先合法性校验 | `git merge-base --is-ancestor` 退出 1 → 按全量处理 |
| 模块 key 计算 | `dir_and_metadata.md`「module_docs key 计算」段 | `key = <url_basename_sanitized> + "_" + sha256(repo_url + ":" + branch)[:12]` |
| 模块 commit 推进触发覆盖 | `dir_and_metadata.md`「module_docs key 计算」段、正文 §5 模块全量重生成 | key 不含 commit，但 `current_commit` 不一致时按全量重生成覆盖 |
| 段零 DOC_DIR 分支过滤 | `dir_and_metadata.md`「段零·工程文档检索」段 | "按 `resolve_project_id(cwd)` 算出 `BRANCH_SAFE`，只读 `<DOC_DIR>` 子目录，不交叉读其他分支子目录" |
| 段零 stale 检测（分支+祖先双校验） | `dir_and_metadata.md`「段零·工程文档检索」段（stale 检测 + 分支校验 + 祖先校验） | 分支不一致 → 整个工程文档 stale；祖先不合法 → 走"祖先 + diff"校验，不直接判 stale |
| 跨工程/跨分支上下文借鉴边界 | `dir_and_metadata.md`「段零·工程文档检索」段 | 跨工程借鉴仅看 `00_overview.md`，不读其他章节 |

### 7. 嵌入式入口（写代码时随时可调用，别忘了）

- **本文档「project_id 解析」段**（行 93-133，下方 `git -C "$GIT_ROOT" rev-parse --abbrev-ref HEAD` 立即拿到当前分支名 + BRANCH_SAFE 推导）
- **`dir_and_metadata.md` 「project_docs 工程文档库」段 + 「module_docs 工程模块库」段 + 「段零·工程文档检索」段**
- **`dir_and_metadata.md`「段零·工程文档检索」段**（段零 AI 必读）

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
    echo "❌ 错误：cwd 不在 git 仓库或 repo 管理的项目内"
    echo ""
    echo "💡 解决方案："
    echo "   /icode doc 必须在 git 仓库或 repo 管理的项目根目录下运行"
    echo "   1. 检查当前目录：pwd（确认你在工程根目录）"
    echo "   2. 如果不在 git 仓库：cd 到 git 仓库根目录"
    echo "   3. 如果不在 repo 管理项目：使用 Google repo 工具管理（或 cd 到 .repo/ 所在目录）"
    echo "   4. 如果工程根不在 cwd：cd <工程根> 后再跑 /icode doc"
    exit 1
  fi
fi
# worktree 归一化（F1）：worktree 内 .git 是普通文件（gitdir 指针）= git worktree 成员，
# project_id 须归一到主仓根（跨 checkout 共享依据）；GIT_ROOT 保持当前 checkout 根不动（分支判定/代码实证用）
if [ -f "$GIT_ROOT/.git" ]; then
  MAIN_ROOT=$(git -C "$GIT_ROOT" worktree list --porcelain | awk '/^worktree /{print substr($0,10); exit}')
else
  MAIN_ROOT=""
fi
if [ -n "$MAIN_ROOT" ]; then PROJECT_ID=$(basename "$MAIN_ROOT"); else PROJECT_ID=$(basename "$GIT_ROOT"); fi
# 分支感知：DOC_DIR 按 <project_id>/<branch> 分目录，**切分支跑 doc 不污染其他分支**
# detached HEAD / 非 git 仓库 → BRANCH="(detached)" 或 BRANCH="(no-git)"
BRANCH=$(git -C "$GIT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)
[ -z "$BRANCH" ] && BRANCH="(no-git)"
[ "$BRANCH" = "HEAD" ] && BRANCH="(detached)"
# 分支名 sanitize（去除路径分隔符、特殊字符，避免破坏目录结构）
BRANCH_SAFE=$(echo "$BRANCH" | tr '/\\:*?"<>|' '_')
DOC_DIR="$HOME/.claude/icode_data/project_docs/$PROJECT_ID/$BRANCH_SAFE"
```

**冲突检测**：`$DOC_DIR` 已存在时读其 `00_overview.md` 元信息块的 `project_path`——与当前 `GIT_ROOT` 一致则复用；不一致（同名分支但不同工程）则追加短 hash 后缀 `${PROJECT_ID}__${BRANCH_SAFE}__$(echo $GIT_ROOT|sha256sum|cut -c1-4)`，输出 ℹ️ 一行提示。**同一工程不同分支**（如 `myproject/main/` vs `myproject/feature/`）按分支目录天然隔离，**互不覆盖**；**detached HEAD / 非 git 工程**落到 `(detached)` / `(no-git)` 目录，单独记录。`PROJECT_TYPE`（`git-root`/`repo-root`）写进工程 _meta.json。

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

**Google `repo` 嵌套子项目路径推导**（dir_and_metadata 表未含，doc.md 步骤 2 独有）：当工程由 Google `repo` 工具管理时，子项目**不一定是 `${GIT_ROOT}/<module_name>` 字面路径** —— 部分模块会嵌套在业务父项目子目录下（如 `<业务父项目 A>/<模块 A1>` / `<业务父项目 B>/<模块 B1>` 等典型模式）。**禁止用字面 `${GIT_ROOT}/<module_name>` 判定 path**，否则会误判 `path_gone`。正确做法：

```bash
# 1. 优先从 .repo/projects/*.git worktree 路径（不受 maxdepth 限制，最权威）
#    .repo/projects/<name>.git 是 git bare 仓库，路径即 manifest <project path> 的真实位置
worktree_path="${GIT_ROOT}/.repo/projects/<module_name>.git"
if [ -d "$worktree_path" ]; then
  realpath "$worktree_path"
fi

# 2. fallback: 从 manifest 解析 <project path="..."> 字段（manifest 缺 path 字段时无输出）
python3 -c "
import xml.etree.ElementTree as ET
m = ET.parse('${GIT_ROOT}/.repo/manifest.xml').getroot()
for p in m.findall('project'):
    if p.get('name') == '<project_name>':
        path = p.get('path')
        print(path if path else 'NO_PATH_ATTR_FALLBACK_TO_FIND')
"

# 3. fallback: find -maxdepth 3 全局搜索（兼容 monorepo + 嵌套 >2 层场景）
#    不建议用 maxdepth 1（漏检嵌套）+ 慎用 maxdepth 4+（易误中辅助目录）
find "${GIT_ROOT}" -maxdepth 3 -name "<module_name>" -type d
```

**优先级与回退**：

1. **首选方案 1**：`.repo/projects/<name>.git` 是 git bare 仓库，路径即 manifest `<project path>` 字段的真实位置——**不受 maxdepth 限制、不依赖 manifest 字段、不依赖 grep/find 遍历**，是嵌套任意层都正确的终极 fallback
2. **次选方案 2**：manifest 解析；若 manifest 缺 `path` 属性（早期 manifest.xml 简写模式可能省略），输出 `NO_PATH_ATTR_FALLBACK_TO_FIND` 提示，AI 立刻知道要跳到方案 3
3. **末选方案 3**：`find -maxdepth 3` 兜底（覆盖嵌套 ≤3 层）；若仍找不到 → 报错"嵌套深度超 3 层或路径异常，请用户人工指定"

**触发条件**：用户工程用 Google `repo` 管理，且业务上把多个 git 子项目按业务域分组到父项目目录（典型模式：`<业务域分组目录>/<模块名>`，如测试设备组 / 传感器组 / 网络管理组等业务分组容器）。`/icode doc` 检查 / 段零检索时遇到 "path_gone" 但 `find -maxdepth 3` 能找到 → 即嵌套场景，path 字段需补全为真实嵌套路径。

若返回多条结果，优先取 `.repo/projects/*.git` 中同名 entry 的 worktree 路径（最权威）；若无 `.repo`（如纯 monorepo），按 README + `.gitignore` 综合判别。**写入 `_meta.json.module_deps[].path` 字段时必须用真实嵌套路径**（不是字面 `<module_name>`），否则后续段零检索 / git diff 锚点校验会因 path 不匹配而失效。

**去重**：详见 dir_and_metadata.md 同段「去重」两步（先按归一化绝对路径合并 + 再按 `key = <url_basename_sanitized>_<sha256(url+":"+branch)[:12]>` 去重，key 格式含模块名前缀便于人眼辨认，见 [dir_and_metadata.md](../references/dir_and_metadata.md)「module_docs key 计算」）。输出 modules 列表（每个含 url+branch+key+commit+path+type），写进工程 _meta.json 的 `module_deps` 字段。

**代码特征扫描**（grep 优先）：用 Grep 扫描工程代码特征识别 entry 函数/导出 API/关键数据结构，结果作为 00_overview.md「核心模块清单」+「全栈图」输入，按本表「动态章节」段（doc_template.md「五」）决定追加哪些章节（AI 根据工程实际技术栈选 grep 模式，**不硬编码框架名**）。汇总「章节规划清单」：固定（00/10/90/99）+ 命中的动态章节。

### 3. 增量判定（非全量时）

- `$DOC_DIR` 不存在 → 首次全量
- 存在 → 读 `00_overview.md` 的 `generation_commit`，**先做祖先合法性校验**（关键，防跨分支误判增量）：
  - **HEAD 与 prev 是同一 commit**→"已是最新"
  - **`git merge-base --is-ancestor <prev> HEAD` 退出 0**（prev 是 HEAD 祖先，正常前向演进）→`git diff <prev>..HEAD --name-only` 拿变更文件，命中某章 KEYS"文件位置"的章增量重生成，其余跳过；变更文件在未覆盖目录→提示"是否生成新章节"
  - **退出 1（HEAD 是 prev 的祖先/分叉 / prev 与 HEAD 在不同分支）**→**`git diff` 不可信，必须视为"另一版工程"，按全量重生成处理**（不分青红皂白继续增量会因 merge-base 远导致 diff 失真，把大部分文件当变更/漏判关键变更），提示用户「prev `<prev>` 与 HEAD `<HEAD>` 不在同一祖先链（分支切换/fork/换库），按新工程全量重生成」
  - **退出 128（prev 不可达，如 GC/换库）**→按全量重生成处理
- **模块依赖变化检测**（关键，否则新增/删除依赖漏生成）：比较旧 `_meta.json.module_deps` 与步骤 2 检测的新 modules 列表 → 差异：
  - 新加的 dep（key 不在旧列表）→ 触发对应 module_docs 生成
  - 删除的 dep（旧列表有但新列表无）→ 提示「工程不再依赖 X，module_docs/{key}/ 是否保留（默认保留，需手动清理）」
  - key 变化的 dep（url 或 branch 变）→ 触发新 key 的 module_docs 全量重生成 + 旧 key 提示是否清理

- **首次全量 + 模块名参数**（`$DOC_DIR` 不存在但用户给了模块名）-> 该模块名作为"聚焦生成目标"，而非"匹配已有章节"（首次无章节可匹配，原匹配语义悬空）：
  - 该模块是独立仓库 -> 生成该模块 module_docs + 工程固定章节（00/90/99）+ 命中动态章节，其中 `10_architecture` 描述该模块在工程拓扑中的角色（不重复 module_docs 内部）
  - 该模块非独立仓库 -> 生成 `project_docs` 该模块章节 + 固定章节（00/90/99）
  - 其余章节按代码特征自适应生成；**不因用户只点一个模块而省略固定章节**（00/90/99 永远生成）

### 4. 强制思考前置（不可跳过）

**必须按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行三件套 Read + Read [references/doc_template.md](../references/doc_template.md) 完整内容**（doc 模板多 Read 场景）。子项（≥4步）= 扫描结果分析 → 章节规划 → 元信息字段准备 → 风险评估（手动编辑/冲突）。

### 5. 生成 module_docs（依赖）+ 工程章节

#### 5.0 质量审视与模板版本迁移（v2 新增，**必经子步骤**）

> 每次跑 `/icode doc` 前必须先执行本子步骤，**不通过则不能跳过**——是模板升级后保持文档质量一致性的关键机制。

**5.0.1 读 SCHEMA_VERSION**

从 [doc_template.md](../references/doc_template.md) 顶部 `<!-- SCHEMA_VERSION: ... -->` 注释读取当前模板版本号（如 `v2.0.0`）。

**5.0.2 扫描现有章节**

对 `$DOC_DIR` 与各 `$HOME/.claude/icode_data/module_docs/<key>/` 下所有 `*.md` 章节（不含 `_meta.json`）：

1. 读每章 `_meta.json.template_version`（缺失视为 `v1`）
2. 读每章正文元信息块 `template_version` 字段（缺失视为 `v1`，但若 `_meta.json` 有则取 `_meta.json`）
3. 对比 SCHEMA_VERSION：

| 章节状态 | 动作 |
|----------|------|
| `template_version >= SCHEMA_VERSION` 且通过 [doc_template.md](../references/doc_template.md)「十、质量审视检查清单」达标率 ≥ 0.9 | 跳过，无需重生成 |
| `template_version >= SCHEMA_VERSION` 但达标率 0.7-0.9 | 标记"局部补全"，增量补全缺失元素（不重生成全文） |
| `template_version < SCHEMA_VERSION`（含缺失/视为 v1） | 标记"待升级"，强制全量重生成（**不走"局部补全"路径**，模板大版本变更必须整体重生成） |
| 达标率 < 0.7（任何版本） | 标记"待升级"，全量重生成 |

**5.0.3 确认门（必经）**

对所有标记为"待升级"的章节，**先检测手动编辑**（读每章元信息块"章节生成时间" T_gen，对比文件 mtime；mtime 晚于 T_gen → 被手动改过）：

- 无手动编辑 → 直接重生成（无需询问用户，模板升级是 doc 步骤的常规行为）
- 有手动编辑 → **禁止静默覆盖**，提示询问（同全量确认门三选一：全量覆盖 / diff 后人工合并 / 跳过这些章只升级其余）

**5.0.4 升级后写新版本号**

重生成完成的章节必须：
- 正文元信息块写入 `template_version: v2.0.0`（或当前 SCHEMA_VERSION）
- `_meta.json` 同步写入 `template_version` 字段

**5.0.5 进度输出**

- 步骤 7 末尾输出"模板迁移汇总"表（见下文 §7 更新）

**5.0.6 项目目录布局迁移（v1 → v2，仅升级时触发一次）**

> **重要**：本节与 5.0 "模板版本迁移"是**两个不同维度**——
> - 5.0 是章节**内容**升级（template_version v1 → v2，章节内容随之重生成）
> - 5.0.6 是工程**目录布局**升级（`project_docs/<id>/00_overview.md` 平铺 → `project_docs/<id>/<branch>/` 多分支子目录），**整库只发生一次**（v1 → v2 升级时），完成后永久不再触发
>
> **触发条件**：`ls project_docs/<id>/00_overview.md` 直接存在 + 无分支子目录
>
> **迁移 7 步**：
> 1. `mkdir -p project_docs/<id>/<BRANCH_SAFE>/` 创建新分支子目录
> 2. `mv project_docs/<id>/00_overview.md project_docs/<id>/<BRANCH_SAFE>/` 移动所有章节
> 3. 复制所有 `_meta.json` 字段到新位置（含 `module_deps` / `unresolved_modules` / `stale_files` / `project_id` / `project_type` / `git_root` 全集）
> 4. 旧 `project_docs/<id>/_meta.json` 备份为 `_meta.json.v1_migrated_from`（**禁止直接覆盖**）
> 5. 删旧空目录（`rmdir project_docs/<id>/`；如非空说明漏迁章节须重试）
> 6. 输出 ℹ️ 提示行说明已迁移
> 7. 段零检索时若 `<id>/<branch>/` 不存在但 `<id>/` 直接有 `00_overview.md`（v1 布局）→ **回退按 legacy 方式读** + 输出 ⚠️ 提示「v1→v2 迁移未完成，下次 `/icode doc` 自动迁移」
>
> **禁止**：
> - ①检测到 v1 旧布局但不迁移，直接写到新子目录 → 旧章节成孤儿
> - ②迁移时丢字段（如漏 `module_deps`）→ 模块依赖信息丢失
> - ③删旧目录不留备份 → 无法回退
> - ④段零检索时 v1 布局不读 → 用户工程再也看不到旧章节

#### 5.1 正常生成流程（用户意图驱动）

**先生成 module_docs**（依赖先于依赖者，用户闲时跑不省 token，模块文档可详细完整生成）：

**module_docs 生成范围（按用户意图聚焦，避免全量 token 爆炸）**：

- **用户指定模块名** -> 聚焦该模块（其余检测到的独立仓库模块标 `generated: false` 写入 `module_deps`，不生成文档，末尾汇总提示"其余 N 个模块未生成 module_docs，可 `/icode doc <name>` 按需生成"）：该模块是独立仓库 -> 生成该模块 module_docs；该模块非独立仓库 -> 不生成 module_docs（该模块走下方「再生成工程自身章节」生成 project_docs 章节）
- **无模块名（全量）**→ 检测到的所有模块写入 `module_deps`（可读模块 `generated: true`）；module_docs 只生成"代码已本地化可读"的（git submodule / `repo` 子项目 / monorepo / vendor），不可读的（如 CMake FetchContent build 目录未下载）标 `unresolved_modules`
- **反例（禁止）**：**不得因数量大而全部降级、把模块内容塞进 `project_docs` 章节替代 module_docs**（历史 bug：某工程 34 个 `repo` 子项目时 AI 把模块塞进 project_docs 章节，module_docs 一个没生成）；全量时可读模块数量过多（如超过 10 个）时，应**分批生成或提示用户分次 `/icode doc <name>` 聚焦**，而非降级塞 project_docs

- 对每个**待生成**的 module（按上述范围筛后的列表，非全量 modules）：
  - 有远程 URL 的模块（git submodule / `repo` 子项目）：先调 `mcp__cheap-research__fetch_remote` 拉取其远程 README（从模块 `repo_url` 推导 raw 文件 URL，如 GitHub 类平台 `{repo_url_raw}/HEAD/README.md`；**SSH 格式 `git@` 需转为 HTTPS**）。返回内容作为模块文档生成的参考输入。**降级**（fetch_remote 不可用/URL 格式不兼容/SSRF 拦截）：跳过，仅靠本地代码生成，不阻塞。**不接管决策**：模块文档内容仍由主代理基于本地代码 + 远程 README 参考综合生成，fetch_remote 只提供额外参考源。
  - 克隆/读取该 module 的代码到临时目录（git submodule 用 `git submodule foreach 'git archive HEAD | tar -x -C $tmp/<name>'`；repo 子仓库用 `cd <submodule_path> && git archive HEAD | tar -x -C $tmp`；monorepo/vendor 直接读子目录；CMake FetchContent 通常 build 目录未下载，fallback 警告）
  - 按 [doc_template.md](../references/doc_template.md)「九、模块章节模板」+「十四项必含元素」生成章节（前 50 行四块 + 必含元素清单 + 模板自适应 grep 表；KEYS 按 doc_template.md「七」提取）
  - Write 到 `$HOME/.claude/icode_data/module_docs/<key>/<NN>_<topic>.md`（十位桶）
  - **读 `module_docs/<key>/_meta.json`（如存在）→ 提取现有 `used_by` → 与本工程（按 `project_id` 标识）合并去重**（避免 B 工程生成时覆盖 A 工程的引用）
  - 写 `_meta.json`（`repo_url` / `branch` / `current_commit` / `used_by` = 合并去重后的列表 + **`template_version: v2.0.0`**）
  - 检查 `<key>/_meta.json` 的 `current_commit` 与当前模块 commit 是否一致：一致跳过（已是最新），不一致→**该 module 全量重生成**（单 module 文档量小，全量合理；不需增量 diff，简化逻辑）
- 拉取失败→**不写**该 dep 到工程 `_meta.json.module_deps`（避免段零检索时查不到对应 `module_docs/<key>/` 漏匹配）；在工程 `_meta.json.unresolved_modules` 数组里记录 `{name, type, reason, attempt_at}`（`reason` 如"CMake FetchContent build 目录未下载"）；不拖垮（其他 module 继续）

**再生成工程自身章节**（依赖者引用依赖者，含"依赖子模块"字段）：

- **v1 → v2 自动迁移（关键，兼容现有旧数据）**：写入 `$DOC_DIR/_meta.json` 前先检测旧 v1 单级布局：
  - 若 `~/.claude/icode_data/project_docs/<PROJECT_ID>/` 目录下**直接平铺** `00_overview.md` / `*.md` / `_meta.json`（无分支子目录）→ 是 v1 旧布局，**必须先迁移再写**：
    1. `mkdir -p $DOC_DIR`（即 `<project_id>/<BRANCH_SAFE>/`）
    2. 把旧 `<project_id>/*.md` 全部 `mv` 到 `$DOC_DIR/`
    3. 把旧 `<project_id>/_meta.json` `mv` 到 `$DOC_DIR/`
    4. 读旧 `_meta.json` 的 `project_type` + `git_root` + `module_deps` + `unresolved_modules` + `stale_files`（**全部字段不丢**），按 [dir_and_metadata.md](../references/dir_and_metadata.md)「工程 _meta.json 模板」格式重写到 `$DOC_DIR/_meta.json`（字段集：`project_id` + `project_type` + `git_root` + `branch` + `head_commit` + `module_deps` + `unresolved_modules` + `stale_files` + `template_version`）
    5. 旧 `<project_id>/` 目录留空后 `rmdir` 删除
    6. **留迁移追踪**：删旧 `_meta.json` 前先 cp 到 `$DOC_DIR/_meta.json.v1_migrated_from`（防回退）
    7. 输出 ℹ️「v1→v2 迁移完成：`<id>/` 单级布局 → `<id>/<branch>/` 多分支布局，N 个章节迁移成功，旧数据备份在 `_meta.json.v1_migrated_from`」
  - 若 `$DOC_DIR` 已存在（即分支子目录已建）→ 跳过迁移，走正常生成路径
  - **v1+v2 混合布局边缘**（罕见：用户已手动 mkdir 创建 `<id>/<branch>/` 但旧 `<id>/` 还有平铺章节）：检测到**同时存在**两个布局 → **不静默决定**：①自动备份 v1 平铺的 `<id>/*.md` + `<id>/_meta.json` 到 `<id>/_v1_legacy_backup_<timestamp>/`；②询问用户「检测到混合布局：v1 平铺的章节会被移到 `v1_legacy_backup_<timestamp>/` 子目录且不在段零检索范围，**v2 `<id>/<branch>/` 才是新主目录**。确认迁移？」→ 用户确认后按 v1→v2 路径正常处理；用户拒绝则按 v2 已存在路径处理（v1 数据进 backup 目录不参与检索）
  - **禁止**：直接写入新布局但**不迁移旧布局**——会让旧章节留在 `<id>/` 目录孤立、再下次 `/icode doc` 时被强制清空或判为"未迁移的孤儿"，数据丢失
- 对每个待生成章节：读相关代码（Read/Grep）→ 按模板写正文（含十四项必含元素）→ 生成前 50 行四块（含「依赖子模块」+「关联工程」字段 + **`template_version`**）→ Write 到 `$DOC_DIR/<NN>_<module>_<topic>.md`（十位桶，新增取 `max(NN)+1`）
- **00_overview「关联工程」字段必填**（工程级，其他章节元信息块填同值）：从「工程定位与产品族」章节提炼姊妹/同族工程标识，优先填 project_id（即 `project_docs/` 目录名），不知目录名可填工程名/产品代号（段零模糊匹配兜底）；无关联填"无"
- 写 `_meta.json`（`project_id` / `project_type` / `git_root` / **`branch = git rev-parse --abbrev-ref HEAD`** / **`head_commit = git rev-parse --short HEAD`** 显式持久化分支与提交，**下游段零借鉴时用这两个字段比对当前 cwd 的 HEAD/分支是否还匹配，跨分支直接 stale 不注入正文，避免误导** / `module_deps` 含所有检测到的可读模块（已生成 `generated: true`、按需未生成 `generated: false`，见上「module_docs 生成范围」）/ `unresolved_modules` 含拉取失败的模块 / **`template_version: v2.0.0`** / **`stale_files`** —— **保留既有 stale_files 字段**，步骤 8 主动 stale 扫描会刷新；不要因为新增 template_version 而丢失 stale_files 数据，导致段零 stale 检测失效）
- 单章失败→标记不拖垮

### 6. 代码事实审计（`99_code_facts_audit.md`，永远生成）

增量+并行（见 [doc_template.md](../references/doc_template.md)「六」）：首次并行子代理验证全库 file:line；增量只重验变更文件相关引用（**含一跳联动**：grep 变更文件调用的符号，把被引用的未变更文件也纳入重验，缩小语义联动盲区）。**已知盲区**：未变更文件因他处变更而语义变化时（二跳及以上联动）增量审计不重验，残余过时由段零 stale 检测 + 不盲信约束兜底（见 [dir_and_metadata.md](../references/dir_and_metadata.md)）。子代理失败→该批标 `[未验证-子代理失败]`。**并行子代理等待按 [subagent_spawn_wait.md](../references/subagent_spawn_wait.md) 通用契约**（后台 spawn + `TaskOutput` 阻塞等 + `INTEGRATION_WALL_CLOCK_DEADLINE_SECONDS=1200` 墙钟硬截止，禁止裸同步 spawn / 被动等通知 / 无限等待）。

### 7. 进度输出（阶段级 + 末尾汇总）

```text
▶ /icode doc myproject
[1/5] 扫描代码特征... ✓ 识别 N 个章节候选
[2/5] 模板版本审视... ✓ 发现 M 个旧版本章节（当前模板 v2.0.0）
[3/5] 生成章节 [8/12]... (当前 <章节名>)
[4/5] 代码事实审计 [验证 45/120 引用]...
[5/5] 主动 stale 扫描... ✓

✓ 完成。汇总：
| 章节 | 状态 | 模板版本 | 备注 |
|------|------|---------|------|
| 00_overview.md | 新增 | v2.0.0 | - |
| <章节名> | 模板升级 | v1 → v2.0.0 | 14 项必含清单检查：12/14 通过 |
| <章节名> | 增量更新 | v2.0.0 | 3 处 file:line 重验 |
| 99_code_facts_audit.md | 失败 | v2.0.0 | 子代理超时，重跑 |

模板迁移汇总（v2 新增）：
| 工程 | 旧版本 | 新版本 | 升级章节 | 跳过章节 | 状态 |
|------|-------|-------|---------|---------|------|
| myproject | v1 | v2.0.0 | 9 章 | 2 章（手动编辑跳过） | 升级成功 |
| another_project | v2.0.0 | v2.0.0 | 0 章 | 11 章 | 无需升级 |

未生成模块 N 个（unresolved_modules）：<name1> (<reason1>), <name2> (<reason2>)... — 重跑 /icode doc 时自动恢复
```

**模板迁移汇总表说明**（v2 新增）：
- **升级章节**：本轮从旧版本升级到 `v2.0.0` 的章节数
- **跳过章节**：检测到手动编辑且用户选"跳过这些章只升级其余"的数量
- **状态**：升级成功 / 部分跳过 / 失败（失败章节数）

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
- **工程本地路径（project_path）**：`$GIT_ROOT` 绝对路径（段零 3.6 跨工程源码定位用，见 [dir_and_metadata.md](../references/dir_and_metadata.md)「段零·工程文档检索」步骤 3.6；步骤 2 冲突检测亦读此字段判同名分支是否同工程）；非 git 工程填空串
- **Git 地址**：`git -C "$GIT_ROOT" remote get-url origin`（无 remote 填"无 remote"）
- **分支/提交**：`git rev-parse --abbrev-ref HEAD` + `git rev-parse --short HEAD` + `git log -1 --format=%ci`
- **项目类型 / 模块类型**：`PROJECT_TYPE`（`git-root` 或 `repo-root`），或模块的 `MODULE_TYPE`（`git-submodule`/`repo`/`cmake`/`monorepo`/`vendor`/`user`）
- **子模块（git submodule，v1 字段）**：读 `.gitmodules`（无则"无子模块"）+ `git submodule status`，每行 `path → url @ commit`
- **依赖子模块（按仓库+分支）**：从工程 _meta.json 的 `module_deps` 列表取，章节元信息块格式 `module_a → module:module_a@main@a3f2b1c（module_docs/<key>/）`
- **被工程引用（模块章节 used_by）**：从模块 _meta.json 的 `used_by` 列表取（如 `myproject（git-root）, another_project（repo-root）`）
- **产品线/型号**：grep 工程的产品型号宏/配置，推断不出填"未识别"
- **关联工程（段零跨工程检索用）**：从工程「产品线/型号」+「工程定位与产品族」章节提炼姊妹/同族工程的 project_id（即 git 仓库根 basename，对应 `project_docs/` 目录名），多个逗号分隔；推断不出填"无"。段零据此检索关联工程 00_overview 作为参考候选
- **章节归属模块**：与文件名 `<module>` 一致；跨模块章节填 `null`
- **章节生成时间**：运行时 `date +%Y-%m-%dT%H:%M:%SZ`，**禁止写死**
- **模板版本（v2 新增）**：`template_version`，与 [doc_template.md](../references/doc_template.md) 顶部 `<!-- SCHEMA_VERSION: v2.0.0 -->` 一致；**写入章节正文元信息块 + `_meta.json`**（双写防丢失）。缺失或 < 当前 SCHEMA_VERSION → 视为旧版本章节，触发模板版本迁移

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
- 每章前 50 行四块齐全 + `template_version: v2.0.0` 字段写入
- 14 项必含元素（按章节类型）达标率 ≥ 70%
- `00_overview.md` 元信息块含当前 HEAD 的 `generation_commit` + 完整子模块列表 + `template_version`
- `99_code_facts_audit.md` 已生成（部分失败也标明）
- 工程 _meta.json.stale_files 已刷新（步骤8 主动 stale 扫描，段零检索据此跳过过时章节）
- 模板迁移汇总表输出（v2 新增，详见步骤 7 末尾）
- 末尾汇总表输出

## 衔接与可重复

- **段零消费**：`/icode init`/`log`/`plan`/`start`/`fast` 启动时段零自动检索（见 [dir_and_metadata.md](../references/dir_and_metadata.md)「段零·工程文档检索」段）；**doc 自身不写 `_inject_cache.json`**（工单目录缓存，doc 不创建工单）
- **可重复**：多次 `/icode doc` 覆盖更新，手动编辑受确认门保护
## MCP 推荐（强证据二元化）
| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| vision-bridge | 🟢* | 截图分析--用户给图时 |
| **cheap-research** | 🟢* | **降本甜点**：audit_facts（代码事实审计）+ fill_template（章节+进度模板）+ scan_modules（6 级模块识别）+ scan_patterns/diff_summary（增量判定）+ parse_project_id + fetch_remote（拉远程依赖 README 作模块文档输入）。不接管决策：意图识别走主会话 |
| context7 | ⚪ | 本步骤不推荐 |
| memory | ⚪ | 本步骤不推荐 |
| playwright | ⚪ | 本步骤不推荐 |

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
