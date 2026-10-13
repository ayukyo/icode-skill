# 步骤 doc — 工程级知识库生成（独立步骤，不参与 1~6 流程推进）

**命令**: `/icode doc [自然语言]`
**产出**: `~/.claude/icode_data/project_docs/<project_id>/<branch>/*.md`（分支子目录隔离，**切换分支跑 doc 不互相覆盖**；章节自带身份证）
**会话**: 主会话
**定位**: **工程级知识库生成与维护，独立步骤**。不创建 `.icode_output_N/`、不写 `.ico_metadata.json`、不更新工单 `completed_steps`/`status`。知识库供 `/icode init`/`log`/`plan`/`start`/`fast` 启动时**段零检索**自动注入。

> **核心设计哲学**（必须先 Read [references/dir_and_metadata.md](../references/dir_and_metadata.md)「project_docs 工程文档库」段 + [references/doc_template.md](../references/doc_template.md)）：不要求用户维护工程专属配置；章节前50行承载检索信息，源码身份、正文SHA和发布状态由元信息/工作清单核对。

> **共享技能路由**：语料接入前读取 [references/skill_routing.md](../references/skill_routing.md)，只加载命中当前资料类型的技能；邮件正文、线程、表格、图片或附件先走 `email-evidence-intake`，再复用文档/媒体/表格/证据/原理图能力。
> **工程接入门**：读取 [references/project_intake.md](../references/project_intake.md)。唯一嵌套仓的知识库绑定实际 Git 根；large/huge 仓先按 tracked paths、活动构建配置和目标模块建立 worklist，代码语义画像禁止默认全树内容扫描。版本捕获另按实际输入流式计算内容SHA，开销随未忽略输入总量增长，不把哈希扫描宣称为全树语义分析。

## 多工程、多分支与工作区版本（执行前必读）

读取 [doc_snapshot_contract.md](../references/doc_snapshot_contract.md) 全文；它是版本、存储、依赖绑定、缓存和续跑的唯一真源。正文模板另读 [doc_template.md](../references/doc_template.md)，不把存储版本当模板版本。

| 维度 | 身份与行为 |
|---|---|
| 工程 | project_id与原始分支；实际checkout根用于源码读取，worktree不替换主工程身份 |
| 模块族 | 原URL+分支复合key，保留既有目录键 |
| 源码版本 | 完整HEAD+当前工作区内容指纹+已声明依赖身份；同HEAD的dirty变化也形成不同版本 |
| 文档修订 | snapshots/<snapshot_id>/不可变正文与审核SHA；多个工程可分别绑定不同快照 |
| 旧平铺库 | 保留正文和未知元信息，作为旧版概览/合并材料；不得自动当精确工作区快照 |

当前工作区是默认来源，不用git archive HEAD漏掉未提交改动。相同族键不同源码身份不去重；不匹配版本不注入正文。先读取旧基线，完成草稿、语义审计与发布，再回写工程依赖和done状态。用户已给定的全量、合并、跳过或暂停选择持续有效。

分支目录名保留可读sanitize结果；原始分支含需替换字符时追加原始分支SHA前8字符，避免feature/a与feature_a撞名。已有旧分支目录不删除；仅在其元信息原始分支精确匹配时作为legacy来源，不跨分支读取正文。detached与普通分支分开，并继续核对完整HEAD/内容身份。

工程内部非独立Git模块写工程章节；独立Git模块先生成模块快照，再写它在工程拓扑中的角色。正文不重复，依赖身份不能省略。

## 前置校验

1. 请求路径必须先经 `project_intake.md` 解析；解析后的 cwd 必须在 git 仓库或 `repo` 管理的项目内：
   - `git rev-parse --show-toplevel` 成功 → git-root 模式
   - 请求路径自身不在 Git 仓、但深度 2 内仅一个 Git 根 → 切到该根后进入 git-root 模式
   - 否则从 cwd 向上逐级 `test -d $d/.repo`，首个命中 → repo-root 模式（Google `repo` 工具管理的多仓库项目如独立子仓库组成的超级项目）
   - 都失败 → 报错"请在 git 仓库或 `repo` 管理的项目内运行 /icode doc"
2. 全局目录 `~/.claude/icode_data/project_docs/` 和 `~/.claude/icode_data/module_docs/`（首次自动创建）

## project_id 解析

```bash
# 前置：已按 project_intake.md 把 cwd/workdir 切到解析后的实际工程根
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
    echo "   4. 如果给的是容器目录：按 project_intake.md 解析唯一嵌套根；多根时显式选择后重试"
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
BRANCH_SAFE=$(printf '%s' "$BRANCH" | tr '/\\:*?"<>|' '_')
if [ "$BRANCH_SAFE" != "$BRANCH" ] || printf '%s' "$BRANCH_SAFE" | grep -Eiq '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$'; then
  BRANCH_HASH=$(printf '%s' "$BRANCH" | sha256sum | cut -c1-8)
  BRANCH_SAFE="${BRANCH_SAFE}_${BRANCH_HASH}"
fi
DOC_DIR="$HOME/.claude/icode_data/project_docs/$PROJECT_ID/$BRANCH_SAFE"
```

以上Bash为算法示例；Windows宿主按PowerShell或Python等价执行，使用系统实际home，不要求设置HOME环境变量。分支目录采用同一跨平台规则，Windows设备名也追加原始分支指纹；保留原始分支字段，不把目录名当分支身份。

**冲突检测**：`$DOC_DIR` 已存在时读其 `00_overview.md` 元信息块的 `project_path`——与当前 `GIT_ROOT` 一致则复用；不一致（同名分支但不同工程）则追加短 hash 后缀 `${PROJECT_ID}__${BRANCH_SAFE}__$(echo $GIT_ROOT|sha256sum|cut -c1-4)`，输出 ℹ️ 一行提示。**同一工程不同分支**（如 `myproject/main/` vs `myproject/feature/`）按分支目录天然隔离，**互不覆盖**；**detached HEAD / 非 git 工程**落到 `(detached)` / `(no-git)` 目录，单独记录。`PROJECT_TYPE`（`git-root`/`repo-root`）写进工程 _meta.json。

## 意图识别（去参数化）

AI 解析自然语言的「目标工程」+「动作」：

- **目标**：无描述→全局扫描（列各工程 stale 状态 + 建议动作，用户选）；路径→`git rev-parse` 解析 git 根（路径相对根部分仅过滤模块，不改 project_id）；工程名→`project_docs/` basename + `00_overview.md` 工程名匹配（多命中问、零命中用 cwd）；模块名→按 cwd 定 project_id 后，**先判定该模块是否为独立 git 仓库**（查 `.repo/projects/<name>.git`、`.gitmodules`、`CMakeLists.txt` 的 `FetchContent_Declare`，方法同步骤 2 模块检测）：**是独立仓库**→生成/更新该模块 `module_docs/<key>/`（聚焦），工程 `project_docs` 仅补"该模块在工程 IPC 拓扑中的角色"上下文章节（不重复 module_docs 内部细节，职责划分见 [doc_template.md](../references/doc_template.md)「九 · 职责划分」）；**非独立仓库**（工程内普通子目录）→生成/更新 `project_docs` 中该模块章节。两者均按 KEYS 匹配已有章节做增量判定（首次无章节可匹配时见步骤 3「首次全量 + 模块名参数」）
- **动作**：全量词（重新生成/全量/从头）→全量重生成（**确认门**）；增量词（检查/刷新/更新）→增量；新增词（加/补充）→新章节；只读词（只看/列一下）→扫描不写；无动作词→默认增量
- **歧义必问**：目标明确无动作词但 git diff 命中 ≥50% 章节→问"全量/增量N个/仅列"；"加 X"但 X 已存在→问"覆盖/新建/合并"；完全模糊→列候选工程让选
- **独立仓库模块默认不问**：模块名命中独立 git 仓库时，默认按 "module_docs + 工程上下文章节" 互补生成（见「目标」+ 步骤 5），**不强制三选一问询**（判定规则已定死 "独立仓库→两者互补"，问询徒增交互成本）；**仅当用户动作词明确表达 "只要模块本身"**（如 "只看/仅模块/不要工程上下文"）时→跳过工程上下文章节，只生成该模块 module_docs

## 手工内容保护与生成范围

先读所有将继承/合并的旧章节及元信息。已有正文SHA时按内容比较检测编辑；mtime只提示迁移/编辑可能性，无SHA时保守核查，不能把mtime较晚直接说成手改或声称N处修改。

新快照保留旧版本，不原位覆盖。旧人工备注仍须按用户既有选择逐章合并或保留；用户已授权全量/合并/跳过时不重复问同一事项。未有选择且新阅读版本会遗漏人工内容时，准备具体diff后询问合并/保留/跳过。模板升级同样先审旧内容，失败不推进旧基线。

对工程平铺章节的更新：写前比较旧SHA、保留原内容和未知字段，逐章合并，临时文件原子替换；跳过章保留其源/模板版本，不能升级整个工程的last_generated_commit掩盖剩余待办。

## 执行流程

### 1. 解析 project_id + 意图（见上）

### 2. 模块检测 + 代码特征扫描（先扫描后生成）

**模块检测**（按 6 级优先级识别工程依赖的独立模块）：详见 [dir_and_metadata.md](../references/dir_and_metadata.md)「module_docs 工程模块库」段「6 级模块检测」表（git submodule / `repo` 管理 / CMake FetchContent / monorepo 启发式 / vendor 扫描 / 用户配置 + `.icode_doc_modules` JSON 格式）。**"独立模块"判定 = 该模块本身是独立 git 仓库，与"是否工程核心"无关**——工程核心模块同时是独立仓库时两者都生成，互补不重复（职责划分见 [doc_template.md](../references/doc_template.md)「九 · 职责划分」）。

**monorepo 启发式补充判别条件**（dir_and_metadata 表未含，doc.md 步骤 2 独有）：子目录**不在工程根 `.gitignore` 中**（避免误把工程内辅助目录当独立模块）+ **子目录有自己的 README**（含 `## ` 等 markdown 标题）。

**Google `repo` 嵌套子项目路径推导**（dir_and_metadata 表未含，doc.md 步骤 2 独有）：当工程由 Google `repo` 工具管理时，子项目**不一定是 `${GIT_ROOT}/<module_name>` 字面路径** —— 部分模块会嵌套在业务父项目子目录下（如 `<业务父项目 A>/<模块 A1>` / `<业务父项目 B>/<模块 B1>` 等典型模式）。**禁止用字面 `${GIT_ROOT}/<module_name>` 判定 path**，否则会误判 `path_gone`。正确做法：

```bash
# manifest path是实际checkout位置；缺path按project name默认值解析。
# .repo/projects/*.git是Git管理数据，不把它作为源码目录。
python3 - "$GIT_ROOT" "<project_name>" <<'PY'
import pathlib, sys, xml.etree.ElementTree as ET
root = pathlib.Path(sys.argv[1]).resolve()
manifest = ET.parse(root / ".repo/manifest.xml").getroot()
for project in manifest.findall("project"):
    if project.get("name") == sys.argv[2]:
        checkout = root / project.get("path", project.get("name"))
        print(checkout.resolve())
PY
# 再用git -C <实际checkout> rev-parse --show-toplevel验证；
# 无法解析include/extend-project等manifest时记录未解析，不能假装得到源码路径。
```

**优先级与回退**：先解析manifest的真实path（缺path按name默认值）；可用`.repo/project.list`与各仓`git rev-parse --show-toplevel`交叉核对。`.repo/projects/*.git`只辅助确认仓库管理关系，不能直接当源码位置。include/extend-project等无法完整解析时列未解析原因，保留待办；人工指定路径须核实际Git根，多个候选不猜选。写入`module_deps[].path`使用相对repo根的真实checkout路径，不限定嵌套层数。

**去重**：先合并同一真实路径的多来源检测，再按`(module_key, source_id)`去重；不同提交/dirty内容分别保留。用[doc_snapshot.py](../tools/doc_snapshot.py)捕获当前工作区和声明依赖。新modules列表先保存在本次工作目录，与旧module_deps比较，发布完成前不写回工程元信息。

**代码特征扫描**（grep 优先）：用 Grep 扫描工程代码特征识别 entry 函数/导出 API/关键数据结构，结果作为 00_overview.md「核心模块清单」+「全栈图」输入，按本表「动态章节」段（doc_template.md「五」）决定追加哪些章节（AI 根据工程实际技术栈选 grep 模式，**不硬编码框架名**）。汇总「章节规划清单」：固定（00/10/90/99）+ 命中的动态章节。

### 2.5 工作清单（doc_worklist.json，防中断丢进度）

**动机**：doc 全量/多模块生成耗时长，中断后重跑若靠"重扫模块→重判增量"恢复，会重复生成已完成的章节（浪费 token）或漏掉半成品。步骤 2 检测完成后把**候选清单固化为磁盘状态**，每次生成/跳过/更新都更新它——中断续跑 = 只处理未完成项。

**位置**：`$DOC_DIR/doc_worklist.json`（与工程`_meta.json`同级；`$DOC_DIR=~/.claude/icode_data/project_docs/<project_id>/<BRANCH_SAFE>/`）。兼容已存在的`_doc_worklist.json`：复用同一文件，不并建两份状态。

**结构**：

```json
{
  "schema_version": 1,
  "project_id": "<project_id>",
  "request_id": "<本次 /icode doc 调用的幂等键，新调用生成新 id>",
  "generated_at": "date +%Y-%m-%dT%H:%M:%SZ",
  "items": [
    {
      "id": "<章节名或 module_key@source_id>",
      "kind": "project_chapter | module_doc | fixed_chapter",
      "candidate": "<章节/模块显示名>",
      "reason": "候选原因：新依赖 / 代码特征命中 / 用户指定 / 增量 diff 命中 / 固定章节",
      "baseline": "<完整HEAD与工作区/依赖指纹>",
      "source_id": "<本项源码身份>",
      "snapshot_id": "<已发布快照；发布前为null>",
      "target": "<章节文件或 module_docs/<key>/snapshots/<snapshot_id>/>",
      "status": "pending | generated | verified | done | blocked | deferred | out_of_scope",
      "note": ""
    }
  ]
}
```

**状态机**：`pending`（候选待生成）→ `generated`（初稿已写）→ `verified`（99 章代码事实审计核验通过）→ `done`（收尾完成）；失败/无法生成 → `blocked`（附 `note` 原因）；本次跳过（用户点模块/不在本次范围）→ `deferred`；明确范围外（如 `C_follow_up` 型）→ `out_of_scope`。

**读写契约**：
- **步骤 2 末**：按源码身份合并旧项并补新增候选，保留既有结果与未完成原因；新调用更新request_id，显式暂停记录run状态且不改done计数。
- **每个模块/章节生成后**：更新generated/blocked及note；99章语义核验绑定正文SHA后才verified，快照可见且工程绑定回读一致后才done。partial不能把整个module item置done。
- **收尾（步骤 7）**：全部 item 达 `done`/`blocked`/`deferred`/`out_of_scope` 后置 `request_id` 保持 + 汇总
- **幂等续跑**：先核本项当前source_id、精确绑定、输出正文SHA和审计范围；仅全部仍匹配的verified/done项可跳过。基线改变则新增/重置待办并保留旧记录；无指纹旧状态先核查，不能直接当done。generated重验，blocked重试或保留原因；同request_id也不能绕过源码/输出变化检测。
- **写前重读合并 + 原子写**：与全局索引同契约（并发 doc 会话防丢项）
- vNext 工单上下文下，每次状态迁移追加 `doc_module_status` 事件（`event --type doc_module_status --payload '{"module":"<id>","status":"<新状态>","request_id":"<id>"}'`）；doc 独立运行时无工单目录 → 状态记录以 worklist 本身为准，不强制事件

**报告列**（步骤 7 末尾汇总输出）：`done / blocked / deferred / out_of_scope` 各计数 + 失败原因清单，禁止把 blocked 项并入 done 假装完成。

### 3. 增量判定（非全量时）

- `$DOC_DIR` 不存在 → 首次全量
- 存在 → 读 `00_overview.md` 的 `generation_commit`，**先做祖先合法性校验**（关键，防跨分支误判增量）：
  - **HEAD相同且当前工作区/声明依赖指纹、正文SHA及审计范围都匹配**→可复用；同HEAD但内容变化仍要按文件清单/调用边界更新，旧无指纹记录不能直接称最新
  - **`git merge-base --is-ancestor <prev> HEAD` 退出 0**（prev 是 HEAD 祖先，正常前向演进）→`git diff <prev>..HEAD --name-only` 拿变更文件，命中某章 KEYS"文件位置"的章增量重生成，其余跳过；变更文件在未覆盖目录→提示"是否生成新章节"
  - **退出 1（HEAD 是 prev 的祖先/分叉 / prev 与 HEAD 在不同分支）**→**`git diff` 不可信，必须视为"另一版工程"，按全量重生成处理**（不分青红皂白继续增量会因 merge-base 远导致 diff 失真，把大部分文件当变更/漏判关键变更），提示用户「prev `<prev>` 与 HEAD `<HEAD>` 不在同一祖先链（分支切换/fork/换库），按新工程全量重生成」
  - **退出 128（prev 不可达，如 GC/换库）**→按全量重生成处理
- **模块依赖变化检测**：比较保留的旧`module_deps`与本次捕获的新列表，按族key、source_id和snapshot_id区分；同key换提交/dirty输入也是版本变化：
  - 新增dep或同key源码身份变化→生成/审计对应快照，不覆盖其他工程的旧版本
  - 删除的 dep（旧列表有但新列表无）→ 提示「工程不再依赖 X，module_docs/{key}/ 是否保留（默认保留，需手动清理）」
  - URL或分支改变→新族key与精确快照；旧族/快照默认保留，清理须明确选择

- **首次全量 + 模块名参数**（`$DOC_DIR` 不存在但用户给了模块名）-> 该模块名作为"聚焦生成目标"，而非"匹配已有章节"（首次无章节可匹配，原匹配语义悬空）：
  - 该模块是独立仓库 -> 生成该模块 module_docs + 工程固定章节（00/90/99）+ 命中动态章节，其中 `10_architecture` 描述该模块在工程拓扑中的角色（不重复 module_docs 内部）
  - 该模块非独立仓库 -> 生成 `project_docs` 该模块章节 + 固定章节（00/90/99）
  - 其余章节按代码特征自适应生成；**不因用户只点一个模块而省略固定章节**（00/90/99 永远生成）

### 4. 思考分级

本步骤为 **L1（短决策记录）**（见 [references/mcp_per_step.md](../references/mcp_per_step.md)「通用前置·分级思考」段），不调用 sequential-thinking；三件套 Read + Read [references/doc_template.md](../references/doc_template.md) 完整内容（doc 模板多 Read 场景）。决策字段记入 `.decision_anchors.json` = 扫描结果分析 → 章节规划 → 元信息字段准备 → 风险评估（手动编辑/冲突）。

### 5. 生成 module_docs（依赖）+ 工程章节

#### 5.0 质量审视与模板版本迁移（v2 新增，**必经子步骤**）

> 每次跑 `/icode doc` 前必须先执行本子步骤，**不通过则不能跳过**——是模板升级后保持文档质量一致性的关键机制。

**5.0.1 读 SCHEMA_VERSION**

从 [doc_template.md](../references/doc_template.md) 顶部 `<!-- SCHEMA_VERSION: ... -->` 注释读取当前模板版本号（如 `v2.0.0`）。

**5.0.2 扫描现有章节**

对当前工程章节、当前绑定快照及计划继承的旧库章节做质量审视；不递归把其他版本正文按当前源码判stale，不原位重写已发布快照。

1. 读每章 `_meta.json.template_version`（缺失视为 `v1`）
2. 读每章正文元信息块 `template_version` 字段（缺失视为 `v1`，但若 `_meta.json` 有则取 `_meta.json`）
3. 模板版本按数字分量对比SCHEMA_VERSION；下面评分只决定改进顺序，全部适用必含项、来源/正文SHA与事实审计未通过的章保持partial/待完善，不得因高分跳过：

| 章节状态 | 动作 |
|----------|------|
| `template_version >= SCHEMA_VERSION`、适用必含项及事实/来源核验均通过，且评分 ≥ 0.9 | 可复用已核精确快照，无需重生成 |
| `template_version >= SCHEMA_VERSION` 但达标率 0.7-0.9 | 标记"局部补全"，增量补全缺失元素（不重生成全文） |
| `template_version < SCHEMA_VERSION`（含缺失/视为 v1） | 标记待升级，在stage按新模板逐章合并审核，保留旧版本与未完成基线 |
| 达标率 < 0.7（任何版本） | 标记待升级，沿用授权在stage合并审核，不原位覆盖 |

**5.0.3 确认门（必经）**

对待升级章按正文SHA检测内容变化，mtime只提示可能编辑/迁移；旧无SHA章节先完整读并核查。按用户既有合并/保留/跳过选择执行；未有选择且新稿可能遗漏人工内容时，准备具体diff再询问。旧版本保持可读，不原位覆盖快照。

**5.0.4 升级后写新版本号**

重生成完成的章节必须：
- 正文元信息块写入 `template_version: v2.0.0`（或当前 SCHEMA_VERSION）
- 已审章节/新快照元信息记当前template_version；工程部分升级按章记版本，未完成章及全工程基线不提前推进

**5.0.5 进度输出**

- 步骤 7 末尾输出"模板迁移汇总"表（见下文 §7 更新）

**5.0.6 旧布局与模板迁移**

按[快照契约§5](../references/doc_snapshot_contract.md#5-续跑旧库与人工编辑)保留旧正文、人工内容及完整未知元信息。在新stage完成生成/合并/审计再发布并切换绑定；用户拒绝迁移时旧数据仍可读，不移动、不删除。部分迁移按章记基线，不把未完成章模板或commit提前升级。v1→分支布局的父目录包含新子目录，不执行rmdir父目录。

#### 5.1 正常生成流程（用户意图驱动）

**先生成 module_docs**（依赖先于依赖者，用户闲时跑不省 token，模块文档可详细完整生成）：

**module_docs 生成范围（按用户意图聚焦，避免全量 token 爆炸）**：

- **用户指定模块名**→聚焦该模块：独立Git模块生成module_docs快照；非独立Git目录生成project_docs章节。其余已发现依赖保留真实事实与既有精确绑定，先重核后保留可读full快照的generated=true；未生成、失配或不可读依赖记false并列原因，不能因本次未聚焦就抹去旧有效绑定。末尾汇总其余待生成模块，可按需继续。
- **无模块名（全量）**→ 检测到的全部真实依赖进入计划；可读模块逐版本生成/审计/发布并绑定后才generated=true，未完成或不可读保持false并列原因。非独立Git子目录写工程章节，不伪造模块仓库身份。
- **反例（禁止）**：**不得因数量大而全部降级、把模块内容塞进 `project_docs` 章节替代 module_docs**（历史 bug：某工程 34 个 `repo` 子项目时 AI 把模块塞进 project_docs 章节，module_docs 一个没生成）；全量时可读模块数量过多（如超过 10 个）时，应**分批生成或提示用户分次 `/icode doc <name>` 聚焦**，而非降级塞 project_docs

- 对每个**待生成**的 module（按上述范围筛后的列表，非全量 modules）：
  - 有远程 URL 的模块（git submodule / `repo` 子项目）：先调 `mcp__cheap-research__fetch_remote` 拉取其远程 README（从模块 `repo_url` 推导 raw 文件 URL，如 GitHub 类平台 `{repo_url_raw}/HEAD/README.md`；**SSH 格式 `git@` 需转为 HTTPS**）。返回内容作为模块文档生成的参考输入。**降级**（fetch_remote 不可用/URL 格式不兼容/SSRF 拦截）：跳过，仅靠本地代码生成，不阻塞。**不接管决策**：模块文档内容仍由主代理基于本地代码 + 远程 README 参考综合生成，fetch_remote 只提供额外参考源。
  - 读取解析后的实际工作区；先capture完整HEAD、实际内容与声明依赖，再基于该工作区写隔离stage。不用archive HEAD代替dirty代码，不自动克隆/下载不可读依赖；读取失败记录unresolved。
  - 按 [doc_template.md](../references/doc_template.md)「九、模块章节模板」+「十四项必含元素」生成章节（前 50 行四块 + 必含元素清单 + 模板自适应 grep 表；KEYS 按 doc_template.md「七」提取）
  - 先写本次隔离stage/<NN>_<topic>.md（十位桶），不在旧族正文或已发布快照内原位改稿
  - 源码身份已有匹配full快照且正文SHA/模板/审计范围未变→复用该snapshot_id；否则生成新stage逐章合并。既有未知元信息与人工内容保留。
  - 审计通过后用doc_snapshot.py publish重新核源码/依赖和正文SHA，只发布不可变快照；失败/变化不推进旧commit或done。
  - 发布可见后用doc_snapshot.py bind将精确版本及真实相对path合并回源码工程之外的父工程文档_meta.json（参数见快照契约§3）。工具保留未知字段和工程整体基线，full才generated=true，partial保持待办；锁忙/内容冲突重读后重试，回读确认绑定成功才推进done。
- 不可读/未发布dep→保留已知依赖事实并标generated=false，在unresolved_modules记录name/type/reason/attempt_at；不能因失败把工程真实依赖从列表丢掉。其他模块独立继续。

**再生成工程自身章节**（依赖者引用依赖者，含"依赖子模块"字段）：

- **旧布局迁移**：先备份旧全文与完整元信息，在新分支位置生成/合并、逐章审计。新位置可读且基线核对通过后切换检索；旧布局继续保留。用户已拒绝迁移时不移动旧数据。具体保护/部分完成规则以快照契约§5为准。

- 对每个待生成工程章，Read/Grep当前实际源码→按模板与十四项必含元素生成四块、正文及精确依赖绑定→在stage逐章合并旧内容；审核绑定正文SHA后才原子写入$DOC_DIR/<NN>_<module>_<topic>.md。新增编号留既有兼容章名，不用当前模块版覆盖其他工程版。
- 00_overview的关联工程必填，优先用真实project_id，无关联填“无”；其余工程章填同一已核值，作为概览定位线索。
- 工程_meta写前重读并核旧SHA，保留project_id/project_type/git_root、原始branch、完整head_commit、未知字段、真实module_deps、unresolved_modules和stale_files。逐章记录source_id、template_version和正文SHA；只有全部规划章与依赖绑定完成，才推进全工程last_generated_commit。
- 单章失败/用户跳过记录原因和旧基线，其他章可独立完成；部分完成不称工程全量完成。

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
| <章节名> | 模板升级 | v1 → v2.0.0 | 适用必含清单全部通过；未通过章列为待完善 |
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
- **范围**：当前工程分支目录的章节，以每章记录的实际源码根/依赖身份核查；扫描其他工程时先切换其真实源码上下文，不用当前cwd判全局其他版本的锚点
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
- **依赖子模块**：从工程module_deps的精确绑定取；元信息写module_a@branch@完整HEAD/source_id及module_docs/<key>/snapshots/<snapshot_id>/，不只列族目录。
- **被工程引用**：按父工程实际source_id/snapshot_id绑定核对；旧族used_by仅提示关联，不能证明所有工程使用该版本正文。
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
- 按章节类型全部适用的必含元素通过；不适用项须有实际源码依据。质量评分只用于改进排序，不能豁免缺少的必含内容
- 章节元信息包含完整generation_commit、source_id、正文SHA与精确依赖快照；只有所有规划章/绑定完成才推进全工程基线
- `99_code_facts_audit.md` 已生成并对完整范围核验通过；部分失败保留partial/待办，不能把未验证章算全量完成
- 工程 _meta.json.stale_files 已刷新（步骤8 主动 stale 扫描，段零检索据此跳过过时章节）
- 模板迁移汇总表输出（v2 新增，详见步骤 7 末尾）
- 末尾汇总表输出

## 衔接与可重复

- **段零消费**：`/icode init`/`log`/`plan`/`start`/`fast` 启动时段零自动检索（见 [dir_and_metadata.md](../references/dir_and_metadata.md)「段零·工程文档检索」段）；**doc 自身不写 `_inject_cache.json`**（工单目录缓存，doc 不创建工单）
- **可重复**：匹配快照幂等复用；新源码/审稿修订保存新快照，旧版和人工内容保留；续跑、缓存按源码与正文身份重新核验。
## MCP 推荐（强证据二元化）
| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| vision-bridge | 🟢* | 截图分析--用户给图时 |
| **cheap-research** | 🟢* | **降本**：fetch_remote（有远程 URL 模块拉取其 README 作模块文档参考输入，见「模块文档」段）。其余（propose_repo_facts / fill_template 章节模板 / scan_modules / scan_patterns / diff_summary / parse_project_id）可作可选增强，非强证据场景不评估。不接管决策：意图识别走主会话 |
| context7 | ⚪ | 本步骤不推荐 |
| memory | ⚪ | 本步骤不推荐 |
| playwright | ⚪ | 本步骤不推荐 |

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
