---
name: icode
description: 六步全流程编码工作流，支持分步手动调用：/icode help (帮助), /icode new <需求> (新建+计划), /icode review (审查), /icode merge (定稿), /icode code (编码), /icode deepcheck (复检), /icode audit (终审)
---

**版本**: v1.2.0

# ICode 六步全流程编码工作流

端到端编码工作流，将需求到交付拆解为 6 个严格步骤，每步可单独调用，方便你自行切换模型。

## 调用命令

所有输出保存在 `.icode_output_N/`（N 自动递增）目录下：

| 命令 | 功能 | 创建目录？ |
|------|------|-----------|
| `/icode help` | **帮助**：输出使用流程示例 | 否 |
| `/icode new <需求>` | **全流程**：创建新目录 → 步骤1→6 串联 | ✅ 创建新目录 |
| `/icode plan <需求>` | **仅步骤1**：拟定项目计划 | ✅ 创建新目录 |
| `/icode review` | **仅步骤2**：多轮循环审查 | 用最新目录 |
| `/icode merge` | **仅步骤3**：合并审查意见定稿 | 用最新目录 |
| `/icode code` | **仅步骤4**：落地编码实施 | 用最新目录 |
| `/icode deepcheck` | **仅步骤5**：无限轮循环复检 | 用最新目录 |
| `/icode audit` | **仅步骤6**：终极终审 + 统一修复 | 用最新目录 |

### 帮助说明（`/icode help`）

在对话中输出使用流程示例和命令一览（不创建目录和文件）。

### 使用流程示例

```bash
# 方式A：全流程一步到位（自动串联所有步骤，自动切换模型）
/icode new 实现MCU雨量传感器I2C驱动

# 方式B：分步执行，每步可切换模型
/icode plan 实现MCU雨量传感器I2C驱动   # 步骤1
/icode review                          # 步骤2
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
  "phase": "reverse",
  "code_compile_failed": false,
  "deepcheck_total_rounds": 0,
  "deepcheck_clean_rounds": 0,
  "deepcheck_phase": "reverse"
}
```

每步执行后必须更新 `status` 和 `completed_steps`。步骤4编码后必须记录 `code_files`。

**`code_files` 路径基准**：所有路径**相对于项目根目录**（即用户运行 `/icode` 命令的目录），不含前导 `./`。例：`src/foo.c`、`include/bar.h`。主 Agent 使用 Read 工具时须自行拼接绝对路径。

**可选字段**（按需写入，缺失视为默认值）：
- `total_rounds` / `clean_rounds`：步骤 2 续跑用
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

### 模型分配

| 步骤 | 功能 | 全流程模式 | 会话 |
|------|------|-----------|------|
| 1 | 拟定计划 | 主会话 | 主会话（无需切换） |
| 2 | 专项审查 | sonnet | 子 Agent |
| 3 | 合并定稿 | opus | 子 Agent |
| 4 | 编码实施 | opus | 子 Agent |
| 5 | 循环复检 | sonnet | 子 Agent |
| 6 | 终极终审 | opus | 子 Agent |

- **步骤1** 直接在主会话中执行，保留全部需求对话上下文
- **步骤2-6** 使用子 Agent 执行，便于切换模型和隔离上下文
- 分步模式：不设置 Agent `model` 参数，使用当前会话模型

### 全流程串联规则

`/icode new` 执行步骤1后，如果会话断开，恢复时必须：

1. **先读取 `.ico_metadata.json`** 查看 `completed_steps` 字段
2. **根据最后一个完成步骤继续**：
   - `completed_steps = ["1"]` → 继续执行步骤2
   - `completed_steps = ["1","2"]` → 继续执行步骤3
   - `completed_steps = ["1","2","3"]` → 继续执行步骤4
   - ...以此类推
3. **输出恢复确认**：`▶ 会话恢复，从步骤N继续执行`

不可跳过未完成的步骤。

### 子 Agent 启动规范（步骤2-6必须遵守）

> **步骤1 不使用子 Agent**，直接在主会话中执行。以下规则仅适用于步骤2-6。

0. **并发子 Agent 最多 3 个** — 同一时刻同时在跑的子 Agent 数量不超过 3 个。子 Agent 过多同时并发容易失败。**阶段数量与子 Agent 数量无关**：步骤 2/5 的多轮循环可包含任意个子 Agent，只要遵守并发≤3 的约束（通常串行启动即可满足）。
1. **确定当前模型** — 全流程模式按上表设置 `model`；分步模式不设置 `model` 参数。将 `{当前模型名称}` 替换为实际模型名。
2. **输出模型确认** — 启动子 Agent 前，必须先在对话中输出：`▶ 步骤N 使用模型：{当前模型名称}`，让用户确认模型切换生效。
3. **必须使用 Agent 工具启动一个子 Agent**（不可由主 Agent 直接执行），设置 `model` 参数（分步模式不设置）。

4. **主 Agent 读文件后传 inline prompt（关键！）** — 主 Agent **必须先读取当前步骤所需的输入文件**（计划、代码、审查意见等），将内容填入 prompt 后传给子 Agent。严禁让子 Agent 自行读取文件。

   **prompt 组装规则**：
   - 主 Agent 从 step 文件的 prompt 模板中识别 `{读取 ...}` 占位符，解析为文件读取指令
   - 主 Agent **必须读取这些文件并将内容替换进 prompt**，再将完整的 prompt 传给子 Agent
   - 每个 prompt 必须是**自包含的**，子 Agent 无需读任何其他文件即可执行
   - prompt 末尾必须强调"直接输出结果，以 ===XXX START=== 开头"，防止子 Agent 跑偏
   - prompt 末尾可追加：`**优先使用 prompt 中已提供的信息进行分析，必要时可执行工具调用**`

   **`{用户输入的原始需求}` 的传递规则**：
   - 子 Agent 是全新上下文，**看不到主会话中的任何历史对话**
   - 主 Agent 将需求内容嵌入 prompt，而不是让子 Agent 从 metadata 读取
   - 如果主会话中有额外补充的需求讨论，一并归纳进 prompt 中的需求描述
   - 格式示例：
     ```
     原始命令输入：实现计算器
     对话中补充的需求：
     - 需要支持浮点数
     - 用 C 语言实现
     - 不需要 GUI，命令行即可
     ```

5. **禁止主 Agent 接管执行** — 子 Agent 返回结果后，主 Agent 只负责提取内容和更新元信息，**不可自己生成计划/审查报告/代码等内容**。
   **严禁切换 Agent 类型或自建工作流** — 如"先用 Explore 收集信息再交给 Plan agent"等都属于违规。

   **完成判定标准（四级判定）**：
   a. 回复中包含 `===XXX START===` / `===XXX END===` 标记 → 提取标记间内容，完成
   b. 回复中无标记但有实质内容（>50 tokens） → 将全部回复内容作为输出，完成（标记不是必需的）
   c. 回复内容极少（≤50 tokens 或无实质内容） → **可能是后台 Agent 工具调用中间状态**，先检查 TaskOutput 取得最终结果。如果 TaskOutput 返回实质内容，按 a/b 处理。
   d. 以上均无内容 → 重新启动子 Agent（使用同一类型），不可自行切换策略

   **子 Agent 沉默恢复（4 级判定 c/d 触发时）**：
   - **不要用 `max_tokens` 限长**——会截断分析输出，治标不治本
   - **步骤 1：SendMessage 续接** — 向同一 Agent ID 发 "请直接输出完整结果" 类短 prompt，60% 概率可恢复
   - **步骤 2：补强指令后重发** — 续接失败时，重新启动子 Agent，prompt 前置更强的指令：例如 "你必须输出至少 200 tokens" / "必须包含 ===XXX START=== 标记" / "禁止说'无法完成'" / 直接列出必含字段
   - **步骤 3：换模型** — 仍失败时，换用其他模型（如 sonnet → opus 或反之），不换 Agent 类型
   - **步骤 4：放弃该轮** — 三次均失败，输出 `⚠️ 子 Agent 连续沉默，本轮跳过` 警告并继续后续步骤；残留问题由步骤 6 终审兜底
   - **禁止**：在 c/d 状态下由主 Agent 接管生成内容（违反"禁止主 Agent 接管"）
6. **禁止合并或跳过步骤** — 6 个步骤必须逐一执行，**不可合并**（如"快速自检合并步骤5-6"）、**不可跳过**任何步骤。步骤2-6每个都必须启动独立的子 Agent 执行，完成后才能继续下一步。步骤1在主会话中直接执行。

7. **主 Agent 必须读取输入文件（仅限步骤2-6）** — 主 Agent **必须自行读取当前步骤所需的全部输入文件**（计划、代码、审查意见等），将内容填入 prompt 后传给子 Agent。这是子 Agent 获取信息的唯一渠道。**例外：步骤1在主会话中执行，需要主 Agent 阅读代码并直接撰写计划。**

8. **反偷懒机制（步骤5、6必须遵守）** — 代码简洁或编译通过不代表复检/审计通过。步骤5和6的子 Agent **必须先建立计划-代码追溯矩阵**（逐条列出计划中的功能点/接口/约束，找到代码中的对应实现位置，标记完成状态），**再基于追溯矩阵逐维度评估**。禁止跳过追溯直接给"全部通过"的结论。具体规则见 `steps/05_deepcheck.md` 和 `steps/06_audit.md`。

### 跨文件关联思维（所有步骤必须遵守）

任何涉及多文件的项目，子 Agent prompt 中必须加入以下强制指令：

```
**跨文件关联要求（必须遵守）**：
1. **修改前必须画出关联图**：列出本次修改涉及的所有文件及其依赖关系（谁调用谁、谁包含谁、谁实现谁声明的接口）
2. **修改任何文件前必须检查影响面**：该文件被哪些文件引用？修改后这些引用方是否需要同步修改？
3. **接口变更必须全链路同步**：如果修改了函数签名、数据结构、宏定义、头文件，必须找到所有使用点并同步更新
4. **修改后必须验证关联一致性**：检查调用方与被调用方的参数/返回值是否匹配、头文件与实现是否一致、上下游数据结构是否对齐
```

步骤2-6的子 Agent prompt 末尾必须追加此段落。步骤1 已在计划模板中内置跨文件关联分析。

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
