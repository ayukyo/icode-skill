# 子代理 spawn 等待通用契约（共享规则）

> 本文件是 icode 所有"**spawn 子代理并等结果**"场景的**通用等待契约**——后台 spawn 方式、超时档位、墙钟硬截止。质疑者对抗是它的一个实例（质疑者特化规则见 [adversarial.md](adversarial.md)「显式等待 + 超时机制」「整合墙钟硬截止」两段，机械等待与墙钟规则与本契约逐条对齐）；本契约覆盖**非对抗** spawn 点：doc `99_code_facts_audit` 并行子代理、audit §6.7 三视角并发收集 facts、cheap-research 降级 `Agent(model="haiku")` 兜底、超大工单派子代理消化摘要、多个独立子任务并行启动等。**凡 spawn 子代理的步骤，默认适用本契约**（2026-08-23 实测：质疑者之外的其他子代理同样可挂死数小时，等待契约不止覆盖对抗场景）。

## 机械等待与墙钟契约

按 [宿主适配](host_adapters.md) 发现当前允许的后台启动、有限等待和停止能力。Claude 的 `Agent`/`TaskOutput` 仅为其宿主示例；Codex 使用实际暴露的 collaboration 启动、等待和停止接口。禁止虚构工具、模型类型或越过上层授权。若无可执行有界等待，记录未验证并停止依赖该结果的动作。

相互独立的质疑者按宿主可用并发容量分批启动，每位必须是独立上下文，不互相传递结论。等待使用宿主原生有限超时，单次等待同时服从上层交互限制；未返回不等于失败。首次等待默认 600 秒、后续每次重试默认 120 秒（可由工单允许的配置覆盖），分段等待以遵守宿主单次等待上限；默认总墙钟预算 1200 秒，宿主更严格限制优先；超过预算先停止仍运行的句柄，不能停止则记录事实，未返回结果保留未验证。已返回 verdict 仍须主代理核对证据。失败在宿主能力和授权允许时最多重试 2 次，第一次更换表述、第二次在宿主支持且授权时换合适的代理类型；类型不可用、策略禁止或容量不足须如实记录，不伪造重试，不因型号或工具名不存在冒充失败，亦不允许主代理代行冒充独立裁决。

质疑者角色、裁决优先级、独立性与真实性仍以 [adversarial.md](adversarial.md) 为准；结构性无能力、未暴露、策略禁止、容量不足及真实调用失败须分别留痕。

## 等待原语探测与降级

启动前记录实际发现依据及 `wait_primitive`。宿主原生等待、`TaskOutput` 与 `Monitor` 的调用均须能按指定超时交还控制权。

| 值 | 当前宿主能力 |
|---|---|
| `native_wait` | 原生子代理有界等待接口 |
| `task_output` | 实际可用的 `TaskOutput` |
| `monitor_until` | 实际可用的 `Monitor` 或等价条件等待 |
| `none` | 未发现有界等待能力 |

`none` 时，仅在步骤允许且还有不依赖结果的工作时，才并行后台启动。整合时只使用已实际返回的结果，未返回者标 `[未验证-无等待原语-未收口]`。无有界等待时不声称达到墙钟期限，也不把该标签当作调用失败。将 `wait_primitive` 与发现依据记入适用的 trace，以及 `adversarial_verification` 或 `extensions.agent.spawns[].join_condition`。依赖独立结果的动作保持阻塞。

## 观测记录契约（spawn 留痕，防"子代理干了什么"不可审计）

每次 spawn 子代理（含质疑者对抗、doc 审计、facts 提取、haiku 兜底、摘要消化、并行子任务等全部 5 类非对抗 spawn 点）在**发起前**把观测字段写进 metadata `extensions.agent.spawns`（vNext 工单；legacy 缺失视为不强制，向后兼容）：

```json
{
  "extensions": {
    "agent": {
      "spawns": [
        {
          "spawn_id": "<uuid>",
          "at": "<发起时刻>",
          "task_scope": "一句话：本次子代理负责什么、边界在哪",
          "expected_artifact": "预期返回产物（文件/JSON 片段/结论）",
          "evidence_boundary": "子代理可读的证据范围（禁止越界读/改）",
          "join_condition": "如何判定加入整合（结果 schema / 超时 / deadline 降级）",
          "backend": "宿主或模型后端标识",
          "model": "实际请求模型标识",
          "capabilities": ["text"],
          "result": "joined | timed_out | stopped | failed",
          "adopted": "yes | partial | no",
          "adoption_reason": "主代理采纳或拒绝原因",
          "result_at": "终结时刻",
          "evidence_refs": ["短证据引用"],
          "summary_digest": "结果正文 sha256"
        }
      ]
    }
  }
}
```

**强制规则**：
1. **spawn 前必写**：调用 `record-agent-spawn` 原子写入 `task_scope` / `expected_artifact` / `evidence_boundary` / `join_condition` / `backend` / `model` / `capabilities`（防"发出去了才想边界"）
2. **收尾必补**：调用 `record-agent-result` 写 `result`（joined/timed_out/stopped/failed）+ `adopted`（yes/partial/no）+ `adoption_reason`；模型正文只存摘要——**采纳记录防"子代理结果被静默丢弃或伪造采纳"**
3. **并发上限（每工单硬上限）**：控制面拒绝第四个开放 spawn；调用方排队串行。估算总 token 超软预算时先问用户，不无限并行
4. **降级联动**：仅对真实已启动且已终结的 spawn 如实记 `result=stopped|failed` 和原因；未启动不可伪造 spawn 或结果，未完成保持未验证。
5. **校验入口**：`python3 tools/icode_control.py validate --dir <out_dir>` 严格校验 extensions.agent 结构、事件重建、终态配对与并发上限；真源 schema 见 [schemas/ticket-metadata.schema.json](../schemas/ticket-metadata.schema.json) `extensions`
