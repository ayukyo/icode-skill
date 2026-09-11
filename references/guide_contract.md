# `init --guide` 新人指导文档契约

本契约只服务 `/icode init --guide [补充要求]`。它把既有 `00_init.md` 中已经收集的工程事实和证据重新组织成可交给新人的独立指南；它不是新需求、不是主流程步骤，也不改变原工单状态。

## 1. 固定输入与输出

- 输入：当前工作区**最新且合格**的正常工单，必须满足 `status=init_in_progress`、步骤 0 标记存在、`00_init.md` 含 7 个必需章节且各章有实质内容；零字节、纯模板或仅写“待补”的较新初稿不合格，不能遮住较早的完整分析。
- 输出正文：`{ICODE_OUT_DIR}/deliverables/guide.md`。
- 内部证据账本：`{ICODE_OUT_DIR}/deliverables/guide.audit.json`。
- 重跑：刷新上述两个固定文件；不得另建工单，不得递增 N，不得修改 `00_init.md`、`.ico_metadata.json`、事件链、全局索引、`status` 或 `completed_steps`。
- `--guide` 后的文字只约束读者、范围、篇幅、排除项和呈现方式，不是对原需求的补充。例如“给新人看，不要提邮件”应转成指南约束，不能写回需求初稿。

## 2. 来源与事实边界

生成前必须同时阅读 `00_init.md` 和它引用的本地源码、脚本、配置、测试、文档及已落盘证据。不能仅凭 `00_init.md` 的结论重述，更不能补写仓库与证据中不存在的接口、流程、命令、阈值或测试结果。

每个会影响读者决策的陈述都在内部账本 `claims` 中归入以下一种 `kind`：

| kind | 含义 | 指南写法 |
|---|---|---|
| `current_fact` | 当前代码、配置或当前有效文档可直接证明 | 用确定语气，并在账本给出精确 `source_refs` |
| `historical_evidence` | 历史邮件、报告、日志、表格或旧版本曾出现的做法/结果 | 正文明确“历史样例/当时版本”，账本必须写适用 `scope` |
| `engineering_recommendation` | 为补齐缺口而给的建议，不是现行制度 | 正文明确“建议”，不得伪装成必做规则 |
| `unverified` | 证据不足、当前环境未执行或仍需人工确认 | 正文明确“待确认/未实测”，账本必须写缺口 `scope` |

`kind` 不只存在于账本：`historical_evidence` 的正文必须出现“历史/当时/曾”等边界词，`engineering_recommendation` 必须写“建议/推荐”，`unverified` 必须写“待确认/未验证/仍需验证”等；不能把历史数字在正文写成无时间边界的当前能力。

证据权威顺序：当前实现与当前有效配置 > 当前可执行脚本/测试 > 当前文档 > 历史记录。历史说法与当前代码冲突时，以当前代码为准并在正文解释差异；“邮件里说过”“旧报告通过”不能推导为当前版本已经通过。

## 3. 数字与通过标准

凡是“多少次、多久、概率、通过率、失败率、阈值、默认轮数”等会影响验收的数字（含 `6 万次`、`1/10` 等写法），必须在对应 claim 的 `quantitative` 中分类：

- `acceptance_threshold`：正式准入/通过阈值；必须有当前有效依据，否则只能写建议或待确认。
- `observed_result`：某次历史或当前观测结果，例如“连续运行 12 小时”；不能改写成通用通过线。
- `defect_rate`：复现概率、失败率、成功率等统计结果；同时说明样本数、版本、设备和环境范围，缺一则降级为局部观察。
- `tool_default`：工具脚本自身默认值，例如默认循环次数；不能改写成项目验收标准。

`value` 与 `unit` 必填。若找不到正式阈值，正文应直接写“当前资料未定义统一通过次数/概率”，再把拟定门槛放到“建议”中，禁止凭经验伪造一个通过数字。

## 4. 测试工具与部署审计

指南中出现的每个测试、转换、采集或部署工具都必须先检查真实脚本/配置，并在 `tool_checks` 记录：

- `name`：工具名；
- `source_path`：源码或安装位置；若只有历史路径，正文与账本都标历史；
- `run`：可复制的实际命令；
- `cwd`：要求从哪个**工作目录**运行；
- `prerequisites`：系统、ROS2、SDK、设备、权限、依赖包等前置条件；
- `config`：配置文件、环境变量和关键参数；
- `outputs`：输出目录、日志、报告、包或设备侧落点；
- `write_mode`：输出是 `none`、`overwrite`、`append`、`mixed` 或 `unknown`，即明确**覆盖还是追加**；
- `hardcoded_constraints`：硬编码设备号、IP、架构、路径、次数等限制；
- `verification`：`static` 仅表示读过实现，`runtime` 才表示本轮在声明环境执行成功。

只做静态检查时，正文不能写“工具可用/部署成功/测试通过”；应写“仓库提供该工具，实际运行仍需目标环境验证”。部署必须讲清“构建产物从哪里来 → 放到哪里 → 如何启动 → 如何确认版本/进程/数据 → 如何收集失败证据”，不能只给一个命令名。

## 5. 新人可读性与建议结构

正文必须脱离内部分析过程独立可读。标题可以按主题合并或改名，但 linter 会独立检查架构、接口、流程/交付、测试、环境/部署、边界/待确认六类语义，不能靠 `required_sections` 自报绕过；此外至少覆盖读者真正需要的部分：

1. 文档目的、适用范围与结论边界；
2. 工程定位和架构图；
3. 接口类型、发现方式、关键输入输出与一个端到端例子；
4. 从接口冻结、开发自测、提测、测试、问题回归到交付的流程图；
5. 测试环境、工具路径、运行方式、主要测试内容；
6. 部署/启动/验收/回滚或恢复方式；
7. 现有通过标准、历史观测数字与仍待确认项；
8. 新人第一天可执行的检查清单。

强制可读性门：

- **术语首次出现**时用一句白话解释；不能只罗列 Topic、Service、launch、SDK 等词。
- 必须分别给出**架构图和流程图**；每张图后紧跟“怎么看这张图”，说明起点、箭头、责任边界和失败时先查哪里。
- 至少给一个端到端例子，把“客户调用 → ROS2 封装 → SDK/设备 → 数据返回 → 测试验收”或本工程真实等价链路串起来。
- 明确区分“当前就是这样”“历史曾这样”“建议以后这样”“尚未验证”，不让新人猜语气。
- 标题和正文使用项目语言；路径、命令、标识符保持原样。

## 6. 对外正文清洁边界

`guide.md` 默认不得暴露生成机制或摄取来源：不出现 `/icode`、`.icode_output`、`00_init.md`、`.ico_metadata.json`、`ticket_id`、`completed_steps`、审查轮次/工单/步骤编号等内部词，也不写“根据某封邮件/本次对话生成”。完整禁用表由 linter 维护；确需编写 ICODE 自身指南时才传 `--allow-internal-terms`。

用户明确说“不要提 X”时，把 X 逐项作为 `--forbid-term` 传给校验器。隐藏来源不等于删除事实边界：历史数字仍要在正文标注“历史样例”，只是不披露不必要的采集过程或内部文件名。

## 7. 内部证据账本格式

最小示例：

```json
{
  "schema_version": 1,
  "profile": "beginner_guide",
  "source_init": "/abs/path/to/00_init.md",
  "guide": "guide.md",
  "required_sections": ["架构", "测试", "边界"],
  "claims": [
    {
      "text": "历史样例曾连续运行 12 小时。",
      "kind": "historical_evidence",
      "source_refs": ["report.xlsx#Sheet1!A1"],
      "scope": "仅对应当时的软件版本、设备和环境",
      "quantitative": {"category": "observed_result", "value": "12", "unit": "hour"}
    }
  ],
  "tool_checks": [
    {
      "name": "example_tool",
      "source_path": "tools/example.py",
      "run": "python3 tools/example.py --help",
      "cwd": "workspace root",
      "prerequisites": [],
      "config": [],
      "outputs": [],
      "write_mode": "none",
      "hardcoded_constraints": [],
      "verification": "static"
    }
  ],
  "manual_checks": {
    "jargon_explained": true,
    "diagrams_explained": true,
    "end_to_end_example": true,
    "fact_recommendation_boundary": true
  },
  "clean_rounds": []
}
```

初稿中的 `clean_rounds` 必须为空；两次 `--record-round` 成功后，linter 会原子写入轮次、UTC 时间、`guide_sha256`、`audit_basis_sha256`、前序回执和 `receipt_sha256`。第二轮校验第一轮回执链；第二轮后正文或事实账本再变化会让 hash 失效，必须重跑第二轮。回执用于发现误改/手填，不应表述为防恶意篡改的安全签名。

`source_refs` 必须能回读到本地真实文件，优先使用 `path:line`、表格 `path#sheet/cell`、日志文件+时间窗、命令输出文件等定位；不存在的路径、URL 或自然语言概述不能替代本地证据。正文中带“当前/现有/历史/建议/待确认/未验证”等决策语气的行必须纳入 `claims`，不能只登记少数样例。工具 `source_path` 同样必须存在。没有可执行测试/部署工具时允许 `tool_checks=[]`，但必须用 `no_tools_reason` 写出原因，并把完全相同的说明放进正文，不能静默留空。账本是内部审计文件，不交给普通指南读者。

## 8. 两轮清洁审计（强制）

两轮都必须从源证据重新回看，不能把第二轮变成对第一轮输出的复述；`clean_rounds` 只能由 linter 的 `--record-round` 写入，手填 `pass` 无效：

1. 第一轮：逐条检查来源、事实分层、所有数字类别、工具路径/工作目录/前置条件/输出/覆盖语义、静态与实测边界；修正后运行 linter。
2. 第二轮：从新人视角检查术语首释、图后说明、端到端例子、章节完整、事实与建议不混淆、内部机制与用户排除词未泄漏；修正后再次运行 linter。

两轮通过后在 `clean_rounds` 留痕，再执行：

```bash
python3 "${ICODE_SKILL_ROOT}/tools/lint_guide_contract.py" \
  --guide "{ICODE_OUT_DIR}/deliverables/guide.md" \
  --audit "{ICODE_OUT_DIR}/deliverables/guide.audit.json" \
  --record-round 1 [--forbid-term <用户要求排除的词>]

# 第二轮重新回看并修正后执行；本次成功会同时验证两轮 hash 回执
python3 "${ICODE_SKILL_ROOT}/tools/lint_guide_contract.py" \
  --guide "{ICODE_OUT_DIR}/deliverables/guide.md" \
  --audit "{ICODE_OUT_DIR}/deliverables/guide.audit.json" \
  --record-round 2 [--forbid-term <用户要求排除的词>] [--json]
```

任一轮或 linter 失败都不能宣称指南已完成。最终只向用户展示 `guide.md` 的绝对路径，并简述已确认范围与待确认项；`guide.audit.json` 仅作为可追溯附属物列出。
