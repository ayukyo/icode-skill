---
name: icode
description: 六步全流程编码工作流，支持分步手动调用：/icode help (帮助), /icode new <需求> (新建+计划), /icode review [N] (审查), /icode merge (定稿), /icode code (编码), /icode deepcheck (复检), /icode audit (终审)
---

**版本**: v1.3.0

# ICode 六步全流程编码工作流

端到端编码工作流，将需求到交付拆解为 6 个严格步骤，每步可单独调用，方便你自行切换模型。

## 调用命令

所有输出保存在 `.icode_output_N/`（N 自动递增）目录下：

| 命令 | 功能 | 创建目录？ |
|------|------|-----------|
| `/icode help` | **帮助**：输出使用流程示例 | 否 |
| `/icode new <需求>` | **全流程**：创建新目录 → 步骤1→6 串联 | ✅ 创建新目录 |
| `/icode plan <需求>` | **仅步骤1**：拟定项目计划 | ✅ 创建新目录 |
| `/icode review [N]` | **仅步骤2**：多轮循环审查（N=轮数，默认3） | 用最新目录 |
| `/icode merge` | **仅步骤3**：合并审查意见定稿 | 用最新目录 |
| `/icode code` | **仅步骤4**：落地编码实施 | 用最新目录 |
| `/icode deepcheck` | **仅步骤5**：三阶段递进复检 | 用最新目录 |
| `/icode audit` | **仅步骤6**：终极终审 + 统一修复 | 用最新目录 |

### 帮助说明（`/icode help`）

在对话中输出使用流程示例和命令一览（不创建目录和文件）。

### 使用流程示例

```bash
# 方式A：全流程一步到位（自动串联所有步骤）
/icode new 实现MCU雨量传感器I2C驱动

# 方式B：分步执行
/icode plan 实现MCU雨量传感器I2C驱动   # 步骤1
/icode review                          # 步骤2（默认3轮）
/icode review 5                        # 步骤2（指定5轮）
/icode merge                           # 步骤3
/icode code                            # 步骤4
/icode deepcheck                       # 步骤5
/icode audit                           # 步骤6
```

## 通用规则

### 目录管理

**创建新目录**（用于 `new` / `plan`）：
```bash
LAST=$(ls -d .icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
NEXT=${LAST:-0}; NEXT=$((NEXT + 1))
ICODE_OUT_DIR=".icode_output_${NEXT}"
mkdir -p "$ICODE_OUT_DIR"
```

**检测最新目录**（用于 `review`/`merge`/`code`/`deepcheck`/`audit`）：
```bash
LAST=$(ls -d .icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
if [ -z "$LAST" ]; then
  echo "错误：没有找到 .icode_output_N 目录，请先运行 /icode new <需求>"
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
| 1 | `plan_done` | 步骤1计划完成 |
| 2 | `review_in_progress` → `review_done` | 步骤2审查中 → 完成 |
| 3 | `plan_finalized` | 步骤3定稿完成 |
| 4 | `code_in_progress` → `code_done` | 步骤4编码中 → 完成 |
| 5 | `deepcheck_in_progress` → `deepcheck_done` | 步骤5复检中 → 完成 |
| 6 | `completed` | 步骤6终审完成（终态） |

**`in_progress` 状态用于支持分步中断续跑**（详见各步骤"分步中断续跑"段）：步骤 2/5 在每轮结束时实时落盘 `*_in_progress` + 当前轮次/phase，崩溃后重启可从断点恢复。

### 执行模式

所有 6 个步骤在主会话中执行，使用当前会话模型。**不主动切换模型**，用户如需切换可手动 `/model`。

### 深度思考要求（所有步骤必须遵守）

**每个步骤开始前，必须先进行深度思考**：需求分解 → 方案分析 → 风险评估。不得跳过思考直接产出。具体规则见各步骤文件。

### 全流程串联规则

`/icode new` 执行步骤1后，如果会话断开，恢复时必须读取 `.ico_metadata.json` 的 `completed_steps`，从最后一个完成步骤的下一步继续。不可跳过未完成的步骤。

步骤 2/5 的 `*_in_progress` 状态 + 轮次计数器支持断点续跑。

### 注意事项

- **Git 安全**：禁止执行任何 Git 危险操作（`git reset --hard`、`git push --force` 等），**也禁止 `git commit` 和 `git push`**
- **`.icode_output_N/` 目录无需用户确认**：该目录下创建/写入/修改 `.md`/`.json`/`.log` 文件均为安全操作
- **跨会话恢复**：运行 `ls -d .icode_output_*` 确认目录后，直接调用对应步骤即可
- **中断恢复**：重新执行某步骤可覆盖该步骤输出

## 各步骤详细规则

各步骤的详细 prompt、维度要求、执行流程请读取对应文件：

| 步骤 | 命令 | 详细文件 |
|------|------|----------|
| 1 | `plan` / `new` | [steps/01_plan.md](steps/01_plan.md) |
| 2 | `review` | [steps/02_review.md](steps/02_review.md) |
| 3 | `merge` | [steps/03_merge.md](steps/03_merge.md) |
| 4 | `code` | [steps/04_code.md](steps/04_code.md) |
| 5 | `deepcheck` | [steps/05_deepcheck.md](steps/05_deepcheck.md) |
| 6 | `audit` | [steps/06_audit.md](steps/06_audit.md) |

**执行步骤时，必须先读取对应的 `steps/XX_*.md` 文件，按其中的详细指令执行。**
