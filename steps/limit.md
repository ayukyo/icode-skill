# 步骤 limit — 项目约束红线（独立步骤，不参与 1~6 流程推进）

**命令**: `/icode limit [自然语言]`
**产物**:
- 主存：`~/.claude/icode_data/limits/<project_id>.md`（全局，跨 checkout 共享）
- 覆盖：`<project_root>/.icode_output/limit.local/<project_id>.md`（单 checkout，自动 gitignore）
**会话**: 主会话
**定位**: **项目约束红线生成与维护，独立步骤**。不创建 `.icode_output_N/`、不写 `.ico_metadata.json`、不更新工单 `completed_steps`/`status`。产物供 `/icode plan`/`start`/`fast` 启动时**作为硬基线引用**（plan §3 架构设计/§4 ADR/§6 异常处理须呼应 limit 条目）。

> **核心设计哲学**：
> - **约定 vs 事实分离** —— limit 是"代码应该这样写"的约定（团队私有，不上传），doc 是"代码长这样"的事实快照（独立于仓库全局可索引）。两者职责严格分离。
> - **全局共享 + 单 checkout 覆盖** —— 主存全局共享（同一 project_id 所有 clone 共享），local 覆盖单 checkout 私有（自动 gitignore）。local 完全覆盖 main（同字段 local 优先）。
> - **追加式演进** —— 每次 `/icode limit <...>` 增量追加新约束条目，保留历史。不覆盖、不 diff。
> - **不阻断流程** —— plan 时检测不到 limit → 柔性提示建议生成，不阻断。

## 与 `/icode doc` 的差异

| 维度 | `/icode doc` | `/icode limit` |
|---|---|---|
| 内容性质 | 事实快照（代码长什么样） | 约定红线（应该怎么做） |
| 触发方式 | 无描述→全局扫描/有描述→针对操作 | **同** doc 模式 |
| 模板 | 14 项必含元素 + 99 章审计 | 自由 markdown（推荐条目列表 + 编号红线） |
| 产物复杂度 | 多章节（前 50 行四块 + KEYS） | 单文件（约定条目列表） |
| 上传策略 | 全局（跨工程可检索） | **不上传**（团队私有，gitignore 自动覆盖） |
| 步骤推进 | 不参与 | **不参与** |
| 演进方式 | 模板版本迁移（v1 → v2） | 追加式（条目累积，保留历史） |
| 检索 | 段零（project_docs） | 直接 Read（plan 步骤强制 Read） |

## 前置校验

1. cwd 必须在 git 仓库或 `repo` 管理的项目内（**与 `/icode doc` 一致**）：
   - `git rev-parse --show-toplevel` 成功 → git-root 模式
   - 否则从 cwd 向上逐级 `test -d $d/.repo`，首个命中 → repo-root 模式
   - 都失败 → 报错"请在 git 仓库或 `repo` 管理的项目内运行 /icode limit"
2. 全局目录 `~/.claude/icode_data/limits/`（首次自动创建）

## project_id 解析

```bash
GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
PROJECT_TYPE="git-root"
if [ -z "$GIT_ROOT" ]; then
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
    echo "   /icode limit 必须在 git 仓库或 repo 管理的项目根目录下运行"
    echo "   1. 检查当前目录：pwd（确认你在工程根目录）"
    echo "   2. 如果不在 git 仓库：cd 到 git 仓库根目录"
    echo "   3. 如果不在 repo 管理项目：使用 Google repo 工具管理（或 cd 到 .repo/ 所在目录）"
    echo "   4. 如果工程根不在 cwd：cd <工程根> 后再跑 /icode limit"
    exit 1
  fi
fi
PROJECT_ID=$(basename "$GIT_ROOT")

# 主存路径（全局）
MAIN_FILE="$HOME/.claude/icode_data/limits/$PROJECT_ID.md"
# 覆盖路径（单 checkout）
LOCAL_DIR="$GIT_ROOT/.icode_output/limit.local"
LOCAL_FILE="$LOCAL_DIR/$PROJECT_ID.md"
```

**冲突检测**：与 `/icode doc` 不同，limit 主存不做工程名冲突短 hash 后缀（理由：limit 是团队私有约定，工程名冲突概率极低；如真冲突用户手动重命名文件即可，遵循 KISS）。**同一工程多 checkout 共享同一份 main（这是设计意图）**。

## 意图识别（对齐 `/icode doc` 模式）

- **无描述** (`/icode limit`) → 合并显示本工程当前约束（main + local 整文件覆盖视图），不做任何写入
- **有描述** (`/icode limit <自然语言>`) → 针对操作（生成/追加）：
  - AI 解析自然语言，提取 1~N 条新约束条目（每条格式见「条目格式」段）
  - 追加到主存 main（不覆盖 local，local 是单 checkout 覆盖专用，不接受命令行追加）
  - **追加式演进**（不覆盖、不 diff、不询问"是否覆盖"）

**歧义处理**：
- 描述模糊（无法提取明确约束条目）→ 询问"请补充具体约束（如'所有 API 必须 RAII'、'所有日志必须带原始值'）"
- 描述含已有约束关键词 → 询问"该约束与已有红线 N 相似，是 ① 增强表述 / ② 新增独立条目 / ③ 替换已有条目？"

## 执行流程

### 1. 解析 project_id + 路径（见上）

### 2. 读取主存 + 覆盖（合并显示）

**合并规则**：local 完全覆盖 main（同字段 local 优先）

- 主存 main 存在 → 读其所有条目
- 覆盖 local 存在 → **整文件覆盖**（local 文件内容直接当作完整 limit 视图）；未匹配的 main 条目保留语义不实现，统一由用户维护 local 时人工合并
- local 不存在 → 仅显示 main
- main 不存在 → 提示"⚠️ 本工程尚无 limit 约束，建议运行 `/icode limit <约束描述>` 生成"

> **整文件覆盖语义**：为避免"按编号/标题合并"复杂度，**本实现采用整文件覆盖**——local 存在时把 local 文件整体当作 limit 视图；local 不存在时显示 main。**理由**：limit 条目通常不多（典型 5~20 条），复杂合并规则维护成本高于收益。**约定**：用户编辑 local 时自行把想保留的 main 条目复制进去。如未来需要精细合并可迭代。

### 3. 强制思考前置（不可跳过）

**必须按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行**。子项（≥3步）：
- 需求分解（自然语言 → 1~N 条约束条目）
- 条目格式校验（每条是否满足"红线 N：X 描述 + 理由 + 适用范围"）
- 现有 main 内容核查（追加是否与现有红线冲突/重复）

### 4. 追加 / 显示

**有描述 → 追加**：
1. mkdir -p `~/.claude/icode_data/limits/`（首次）
2. 读 main 文件（如存在）→ 解析最后一条红线编号 N_last
3. AI 提取新条目（1~N 条），编号从 N_last + 1 开始
4. 追加到 main 末尾（用 Edit 工具或 Write 完整重写，原子写 `.tmp` + `mv`）
5. 输出"▶ 已追加红线 N：<标题>"+ 完整 limit 内容（合并视图）

**无描述 → 显示**：
1. 读 main + local（如存在），合并视图输出
2. 输出格式：每个红线一条，标注来源 `main` / `local`
3. 不写入任何文件

### 5. plan 步骤引用契约（不直接执行，在 SKILL.md 与 steps/01_plan.md 体现）

- plan §3 架构设计必须读 limit（若存在）作为硬基线
- plan §4 ADR 必须呼应相关 limit 条目（"本工程 limit 红线 N：X，本方案选择 Y 因为..."）
- plan §6 异常处理必须呼应 limit 异常处理相关条目
- plan 步骤 metadata 写入 `limit_refs` 数组（可选，记录本计划引用的红线编号）

**柔性提示**：plan 步骤入口检测 main + local 都不存在 → 输出"💡 本工程尚无 limit 约束（建议运行 `/icode limit <约束描述>` 生成），不阻断流程"。

## 条目格式

每条 limit 红线推荐格式（**非硬性模板，AI 灵活调整**）：

```markdown
### 红线 N：<简短标题>

- **约束**：<具体约束描述，如"所有新增 API 必须 RAII，禁止裸指针跨函数传递">
- **理由**：<为何设此红线，如"工程历史 bug 中 X% 由裸指针生命周期导致">
- **适用范围**：<工程级 / 模块级 / 接口级，如"src/ 下所有模块">
- **例外**：<如有，如"内部单元测试可豁免">
- **来源**：<如"团队规范 / 同事提示 / 历史 bug 复盘">
```

**字段约定**：
- 红线编号 N 由 AI 自动递增（读 main 最后一条 + 1）
- "约束" 必填，其他可选
- AI 解析自然语言时按此格式填充，未提及字段留空或填"未指定"

## 反偷懒

- **不得**为生成而生成——用户描述模糊时主动询问，禁止 AI 自行编造约束内容
- **不得**覆盖式重写——追加式演进是核心约定，违反即不合规
- **不得**写工程内 main 文件——main 永远在全局 `~/.claude/icode_data/limits/`，local 永远在 `.icode_output/limit.local/`
- **不得**触发任何工单步骤（不写 `.ico_metadata.json`，不创建 `.icode_output_N/`，不更新 status）
- **不得**在产物里写 icode 工作流元数据（如 `// ticket: xxx`），产物是给团队读的约定

## 工程污染防护

- **main 在全局** `~/.claude/icode_data/limits/`，不污染工程根
- **local 在 `.icode_output/limit.local/`**，默认被 `.gitignore` 覆盖（SKILL.md 建议），不污染 git
- **不上传任何约定内容到工程仓库**——limit 是团队私有，跟 doc（事实快照可共享）职责严格分离
- **产物路径不动** —— 不在工程根创建新配置文件

## 完成标志

- 无描述 → 仅显示，不写任何文件
- 有描述 → main 文件已追加新条目，原子写成功（`.tmp` + `mv`），输出追加确认
- main 不存在 + 有描述 → 自动创建 main（首次），追加新条目
- local 不存在 → 不创建 local 文件（local 仅在用户主动写覆盖时存在）

## 衔接与可重复

- **plan 消费**：`/icode plan`/`start`/`fast` 启动时自动检测 main + local，柔性提示或读取作为硬基线
- **可重复**：多次 `/icode limit <...>` 持续追加，编号自动递增
- **手动编辑**：用户可直接编辑 main/local 文件（约定文件，非 AI 独占），AI 追加时按编号续接

## MCP 推荐（v2.2 强证据二元化）

| MCP | 推荐级别 | 用途 |
|-----|----------|------|
| serena | ⚪ | 本步骤不推荐（limit 不读源码） |
| context7 | ⚪ | 本步骤不推荐 |
| vision-bridge | ⚪ | 本步骤不推荐 |
| memory | ⚪ | 本步骤不推荐 |
| playwright | ⚪ | 本步骤不推荐 |
| **cheap-research** | ⚪ | 本步骤不推荐（自然语言解析走主会话足够，无 token 压力） |

**说明**：limit 步骤以 AI 解析自然语言为主，不涉及代码事实/库文档/视觉理解，单会话 token 压力小（典型约束描述 100~500 token，主存文件 ≤2K token）。**全部 MCP 本步骤 ⚪ 不必调**，无需双保险承载。
