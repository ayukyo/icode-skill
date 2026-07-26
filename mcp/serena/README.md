# serena (MCP for icode-skill)

LSP 增强 AI 编码——按符号跳转、引用查找、语义理解。`/icode install` 标准组件。

> 形态区别于 npm 类 MCP：Python + uv + LSP 工具链。

- **官方**: [oraios/serena](https://github.com/oraios/serena)
- **依赖**: Python 3.10+ / `uv` (含 `uvx`) / LSP server
- **KEY**: 无

## 装 vs 不装

- ✅ **项目语言**：Python / TS/JS / Java / C/C++ / Rust / Go
- ⚪ **不装**：纯 markdown / 文档项目（无 LSP 需求）

## 安装

```bash
# 1. 装 uv（一次性）
curl -LsSf https://astral.sh/uv/install.sh | sh
# 或 Windows: winget install --id=astral-sh.uv

# 2. 装至少一个 LSP server
pip install pyright                      # Python
npm install -g typescript-language-server typescript  # TS/JS
# C/C++: 系统装 clangd
# Rust:  rustup component add rust-analyzer

# 3. 跑 /icode install
cd <icode-skill 仓库>/mcp
./install.sh
```

**装完首次启动**：`uvx --from git+https://github.com/oraios/serena serena start-mcp-server` 会从 git clone serena 仓库（约 50MB），之后就不需要再 clone。

## 工作流增益

**对 icode 步骤 4 编码是 game-changer**：
- 步骤 1 plan：理解代码结构（哪些函数被谁调用）
- 步骤 4 code：按符号编辑、重命名引用追踪
- 步骤 5 deepcheck：找所有调用点评估影响

比 Read/Write 模式的 10 倍效率。

## 主要工具

```
find_symbol / find_referencing_symbols
get_symbols_overview / get_document_symbols
insert_after_symbol / insert_before_symbol
replace_symbol_body / rename_symbol
read_file / create_text_file / list_dir
activate_project                          # 必须先激活项目
```

## 卸载

```bash
./mcp/serena/uninstall.sh
```

仅移除 `~/.claude.json` 注册项。**serena 缓存** 留在 `~/.cache/uv/`，如需清理：`uv cache clean`。
