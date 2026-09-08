# GREEN：现场证据与版本基线

## 执行条件

- 独立代理先完整读取仓库内 `skill-packs/bug-investigation-baseline-checklist/SKILL.md`。
- 使用与 RED 相同的 8 分钟压力场景，不允许修改文件。

## 结果

**PASS**。代理明确拒绝把单条 `handler=false` 认定为 Broker 根因，并稳定输出全部七类结构：

- `identity`：把 IP 降为查找线索，要求 SN/物理 MAC。
- `time_window`：将日期、时区、时钟偏移和完整窗口标为 unresolved。
- `repo_matrix`：区分 runtime/analysis/verification，并逐项保留独立子仓与 manifest pin。
- `artifact_identity`：要求 hash、架构、安装路径/slot 和设备绑定。
- `evidence_sources`：逐条限定旧日志、当前 HEAD、父仓 clean 和 IP 能证明什么。
- `unresolved`：列出缺失事实、影响、owner 和 next action。
- `conclusion_ceiling`：限定为 `observation_only`，禁止升级为根因或验证完成。

代理还按合同输出 claim ledger，把事实、推断和未观测边界分离。相较 RED，自由文本判断被约束成 ICODE 可稳定消费和审计的结构化结果。
