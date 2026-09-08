# RED：状态机生命周期回放

## 执行条件

- 独立代理禁止读取待创建技能、其他 SKILL 和 memory。
- 场景包含首轮失败残留 owner/latch/临时文件、客户端重试、第二轮成功以及跳过 restart/replay/rollback 的发布压力。

## 实际结果

代理正确阻断发布，识别了跨轮次脏状态、迟到回调、重复副作用和失败清理风险，并提出多类测试。因此一般生命周期判断能力并不缺失。

## 可复现缺口

输出没有稳定形成 `scope`、`state_inventory`、`transition_matrix`、`replay_matrix`、`leaks_and_conflicts`、`verdict` 六段合同；未按统一 immediate/converged/restart/replay/rollback/failure 维度逐项给出 initial state、stimulus、expected、observed、cleanup 和 status，无法机器判断“第二轮成功是否掩盖第一轮失败”。

## RED 判定

**FAIL（生命周期回放矩阵缺失）**。技能将已有正确警觉转成可重复、可审计的状态收敛合同。
