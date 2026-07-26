# playwright (MCP for icode-skill)

浏览器自动化（点击、截图、表单、JS 执行）。`/icode install` 标准组件。

- **包名**: `@microsoft/playwright-mcp`
- **KEY**: 无
- **本工程改进路径**: 步骤 5 deepcheck（跑 E2E）、步骤 6 audit（真实 UI 验证）

## 首次调用

**首次调用 MCP 工具时**，playwright 会自动下载浏览器（Chromium/Firefox/WebKit，按需）。下载会占用 ~200MB 磁盘。

**预装加速**（可选）：
```bash
npx playwright install chromium
```

## 主要工具

```
browser_navigate / browser_click / browser_fill
browser_screenshot / browser_snapshot
browser_evaluate (JS 执行)
browser_console_messages / browser_network_logs
browser_tabs (多 Tab 管理)
```

## 装 vs 不装

- ✅ **装**：前端项目、E2E 测试、UI 验证
- ⚠️ **代价**：20+ 工具 schema 永久加载到 system prompt，**非前端项目慎装**

## 安装

```bash
cd <icode-skill 仓库>/mcp
./install.sh
```

## 卸载

```bash
./mcp/playwright/uninstall.sh
```

**注意**：浏览器缓存不删（下次还能用）。如需清理：`npx playwright uninstall`。
