---
name: icode
description: 端到端编码工作流（步骤 0~6，含可选需求初稿步骤），支持分步手动调用：/icode help (帮助), /icode init [<粗略需求>] (需求初稿), /icode start <需求> (全流程), /icode review [N] (审查), /icode merge (定稿), /icode code (编码), /icode deepcheck (复检), /icode audit (终审)
---

**版本**: v1.4.0

# ICode 全流程编码工作流（步骤 0 + 1~6）

端到端编码工作流，将需求到交付拆解为严格步骤，每步可单独调用，方便你自行切换模型。

- **步骤 0（可选）**：需求初稿对话，多轮迭代后落档为 `00_init.md`，独立步骤、不自动串联到步骤1
- **步骤 1~6**：拟定计划 → 审查 → 定稿 → 编码 → 复检 → 终审

## 调用命令

所有输出保存在 `.icode_output_N/`（N 自动递增）目录下：

| 命令 | 功能 | 创建目录？ |
|------|------|-----------|
| `/icode help` | **帮助**：输出使用流程示例 | 否 |
| `/icode init [<粗略需求>]` | **可选步骤0**：多轮对话产出 `00_init.md`（需求初稿）。**每次调用都新建目录，不复用、不续聊**（详见 [steps/00_init.md](steps/00_init.md)） | ✅ 每次都新建 |
| `/icode start <需求>` | **全流程**：创建/复用目录 → 步骤1→6 串联（**复用规则见下**） | ✅ 创建新目录 / 复用 |
| `/icode plan <需求>` | **仅步骤1**：拟定项目计划（**复用规则见下**） | ✅ 创建新目录 / 复用 |
| `/icode review [N]` | **仅步骤2**：多轮循环审查（N=轮数，默认3） | 用最新目录 |
| `/icode merge` | **仅步骤3**：合并审查意见定稿 | 用最新目录 |
| `/icode code` | **仅步骤4**：落地编码实施 | 用最新目录 |
| `/icode deepcheck` | **仅步骤5**：三阶段递进复检 | 用最新目录 |
| `/icode audit` | **仅步骤6**：终极终审 + 统一修复 | 用最新目录 |

> **`/icode start` / `/icode plan` 的目录复用规则**：启动时检查最新 `.icode_output_N/` 目录，若该目录**只有 `00_init.md`（仅 `.ico_metadata.json` + `00_init.md`，无其他步骤产物）**，则**复用该目录**（不递增 N），并将 `00_init.md` 作为需求输入；否则按"创建新目录"逻辑走。详见下文「目录管理」段落。

### 帮助说明（`/icode help`）

在对话中输出使用流程示例和命令一览（不创建目录和文件）。

### 使用流程示例

```bash
# 方式A：全流程一步到位（自动串联所有步骤）
/icode start 实现MCU雨量传感器I2C驱动

# 方式B：分步执行
/icode plan 实现MCU雨量传感器I2C驱动   # 步骤1
/icode review                          # 步骤2（默认3轮）
/icode review 5                        # 步骤2（指定5轮）
/icode merge                           # 步骤3
/icode code                            # 步骤4
/icode deepcheck                       # 步骤5
/icode audit                           # 步骤6

# 方式C：先讨论需求再进入流程（推荐用于需求不明确的场景）
/icode init 录制 point_cloud / lidar_imu 转包       # 步骤0：起一稿，进入对话
# ... 多轮对话补充需求，文档 00_init.md 每轮都被增量更新 ...
/icode start                             # 复用同一目录，把 00_init.md 作为需求输入，进入步骤1→6
# 或：
/icode plan                            # 复用同一目录，仅执行步骤1
```

## 通用规则

### 目录管理

**创建新目录**（用于 `init`，以及 `start` / `plan` 在不满足复用条件时）：
```bash
LAST=$(ls -d .icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
NEXT=${LAST:-0}; NEXT=$((NEXT + 1))
ICODE_OUT_DIR=".icode_output_${NEXT}"
mkdir -p "$ICODE_OUT_DIR"
```

**复用 / 创建新目录决策**（仅用于 `start` / `plan`）：
```bash
LAST=$(ls -d .icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
REUSE=0
if [ -n "$LAST" ]; then
  CAND=".icode_output_${LAST}"
  # 只有 .ico_metadata.json + 00_init.md 时复用
  FILES=$(ls -A "$CAND" 2>/dev/null | sort | tr '\n' ' ')
  if [ "$FILES" = ".ico_metadata.json 00_init.md " ]; then
    REUSE=1
    ICODE_OUT_DIR="$CAND"
  fi
fi
if [ "$REUSE" = "0" ]; then
  NEXT=${LAST:-0}; NEXT=$((NEXT + 1))
  ICODE_OUT_DIR=".icode_output_${NEXT}"
  mkdir -p "$ICODE_OUT_DIR"
fi
```

复用时：将 `00_init.md` 内容作为步骤1的需求输入（优先级高于命令行参数；命令行参数若有，仅作为补充上下文）。

**检测最新目录**（用于 `review`/`merge`/`code`/`deepcheck`/`audit`）：
```bash
LAST=$(ls -d .icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
if [ -z "$LAST" ]; then
  echo "错误：没有找到 .icode_output_N 目录，请先运行 /icode start <需求> 或 /icode init"
  exit 1
fi
ICODE_OUT_DIR=".icode_output_${LAST}"
```

### 前置文件校验

| 步骤 | 必须存在的文件 |
|------|---------------|
| review | `{ICODE_OUT_DIR}/01_plan.md` |
| merge | `{ICODE_OUT_DIR}/01_plan.md` + `{ICODE_OUT_DIR}/02_review.md` |
| code | `{ICODE_OUT_DIR}/03_plan_final.md` |
| deepcheck | `{ICODE_OUT_DIR}/03_plan_final.md` + 步骤4代码文件 |
| audit | `{ICODE_OUT_DIR}/03_plan_final.md` + 步骤4代码文件 |

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
  "phase": "reverse",
  "code_compile_failed": false,
  "deepcheck_total_rounds": 0,
  "deepcheck_clean_rounds": 0,
  "deepcheck_phase": "reverse"
}
```

每步执行后必须更新 `status` 和 `completed_steps`。步骤4编码后必须记录 `code_files`。

**`code_files` 路径基准**：所有路径**相对于项目根目录**（即用户运行 `/icode` 命令的目录），不含前导 `./`。例：`src/foo.c`、`include/bar.h`。使用 Read 工具时须将相对路径拼接为绝对路径。

**可选字段**（按需写入，缺失视为默认值）：
- `total_rounds` / `clean_rounds` / `max_rounds`：步骤 2 续跑用（`max_rounds` 由 `/icode review [N]` 参数决定，默认 3）
- `phase`：步骤 5 续跑用（值：`reverse` / `fixed` / `free`）
- `code_compile_failed`：步骤 4 编译失败标记（`true` 时步骤 5 入口输出警告）
- `deepcheck_total_rounds` / `deepcheck_clean_rounds` / `deepcheck_phase`：步骤 5 完成时记录

**`status` 字段枚举**（统一词表，所有步骤必须严格遵守，禁止自定义）：

| 步骤 | 状态值 | 含义 |
|------|--------|------|
| 0 | `init_in_progress` | 步骤0需求初稿讨论中（多轮对话每轮更新文档，无显式"完成"态） |
| 1 | `plan_done` | 步骤1计划完成 |
| 2 | `review_in_progress` → `review_done` | 步骤2审查中 → 完成 |
| 3 | `plan_finalized` | 步骤3定稿完成 |
| 4 | `code_in_progress` → `code_done` | 步骤4编码中 → 完成 |
| 5 | `deepcheck_in_progress` → `deepcheck_done` | 步骤5复检中 → 完成 |
| 6 | `completed` | 步骤6终审完成（终态） |

**步骤0说明**：步骤0产出 `00_init.md` 后 status 一直保持 `init_in_progress`，直到 `/icode start`/`/icode plan` 复用该目录进入步骤1时才被切换为 `plan_done`。`completed_steps` 含 `"0"` 表示走过步骤0。

**`in_progress` 状态的两种语义**：

- `init_in_progress`：步骤0**稳态**标记，文档每轮增量更新，等待 `/icode start`/`/icode plan` 复用并切换到 `plan_done`。**不参与崩溃续跑判定**。
- `review_in_progress` / `deepcheck_in_progress`：步骤 2/5 的**中断续跑标记**，每轮结束时实时落盘 `*_in_progress` + 当前轮次/phase，崩溃后重启可从断点恢复。
- `code_in_progress`：步骤 4 的**执行中标记**（只在步骤4整体开始时落盘 `code_in_progress`，完成后切换为 `code_done`，不带轮次/phase 维度的断点续跑）。

### 执行模式

所有步骤（含可选步骤0）在主会话中执行，使用当前会话模型。**不主动切换模型**，用户如需切换可手动 `/model`。

### 深度思考要求（所有步骤必须遵守）

**每个步骤开始前，必须先进行深度思考**：需求分解 → 方案分析 → 风险评估。不得跳过思考直接产出。具体规则见各步骤文件。

### 全流程串联规则

`/icode start` 执行步骤1后，如果会话断开，恢复时必须读取 `.ico_metadata.json` 的 `completed_steps`，从最后一个完成步骤的下一步继续。不可跳过未完成的步骤。

**续跑判定规则**：以 `completed_steps` 中**编号 1~6 范围内最大的已完成步骤**为基准推进下一步。`"0"` 仅作为"已走过步骤0"的标记，**不影响**推进逻辑。例：`["0"]` → 下一步是步骤1；`["0","1"]` → 下一步是步骤2。

步骤 2/5 的 `*_in_progress` 状态 + 轮次计数器支持断点续跑（步骤0的 `init_in_progress` 不参与，详见上节"两种语义"）。

### 注意事项

- **Git 安全**：禁止执行任何 Git 危险操作（`git reset --hard`、`git push --force` 等），**也禁止 `git commit` 和 `git push`**
- **`.icode_output_N/` 目录无需用户确认**：该目录下创建/写入/修改 `.md`/`.json`/`.log` 文件均为安全操作
- **跨会话恢复**：运行 `ls -d .icode_output_*` 确认目录后，直接调用对应步骤即可
- **中断恢复**：重新执行某步骤可覆盖该步骤输出

## 各步骤详细规则

各步骤的详细 prompt、维度要求、执行流程请读取对应文件：

| 步骤 | 命令 | 详细文件 |
|------|------|----------|
| 0 | `init` | [steps/00_init.md](steps/00_init.md) |
| 1 | `plan` / `start` | [steps/01_plan.md](steps/01_plan.md) |
| 2 | `review` | [steps/02_review.md](steps/02_review.md) |
| 3 | `merge` | [steps/03_merge.md](steps/03_merge.md) |
| 4 | `code` | [steps/04_code.md](steps/04_code.md) |
| 5 | `deepcheck` | [steps/05_deepcheck.md](steps/05_deepcheck.md) |
| 6 | `audit` | [steps/06_audit.md](steps/06_audit.md) |

**执行步骤时，必须先读取对应的 `steps/XX_*.md` 文件，按其中的详细指令执行。**
