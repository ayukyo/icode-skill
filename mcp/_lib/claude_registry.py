"""Claude Code MCP 注册共享模块（原子写 + 损坏保护 + 回读校验 + entry 导出）。

6 个子工程的 register_mcp.py 共用，消除「各自直接读写 ~/.claude.json」的重复与隐患：
- 原子写：写 .tmp 后 os.replace，中途崩溃不损坏原文件；
- 损坏保护：~/.claude.json 解析失败/非对象时备份原文件、停止并报错，绝不覆盖；
- 回读校验：写后重读比对 command/args/env/cwd，不一致即报错（防静默写坏）；
- entry 导出：把 server 描述写到 ~/.claude/icode_data/mcp_entries/<name>.json，
  供顶层 mcp/install.sh 的 --client codex|all 分支注册到 Codex（entry 真源仍是子工程）。

用法（register_mcp.py 内）：
    import claude_registry
    entry = {...}                       # 子工程构造的 server 描述
    claude_registry.register("<name>", entry)
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

CLAUDE_JSON = Path.home() / ".claude.json"
# 运行期 entry 导出目录（用户数据目录，不进 dev repo；顶层脚本同机可读）
ENTRY_DIR = Path.home() / ".claude" / "icode_data" / "mcp_entries"

# 回读校验比对的关键字段（忽略 _fallback 等辅助元字段）
_COMPARE_KEYS = ("command", "args", "env", "cwd")


def _configure_utf8_stdout() -> None:
    """强制 stdout/stderr 用 UTF-8，兼容 Windows 默认 GBK 控制台。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _backup(path: Path) -> Path:
    """把损坏文件备份为 <name>.bak.<ts>，返回备份路径。"""
    backup = path.with_name(f"{path.name}.bak.{time.strftime('%Y%m%d%H%M%S')}")
    try:
        shutil.copy2(path, backup)
    except Exception:
        pass
    return backup


def _read_cfg(path: Path) -> dict:
    """读 ~/.claude.json；不存在返回 {}；损坏/非对象 → 备份并报错，绝不覆盖。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        backup = _backup(path)
        raise RuntimeError(
            f"{path} 解析失败（{exc.__class__.__name__}），已备份到 {backup}，未做任何修改。"
            f"请人工修复该文件后重试。"
        ) from exc
    if not isinstance(data, dict):
        backup = _backup(path)
        raise RuntimeError(
            f"{path} 顶层不是 JSON 对象（{type(data).__name__}），已备份到 {backup}，未做任何修改。"
            f"请人工修复该文件后重试。"
        )
    return data


def _write_cfg(path: Path, cfg: dict) -> None:
    """原子写：先写 .tmp 再 os.replace，中途崩溃不损坏原文件。"""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def export_entry(name: str, entry: dict) -> Path:
    """把 server 描述导出到 ENTRY_DIR/<name>.json（原子写），供 Codex 注册分支读取。"""
    ENTRY_DIR.mkdir(parents=True, exist_ok=True)
    target = ENTRY_DIR / f"{name}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target


def _entry_matches(actual: dict, expected: dict) -> bool:
    """比对关键可执行字段（command/args/env/cwd），忽略 _fallback 等辅助元字段。"""
    return all(actual.get(k) == expected.get(k) for k in _COMPARE_KEYS)


def _mcp_servers(cfg: dict) -> dict:
    """取 mcpServers 段；缺失则建空，非 dict 视为损坏（备份 + 报错，不覆盖）。"""
    servers = cfg.get("mcpServers")
    if servers is None:
        servers = cfg["mcpServers"] = {}
        return servers
    if not isinstance(servers, dict):
        backup = _backup(CLAUDE_JSON)
        raise RuntimeError(
            f"{CLAUDE_JSON} 的 mcpServers 段不是 JSON 对象（{type(servers).__name__}），"
            f"已备份到 {backup}，未做任何修改。请人工修复后重试。"
        )
    return servers


def register(name: str, entry: dict) -> dict:
    """幂等注册到 ~/.claude.json mcpServers.<name> + 导出 entry + 回读校验。"""
    _configure_utf8_stdout()  # 自保护: 报错分支也要在 GBK 控制台可读
    cfg = _read_cfg(CLAUDE_JSON)
    _mcp_servers(cfg)[name] = entry
    _write_cfg(CLAUDE_JSON, cfg)
    export_entry(name, entry)
    # 回读校验：写后重读，关键字段必须一致，防静默写坏
    after = _read_cfg(CLAUDE_JSON)
    actual = _mcp_servers(after).get(name)
    if actual is None or not _entry_matches(actual, entry):
        raise RuntimeError(
            f"注册回读校验失败：{name} 写入后与期望不一致，请检查 {CLAUDE_JSON} 手动修正。"
        )
    return entry


def unregister(name: str) -> bool:
    """只删目标节点；未注册幂等返回 False。同时清理导出的 entry。"""
    _configure_utf8_stdout()  # 自保护: 报错分支也要在 GBK 控制台可读
    cfg = _read_cfg(CLAUDE_JSON)
    servers = _mcp_servers(cfg)
    if name not in servers:
        return False
    del servers[name]
    _write_cfg(CLAUDE_JSON, cfg)
    entry_file = ENTRY_DIR / f"{name}.json"
    if entry_file.exists():
        entry_file.unlink()
    return True


def inspect(name: str) -> dict | None:
    """返回 mcpServers.<name> 节点；未注册返回 None。"""
    cfg = _read_cfg(CLAUDE_JSON)
    return _mcp_servers(cfg).get(name)


def main() -> int:
    """CLI 入口（供测试/手工）：python3 claude_registry.py <inspect|unregister> <name>"""
    _configure_utf8_stdout()
    if len(sys.argv) < 3 or sys.argv[1] not in ("inspect", "unregister"):
        print("用法: claude_registry.py <inspect|unregister> <name>")
        return 1
    cmd, name = sys.argv[1], sys.argv[2]
    if cmd == "inspect":
        try:
            node = inspect(name)
        except RuntimeError as exc:  # 损坏保护: 报错并失败退出, 绝不覆盖
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        print(json.dumps(node, ensure_ascii=False, indent=2) if node else "absent")
    else:
        try:
            removed = unregister(name)
        except RuntimeError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        print("已移除" if removed else "未注册，跳过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
