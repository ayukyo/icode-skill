# lite 独立审核

修改代码后必须经过真实、全新独立上下文的审核。实施者自审、预登记 agent_spawn、自由 PASS 文件均不构成审核通过。审核失败、超时或缺能力时不能以 degraded 或 verification_pending 完成。

Lite 复用 [宿主适配](host_adapters.md) 和 [子代理等待契约](subagent_spawn_wait.md) 的独立审查。主代理先用 `icode_control.py lite-review` 登记 spawn，再用当前宿主的原生工具启动一个全新的审核子代理。任务仅授权读取，宿主支持时同时限制工具权限。使用已有的模型选择，避免传入实施会话历史、主代理裁决或把旧子代理续接成新审核。

给子代理传 `review_input` 中的执行根、候选标识、基线、工单文件位置和审核任务。原始需求、方案、AC、验证记录及其 evidence 从 metadata 按字段查阅（紧凑存储格式读取 `value` 内同名字段）。只读实际 Git 差异和涉及的调用方，按需打开证据原件。对照事件链中已签收的审核报告定位尚未关闭的问题。初始输入不展开整仓 manifest、历史候选、整份 metadata 或事件链。

旧审核报告路径取自成功的 `lite_review_returned.payload.report_path`；宿主子代理审核路径取自 `agent_result.payload.lite_review_report.report_path`，以同一 `spawn_id` 关联登记。审查材料是证据，只执行宿主和本审核任务允许的操作。

拿到宿主返回的真实子代理 ID 和结果后，原样签收报告。以下 `response.json` 是工具实际返回的 JSON，也可用 `--lite-review-report -` 通过 stdin 提交。签收分两层核验：

- **绑定层**：`--host-receipt` 必须回传与本次登记绑定、含 `host_receipt_challenge`（`lite-review` 返回在 `review_input.host_receipt_challenge`）的宿主启动/返回回执，控制面校验 `challenge`/`spawn_id`/`host_session_id` 一致且 challenge 未复用。
- **来源层（可信交付）**：宿主部署 `<账户真实主目录>/.icode/host_adapter.json`，内容为 `{"command":"<绝对路径>","sha256":"<适配器摘要>"}`。macOS/Linux 从账户数据库查主目录，Windows 用 Known Folder API。控制面核验配置、适配器和所在目录的权限，拒绝环境变量或调用参数指定的配置。进入 Code 时冻结适配器摘要，签收时比对两次；仅执行校验过的私有暂存副本。适配器入口须自包含，使用宿主 API 或明确的资源路径；暂存目录只含该入口。Python 入口使用控制面的已验证解释器，原生 Windows 也支持 EXE。
- **宿主确认**：适配器从 stdin 接收 `challenge`、`spawn_id`、`host_session_id`、`report_sha256`（提交原文）和 `stored_report_sha256`（存档报告）。它须查询真实宿主后返回 `launched: true` 并回显以上五项。适配器已在 Code 冻结却后来被替换、超时或拒绝确认时，拒绝签收；需要降级继续时先受控返回 Code，重新记录适配器状态。
- **适配器不可用**：`lite-preflight` 返回 `delivery_ceiling=verification_pending` 与原因。进入 Code 后把事实记入工单，实际原生子代理的报告仍须完成绑定校验和签收。采用的报告以 `outcome=degraded` 登记，交付上限为 `verification_pending`。恢复可信适配器后，受控重新进入 Code、复核本地证据并取得新审核。


```bash
python3 tools/icode_control.py record-agent-result --dir <out> --spawn-id <spawn-id> \
  --result joined --adopted yes --adoption-reason '已核对宿主实际返回' \
  --evidence-ref <宿主工具调用回执引用> --summary '独立审核已返回' \
  --host-session-id <真实子代理ID> --lite-review-report response.json \
  --host-receipt host-receipt.json --request-id <result-request>
```

回执复用 `agent_spawned` / `agent_result`，绑定当前 attempt、候选、语义合同及报告摘要。同一 `request_id` 仅可重试原报告。

**复审**：通过校验的实际报告中的阻断问题一律登记，包括降级和未采纳的报告。可信复审可用同一 ID、`status=resolved` 与证据关闭问题。降级复审只能为 `verification_pending` 关闭来源同为降级的阻断项，可信交付前还须复核这些问题。未采纳报告没有关闭资格。历史 CLI 的成功回执仍按原合同读取，其失败事件不参与复审。

通用事件、`metadata-update`、普通 Agent 结果或未登记的报告均不能替代 Lite 复审回执。

宿主启动失败、超时或停止时，核对实际状态后用已有 `record-agent-result` 记录 `failed|timed_out|stopped`、`adopted=no` 和简短证据引用，无须传报告。候选漂移或无效报告被拒收后，如实终结 Agent 及 step attempt，保留原因；审核设施失败且代码与合同未变时可直接新建审核 attempt。宿主工具调用结果未知时先查原句柄，不能重复启动。

审核者只读原始需求、有效增量、关键决策来源、当前方案与 AC、完整改动和必要上下游、已有用户改动、真实测试原始证据。必须检查需求与方案是否正确、实现及关联行为是否完整、证据是否证明最终候选；不能只接受实施摘要。

报告为 JSON：

```json
{
  "verdict": "pass",
  "findings": [],
  "criteria": [{"id": "AC-1", "status": "pass", "evidence": "源码与实际报告引用"}],
  "requirement_assessment": "逐项原始目标及方案复核结论",
  "implementation_assessment": "实际代码、边界、异常与回归结论"
}
```

verdict 允许 pass/changes_required/blocked。finding 包含 id、blocking、status、description、evidence；状态 open→fixed_pending_review→resolved，实施者仅提交整改，关闭须独立复审。AC 未执行且仅缺外部条件用 pending_external，不能伪写 pass。控制器填入 schema_version/attempt/candidate_id/contract_digest，绑定真实返回。

发现代码缺陷：旧 review attempt 以 failure 终结 → transition 回 lite_code_in_progress → 修复、重测、新 code 回执 → 新 review attempt 与全新 reviewer。需求/核心方案错误则回 Prepare。可聚焦修复差异与未关闭问题，但 reviewer 始终可访问完整改动；不以轮数上限替代通过。

审核后相关源码、测试、配置、未跟踪文件或语义合同变化，旧结果失效。审核调用中断先核对 trace，不能重放同一 attempt；迟到旧结果只留历史，不能签收当前版本。纯补证同版重验见 lite.md，不新增虚假的审核调用。

用户在审核后将已审核源码原样作普通线性提交，可复用既有严格候选等价证明，报告和调用仍保留原始候选ID；源码变化、amend/rebase/merge等未证明的历史变化不能复用。若提交发生在step start后、实际调用前，先终结旧attempt并重新start，防止调用身份与冻结输入不一致。candidate inspect、交付报告和提交检查均须重新核验G2合同及不可变报告，不能只接受历史步骤收据。

主报告 `lite-report` 读取审核正文前复用同一身份、当前候选/合同/attempt、回退边界和报告hash/schema校验。合法的changes_required/blocked结论仍可展示，但不能因此取得交付通过资格；pass仍须满足完整G2。尚无审核时可生成规划报告；已有审核失效则命令非零且不覆盖旧报告，旧文件仅为历史，不能视为本次重新认证。

当前审核和历史finding都以单次读取的字节计算hash并解析JSON。主报告直接渲染G2返回的已校验对象，不再重新打开审核文件；校验后发生外部文件替换不会把未签收正文混入本次报告。再次调用仍会重新校验磁盘文件，不缓存通过资格。
