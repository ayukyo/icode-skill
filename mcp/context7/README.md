# context7 (MCP for icode-skill)

第三方库的最新 API 文档实时查询（npm/PyPI 等）。`/icode install` 标准组件。

- **包名**: `@upstash/context7-mcp`
- **KEY**: 无（公开 API）
- **本工程改进路径**: 步骤 0 init（库调研）、步骤 1 plan（API 核对）、步骤 4 code（实时查 API）

## 主要工具

```
resolve-library-id(libraryName)   # 库名 → context7 ID
get-library-docs(context7ID, topic, tokens)  # 拉文档
```

## 用法示例

```
# 查 React 19 的 Server Components 最新文档
1. resolve-library-id("/reactjs/react.dev") → react
2. get-library-docs("react", "server components", 5000)
```

## 装 vs 不装

- ✅ **装**：用第三方库时（React、Next.js、Pandas、TensorFlow 等）
- ⚪ **不装**：纯自有代码项目

## 安装

```bash
cd <icode-skill 仓库>/mcp
./install.sh
```

## 卸载

```bash
./mcp/context7/uninstall.sh
```
