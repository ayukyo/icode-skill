#!/usr/bin/env bash
# serena (LSP 增强 AI 编码) - Python + uv 形态
# 官方包: serena-mcp-server (从 git+https://github.com/oraios/serena 安装)
# 使用:
#   ./install.sh                  # 注册(需要 uv + uvx + Python 3.10+)
#   ./install.sh --uninstall      # 卸载
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"

# 守卫: 合法工程根必须存在 scripts/register_mcp.py
if [ ! -f "$HERE/scripts/register_mcp.py" ]; then
  echo "❌ 当前目录不是 serena 工程根目录"
  echo "   source: $HERE"
  echo "   请 cd 到 mcp/serena 目录后重跑"
  exit 1
fi

# 探测 Python (避免 Git Bash 下命中 WindowsApps 的 python3 stub)
PYTHON_BIN=""
for _py in python3 python; do
  if command -v "$_py" >/dev/null 2>&1; then
    _bin="$(command -v "$_py")"
    if [ -n "$("$_bin" --version 2>&1 | grep -i 'python')" ]; then
      PYTHON_BIN="$_bin"
      break
    fi
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "❌ 未找到可用的 python 或 python3"
  exit 1
fi

# 探测 Python >= 3.10 (serena 要求)
PY_VER="$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")"
PY_MAJOR="$("$PYTHON_BIN" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)"
PY_MINOR="$("$PYTHON_BIN" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
  echo "❌ Python >= 3.10 required (current: $PY_VER)"
  exit 1
fi

# 探测 uv (Astral 出品的 Python 包管理工具,含 uvx 子命令)
# 主动安装 uv(若缺失):按平台优先级尝试 brew/curl/winget/PowerShell
# 用 --no-auto-install 跳过自动安装(回到手动提示)
attempt_install_uv() {
  echo "ℹ️  未找到 uv,尝试自动安装..."

  # 探测 OS(uname 在 Git Bash / Linux / macOS 有效;Windows 上 fallback)
  _os="$(uname -s 2>/dev/null || echo Windows)"
  case "$_os" in
    Linux|Darwin)
      # macOS 优先 brew(更快、更幂等)
      if command -v brew >/dev/null 2>&1; then
        echo "  → brew install uv"
        if brew install uv; then return 0; fi
      fi
      # Linux/macOS: 官方脚本(curl)
      if command -v curl >/dev/null 2>&1; then
        echo "  → curl -LsSf https://astral.sh/uv/install.sh | sh"
        if curl -LsSf https://astral.sh/uv/install.sh | sh; then
          # 官方脚本默认装到 ~/.cargo/bin,可能不在 PATH,放到候选
          return 0
        fi
      fi
      ;;
    *)
      # Windows: 优先 winget(系统自带)
      if command -v winget >/dev/null 2>&1; then
        echo "  → winget install --id=astral-sh.uv"
        if winget install --id=astral-sh.uv --accept-source-agreements --accept-package-agreements; then
          return 0
        fi
      fi
      # PowerShell: 官方脚本(PowerShell 5+/7+)
      if command -v powershell >/dev/null 2>&1; then
        echo "  → powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\""
        if powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"; then
          return 0
        fi
      fi
      ;;
  esac
  return 1
}

UV_BIN="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV_BIN" ] && [ "${1:-}" != "--no-auto-install" ]; then
  if attempt_install_uv; then
    # 装完重新探测,带 fallback 路径(Linux 官方脚本装到 ~/.cargo/bin)
    UV_BIN="$(command -v uv 2>/dev/null || true)"
    [ -z "$UV_BIN" ] && [ -x "$HOME/.cargo/bin/uv" ] && UV_BIN="$HOME/.cargo/bin/uv"
    [ -z "$UV_BIN" ] && [ -x "$HOME/.local/bin/uv" ] && UV_BIN="$HOME/.local/bin/uv"
  fi
fi
if [ -z "$UV_BIN" ]; then
  echo "❌ 未找到 uv 命令"
  if [ "${1:-}" = "--no-auto-install" ]; then
    echo "   (--no-auto-install 模式,跳过自动安装)"
  else
    echo "   (自动安装失败)"
  fi
  echo "   手动安装:"
  echo "     • macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "     • Windows:     winget install --id=astral-sh.uv"
  echo "     • Homebrew:    brew install uv"
  echo "   装完重跑 ./install.sh"
  exit 1
fi

# 探测 uvx (uv 的子命令)。uvx 通常随 uv 一起装,这里做 fallback 兜底
UVX_BIN="$(command -v uvx 2>/dev/null || true)"
[ -z "$UVX_BIN" ] && [ -x "$HOME/.cargo/bin/uvx" ] && UVX_BIN="$HOME/.cargo/bin/uvx"
[ -z "$UVX_BIN" ] && [ -x "$HOME/.local/bin/uvx" ] && UVX_BIN="$HOME/.local/bin/uvx"
if [ -z "$UVX_BIN" ]; then
  echo "❌ uvx 未找到。uvx 是 uv 的子命令,如未安装请升级 uv"
  echo "   升级: uv self update"
  exit 1
fi

echo "📦 serena 安装"
echo "   python: $PYTHON_BIN ($PY_VER)"
echo "   uv:     $UV_BIN"
echo "   uvx:    $UVX_BIN"

# 卸载分支
if [ "${1:-}" = "--uninstall" ]; then
  "$HERE/uninstall.sh"
  exit 0
fi

# 注册到 ~/.claude.json
# 用 uvx 启动 serena(首次调用会自动从 git clone + 装包)
"$PYTHON_BIN" "$HERE/scripts/register_mcp.py"

# ── serena-doctor 部署 ──
# 1. 软链到 ~/.local/bin/（确保 PATH 可见）
SERENA_DOCTOR_BIN="$HOME/.local/bin/serena-doctor"
if [ -f "$HERE/scripts/serena-doctor" ]; then
  mkdir -p "$HOME/.local/bin"
  ln -sf "$HERE/scripts/serena-doctor" "$SERENA_DOCTOR_BIN"
  echo "  ✅ serena-doctor 已部署到 $SERENA_DOCTOR_BIN"
else
  echo "  ⚠️  scripts/serena-doctor 未找到，跳过部署"
fi

# 2. 注入规则到 ~/.claude/CLAUDE.md
CLAUDE_RULES_FILE="$HERE/claude-rules.md"
"$PYTHON_BIN" - "$CLAUDE_RULES_FILE" <<'PYEOF'
import sys
from pathlib import Path

claude_md = Path.home() / ".claude" / "CLAUDE.md"
rules_file = Path(sys.argv[1])

if not claude_md.exists() or not rules_file.exists():
    sys.exit(0)

content = claude_md.read_text()
if "serena-doctor:rules-start" in content:
    print("  ℹ️  CLAUDE.md 规则已存在，跳过注入")
    sys.exit(0)

rules = rules_file.read_text()
marker = "能用 serena 解决的就不用其他工具（serena 不可用降级 ripgrep/grep 并显式声明）"
if marker not in content:
    print("  ⚠️  CLAUDE.md 未找到标记行，跳过注入")
    sys.exit(0)

new_content = content.replace(marker, marker + "\n\n" + rules)
claude_md.write_text(new_content)
print("  ✅ 规则已注入 ~/.claude/CLAUDE.md")
PYEOF

echo ""
echo "🎉 完成!"
echo "   v2.1+ 已通过 uv tool install 持久化安装 serena-agent (含 serena CLI)。"
echo "   后续启动秒级 (无 git fetch), 详见 scripts/register_mcp.py"

# v2.1+: 自动探测 LSP server 状态（按 mcp_per_step.md 推荐 🟢 必装段）
echo ""
echo "🔍 探测 LSP server 状态（serena 必需）:"
LSP_FOUND=0
check_lsp() {
    local cmd="$1"
    local lang="$2"
    local install_cmd="$3"
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "  ✅ $lang ($cmd) — 可用"
        LSP_FOUND=$((LSP_FOUND + 1))
    else
        echo "  ⚠️  $lang ($cmd) — 未装，建议: $install_cmd"
    fi
}
check_lsp pyright                   "Python"     "pip install pyright"
check_lsp clangd                     "C/C++"      "apt install clangd / brew install clangd"
check_lsp typescript-language-server "JS/TS"      "npm install -g typescript-language-server typescript"
check_lsp rust-analyzer              "Rust"       "rustup component add rust-analyzer"
check_lsp jdtls                      "Java"       "系统装 jdtls"
check_lsp gopls                      "Go"         "系统装 gopls"

# v2.1+ 主动装兜底 LSP server：如果 LSP_FOUND == 0，主动 pip install pyright
# pyright 是纯 Python 跨平台 LSP，无系统包管理器依赖，装上至少让 serena 能启动不超时
# 用户工程是 C/JS/Rust 等其他语言时，serena 会按需提示装对应 LSP
attempt_install_pyright() {
    echo ""
    echo "ℹ️  LSP server 覆盖不足（< 2），主动装 pyright（跨平台兜底）..."
    # 优先用 pip（跨平台）
    if command -v pip3 >/dev/null 2>&1; then
        if pip3 install --user pyright 2>&1 | tail -3; then
            return 0
        fi
    fi
    if command -v pip >/dev/null 2>&1; then
        if pip install --user pyright 2>&1 | tail -3; then
            return 0
        fi
    fi
    # npm fallback（pyright 也有 npm 包）
    if command -v npm >/dev/null 2>&1; then
        if npm install -g pyright 2>&1 | tail -3; then
            return 0
        fi
    fi
    return 1
}

if [ "$LSP_FOUND" -lt 2 ] && [ "${1:-}" != "--no-auto-install" ]; then
    # LSP_FOUND < 2 时主动装 pyright 兜底（跨平台纯 Python，无系统依赖）
    # 即使已有 rust-analyzer 等，pyright 仍能补充 Python 语言覆盖
    if ! command -v pyright >/dev/null 2>&1; then
        if attempt_install_pyright; then
            # 装完重新探测
            if command -v pyright >/dev/null 2>&1; then
                echo "  ✅ Python (pyright) - 已自动安装"
                LSP_FOUND=$((LSP_FOUND + 1))
            fi
        else
            echo "  ⚠️  pyright 自动安装失败，请手动装"
        fi
    fi
fi

echo ""
if [ "$LSP_FOUND" -eq 0 ]; then
    echo "❌ 未检测到任何 LSP server -- serena 启动后 find_symbol 会超时不可用"
    echo "   至少装一个 LSP server 后重启 Claude Code 才能用 serena 🟢 推荐"
    echo "   跨平台兜底: pip install pyright"
elif [ "$LSP_FOUND" -lt 2 ]; then
    echo "⚠️  只检测到 $LSP_FOUND 个 LSP server -- serena 可用但语言覆盖不足"
    echo "   按 SKILL.md v2.1 推荐: 至少装 2 个 LSP server 覆盖主力语言"
    echo "   跨平台兜底: pip install pyright（已装则跳过）"
else
    echo "✅ 检测到 $LSP_FOUND 个 LSP server -- serena 🟢 可正常用"
fi

# v2.1+ 预下载 serena 期望位置的 clangd 19.1.2（避免 serena 启动时从 github 下载超时）
# serena 的 solidlsp 库不读系统 clangd，非要自己下载 clangd 19.1.2 到 ~/.serena/language_servers/
# 在 github.com 网络受限环境（中国典型）会超时失败，导致 find_symbol 不可用
# 本段主动预下载（多镜像 fallback），让 serena 启动时直接用已下载的 clangd
CLANGD_VERSION="19.1.2"
CLANGD_TARGET_DIR="$HOME/.serena/language_servers/static/ClangdLanguageServer/clangd/clangd_${CLANGD_VERSION}"
CLANGD_BIN="$CLANGD_TARGET_DIR/bin/clangd"

if [ ! -x "$CLANGD_BIN" ] && [ "${1:-}" != "--no-auto-install" ]; then
    echo ""
    echo "🔍 预下载 serena 期望的 clangd ${CLANGD_VERSION}（避免 serena 启动时 github 下载超时）..."
    # 探测 OS + 架构
    _os="$(uname -s 2>/dev/null || echo Linux)"
    _arch="$(uname -m 2>/dev/null || echo x86_64)"
    case "$_os:$_arch" in
        Linux:x86_64)   CLANGD_PKG="clangd-linux-${CLANGD_VERSION}.zip" ;;
        Linux:aarch64)  CLANGD_PKG="clangd-linux-arm64-${CLANGD_VERSION}.zip" ;;
        Darwin:x86_64|Darwin:arm64) CLANGD_PKG="clangd-mac-${CLANGD_VERSION}.zip" ;;
        *)              CLANGD_PKG="" ;;
    esac

    if [ -z "$CLANGD_PKG" ]; then
        echo "  ⚠️  不支持的平台 $_os:$_arch，跳过 clangd 预下载"
        echo "      serena 启动时会自己尝试下载（可能超时）"
    else
        # 多镜像 fallback（github 直连 + 国内镜像）
        GH_URL="https://github.com/clangd/clangd/releases/download/${CLANGD_VERSION}/${CLANGD_PKG}"
        MIRRORS=(
            "https://gh-proxy.com/${GH_URL}"
            "https://mirror.ghproxy.com/${GH_URL}"
            "https://ghfast.top/${GH_URL}"
            "${GH_URL}"
        )
        CLANGD_DOWNLOADED=0
        TMP_ZIP="$(mktemp -t clangd_XXXXXX.zip 2>/dev/null || mktemp).zip"
        for mirror_url in "${MIRRORS[@]}"; do
            echo "  -> 试 ${mirror_url}"
            if wget -q --timeout=170 -O "$TMP_ZIP" "$mirror_url" 2>&1; then
                # 验证 zip 完整性
                if unzip -t "$TMP_ZIP" >/dev/null 2>&1; then
                    CLANGD_DOWNLOADED=1
                    echo "  ✅ 下载成功 (${mirror_url})"
                    break
                fi
            fi
            rm -f "$TMP_ZIP"
        done

        if [ "$CLANGD_DOWNLOADED" = "1" ]; then
            mkdir -p "$CLANGD_TARGET_DIR"
            TMP_EXTRACT="$(mktemp -d)"
            if unzip -q "$TMP_ZIP" -d "$TMP_EXTRACT" 2>&1; then
                # 找解压出的 clangd_19.1.2 目录
                EXTRACTED_DIR=$(find "$TMP_EXTRACT" -maxdepth 1 -type d -name "clangd_*" | head -1)
                if [ -n "$EXTRACTED_DIR" ]; then
                    cp -r "$EXTRACTED_DIR"/* "$CLANGD_TARGET_DIR/"
                    chmod +x "$CLANGD_BIN" 2>/dev/null
                    if "$CLANGD_BIN" --version >/dev/null 2>&1; then
                        echo "  ✅ clangd ${CLANGD_VERSION} 已装到 $CLANGD_BIN"
                        echo "     $("$CLANGD_BIN" --version 2>&1 | head -1)"
                    else
                        echo "  ⚠️  clangd 装到 $CLANGD_BIN 但无法执行"
                    fi
                else
                    echo "  ⚠️  解压成功但未找到 clangd_* 目录"
                fi
            else
                echo "  ⚠️  解压失败"
            fi
            rm -rf "$TMP_EXTRACT" "$TMP_ZIP"
        else
            echo "  ⚠️  所有镜像下载失败，serena 启动时会自己尝试（可能超时）"
            echo "      手动下载 $GH_URL 解压到 $CLANGD_TARGET_DIR/"
        fi
    fi
fi

echo ""
echo "   完整语言支持: https://github.com/oraios/serena"
echo "   ⚠️ 重启 Claude Code 让注册生效。"
