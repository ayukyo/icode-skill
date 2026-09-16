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

按 `risk_profile.effective_mode`（未声明时按mode）选择阶段：full deepcheck的Reverse/Fixed/Free，实际fast仅Reverse，audit只用独立 `audit` phase；fast请求升级full后仍须三个阶段。每个必需阶段声明metadata.code_files的全部文件及当前hash。修改代码后重新复检，不能将旧Read hash改成新值冒充重读。

`review_scope`依project_intake包含受影响代码及调用/导入/提供者/等价候选；large/huge遵守预算，在明确范围内完成，不自动扩大vendor全树。Dedup按声明范围计算catalog/分类/实际出现家族与逐家族语义裁决，范围内缺任何环节结果即partial/degraded。`complete_within_scope`不能写“全SDK/固定23类全部通过”。Audit可引用有效且源hash未漂移的Dedup结果，但Audit的Read独立完成。

不足时填 `coverage_status=partial|degraded`、`unobserved`、`debt_reason`，以及 `dedup_status=partial|degraded` 与 `dedup_unobserved`；使用finish degraded，报告明确债务/下一步，不称合规全部通过。degraded也须真实声明回执与原因；success拒绝缺文件/阶段、旧hash和未完成项。允许显式降级结束与实现/构建通过同时存在，但不能升级delivery_verdict。

**证据边界**：manifest/hash/回执只能校验代理的覆盖声明、来源及版本一致，不能自动证明实际Read、理解、语义判断或实机行为。遗漏仍需人工独立复核，不能用机器绿灯替代。

统一终检只对最新真实attempt已有finish degraded的覆盖声明接受不足，并输出 `verification_debt` 警告；对应artifact路径/hash须与该attempt回执一致。文件自报partial、旧attempt降级或事后替换报告均不能豁免，新attempt必须有自己的覆盖回执。终检返回通过表示产物与已登记债务一致，不表示债务已解除。

## 3. MCP与偏差

新工单及trace使用cheap gates的 `trace_schema_version=2`。called必须attempted=true；evidence满足catalog必需字段/类型。cache_hit要64位cache_key及input_digest（tool、参数和被读源码hash身份）；不能把目录/文件名未变当缓存有效。v2工单拒绝v1 trace降级。存量v1非空证据可兼容读取，缺新字段报告evidence_untracked，绝不解释为新证据合同全部满足；明显called未尝试/空证据仍拒绝。不要为变绿改写历史trace。

工具履行coverage与Read/去重语义覆盖分开；真实工具结果保留来源引用。字段/形状校验不能证明工具实际执行；原始syscall errno和策略reason分开，结果/计数日志逐分支对照真实条件。

v2另按catalog内全部gate的明确分支复算确定性资格，阈值只读catalog constants；自报eligible或threshold与已声明事实冲突即失败。deepcheck阶段依据须符合有效模式，升级full不能借请求fast跳过。此一致性检查不替代对事实来源真实性的独立审查。

偏差先记metadata.code_deviations稳定id与plan_said/actual_done/reason/source refs，各报告引用同id；没有偏差才能[]。item schema约束结构，跨报告语义一致性仍由终审核对，不用关键词猜测。历史补记写真实事后时间和来源。
