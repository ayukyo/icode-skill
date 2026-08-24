# debug 模式（独立孪生工单，纯对照不入索引）

> **核心**：debug 工单是 **正常工单的孪生对照**——同一工程、同一 cwd 状态下同时产出第二份分析，**不写入全局索引**、**不参与主流程步骤**、**不支持 `/icode patch` 续跑**。纯为研究 BUG 根因 / 设计的合理性而存在（一句话需求：`/icode init --debug` 和 `/icode log --debug` 工单不入索引，纯作为对照）。

---

## 1. 何时使用 debug 模式

| 场景 | 用例 |
|------|------|
| **同档案两场景对比**（最常用） | 正常用 `/icode log` 分析一个 bug；**另一个会话**跑 `/icode log --debug` 同一组症状，产出**两份 `log_analysis.md` 并列对比**，手工读两份产物找差异（视角差别、假说强度、证据缺漏） |
| **同档案三场景对比** | 正常 `/icode init` 初稿后，**同会话**跑 `/icode init --debug` 同样粗略需求，产出两份 `00_init.md` 对比设计 |
| **跨工程交叉分析** | cwd 切到另一个工程，跑 `/icode init --debug` 或 `/icode log --debug`，产物落那个工程的 `.icode_output/.debug/`，作为跨工程参考 |

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

**完成提示差异**（debug 入口步骤末尾的强制输出）：
- debug 模式下 init/log 完成后**不输出**「下一步建议 / 进入修复流程」引导（`/icode plan` / `/icode start` / `/icode fast`——debug 工单 L1 阻断，引导进入修复流程是错误指引）
- 改为输出 **debug 对照说明**：说明本工单是 debug 孪生（不入索引、不参与主流程、产物仅供与正常工单并列对照研读），并提示"如需正式修复，请用不带 `--debug` 的 `/icode init` / `/icode log` 新建正常工单"（见 [steps/00_init.md](../steps/00_init.md) 步骤9 与 [steps/log.md](../steps/log.md) 步骤11 的 debug 分支）

---

## 4. metadata 新增字段

```json
{
  "debug": true,                  // bool — 仅 debug 工单为 true；正常工单不写此字段
  "indexed": false,               // 永远 false（debug 工单永不写入 index.json）
  "status": "debug_in_progress",  // init 的调试态 / 或 "debug_done"（log 调试终态）
  "ticket_id": "",                // debug 工单无 ticket_id（不参与索引）
  "project_path": "<当前工程根绝对路径>",   // 当前工程根绝对路径（git rev-parse --show-toplevel；非 git 仓库 = pwd）。正常工单的 project_path 在索引条目里，debug 不入索引 → 只能写进 metadata 作产物唯一回追锚点（写错副本时能凭它识别真实位置）
  "tb_source": {...},             // TB 缺陷单溯源（从 TB 拉取时填完整版 {lib,num,pid,label,url,meta_path}，无 TB 源时 null）。正常工单的 tb_source 摘要存索引条目、debug 不入索引 → 只能写进 metadata 作 debug 域内"按 lib+num+pid 复用匹配"的唯一依据（批量 TB + --debug 复用判定扫它，见 steps/log.md「批量 TB 分析」段步骤3 debug 变体）
  "requirement": "...",           // 调试用输入（与正常工单相同字段）
  "requirement_summary": "...",   // 调试用摘要（正常字段也填，便于跨工单 Read 时快速理解）
  ...                              // 其它 metadata 字段同正常工单
}
```

**关键决策**：
- **使用独立 status 名**（`debug_in_progress` / `debug_done`，不复用 `init_in_progress` / `log_done`——下游易识别，见 00_init/log 步骤「`--debug` 模式差异」段）；debug 状态名**不进** SKILL.md「status 字段枚举」主流程词表（词表校验只作用于正常工单，debug 目录在 `.icode_output/.debug/` 下天然被「检测最新目录」排除、不入 `--validate` 范围，不产生状态机冲突）
- **debug 标志用元数据 `debug: true` 字段**（不依赖 status 名判断）——各主流程步骤 L1 检测段、以及手动扫描 `.debug/` 下的 debug 工单时都读它区分

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
| `/icode status` | ✅ 可用（默认只读模式只看**当前工程最新正常工单**，`ls .icode_output/.icode_output_*` 天然排除 debug；查 debug 工单须手动 `ls .icode_output/.debug/` + Read metadata，见 §7） | 状态查询不属主流程 |
| `/icode list` | ❌ 不列（debug 工单不入索引） | list 基于 index.json |
| `/icode doc` / `/icode limit` / `/icode ppt` | ✅ 与工单状态无关 | 这些是独立工具 |
| `/icode install` / `/icode help` | ✅ | 系统级工具 |

**L1 阻断的统一规则**（下游 step 文件需在 L1 检测段加一条）：

```python
# 各主流程 step 的 L1 检测段统一加：
metadata = read_metadata()
if metadata.get("debug") is True:
    error_exit(
        # 路径直接用已解析的 ICODE_OUT_DIR（debug 工单 = .icode_output/.debug/.icode_output_N）；
        # 勿用 metadata 的 out_dir 字段（= .icode_output/.icode_output_{N}）再拼 ".icode_output/" 前缀 → 双写前缀成错误路径
        f"当前工单 {ICODE_OUT_DIR}/.ico_metadata.json 的 debug=true，"
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

如需对 debug 工单发现的问题正式修复，请用 `/icode init`（**不带 `--debug`**，同一 cwd 复现该需求）新建正常工单走主流程。

---

## 7. 与 `/icode status` 列表的关系

`/icode status` 模式一（默认只读）只查**当前工程最新正常工单**（[steps/status.md](../steps/status.md)「模式一」）：`ls .icode_output/.icode_output_*` 仅匹配顶层正常目录，天然排除 `.icode_output/.debug/.icode_output_*`——**debug 工单不入本模式查询范围**（无 `[DEBUG]` 前缀显示、不计 LRU）。

**查 debug 工单的办法**（status/list 均不提供 debug 列表视图）：
- 手动 `ls <project_root>/.icode_output/.debug/` 枚举目录
- 或 `cd <project_root>/.icode_output/.debug/.icode_output_N/` + Read `.ico_metadata.json`（读 `debug: true` + `status` 区分）

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
| [references/dir_and_metadata.md](dir_and_metadata.md) | 小改 | 「创建新目录」段含 **debug 变体**：`--debug` 时目录建在 `.icode_output/.debug/` 下，N 递增逻辑**对该子目录独立执行**（debug 工单 N 与正常工单 N 互不干扰） |
| [references/worktree_isolation.md](worktree_isolation.md) | 无关 | worktree 与 debug 互不重叠：**debug 工单忽略 `--worktree`**——debug 需「同一 cwd 状态」作孪生对照，worktree 会切换 checkout 违背该前提；若同传 `--debug` 与 `--worktree`，worktree opt-in 不生效，debug 仍在原地 `.icode_output/.debug/` 创建 |
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

---

## 12. 中断半成品识别与续跑（防超时死循环）

**背景**：tb_watch 定时增量监控触发的 claude 分析可能**超时被杀**（单次 `claude_timeout` 到点 / 会话中断），留下**中断半成品 debug 工单**：目录 + 已下载 TB 附件齐全，但**未写完 `.ico_metadata.json`**（metadata 是流程末步才写）。若不识别，下次分析会把它当"无 debug 孪生"→ 新建第二个 debug 工单 → 重复下载附件 → 同一批单无限重建（实测死循环：同一批单反复重建、重复下载附件）。

**识别标准（中断半成品）**：`.icode_output/.debug/` 下的目录，**同时满足**：
- **无 `.ico_metadata.json`**（与正式 debug 孪生区分——正式孪生必有 metadata）
- **有已下载的 TB 附件**：`tb_source/<LIB>-<NUM>/` 子目录存在，或其内 `*_meta.json`（排除 `.prev.json`）带 `uniqueId` 字段

**归属单识别**：优先读 `tb_source/<LIB>-<NUM>/<LIB>-<NUM>_meta.json`（附件下载产物的 meta）的 `uniqueId` 为权威单号；目录名 `<LIB>-<NUM>` 提供 lib+num（两者存在且一致时以 uniqueId 为准）。真实半成品的 meta.json 就在 `tb_source/<LIB>-<NUM>/` 子目录（附件下载产物），**非工单顶层**——扫描必须递归。

**复用续跑（命中半成品时，不新建第二个 debug 工单）**：
1. `ICODE_OUT_DIR` = 半成品目录（如 `.icode_output/.debug/.icode_output_2`），**不新建**
2. **附件复用**：半成品已下载附件（`tb_source/` 下 tgz/mp4/已抽帧、日志已解压到 `extracted/`）**直接复用、跳过重复下载**，仅补拉缺失附件
3. 完成分析后**收尾补写 `.ico_metadata.json`**：`debug: true` / `indexed: false` / `status: debug_done` / `project_path` / `tb_source` 完整 `{lib,num,pid,label,url,meta_path}`——补写后该目录即正式 debug 孪生，后续走正常 debug 复用匹配（§4）

**与正常 debug 复用匹配的关系**（统一判定顺序）：① 正常复用匹配（扫 metadata 的 `tb_source`，见 [steps/log.md](../steps/log.md) 步骤1 debug 变体 / 批量步骤3 debug 变体）优先 → ② 匹配不到再扫**中断半成品**（本 §）→ ③ 两者皆无才「创建新目录」debug 变体新建。命中①或②均**自动判定复用、不询问**（debug 无人值守场景如 tb_watch 不能弹问），与批量 debug 的"自动判定、不逐单弹问"一致。

---

## 14. debug 工单不参考历史工单（独立形成自己的思考）

> **核心**：debug 工单是**独立孪生对照**——它是**被参考**的对象（产物供正常工单并列对照研读），不是**去参考别人**的分析者。因此 debug 模式分析时**跳过历史工单检索**（源1·`index.json`），保持独立思考，不被历史正式工单结论带偏。

**为什么跳过**：
- 历史检索读 `index.json` 注入的是**历史正式工单**的根因结论（debug 工单不入索引，检索命中的全是正式工单）。
- debug 若参考它们，会被既有结论带偏——正常工单与 debug 孪生**两份对照都收敛到同一历史结论**，对照价值（视角差异/假说强度/证据缺漏）尽失，违背 debug「同 cwd 状态下第二份独立分析」的初衷。
- 用户反馈正是此意："--debug 不应该去参考历史工单，本身 DEBUG 工单就是应该被参考的，如果参考别人，可能会参考到历史正式工单，无法形成自己的思考"。

**跳过范围**（见 [steps/log.md](../steps/log.md) 步骤2 / [steps/00_init.md](../steps/00_init.md) 步骤2 的 `--debug` 分支）：
- **跳过**：源1·历史工单检索（两段式检索 / verdict 分流注入 / 结论级时效校验 / 重复模式检测——后两者依赖源1 命中，随源1 一起跳过）
- **保留**：源2·段零工程文档检索（代码事实快照，非"别人结论"，帮助理解当前代码）+ limit 红线检查点（项目级约定，debug 同样遵守）
- **不受影响**：debug 域内同 TB 单旧 debug 孪生复用（步骤1 debug 变体 / 批量步骤3 debug 变体）——那是**参考自己**（该单自己的旧 debug 产物），不是参考别人，必须保留（增量分析、续跑都依赖它）
- **标注**：跳过后在思考块「历史参考」标注"debug 模式跳过历史工单检索（独立孪生对照），无历史工单参考，仅注入段零工程文档"

**与 §11「debug 产物不可被自动注入」的区别**：§11 讲的是**别人检索不到 debug**（debug 不入索引，检索/段零不复用 debug）；本 § 讲的是 **debug 不去检索别人**（debug 分析跳过历史工单检索）。两者方向相反、互补，共同保证 debug 是"独立存在的对照源"，不与正式工单双向污染。
