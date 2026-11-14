# lite 独立审核

修改代码后必须经过真实、全新独立上下文的审核。实施者自审、预登记 agent_spawn、自由 PASS 文件均不构成审核通过。审核失败、超时或缺能力时不能以 degraded 或 verification_pending 完成。

受控 `icode_control.py lite-review` 启动本机 Codex 或 Claude CLI。Codex 使用 ephemeral + read-only；Claude 使用 no-session-persistence + plan。使用用户当前选择的模型，不主动降档。实际返回的 thread/session ID、执行起止、当前 step attempt、候选、语义合同及不可变 JSON 报告摘要共同构成回执；通用 event/metadata-update 不能签署它。

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
