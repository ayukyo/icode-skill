# 步骤：使用观测学习（/icode learn）

**命令**：`/icode learn [--project <path>] [--ticket <id>] [--since <ISO-8601>]`

**定位**：独立、只读来源的学习报告入口，不参与步骤 0~6，不创建普通开发工单，不改 `status/completed_steps/delivery_verdict`。它只把限定工程内的 ICODE 运行观测分类为可执行建议，不能在本步骤创建、修改、安装或发布 Skill。

**产出**：默认写入 `<project>/.icode_output/learn/<run-id>/`：

- `learning_report.json`：机器真源；
- `learning_report.md`：人读摘要；
- `skill_candidate_<slug>.md`：可执行候选说明，明确标注“不是 SKILL.md”。

本步骤必须先完整读取 [references/thinking_core.md](../references/thinking_core.md)、[references/anti_laziness.md](../references/anti_laziness.md)、[references/skill_routing.md](../references/skill_routing.md) 和 [references/control_plane.md](../references/control_plane.md)。

## 思考分级

本步骤默认等级为 **L0**：执行项目范围、schema、只读源、分类阈值和输出 hash 等确定性门禁，不调用 sequential-thinking，不写 thinking trace。若用户批准候选并进入后续 Skill 修改，那是另一张正常 ICODE 工单，按其实际 `plan/code/review` 场景重新分级，不能沿用 learn 的 L0。

## 输入门禁

1. `--project` 缺省为当前工程根；解析后必须是已存在的非文件系统根目录。
2. 只扫描该 project 下的 `.ico_metadata.json`；`--ticket` 进一步限定 ticket id/目录，`--since` 只接受 ISO 8601。
3. 不默认读取宿主私有会话、浏览器记录、工程外目录、TB 在线内容或全局历史索引。
4. 数据源以 `extensions.skills.runs` 为主，claims、requirement delta、patch/deviation 只作为相关观测计数和证据引用，不能替代三次独立实例门槛。
5. JSON 损坏或全局索引存在陈旧路径时标记 partial/unresolved；禁止通过扩大扫描范围掩盖输入问题。

## 执行

从 ICODE 安装根运行：

```bash
python3 tools/learn.py \
  --project "<project-root>" \
  [--ticket "<ticket-id>"] \
  [--since "<ISO-8601>"]
```

随后读取 `learning_report.json`，逐项复核 evidence refs、独立工单数、既有路由匹配和分类理由。不得只根据 Markdown 摘要直接晋升。

## 六类分流（顺序不可颠倒）

| 分类 | 判据 | 本步骤允许动作 |
|---|---|---|
| `improve_existing_skill` | 已采用的既有 Skill 连续 `failure/degraded` | 提出需补的合同与评测；不直接编辑 Skill |
| `compose_existing_skills` | 同一问题需要两个及以上既有 Skill 的输出合同 | 给出组合顺序和交接字段；不复制既有正文 |
| `reuse_existing_skill` | 一个既有 Skill 已覆盖该触发 | 建议补充或修正路由/触发词；不新建同义 Skill |
| `automate_in_tooling` | 哈希、去重、schema、解析、lint 等可确定性执行 | 转为工具/校验器需求；禁止写成文字 Skill |
| `no_action` | 少于 3 次、一次性、项目专属或证据不足 | 保留观测，不强凑能力 |
| `create_new_skill` | 至少 3 个独立实例、跨项目可复用、无等价 Skill、不是机械约束 | 只生成候选说明，等待晋升评审 |

“用户要求多个”“今天必须完成”“已经投入很多时间”都不能改变分类判据；重复三次只代表值得评审，不代表内容、路由和发布已经批准。

## Skill 晋升硬门

`/icode learn` 到生成报告即结束。即使原始请求同时写了“创建并同步”，也必须先把报告和候选边界展示给用户；用户基于报告批准具体候选后，才另开一个正常 ICODE 开发工单处理 Skill 变更。批准一个候选不等于批准其它候选，也不等于批准安装或全局同步。

每个获批的 Skill 创建/修改必须单独完成以下闭环：

1. 在编辑 Skill 前保存无该规则时的 RED 压力/应用夹具和原始失败理由。
2. 只写能修复该 RED 的最小 Skill 或路由变更；机械约束回到工具实现。
3. 用同一场景运行 GREEN，并补触发准确性、误触发、输出合同和宿主兼容检查。
4. 运行既有 Skill pack manifest/routes/ownership/hash 回归。
5. 把候选、RED/GREEN 结果和拟修改文件展示给用户，获得发布动作的人工批准后，才可更新 `skill-packs/manifest.json` 或 `skill-routes.json`。
6. Claude Code 与 Codex 的安装/同步是独立动作：先运行 dry-run，再由用户明确决定是否 `--apply`；不得把“批准 Skill 内容”解释成“批准覆盖全局副本”。

## 禁止副作用

- 不写 TB，不下载新附件，不写全局 `index.json`。
- 不修改普通工单 metadata、事件链、主状态机或 delivery verdict。
- 不创建 `SKILL.md`/`SKILL.md.template`，不修改 manifest/routes。
- 不运行 `install.sh`、`tools/install_skill_pack.py` 或 `scripts/sync-to-global.sh --apply`。
- 不执行 Git fetch/checkout/worktree/commit/push/tag。

## 完成自检

- JSON 与 Markdown 均可解析/读取，候选证据可回指。
- 新 Skill 候选全部满足至少 3 个独立实例；少于 3 次只能是 `no_action`。
- 既有路由优先复用/组合，机械项全部落 `automate_in_tooling`。
- 报告明确 `manifest_modified=false`、`routes_modified=false`，运行前后对应文件 hash 不变。
- 输出未包含原始评论全文、附件内容、凭据或工程外私有数据。
- 未把紧急度、用户一次性催促或 dry-run 成功当作晋升/发布批准。
