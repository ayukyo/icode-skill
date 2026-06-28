# /icode status — 工单状态查询（纯只读）

**命令**: `/icode status`
**产出**: 无（纯只读，不创建目录/不写文件）
**会话**: 主会话

## 定位

会话中断后恢复时，用户不知道当前在哪个步骤。此命令纯读 metadata 输出摘要，帮助快速定位。

## 执行流程

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 读取 `.ico_metadata.json`
3. 输出状态摘要：

```
最新工单: .icode_output_N (ticket_id)
需求: {requirement}
状态: {status}（{status中文说明}）
已完成: {completed_steps 链路，如 log → 1 → 2 → 3 → 4}
下一步: {根据续跑判定规则推断，如 "/icode deepcheck (步骤5复检)"}
代码文件: {code_files 列表，无则"未编码"}
索引工单: {全局索引 tickets 数} 条
```

4. 若无 `.icode_output_N` 目录，输出提示："未找到工单目录，请先运行 /icode start/init/log"

## 不需要强制思考前置

此命令是纯只读信息查询，不产出任何文件，不涉及思考/审查/编码——**不需要强制思考前置**，不需要 Read references。
