# 审查证据与覆盖合同

机器执行真源为 `tools/icode_control.py` 的 `review_evidence_issues`、`inspection_coverage_issues`、步骤端口及实际artifact摘要事件；本文解释数据格式，不维护第二套退出条件。不增加公开 `/icode` 命令。

关联范围、必审单元、逐文件逐阶段阅读与 finding 源码定位统一执行 [inspection_worklist.md](inspection_worklist.md)。新执行的 worklist 与本文 coverage/Dedup 声明并行必需；哈希和声明不能证明实际阅读/语义正确，主代理仍复核决定性证据。

## 1. review轮次回执

`review_manifest.json` 是review必填输出；每个已完成轮次当场更新manifest、登记真实artifact。首轮始终写 `review_round_1.json`，后续三类issue全空才可省详细JSON。`has_new_issues=false`、clean收敛次数与三类全部为空不同。

```json
{
  "schema_version": 1,
  "ticket_id": "example-1",
  "review_run": "首轮真实step attempt",
  "rounds": [
    {
      "round": 1,
      "origin_attempt": "首轮真实step attempt",
      "new_issues": 0,
      "refuted_issues": 0,
      "pending_verification": 1,
      "detail_path": "review_round_1.json",
      "detail_sha256": "实际文件SHA256"
    },
    {
      "round": 2,
      "origin_attempt": "本轮真实step attempt",
      "new_issues": 0,
      "refuted_issues": 0,
      "pending_verification": 0,
      "detail_path": null,
      "detail_sha256": null
    }
  ]
}
```

round从1连续记录已完成轮次；`metadata.total_rounds`可能已增加为下一轮游标，不能直接当manifest长度。详细JSON三类必须是数组，计数和SHA256与manifest一致。

manifest本体要当前attempt回执；详细JSON要真实origin_attempt的review启动之后、结束之前的artifact回执。计划内修改受保护输入时，按execution_model以blocked终结原attempt、新start复检；manifest沿用同review_run的历史来源，只登记新manifest确认最终数据，不把历史JSON重新登记为当时已写。重新进入review_in_progress的完整新审查运行以新首轮attempt初始化manifest，旧文件/glob不能满足新运行。

```bash
python3 tools/icode_control.py artifact --dir <out> --step review --attempt <actual-attempt> --scope ticket --path review_round_1.json
python3 tools/icode_control.py artifact --dir <out> --step review --attempt <actual-attempt> --scope ticket --path review_manifest.json
python3 tools/icode_control.py check-outputs --dir <out> --step review
```

控制面finish success检查条件JSON、计数、来源及manifest回执；merge前运行同一只读check。历史已完成旧合同不倒填新回执，标historical_evidence_untracked；旧审查缺manifest时仍核对原有首轮JSON三数组。若连首轮结构化JSON都缺，终检报告缺证据，必须真实重跑，不能事后补造。新合规结论需要真实重跑。旧open attempt遇合同漂移走已有blocked重入，禁止强制刷新摘要。

## 2. deepcheck/audit阶段覆盖

分别写 `deepcheck_coverage.json`、`audit_coverage.json`；本attempt结束前登记对应真实artifact。仅在实际Read对应完整文件之后记录hash，不得先批量hash再自称完成Read。

```json
{
  "schema_version": 1,
  "ticket_id": "example-1",
  "attempt": "当前真实step attempt",
  "review_scope": ["src", "include", "有证据的外部caller/provider路径"],
  "coverage_status": "complete_within_scope",
  "unobserved": [],
  "read_phases": {
    "reverse": {"src/example.cpp": "实际Read版本SHA256"},
    "fixed": {"src/example.cpp": "该阶段实际Read版本SHA256"},
    "free": {"src/example.cpp": "该阶段实际Read版本SHA256"}
  },
  "dedup_status": "not_eligible",
  "dedup_reason": "范围内函数数未达catalog阈值",
  "dedup_unobserved": []
}
```

按 `tools/fast_policy.py` 的 `effective_mode(metadata)` 统一选择阶段：full deepcheck的Reverse/Fixed/Free，实际fast仅Reverse，audit只用独立 `audit` phase；fast请求升级full后仍须三个阶段。每个必需阶段声明metadata.code_files的全部文件及当前hash。修改代码后重新复检，不能将旧Read hash改成新值冒充重读。

`review_scope`依project_intake包含受影响代码及调用/导入/提供者/等价候选；large/huge遵守预算，在明确范围内完成，不自动扩大vendor全树。Dedup按声明范围计算catalog/分类/实际出现家族与逐家族语义裁决，范围内缺任何环节结果即partial/degraded。`complete_within_scope`不能写“全SDK/固定23类全部通过”。Audit可引用有效且源hash未漂移的Dedup结果，但Audit的Read独立完成。

新 audit attempt 复用 deepcheck 去重时，必须引用本工单真实成功的 full deepcheck：范围一致、coverage 产物回执和摘要有效，当前源码及阈值与其证据一致；仅有 MCP 结果时还需可验证且覆盖范围的候选源码快照。也可提交本轮可验证的人工去重证据。旧 audit attempt 沿用其开始时合同；无法核实的来源须保留债务，不得冒充完成。

### 无 cheap-research 的人工等价去重

工具不可用时可由当前宿主按原始源码进行实际人工等价审查；不可将工具未暴露、摘要、grep命中或覆盖声明本身冒充语义审查。逐文件读完整范围，形成实际函数目录、家族分类、逐家族比较、结论和源码位置；任一范围/语言/文件/家族未读、截断、不可解析或预算耗尽，均保持 `partial|degraded` 和明确债务，不得写 `complete_within_scope`。保留现有对删除/合并候选的独立验证及授权约束。

实现提供 `tools/manual_dedup_evidence.py`：`collect_sources(workspace, review_scope)` 按实际 Git 工作区的 tracked/unignored 文件有界枚举并计算摘要；未知文件类型、嵌套 Git、超预算和无法访问均失败，需缩小声明范围或保留债务。Git 忽略文件不在分母，不能据此声称覆盖这些文件；涉及忽略源码、非 Git 工程或受保护 Git provider 未接线时不可冒充完整。`validate_report(report, workspace, ticket_id, attempt, review_scope, expected_sources)` 将来源绑定当前工单、attempt、范围和独立枚举结果，校验源码摘要、函数/家族及引用。Python 用 AST 验证普通、异步及嵌套函数目录；JS/TS/Vue/C 等已识别语言使用 `manual_read`，按文件声明真实阅读、语言和完整性，并逐函数登记原始行号和逐字原文。**机器无法证明人工阅读、分类或语义完整，审查者仍须独立核对；空目录也须明确人工完成声明及理由。**

若控制面当前合同支持 `manual_dedup`，将其作为当前步骤 coverage JSON 中的内嵌对象，并经现有 `inspection_coverage` artifact 回执绑定整个文件摘要；没有可验证接线时不得声称已通过机器门。来源对象示例（真实数据和摘要以实际读取为准）：

```json
{"schema_version":1,"ticket_id":"example-1","attempt":"真实attempt","review_scope":["src"],"unobserved":[],
 "sources":[{"path":"src/view.ts","sha256":"实际sha256","read_sha256":"实际sha256","language":"typescript",
   "catalog_method":"manual_read","completeness":"complete_within_scope","catalog_reason":"实际通读与编目依据",
   "functions":[{"name":"render","line":1,"excerpt":"export function render() {}","family":"view"}]}],
 "families":[{"name":"view","members":["src/view.ts:1:render"],"conclusion":"single",
   "reason":"实际比较依据","evidence":["src/view.ts:1"]}]}
```

人工清偿 review 枚举债务时，须先在成功结束的 plan attempt 中登记 `01_plan.md` 和包含全部计划改动的 `plan_candidates.json`；当前真实 review attempt 启动时冻结两者，并将 `review_dedup_evidence.json` 作为受控产物登记。控制面验证事件链、同一计划 attempt 的产物摘要、当前 review 启动及报告摘要回执，再将计划候选文件和已登记修改源码逐一与审查范围核对。**登记时即完成一次完整校验**（结构、函数目录、家族/去重结论、范围覆盖），结果写入受事件链保护的回执；不完整报告拒绝登记。此后 code 阶段修改或按计划删除源码，以该回执维持 review 时的清偿，不再用新源码重验旧报告。旧计划或报告缺少回执、清单无法确定或摘要漂移时保留债务。债务同样可由后续 `deepcheck.dedup` 清偿：工具真实成功，或该 deepcheck attempt 真实成功结束（`success`/`degraded`）且复检报告经 `artifact --step deepcheck --path deepcheck_dedup_evidence.json` 受控登记的人工复检；复检须按仓库逐项覆盖原债务的 `repo_review_scopes`：目录包含关系成立才算覆盖（祖先目录/全仓覆盖子目录），同一仓库多笔债务范围累积，范围非法或无法判定不构成覆盖；债务侧未声明范围按**全仓**保守要求；清偿/复检记录必须显式声明 `repo_review_scopes`，未声明时实际范围未知、不构成覆盖。review 重做同样按此比较（计划覆盖不等于覆盖债务范围）。未结束、失败或未登记的人工复检不构成覆盖。trace 的 `availability.evidence_sha256` 可选——只填 `evidence_ref` 时以受控回执摘要完成绑定。trace 的 `evidence.repo_review_scopes` 将每个受影响仓库根映射为该仓的相对审查范围；多仓证据文件使用 `repositories` 数组，逐项提供 `repo_root` 和该仓 `manual_dedup` 报告。控制面逐仓重新枚举和校验报告，并核对函数数最大值。未声明 `repo_review_scopes` 的记录：债务侧按**全仓**保守要求（局部范围不构成覆盖），清偿/复检侧视为范围未知、不构成任何覆盖（必须显式声明范围）。具体格式与登记命令见 [审查步骤](../steps/02_review.md)。

Python 文件用 `catalog_method=python_ast`、真实 `sha256` 及每个函数的 `name/line/family`；每个实际家族须给 `single/different/duplicates`、理由和所有成员的源码行引用。工具验收仅证明结构、版本、引用一致；不能把静态证据当作模型真正理解的证明。Audit 使用自己 attempt 的覆盖与独立 Read；如要引用 deepcheck 的人工去重，须独立验证原 attempt 真实 artifact 回执及当前范围和源码一致；未支持该可信回指时重新审查或保留债务，禁止直接改 attempt 复用。

不足时填 `coverage_status=partial|degraded`、`unobserved`、`debt_reason`，以及 `dedup_status=partial|degraded` 与 `dedup_unobserved`；使用finish degraded，报告明确债务/下一步，不称合规全部通过。degraded也须真实声明回执与原因；success拒绝缺文件/阶段、旧hash和未完成项。允许显式降级结束与实现/构建通过同时存在，但不能升级delivery_verdict。

**证据边界**：manifest/hash/回执只能校验代理的覆盖声明、来源及版本一致，不能自动证明实际Read、理解、语义判断或实机行为。遗漏仍需人工独立复核，不能用机器绿灯替代。

统一终检只对最新真实attempt已有finish degraded的覆盖声明接受不足，并输出 `verification_debt` 警告；对应artifact路径/hash须与该attempt回执一致。文件自报partial、旧attempt降级或事后替换报告均不能豁免，新attempt必须有自己的覆盖回执。终检返回通过表示产物与已登记债务一致，不表示债务已解除。

## 3. MCP与偏差

新工单及trace使用cheap gates的 `trace_schema_version=2`。called必须attempted=true；evidence满足catalog必需字段/类型。cache_hit要64位cache_key及input_digest（tool、参数和被读源码hash身份）；不能把目录/文件名未变当缓存有效。v2工单拒绝v1 trace降级。存量v1非空证据可兼容读取，缺新字段报告evidence_untracked，绝不解释为新证据合同全部满足；明显called未尝试/空证据仍拒绝。不要为变绿改写历史trace。**资格合同版本**：trace 行可带 `contract_version`（gates.json 机器真源）；无该字段的存量行按合同 v1 旧语义解释，不追溯改判；vNext 出生 metadata `mcp_gate_contract_version=2`（受保护）下全部行按 v2 解释并禁止行降级；旧工单续跑需满足新合同时真实重做相应审查并追加带 `contract_version: 2` 的新行（同 gate 最后一条为准），不得改写历史行。续跑的机器边界：存量工单 metadata 缺合同字段时，控制面在下一次 metadata 写入（`adopt_gate_contract`）补齐 `mcp_gate_contract_version` 并记录 `mcp_gate_contract_adopted_at`（显式 UTC）——linter 以历史边界区分历史行（v1 解释）与续跑新行（当前合同，含无法解析时间的行）：采纳时存在升级前未终结 attempt 则边界回退为其最早 start（`mcp_gate_contract_history_boundary`），升级后、采纳前写入的 trace 按当前合同解释。review.dedup 在 ripgrep 与替代枚举都不可用时写 `enumeration_unverified=true` 声明「枚举未知、保留债务」，唯一合法记录是 eligible=true + unavailable/degraded（called/cache_hit/skip 均非法，与尝试值是否达到阈值无关），报告以 enumeration_debt 计数暴露未解决债务；该债务按当前有效 trace 逐行推演计算工单级余额、跨步骤持续存在；清偿要求出现通过资格与证据校验、属于当前工单的有效最终记录（pending/中断/非法重做行不清偿）；unavailable/degraded 清偿记录还须机器可验证的人工等价证据（availability.evidence_ref 指向工单目录内的人工去重报告，trace 须携带 attempt，报告须绑定当前工单、attempt、受影响仓库范围和源码，并通过 manual_dedup_evidence 校验；可信枚举低于阈值的业务不适用记录可直接清偿；清偿记录须覆盖产生债务的全部受影响仓库）。trace 含其他工单记录、不可读或丢失（而事件链曾记录未清偿债务）时报告为未知（null）而非清偿。余额由控制面透传进门禁结果、状态推进输出与 state_changed 事件（gate_enumeration_debt，0 也显式证明清偿），交付结论可见。trace 行 `at` 建议显式带时区；无时区值按本地时间解释（与控制面事件时间同约定）。

工具履行coverage与Read/去重语义覆盖分开；真实工具结果保留来源引用。字段/形状校验不能证明工具实际执行；原始syscall errno和策略reason分开，结果/计数日志逐分支对照真实条件。

v2另按catalog内全部gate的明确分支复算确定性资格，阈值只读catalog constants；自报eligible或threshold与已声明事实冲突即失败。deepcheck阶段依据须符合有效模式，升级full不能借请求fast跳过。此一致性检查不替代对事实来源真实性的独立审查。

偏差先记metadata.code_deviations稳定id与plan_said/actual_done/reason/source refs，各报告引用同id；没有偏差才能[]。item schema约束结构，跨报告语义一致性仍由终审核对，不用关键词猜测。历史补记写真实事后时间和来源。

## fast 的表达压缩边界

plan 的短合同、review 的发现清单、merge 的完整计划机械复制和 code/deepcheck/audit 的差异正文，均不改变本合同的实际 Read、origin attempt、worklist、coverage 与 artifact 要求。catalog `required_checks` 是必须补齐的检查清单，评估返回清单不等于检查完成。Reverse/Audit 分别真实独立读取并记本轮回执，不能共享一条“已审”声明。矩阵可从有效账本生成，但新验收裁决仍由本轮独立审查形成；缺真实环境或阶段接受继续保留债务。
