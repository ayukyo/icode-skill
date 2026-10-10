# 漏检经验记录（人工模板）

用户确认需要保留经验时，填写以下记录。它用于说明哪一阶段漏检、哪个检查缺失、该如何改善控制；不自动生成默认 rule/skill，不自动同步或发布，也不将单次设备/产品结果泛化为所有产品的事实。字段只表达已有证据，不硬编码产品 PID。

```json
{
  "escape_phase": "<原本应发现问题的阶段>",
  "detected_by": "<实际发现问题的检查、现场证据或人工复核>",
  "missing_check": "<缺失的具体检查与输入边界>",
  "affected_candidate": "<受影响的真实candidate_id；无法绑定时明确untracked>",
  "generic_pattern": "<有证据支持的可复用模式，不把产品特例写成通用规则>",
  "recommended_control": "<建议的最小检查或验证控制，由用户决定是否采用>"
}
```

对故障注入、设备身份和实机证据，引用 [evidence_and_verification.md](evidence_and_verification.md) 的验证合同与来源边界。静态模拟只证明模拟路径；源码、构建、部署、运行版本及设备身份分别绑定。`recommended_control` 只是建议，不能替代当轮补测、候选新鲜度或用户确认。
