# 步骤 list — 跨工程工单查找（纯查询）

**命令**:
- `/icode list [关键词] [--project <path>] [--status <status>] [--since <duration>] [--limit N] [--no-color] [--include-stale]`

**产出**: 默认无（只读，控制台输出表格）；不写 metadata、不写 index.json、不写工程内任何文件
**会话**: 主会话

## 定位

解决"工单目录 `icode_output_N` 分散在多工程，找历史工单要打开很多工程一个个点开"的痛点。`/icode status` 模式一只看当前工程最近一个工单，**`/icode list` 是它的跨工程增强版**：从全局索引 `~/.claude/icode_data/index.json` 的 `tickets` 数组全量读取，按需过滤+排序+格式化输出。

> **debug 工单隔离**（详 [references/debug_mode.md](../references/debug_mode.md)）：**`/icode list` 默认不含 debug 工单**——debug 工单由 [`/icode init --debug`](../steps/00_init.md) / [`/icode log --debug`](../steps/log.md) 创建，**永不写入 `index.json`**（见 [init 阶段 8 跳过硬门](../steps/00_init.md) / [log 阶段 10 跳过硬门](../steps/log.md)），因此本命令基于 `index.json` 全量读取**不会列出 debug 工单**。如需查 debug 工单列表，手动 `ls <project_root>/.icode_output/.debug/` 或写脚本扫描本地目录。

**与 `/icode status` 职责分离**：
- `status`：当前工程最近一个工单的**详细状态**（含 mode/verdict/下一步推断/索引概览）
- `list`：全索引的**鸟瞰视图**（一行一个工单，便于浏览+筛选）

## 关键约定（必读）

- **只读**：不写 metadata、不写 index.json、不写工程内任何文件。**禁止**任何 `--write` 之类的破坏性扩展
- **不创建工单目录**：与 `/icode start` / `/icode init` / `/icode log` 区别（那些是"创建/复用"，list 是"查找"）
- **不参与步骤1~6 推进**：与 `/icode plan` / `/icode review` / `/icode merge` 等区别（那些是"推进流程"，list 是"查询"）
- **不修改全局索引**：与 `--verdict` / `--scan-verdict` 区别（那些是"标注"，list 是"只读浏览"）

## 执行步骤

1. **解析命令行参数**（顺序无关）：
   - 位置参数：可选关键词（无则不过滤）
   - `--project <path>`：按 `project_path` 过滤（支持 basename 匹配，见下文）
   - `--status <status>`：按 `status` 过滤（精确匹配，支持 `,` 分隔多值如 `plan_done,code_done`）
   - `--since <duration>`：按 `last_used_at` 过滤（支持 `24h` / `7d` / `30d` / `1y`）
   - `--limit N`：限制条数（默认 50，0 = 不限）
   - `--no-color`：禁用 ANSI 颜色（管道/重定向场景）
   - `--include-stale`：包含 stale 工单（默认排除）
2. **读取全局索引**：`json.load` 全量解析 `~/.claude/icode_data/index.json` 的 `tickets` 数组（**禁止按行截断**）
3. **空索引处理**：
   - 文件不存在 → 提示"无全局索引，请先跑 `/icode start` 或 `/icode init` 创建工单"后退出
   - `tickets=[]` → 提示"无工单记录"后退出
4. **逐条过滤**（AND 关系，所有条件同时满足才保留）：
   - **关键词过滤**：在 `ticket_id` / `project_path` / `requirement_summary` / `keywords` 中大小写不敏感扫子串（关键词用空格分词后所有 token 都需命中，AND 关系）
   - **`--project` 过滤**：
     - 若 value 是绝对路径 → 精确匹配 `project_path`
     - 若 value 是字符串（无 `/` 开头）→ 在 `project_path` 中按 basename 模糊匹配（如 `--project <品类代号>` 匹配 `.../<品类代号>/...`）
   - **`--status` 过滤**：精确匹配 `status` 字段；多值用 `,` 分隔（OR 关系）
   - **`--since` 过滤**：
     - `24h` / `7d` / `30d` / `1y` 等单位：`now - duration < last_used_at`（含未使用工单 `last_used_at=created_at`）
     - 无单位数字视为天数
   - **stale 默认排除**（除非 `--include-stale`）：`stale=true` 的工单不显示
5. **排序**（默认 `last_used_at` 倒序，最新在前）：
   - 同样时间按 `ticket_id` 升序兜底（稳定排序）
6. **`--limit` 截断**（默认 50，0 = 不限；超出条数时末尾标注 `(还有 N 条未显示，加 --limit N 提高或加关键词缩小范围)`）
7. **格式化输出**（表格 + ANSI 颜色，除非 `--no-color`）：

   | 字段 | 来源 | 宽度 | 颜色规则 |
   |------|------|------|---------|
   | TICKET-ID | `ticket_id` | 自适应（最长对齐） | 基础 |
   | PROJECT | `project_path`/`archive_path`/`backup_path` 按显示优先级选一（见下），智能截断 | 60 字符 | 基础 |
   | STATUS | `status` | 14 字符 | `completed` 灰 / 含 `in_progress` 黄 / 其他 绿 |
   | WORKLOAD | `workload_estimate` | 7 字符 | `large` 粗体红 / `medium` 黄 / `small` 灰 / 缺失 `-` |
   | LAST-USED | `last_used_at` | 16 字符（`YYYY-MM-DD HH:MM`） | 基础 |
   | SCHEMA | `template_version` | 9 字符（`v1.1`/`v0`/`unknown`/`-`） | `v1.1+` 绿 / `v0` 黄 / `unknown` 灰 / 缺失 `-` |
   | VERDICT | `verdict` | 11 字符 | `disproved` 红 / `superseded` 蓝 / `verified` 绿 / `unknown` 灰 |
   | SUMMARY | `requirement_summary` | 60 字符截断（超长加 `…`） | 基础 |

   **PROJECT 列智能截断算法**（修默认 30 字符截断导致 path 不可见的痛点）：

   1. **完整路径 ≤ 60 字符**：原样显示
   2. **完整路径 > 60 字符**：按以下优先级智能截断
      - **优先一**：`$HOME` 替换为 `~`（如 `/home/user/myproject` → `~/myproject`），剩余若仍超 60 字符进行下一级
      - **优先二**：保留 basename + 关键父路径段（保留 basename 和最后 N 级父目录，体现工程身份而非纯路径）
      - **最终兜底**：从路径尾部取 60 字符，前缀加 `…`（如 `…ng-project-name/subdir/.icode_output`），保留可点击的尾部信息
   3. **截断示例**：
      - `/home/user/myproject` → `~/myproject`（19 字符）
      - `/home/user/very-long-namespace-name/myproject` → `~/very-long-namespace-name/myproject`（41 字符，原样）
      - `/home/user/very-long-namespace-name/myproject/.icode_output` → `~/…/.icode_output/`（智能缩中段）
      - `/home/user/very-long-namespace-name/myproject/.icode_output/.icode_output_3` → `~/<truncated header>…myproject/.icode_output_3`（保留尾段）
   4. **绝对优先**：保留的最后一段必须是工单目录名（`.icode_output_N`）或归档目录名（`ticket_id`），让用户能直接 `cd` 进去（archive/backup 路径同样适用，末级目录不得被截掉）

   **PROJECT 列显示优先级**（每条先对 `project_path`/`archive_path`/`backup_path` 三字段只读实判 `test -d`，得布尔 `project_valid`/`archive_valid`/`backup_valid`，再按下表选显示来源；纯查询不写任何字段）：
   1. `project_valid`（工程在）→ 显示 `project_path`（下截断算法）
   2. `!project_valid` 且 `archive_valid` 且 `backup_valid` → 显示 `[path_gone→archive+backup]` + 智能截断的 `archive_path`（归档为生命周期专属直达根，`+backup` 标签显式告知完整快照也可用）
   3. `!project_valid` 且 `archive_valid`（archived 活跃态，worktree 归档产物）→ 显示 `[path_gone→archive]` + 智能截断的 `archive_path`（worktree 已清理但归档可读，用户可直接 `cd` 进去读核心产物）
   4. `!project_valid` 且 `!archive_valid` 且 `backup_valid`（backup 活跃态，`/icode bak` 产物）→ 显示 `[path_gone→backup]` + 智能截断的 `backup_path`（内容实际所在处，用户可直接 `cd` 进去读完整产物）
   5. 均无有效来源（stale path_gone）→ 显示 `[path_gone]`

   **stale 工单**（如 `--include-stale` 显式包含）：`STATUS` 列前缀 `[stale] `，`SUMMARY` 后缀 ` [stale_reason: X]`

   **backup 活跃工单**（工程已删但有 `/icode bak` 备份）：`project_path` 失效、`archive_path` 无效但 `backup_path` 非空且 `test -d` 有效 → **非 stale**（设计保证，见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「过时校验·备份工单」），**默认显示、无需 `--include-stale`**——`PROJECT` 列按上方优先级 4 显示 `[path_gone→backup]` + `backup_path`（若归档与备份同时有效则按优先级 2 显示 `[path_gone→archive+backup]` + `archive_path`）

   **archived 活跃工单**（worktree 已清理但有归档）：`project_path` 失效但 `archive_path` 非空且 `test -d` 有效 → **非 stale**（设计保证，见 [references/dir_and_metadata.md](../references/dir_and_metadata.md)「过时校验·归档工单」），**默认显示、无需 `--include-stale`**——`PROJECT` 列按上方优先级 3 显示 `[path_gone→archive]` + `archive_path`（归档与备份均有效则按优先级 2 显示 `[path_gone→archive+backup]`）

   **path_gone 工单**（工程已删且 archive/backup 均无有效来源）：`PROJECT` 列 `[path_gone]`，保留显示便于用户判断（默认被 stale 排除，`--include-stale` 才显示）
8. **输出统计脚注**（表格下方）：
   ```
   共 N 条（过滤后）/ 全索引 M 条（stale K 条 / disproved L 条 / verified P 条）
   ```

## ANSI 颜色码（参考）

- 红（disproved / large workload）：`\033[31m`
- 绿（verified / 完成态）：`\033[32m`
- 黄（medium workload / in_progress）：`\033[33m`
- 蓝（superseded）：`\033[34m`
- 灰（completed / unknown / stale / small workload）：`\033[90m`
- 粗体（large workload 强调）：`\033[1m`
- 重置：`\033[0m`

**`--no-color` 模式**：所有颜色码替换为空字符串。**TTY 检测**：当 stdout 不是 TTY（如管道到 `less` / `grep` / 文件）时自动等价于 `--no-color`（防乱码）

## 边界处理

| 场景 | 行为 |
|------|------|
| 全局索引文件不存在 | 提示"无全局索引，请先跑 `/icode start` 或 `/icode init` 创建工单"后退出，不报错 |
| 索引为空（`tickets=[]`） | 提示"无工单记录"后退出 |
| 关键词无匹配 | 提示"无匹配工单（索引共 N 条，尝试其他关键词或去掉过滤条件）" |
| 旧 metadata 无 `workload_estimate` | WORKLOAD 列显示 `-`，不报错（向后兼容） |
| 旧 metadata 无 `template_version` | SCHEMA 列显示 `-`（与 WORKLOAD 兼容同形态），不报错 |
| `project_path` 已删但 `archive_path` 有效（archived 活跃态） | **非 stale**，默认显示；PROJECT 列 `[path_gone→archive]` + archive_path（实判 `test -d`） |
| `project_path` 已删但 `archive_path` 与 `backup_path` 均有效 | **非 stale**，默认显示；PROJECT 列 `[path_gone→archive+backup]` + archive_path（归档直达，备份并行可用） |
| `project_path` 已删但 `backup_path` 有效（backup 活跃态） | **非 stale**，默认显示；PROJECT 列 `[path_gone→backup]` + backup_path（实判 `test -d`） |
| `project_path` 已删且 archive/backup 均无有效来源（`stale_reason=path_gone`） | PROJECT 列显示 `[path_gone]`，默认排除（除非 `--include-stale`） |
| 字段格式异常（如 `last_used_at` 缺） | 退化为 `-`，不中断整行渲染 |
| `--limit` 截断 | 末尾标注 `(还有 N 条未显示...)` |
| TTY 检测 | `sys.stdout.isatty() == False` 时自动 `--no-color` |

## 反偷懒

- **禁止按行截断**：用 `json.load` 全量解析 `tickets` 数组（**前 50 行**仅适用于 project_docs 章节，不适用索引）
- **禁止写 metadata/index.json**：纯查询，无 `--write` 之类的破坏性扩展
- **禁止只列当前工程**：明确跨工程（从全索引读）
- **禁止硬编码时间**：用 `datetime.now()` 取真实当前时间（`--since` 过滤的基准）
- **禁止猜测字段值**：缺失字段退化为 `-`，不编造
- **禁止只看路径字段不实判**：`project_path`/`archive_path`/`backup_path` 有效性一律 `test -d` 实判（字段可能是旧值/已失效），archived/backup 活跃态判定必须磁盘验证
- **禁止改 user 项目路径**：只读索引，不 `cd` / 不 `open`（"纯查询不跳转"原则）

## 与 `/icode status` 的协作

- 用户从 `/icode list` 看到感兴趣的 ticket_id → 用 `/icode status --verdict {ticket_id} ...` 标注
- 用户从 `/icode list` 看到要继续推进的工单 → 用对应工程的 `/icode start` / `/icode plan`（**仍需在工程目录下运行**，list 不支持跳转）
- 用户想批量扫证伪信号 → `/icode status --scan-verdict`（跨工程批量治理）

## 性能

- 全索引 64 条工单实测 < 50ms（Python 解析 + 简单字符串过滤）
- 200 条上限（索引 LRU 淘汰 200 条，超过则按规则淘汰）
- 过滤+排序总耗时 < 100ms（无数据库，纯内存）
- 输出渲染：50 条表格 < 30ms

## 示例

```bash
# 列所有工单（默认 last_used_at 倒序，前 50 条）
/icode list

# 关键词搜索（找含 "mcu" 的工单）
/icode list mcu

# 按工程过滤（只显示 <品类代号> 相关的工单）
/icode list --project <品类代号>

# 按状态过滤（只显示已完成工单）
/icode list --status completed

# 组合过滤（<工程名> 工程下 plan_done 或 code_done 的工单，近 30 天用过）
/icode list --project <工程名> --status plan_done,code_done --since 30d

# 禁用颜色（管道场景）
/icode list --no-color | grep <品类代号>

# 不限条数
/icode list --limit 0

# 包含 stale 工单（默认排除）
/icode list --include-stale
```
## MCP 推荐

本步骤仅用 sequential-thinking 强制思考（见 [references/mcp_per_step.md](../references/mcp_per_step.md)「通用前置」段）。其他 5 个 MCP 本步骤不推荐。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
