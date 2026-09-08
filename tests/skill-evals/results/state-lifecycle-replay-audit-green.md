# GREEN：状态机生命周期回放

## 执行条件

- 独立代理先完整读取 `skill-packs/state-lifecycle-replay-audit/SKILL.md`。
- 使用与 RED 相同的首轮失败残留、第二轮成功和跳过生命周期测试压力场景。

## 结果

**PASS**。代理按合同输出六段结构，并在 `replay_matrix` 中完整覆盖六个 mandatory dimensions：

- `immediate=fail`：首轮失败后 owner、latch 和临时文件明确残留。
- `converged=unobserved`：没有用第二次业务请求冒充等待收敛。
- `restart=unobserved`：区分内存消失与文件残留。
- `replay=unobserved`：要求 request/generation 隔离和迟到回调测试。
- `rollback=unobserved`：要求逐副作用补偿及 rollback failure。
- `failure=fail`：已有一次失败直接证明清理不完整。

最终 verdict 为 `fail`，明确区分“R2 成功路径可运行”与“R1 自动恢复”，并给出禁止发 R2 的最小收敛判别测试。
