"""跨客户端 MCP 注册分发共享模块（Claude Code + Codex + CodeBuddy + WorkBuddy）。

顶层 mcp/install.sh / mcp/uninstall.sh 的
--client claude|codex|codebuddy|workbuddy|all 使用。
- detect_clients()：返回 Claude Code、Codex、CodeBuddy、WorkBuddy 的可用性证据
- Codex 适配器：只调官方 CLI（codex mcp add/remove/get --json/list），不直接读写 ~/.codex 配置
- CodeBuddy / WorkBuddy 适配器：静态 JSON 配置（结构同为 {mcpServers:{...}}），
  仅配置路径不同（~/.codebuddy/mcp.json 与 ~/.workbuddy/mcp.json）
- entry 真源：子工程 register_mcp.py 注册 Claude 时已导出到
  ~/.claude/icode_data/mcp_entries/<name>.json（见 claude_registry.export_entry）
- 安全策略（同名已有节点）：
  * 内容一致 → 幂等跳过，不重复写；
  * 内容不一致 → 先直接 add（若 Codex CLI 是 upsert 语义则成功），回读 inspect
    确认；仍不一致才报告「需手动 remove 后重试」——不自动 remove，避免破坏性更新。

用法（顶层脚本）：
    python3 client_registry.py detect
    python3 client_registry.py codex-register <name>
    python3 client_registry.py codex-unregister <name>
    python3 client_registry.py codex-inspect <name>
    python3 client_registry.py codebuddy-register <name>
    python3 client_registry.py codebuddy-unregister <name>
    python3 client_registry.py codebuddy-inspect <name>
    python3 client_registry.py workbuddy-register <name>
    python3 client_registry.py workbuddy-unregister <name>
    python3 client_registry.py workbuddy-inspect <name>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from claude_registry import ENTRY_DIR, _configure_utf8_stdout

# 支持环境变量覆盖（测试注入 mock 用）；真实环境回落到 PATH 里的 codex CLI
CODEX_BIN = os.environ.get("ICODE_CODEX_BIN") or shutil.which("codex")


def detect_clients() -> dict:
    """返回 {claude, codex, codebuddy, workbuddy} 可用性证据。

    - claude：~/.claude.json 可访问（存在或可写）
    - codex：codex CLI 在 PATH
    - codebuddy：~/.codebuddy 目录存在（CodeBuddy 与 Claude 共用 skills 目录，
      仅 MCP 配置独立，故以其配置目录存在为判据）
    - workbuddy：~/.workbuddy 目录存在（WorkBuddy 独立 skills 与 MCP 配置目录）
    """
    cfg = Path.home() / ".claude.json"
    claude_ok = cfg.exists() or cfg.parent.exists()
    return {
        "claude": claude_ok,
        "codex": shutil.which("codex") is not None,
        "codebuddy": CODEBUDDY_CFG.parent.exists(),
        "workbuddy": WORKBUDDY_CFG.parent.exists(),
    }


def _read_entry(name: str) -> dict:
    """读子工程导出的 server 描述；不存在则报错（提示先跑子工程 install 注册 Claude）。"""
    target = ENTRY_DIR / f"{name}.json"
    if not target.exists():
        raise RuntimeError(
            f"entry 未导出：{target} 不存在。请先运行子工程 install（注册 Claude Code）"
            f"后再对 Codex 注册。"
        )
    return json.loads(target.read_text(encoding="utf-8"))


def _codex_run(args: list) -> subprocess.CompletedProcess:
    """执行 codex CLI；失败时输出 stderr 但返回对象供调用方判断。"""
    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr.strip() + "\n")
    return result


def _entry_matches(actual: dict, expected: dict) -> bool:
    """Codex 侧对比关键可执行字段（command/args/env）。

    - 刻意不含 cwd：`codex mcp add` 无 cwd 参数，Codex transport 表达不了 cwd，
      比 cwd 会导致 venv 系 entry 永远 mismatch。command 均绝对路径、config 走绝对
      路径 env，cwd 差异无实质影响。
    - env 空值容错：npx 系 entry 无 env 字段（None），但 Codex transport 的 env
      可能是 {} 或缺失——两者视为等价。
    """
    for key in ("command", "args", "env"):
        exp = expected.get(key)
        act = actual.get(key)
        if key == "env":
            exp = exp or {}
            act = act or {}
        if exp != act:
            return False
    return True


def codex_inspect(name: str) -> str:
    """codex mcp get <name> --json → absent | match | mismatch（对比导出的 entry）。"""
    result = _codex_run([CODEX_BIN, "mcp", "get", name, "--json"])
    if result.returncode != 0:
        # codex 对不存在的 server 返回非 0 且 stderr 提及 No MCP server
        if "No MCP server" in result.stderr:
            return "absent"
        return "error"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "error"
    transport = (payload.get("transport") or {}) if isinstance(payload, dict) else {}
    return "match" if _entry_matches(transport, _read_entry(name)) else "mismatch"


def codex_register(name: str) -> tuple[str, bool]:
    """注册/同步 <name> 到 Codex。返回 (说明, 是否成功)。

    构造：codex mcp add <name> [--env K=V ...] -- <command> [args...]
    （用 subprocess list 传参，不经 shell，避免注入；env 的 dict 展开为 --env K=V。）
    add 后回读 inspect：未生效/未覆盖均视为失败（调用方据此计入失败），
    不自动 remove（避免破坏性更新），给出人工处理指引。
    """
    if not CODEX_BIN:
        return f"未检测到 codex CLI（不在 PATH），跳过注册（{name}）", False
    entry = _read_entry(name)
    status = codex_inspect(name)
    if status == "match":
        return f"已存在且内容一致，跳过（{name}）", True
    if status == "error":
        return f"查询 Codex 现有 {name} 失败，跳过注册（详见上一条 stderr）", False

    cmd = [CODEX_BIN, "mcp", "add", name]
    for key, value in (entry.get("env") or {}).items():
        cmd += ["--env", f"{key}={value}"]
    cmd += ["--"]
    cmd += [entry["command"]] + (entry.get("args") or [])
    _codex_run(cmd)

    after = codex_inspect(name)
    if after == "match":
        return f"已注册到 Codex（{name}）", True
    if after == "absent":
        return f"add 未生效（{name} 不存在），请人工检查 codex mcp add 命令", False
    return (
        f"Codex 已有同名 {name} 且内容不一致，add 未覆盖。"
        f"请先 `codex mcp remove {name}` 再重试 install，或人工核对两者差异。",
        False,
    )


def codex_unregister(name: str) -> str:
    """移除 Codex 上的 <name>；未注册幂等返回说明。"""
    if not CODEX_BIN:
        return f"未检测到 codex CLI（不在 PATH），跳过（{name}）"
    status = codex_inspect(name)
    if status == "absent":
        return f"未注册，跳过（{name}）"
    _codex_run([CODEX_BIN, "mcp", "remove", name])
    after = codex_inspect(name)
    return f"已从 Codex 移除（{name}）" if after == "absent" else f"移除失败，请人工检查（{name}）"


# ---------------------------------------------------------------------------
# CodeBuddy / WorkBuddy 适配器（静态 JSON 配置）
#
# 两个宿主均无官方 MCP CLI，其 MCP 配置为与 Claude Code 的 mcpServers 同源的
# JSON：{"mcpServers": {name: {command, args, env}}}，仅配置路径不同：
#   - CodeBuddy: ~/.codebuddy/mcp.json
#   - WorkBuddy: ~/.workbuddy/mcp.json
# 因此两者共用同一实现：「读 entry 真源 → 合并进 mcpServers → 原子写回」，
# 按宿主绑定不同配置路径。
# 注意：CodeBuddy 与 Claude Code 共用 ~/.claude/skills；WorkBuddy 使用独立的
#      ~/.workbuddy/skills，由 sync-to-global.sh --client workbuddy 负责同步。
# ---------------------------------------------------------------------------

CODEBUDDY_CFG = Path.home() / ".codebuddy" / "mcp.json"
WORKBUDDY_CFG = Path.home() / ".workbuddy" / "mcp.json"

# 对比关键可执行字段（与 Codex 侧一致：不含 cwd，避免 venv 系 entry 误判）
_COMPARE_KEYS = ("command", "args", "env")


def _norm(value):
    """env 空值容错：None / {} 视为等价。"""
    if value in (None, {}, ""):
        return None
    return value


def _static_config_inspect(cfg_path: Path, name: str) -> str:
    """读静态 JSON 配置判定 absent | match | mismatch | error（CodeBuddy/WorkBuddy 共用）。"""
    entry = _read_entry(name)
    if not cfg_path.exists():
        return "absent"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "error"
    actual = (data.get("mcpServers") or {}).get(name)
    if actual is None:
        return "absent"
    for key in _COMPARE_KEYS:
        if _norm(actual.get(key)) != _norm(entry.get(key)):
            return "mismatch"
    return "match"


def _static_config_register(cfg_path: Path, host_label: str, name: str) -> tuple[str, bool]:
    """注册/同步 <name> 到静态 JSON 配置宿主。返回 (说明, 是否成功)。

    同名已存在且内容不一致时不静默覆盖：返回失败并给出人工处理指引
    （与 Codex 适配器一致的「不自动破坏性更新」策略）。
    """
    if not cfg_path.parent.exists():
        return f"未检测到 {host_label} 配置目录 {cfg_path.parent}，跳过注册（{name}）", False
    entry = _read_entry(name)
    status = _static_config_inspect(cfg_path, name)
    if status == "match":
        return f"已存在且内容一致，跳过（{name}）", True
    if status == "error":
        return f"{cfg_path} 解析失败，跳过注册（{name}）", False
    if status == "mismatch":
        return (
            f"{host_label} 已有同名 {name} 且内容不一致，未覆盖。"
            f"请先手工移除 mcpServers.{name} 后重试，或人工核对两者差异。",
            False,
        )

    data = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    if not isinstance(data, dict):
        return f"{cfg_path} 顶层必须是 JSON object，跳过注册（{name}）", False
    servers = data.get("mcpServers") or {}
    if not isinstance(servers, dict):
        return f"{cfg_path} 的 mcpServers 必须是 JSON object，跳过注册（{name}）", False
    servers[name] = {
        key: entry[key] for key in ("command", "args", "env") if entry.get(key) is not None
    }
    data["mcpServers"] = servers
    _atomic_write_static_config(cfg_path, data)

    after = _static_config_inspect(cfg_path, name)
    if after == "match":
        return f"已注册到 {host_label}（{name}）→ 重启会话后生效", True
    return f"写入后校验未通过（{name}：{after}），请人工检查 {cfg_path}", False


def _atomic_write_static_config(cfg_path: Path, data: dict) -> None:
    """同目录临时文件 + fsync + replace，避免并发名字碰撞和半截 JSON。"""
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{cfg_path.name}.icode-stage-", dir=cfg_path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, cfg_path)
        directory_fd = os.open(cfg_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _static_config_unregister(cfg_path: Path, host_label: str, name: str) -> tuple[str, bool]:
    """从静态 JSON 配置宿主移除 <name>；不依赖可能已被子卸载器删除的 entry。"""
    if not cfg_path.exists():
        return f"未注册，跳过（{name}）", True
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        return f"{cfg_path} 解析失败，拒绝改写（{name}）：{exc}", False
    if not isinstance(data, dict):
        return f"{cfg_path} 顶层不是 JSON object，拒绝改写（{name}）", False
    servers = data.get("mcpServers")
    if servers is None:
        return f"未注册，跳过（{name}）", True
    if not isinstance(servers, dict):
        return f"{cfg_path} 的 mcpServers 不是 JSON object，拒绝改写（{name}）", False
    if name not in servers:
        return f"未注册，跳过（{name}）", True
    servers.pop(name)
    _atomic_write_static_config(cfg_path, data)
    return f"已从 {host_label} 移除（{name}）", True


# --- CodeBuddy 绑定 ---

def codebuddy_inspect(name: str) -> str:
    """读 ~/.codebuddy/mcp.json 判定 absent | match | mismatch | error。"""
    return _static_config_inspect(CODEBUDDY_CFG, name)


def codebuddy_register(name: str) -> tuple[str, bool]:
    """注册/同步 <name> 到 CodeBuddy。"""
    return _static_config_register(CODEBUDDY_CFG, "CodeBuddy", name)


def codebuddy_unregister(name: str) -> tuple[str, bool]:
    """从 CodeBuddy 移除 <name>。"""
    return _static_config_unregister(CODEBUDDY_CFG, "CodeBuddy", name)


# --- WorkBuddy 绑定 ---

def workbuddy_inspect(name: str) -> str:
    """读 ~/.workbuddy/mcp.json 判定 absent | match | mismatch | error。"""
    return _static_config_inspect(WORKBUDDY_CFG, name)


def workbuddy_register(name: str) -> tuple[str, bool]:
    """注册/同步 <name> 到 WorkBuddy。"""
    return _static_config_register(WORKBUDDY_CFG, "WorkBuddy", name)


def workbuddy_unregister(name: str) -> tuple[str, bool]:
    """从 WorkBuddy 移除 <name>。"""
    return _static_config_unregister(WORKBUDDY_CFG, "WorkBuddy", name)


def main() -> int:
    _configure_utf8_stdout()
    if len(sys.argv) < 2:
        print(
            "用法: client_registry.py "
            "<detect|codex-register|codex-unregister|codex-inspect|"
            "codebuddy-register|codebuddy-unregister|codebuddy-inspect|"
            "workbuddy-register|workbuddy-unregister|workbuddy-inspect> [name]"
        )
        return 1
    cmd = sys.argv[1]
    if cmd == "detect":
        print(json.dumps(detect_clients(), ensure_ascii=False))
        return 0
    if len(sys.argv) < 3:
        print(f"{cmd} 需要 <name> 参数")
        return 1
    name = sys.argv[2]
    if cmd == "codex-register":
        msg, ok = codex_register(name)
        print(msg)
        return 0 if ok else 1
    elif cmd == "codex-unregister":
        print(codex_unregister(name))
    elif cmd == "codex-inspect":
        print(codex_inspect(name))
    elif cmd == "codebuddy-register":
        msg, ok = codebuddy_register(name)
        print(msg)
        return 0 if ok else 1
    elif cmd == "codebuddy-unregister":
        msg, ok = codebuddy_unregister(name)
        print(msg)
        return 0 if ok else 1
    elif cmd == "codebuddy-inspect":
        print(codebuddy_inspect(name))
    elif cmd == "workbuddy-register":
        msg, ok = workbuddy_register(name)
        print(msg)
        return 0 if ok else 1
    elif cmd == "workbuddy-unregister":
        msg, ok = workbuddy_unregister(name)
        print(msg)
        return 0 if ok else 1
    elif cmd == "workbuddy-inspect":
        print(workbuddy_inspect(name))
    else:
        print(f"未知命令: {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
