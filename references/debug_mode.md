# debug 模式（独立孪生工单，纯对照不入索引）

> **核心**：debug 工单是 **正常工单的孪生对照**——同一工程、同一 cwd 状态下同时产出第二份分析，**不写入全局索引**、**不参与主流程步骤**、**不支持 `/icode patch` 续跑**。纯为研究 BUG 根因 / 设计的合理性而存在（一句话需求：`/icode init --debug` 和 `/icode log --debug` 工单不入索引，纯作为对照）。

---

## 1. 何时使用 debug 模式

| 场景 | 用例 |
|------|------|
| **同档案两场景对比**（最常用） | 正常用 `/icode log` 分析一个 bug；**另一个会话**跑 `/icode log --debug` 同一组症状，产出**两份 `log_analysis.md` 并列对比**，手工读两份产物找差异（视角差别、假说强度、证据缺漏） |
| **同档案三场景对比** | 正常 `/icode init` 初稿后，**同会话**跑 `/icode init --debug` 同样粗略需求，产出两份 `00_init.md` 对比设计 |
| **跨工程、跨工程交叉分析** | cwd 切到另一个工程，跑 `/icode init --debug` 或 `/icode log --debug`，产物落那个工程的 `.icode_output/.debug/`，作为跨工程参考 |

---

## 2. 目录组织

```
.icode_output/                                    # 正常工单父目录
├── .icode_output_1/                              # 正常工单
├── .icode_output_2/                              # 正常工单
└── .debug/                                       # debug 工单子目录
    ├── .icode_output_1/                           # debug 工单（独立 N 序列）
    └── .icode_output_2/
```

**为什么 debug 工单放在 `.debug/` 子目录？**
- 与正常工单**目录隔离子树**（硬熔断：正常 vs debug 不互相污染）
- `ls` 等"找最新目录"算法对 `.icode_output/.icode_output_*` 仍只匹配正常工单——**debug 工单不会被误当最新工单**
- 一键清理：`rm -rf .icode_output/.debug/` 批量删除全部 debug（debug 工单是研究产物，弃用即可）

**debug 工单的 N 序列独立**：与正常工单各自从 1 开始递增，互不干扰。

---

## 3. 命令定义

| 命令 | 行为 |
|------|------|
| `/icode init --debug [<粗略需求>]` | 创建 debug 工单目录 + metadata；**不写** index.json；status = `debug_in_progress`，运行过程同正常 init |
| `/icode log --debug [零散信息...]` | 创建 debug 工单目录 + metadata；**不写** index.json；status = `debug_done`，运行过程同正常 log |

`--debug` 与现有 flag（`--listen` / `--test` 仅 patch）**互不冲突**。

---

## 4. metadata 新增字段

```json
{
  "debug": true,                  // bool — 仅 debug 工单为 true；正常工单不写此字段
  "indexed": false,               // 永远 false（debug 工单永不写入 index.json）
  "status": "debug_in_progress",  // init 的调试态 / 或 "debug_done"（log 调试终态）
  "ticket_id": "",                // debug 工单无 ticket_id（不参与索引）
  "requirement": "...",           // 调试用输入（与正常工单相同字段）
  "requirement_summary": "...",   // 调试用摘要（正常字段也填，便于跨工单 Read 时快速理解）
  ...                              // 其它 metadata 字段同正常工单
}
```

**关键决策**：
- **不使用新 status 名**（`debug_in_progress` / `debug_done` 复用现有状态枚举，避免 metadata 状态机膨胀）
- **debug 标志用元数据 `debug: true` 字段**（不依赖 status 名判断）——`/icode status` 列表扫描本地目录时，**先用 `debug: true` 区分，再读 status**
- **`indexed: false` 永远不变**——debug 工单永不索引（防止被 `/icode status` 误列入）

---

## 5. 与下游步骤的关系（最关键）

debug 工单 **不参与主流程**——所有主流程步骤（plan/review/merge/code/deepcheck/audit/readme/patch）接到 debug 工单应**L1 阻断**：

| 步骤 | 是否允许 debug 工单进入 | 阻断理由 |
|------|---------------------|----------|
| `/icode init --debug` | ✅ 起点 | debug 模式起点 |
| `/icode log --debug` | ✅ 起点 | debug 模式起点 |
| `/icode plan` | ❌ L1 阻断 | debug 工单不进主流程 |
| `/icode start` / `/icode fast` | ❌ L1 阻断 | 同上 |
| `/icode review` / `/icode merge` / `/icode code` / `/icode deepcheck` / `/icode audit` / `/icode readme` | ❌ L1 阻断 | 同上 |
| `/icode patch` | ❌ L1 阻断 | debug 工单**不支持** patch 续跑 |
| `/icode status` | ✅ 可查（仅本地目录扫描 + 标 `[DEBUG]`，不计 LRU） | 状态查询不属主流程 |
| `/icode list` | ❌ 不列（debug 工单不入索引） | list 基于 index.json |
| `/icode doc` / `/icode limit` / `/icode ppt` | ✅ 与工单状态无关 | 这些是独立工具 |
| `/icode install` / `/icode help` | ✅ | 系统级工具 |

**L1 阻断的统一规则**（下游 step 文件需在 L1 检测段加一条）：

```python
# 各主流程 step 的 L1 检测段统一加：
metadata = read_metadata()
if metadata.get("debug") is True:
    error_exit(
        f"当前工单 .icode_output/{out_dir}/.ico_metadata.json 的 debug=true，"
        f"{step_name} 是主流程步骤，不允许作用于 debug 工单。"
        f"debug 工单仅用作对照，不能继续 {step_name}。\n"
        f"如需 {step_name}，请用 `/icode {{init|plan|start}}` 重新建正常工单。"
    )
```

---

## 6. 与 patch 的关系

debug 工单 **不支持** `/icode patch` 续跑——理由：
- debug 工单的产物是"对照参考"，不是"待修复代码" —— 不需要 patch
- L1 阻断（见 §5 表）直接报错

如需对 debug 工单发现的问题正式修复，请用 `/icode init --debug` **复现的正常工单**（同一 cwd 不带 `--debug`）走主流程。

---

## 7. 与 `/icode status` 列表的关系

`/icode status` 默认通过 `~/.claude/icode_data/index.json` 列工单——debug 工单**不在此列**。

**本地目录扫描扩展**（建议在 `steps/status.md` 加 1 段）：

```python
# /icode status 默认行为：列 index.json 工单
# 新增副表（可选 `--include-debug` flag）：
def list_debug_tickets(project_path):
    debug_root = f"{project_path}/.icode_output/.debug"
    if not os.path.isdir(debug_root):
        return []
    return [
        {
            "n": int(m.group(1)),
            "status": read_status(f"{path}/.ico_metadata.json"),
            "path": f".debug/.icode_output_{n}/",
            "input": read_requirement(f"{path}/.ico_metadata.json"),
        }
        for n in sorted(ints)
    ]
```

debug 工单在列表中显示 `[DEBUG]` 前缀，让用户视觉区分正常 vs debug。

---

## 8. 与跨工程使用

debug 模式天然支持跨工程：
```bash
cd /path/to/project-A
/icode init --debug "研究 A 项目的设计合理性"   # → A/.icode_output/.debug/.icode_output_1/

cd /path/to/project-B
/icode log --debug 服务异常日志路径              # → B/.icode_output/.debug/.icode_output_1/
```

每个工程的 debug 工单都在该工程自己的 `.icode_output/.debug/` 下，**全局互不干扰**。

---

## 9. 与现有契约的兼容性

| 契约 | 是否受影响 | 说明 |
|------|----------|------|
| [references/dir_and_metadata.md](dir_and_metadata.md) | 小改 | 「创建新目录」段的 N 递增逻辑**对 `.debug/` 子目录独立执行**（debug 工单 N 与正常工单 N 互不干扰） |
| [references/worktree_isolation.md](worktree_isolation.md) | 无关 | worktree 与 debug 模式互不重叠 |
| [references/decision_anchors.md](decision_anchors.md) | 可选 | debug 工单可写 `.decision_anchors.json`（与正常工单相同），跨 session reload 时帮助 |
| `/icode status` / `[index.json]` | 跳过 | debug 工单永不进入 index.json |
| `/icode list` | 跳过 | 同上 |
| 强制思考前置 / references/thinking_core.md | 继承 | debug 工单也走强制思考前置 |

---

## 10. 清理与保留

| 操作 | 影响 |
|------|------|
| `rm -rf .icode_output/.debug/` | 一键清除所有 debug 工单（debug 是研究产物，弃用即可） |
| debug 工单**不进入** index.json LRU 淘汰 | 独立存在 |
| 长期不清理 | 占用磁盘，但**不影响**正常工单（目录隔离） |

---

## 11. 与下一步工作的边界（防误用）

- **debug 工单的产物可作为正常工单的对照**：用户**手工 Read** 两个目录对比产物（这是设计意图，不是 bug）
- **debug 工单的产物不可被自动注入**：检索/段零不复用 debug 工单（因不入 index.json）
- **debug 工单的产物不可被 audit 6.7 视角 A 引用**：audit 视角 A 检查 `metadata.requirement` vs 实际产物，但**当前迭代的"实际产物"是另一张正常工单，不是 debug 工单**——debug 工单的产物是**独立对照**
- **debug 工单的产物不可被 patch 续跑**：见 §6
- **debug 工单不支持跨工程的 `cross_project_refs`**：入索引的工单才有该字段，debug 工单不入索引 → 不参与跨工程流转
