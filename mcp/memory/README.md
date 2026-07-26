# memory (MCP for icode-skill)

基于知识图谱的持久化记忆（实体、关系、跨会话查询）。`/icode install` 标准组件。

- **包名**: `@modelcontextprotocol/server-memory`
- **KEY**: 无
- **本工程改进路径**: 跨工单/跨会话"经验沉淀"（用户偏好、项目特性）

## 主要工具

```
create_entities / create_relations
add_observations / delete_entities
search_nodes / open_nodes
read_graph
```

## 用法示例

```python
# 跨工单自动记住
memory.create_entities([{
  "name": "user_preference",
  "entityType": "preference",
  "contents": ["user prefers NoSQL", "project uses gRPC v3"]
}])
```

## 安装

```bash
cd <icode-skill 仓库>/mcp
./install.sh
```

## 卸载

```bash
./mcp/memory/uninstall.sh
```

**注意**：记忆持久化到本地 JSON 文件，**移除注册不会丢数据**。如要彻底清理，删除 `~/.local/share/mcp-memory/` 之类路径。
