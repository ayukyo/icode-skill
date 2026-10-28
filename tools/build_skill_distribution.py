#!/usr/bin/env python3
"""Build an offline skills-only plugin snapshot; never install or publish it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import urlsplit

from validate_skill_pack import iter_source_publish_files, validate_routes, validate_skills


ROOT = Path(__file__).resolve().parents[1]
HOSTS = ("claude", "codex", "codebuddy")
COMPONENTS = ("ppt", "docx", "mcp", "ui", "support")
CORE_MAX_FILES = 200
CORE_MAX_BYTES = 5 * 1024 * 1024
CORE_ENTRY_MAX_BYTES = 8 * 1024
# Runtime resources only. Website, tests, demo, host settings and Git are excluded.
DIRECTORIES = {"steps", "references", "tools", "schemas", "templates", "scripts", "agents",
               "mcp", "agent_runtime", "skill-packs", "docs"}
FILES = {"SKILL.md", "LICENSE", "README.md", "README.zh-CN.md", "install.sh", ".gitignore"}
METADATA = {"integrations/discovery/README.md"} | {
    f"integrations/discovery/{host}.plugin.json.template" for host in HOSTS}
REQUIRED = FILES | METADATA | {"skill-packs/manifest.json", "mcp/workflow-gate/gates.json",
                    "mcp/workflow-gate/skill-routes.json", "tools/icode_control.py",
                    "scripts/sync-to-global.sh", "tools/docx/bootstrap_renderer.py",
                    "tools/docx/renderer_support.py", "tools/docx/renderer_launch.py",
                    "tools/docx/renderer_packages.lock.json", "steps/docx.md",
                    "references/docx_authoring.md", "references/docx_revision.md",
                    "tools/docx/build_docx.py", "tools/docx/inspect_docx.py",
                    "tools/docx/render_docx.py", "tools/docx/compare_docx.py",
                    "tools/docx/verify_sources.py", "tools/docx/draw_sequence.py"}
REQUIRED |= {"steps/doc.md", "references/doc_snapshot_contract.md", "tools/doc_snapshot.py"}
REQUIRED |= {"tools/test_result_summary.py", "references/wsl_build.md", "references/mcp_path_access.md"}
REQUIRED |= {"mcp/_lib/environment_diagnostics.py", "tools/mcp_doctor.py",
             "references/environment_diagnostics.md"}


def no_symlinks(path):
    if ".." in path.parts:
        raise ValueError(f"parent traversal is not allowed: {path}")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"symlink is not allowed: {component}")


def allowed(path):
    return (path.as_posix() in FILES | METADATA or path.parts[0] in DIRECTORIES | {"assets"}
            or path.parts[:2] == ("integrations", "codebuddy"))


def check_public_path(path):
    for part in path.parts:
        lower = part.lower()
        if lower.startswith(".") and path.as_posix() != ".gitignore":
            raise ValueError(f"hidden/private path is not publishable: {path}")
        if (re.search(r"(^|[._-])(private|secrets?|credentials?)([._-]|$)", lower)
                or lower in {"__pycache__", "node_modules"}):
            raise ValueError(f"private/runtime path is not publishable: {path}")
    name = path.name.lower()
    # Only these reviewed public cookie tools are payload, never their cookie data.
    if path.as_posix() in {"tools/tb/scripts/tb_cookie.py", "tools/tb/scripts/tb_cookie_win.py"}:
        return
    if any(re.search(r"(^|[._-])(cookies?|token)([._-]|$)", part.lower()) for part in path.parts):
        raise ValueError(f"credential data path is not publishable: {path}")
    if name == "config.example.json" or name.endswith(".template"):
        return
    if (re.search(r"(^|[._-])(private|secrets?|credentials?|cookies?|token)([._-]|$)", name)
            or re.match(r"config([._-]|$)", name)
            or path.suffix.lower() in {".pem", ".key", ".p12", ".pyc", ".pyo", ".db", ".sqlite"}):
        raise ValueError(f"sensitive/config path is not publishable: {path}")


def source_files(source):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], stderr=subprocess.PIPE)
    if Path(git("rev-parse", "--show-toplevel").decode().strip()) != source:
        raise ValueError("--source must be the Git root")
    selected = []
    for row in git("ls-files", "--stage", "-z").decode().split("\0"):
        if not row:
            continue
        metadata, name = row.split("\t", 1)
        mode, _, stage = metadata.split()
        path = Path(name)
        if not allowed(path):
            continue
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"invalid source path: {name}")
        check_public_path(path)
        no_symlinks(source / path)
        if mode not in {"100644", "100755"} or stage != "0":
            raise ValueError(f"non-regular/unmerged Git entry: {name}")
        if not stat.S_ISREG((source / path).stat().st_mode):
            raise ValueError(f"non-regular source file: {name}")
        selected.append(path)
    missing = REQUIRED - {p.as_posix() for p in selected}
    missing |= {f"{d}/" for d in DIRECTORIES if not any(p.parts[0] == d for p in selected)}
    if missing:
        raise ValueError(f"missing workflow resources: {sorted(missing)}")
    return sorted(selected)


def component_for(path):
    """Keep gate data and shared libraries available without MCP service installs."""
    name = path.as_posix()
    if name.startswith("tools/ppt/") or name == "steps/ppt.md":
        return "ppt"
    if name.startswith(("tools/docx/", "references/docx_")) or name == "steps/docx.md":
        return "docx"
    if name.startswith("agent_runtime/") or name == "tools/icode_agent.py":
        return "ui"
    if name.startswith("docs/") or (name.startswith("skill-packs/") and name != "skill-packs/manifest.json"):
        return "support"
    if name.startswith("mcp/") and not (
            name.startswith(("mcp/_lib/", "mcp/workflow-gate/", "mcp/reasoning-gate/"))
            or name in {"mcp/cheap-research/gates.json", "mcp/icode-mcp-policy/policy.json"}):
        return "mcp"
    return None


def compact_entry(original, version):
    metadata = re.match(r"\A---\r?\n.*?\r?\n---\r?\n", original, re.S)
    if not metadata:
        raise ValueError("root SKILL.md must expose frontmatter")
    return metadata.group(0) + f'''
# ICODE 核心入口

**版本**: v{version}

本包是精简导入发行。保持原有 ICODE 触发与授权边界：普通请求不自动转为工单；能力咨询只解释；已绑定工单按已有授权续接。

## 执行入口

先按当前请求读取 [WORKFLOW.md](WORKFLOW.md) 的自然语言入口、命令和适用通用规则，再完整读取 `steps/` 中对应步骤。旧引用 `SKILL.md「某节」` 对应 WORKFLOW.md 同名节。共享规则按 thinking_core.md 的首次实读、同上下文复用、失效重载合同加载；实际源码、状态、测试和独立审查仍履行原门禁。

核心包含代码流程、控制面、schemas/gates 和共享规则。PPT、DOCX、MCP 服务安装、UI 与开发资料按需加载，不把缺失组件写成能力已验证。

插件发行同时提供独立共享技能。若单技能导入后宿主未发现路由所需的领域技能，按需准备 `support`，从返回运行根的 `skill-packs/<技能名>/SKILL.md.template` 读取原始技能正文；失败时仍按既有路由诚实降级，不将模板获取冒充事实验证。

## 按需扩展

| 需求 | 组件 |
|---|---|
| 生成 PPT、模板及预览 | `ppt` |
| Word 编写、修订、检查和渲染引导 | `docx` |
| 安装或修复 ICODE MCP 服务 | `mcp` |
| 打开 ICODE UI 工作台 | `ui` |
| 开发文档、原始共享技能模板 | `support` |

执行这些能力前，从本技能根运行（路径和组件名由 Agent 代入）：

```bash
python -B tools/install_skill_pack.py --component-root "<本技能根>" --component ppt
```

安装器从 components.json 指定的同版本官方发行附件自动获取、校验并缓存，返回 `runtime_root`。随后从该根读取步骤和运行工具。运行资源放在用户缓存中，不追加到 Agent 扫描的核心技能目录，核心文件数保持不变。已有相同文件幂等复用，用户无需寻找或解压扩展包。离线时可追加 `--component-archive "<同版本本地zip>"`；`--dry-run` 只预检，不下载、不安装。失败须说明网络、缺资源或冲突，不静默切版本、不覆盖不同内容、不把下载成功当作业务验证通过。DOCX 渲染环境仍由已有引导工具准备。

完整规则中的相对资源路径以当前返回的运行根为基准。源码全量安装仍按原步骤从真实源码 checkout 执行，不在插件缓存直接运行 install.sh。组件获取本身不注册 MCP、不执行扩展代码、不改宿主设置。离线发布包与核心包绑定版本和 SHA-256；发行地址尚未发布或不可达时，明确保留待准备状态。
'''


def split_components(workflow, archives, version, download_base):
    archives.mkdir()
    base = download_base or f"https://github.com/ayukyo/icode-skill/releases/download/v{version}"
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username
            or parsed.password or parsed.port or parsed.query or parsed.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/download/v" + re.escape(version) + r"/?", parsed.path)):
        raise ValueError("component download base must be a version-matched GitHub release URL")
    manifest = {"schema_version": 1, "profile": "core", "version": version, "components": {}}
    paths = [p for p in sorted(workflow.rglob("*")) if p.is_file()]
    for component in COMPONENTS:
        filename = f"icode-v{version}-{component}.zip"
        archive = archives / filename
        rows = []
        with zipfile.ZipFile(archive, "w") as zipped:
            for path in paths:
                relative = path.relative_to(workflow)
                if component_for(relative) != component:
                    continue
                data = path.read_bytes()
                mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                info = zipfile.ZipInfo(relative.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                zipped.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
                rows.append({"path": relative.as_posix(), "size": len(data),
                             "sha256": hashlib.sha256(data).hexdigest(), "mode": mode})
                path.unlink()  # Only files in this build's private temporary snapshot.
        manifest["components"][component] = {
            "archive": filename, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "size": archive.stat().st_size, "url": base.rstrip("/") + "/" + filename, "files": rows}
    original = (workflow / "SKILL.md").read_bytes()
    (workflow / "WORKFLOW.md").write_bytes(original)
    (workflow / "SKILL.md").write_text(compact_entry(original.decode("utf-8"), version), encoding="utf-8")
    manifest["core_files"] = [
        {"path": p.relative_to(workflow).as_posix(), "size": p.stat().st_size,
         "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
         "mode": 0o755 if p.stat().st_mode & 0o111 else 0o644}
        for p in sorted(workflow.rglob("*")) if p.is_file()]
    (workflow / "components.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    for directory in sorted(workflow.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def build(source, output, profile="full", components_output=None, download_base=None):
    source, output = source.expanduser().absolute(), output.expanduser().absolute()
    no_symlinks(source)
    no_symlinks(output)
    if output.exists() or output.is_relative_to(source) or output.name != "icode":
        raise ValueError("output must be a new directory named icode outside the source")
    if not output.parent.is_dir():
        raise ValueError("output parent must already exist")
    if profile not in {"full", "core"}:
        raise ValueError("unknown distribution profile")
    if profile == "core":
        if components_output is None:
            raise ValueError("core requires --components-output")
        components_output = components_output.expanduser().absolute()
        no_symlinks(components_output)
        if (components_output.exists() or not components_output.parent.is_dir()
                or components_output.is_relative_to(source) or components_output.is_relative_to(output)
                or output.is_relative_to(components_output)):
            raise ValueError("components output must be a new separate directory outside the source")
    elif components_output is not None or download_base is not None:
        raise ValueError("component options require core profile")
    paths = source_files(source)  # Preflight every path before reading any payload.
    templates = source / "integrations/discovery"
    for name in ("README.md", *(f"{host}.plugin.json.template" for host in HOSTS)):
        no_symlinks(templates / name)
    # Validate a tracked-only snapshot: the existing shared-skill validator must
    # never traverse untracked files or personal configuration in the checkout.
    with tempfile.TemporaryDirectory(prefix="icode-distribution-") as temporary:
        package = Path(temporary) / "icode"
        workflow = package / "skills/icode"
        for rel in paths:
            if rel.as_posix() in METADATA:
                continue
            target = workflow / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, target)
            target.chmod(0o755 if (source / rel).stat().st_mode & 0o111 else 0o644)
        version = re.search(r"^\*\*版本\*\*:\s*v(\d+\.\d+\.\d+)\s*$",
                            (workflow / "SKILL.md").read_text(encoding="utf-8"), re.MULTILINE)
        if not version:
            raise ValueError("root SKILL.md must declare a release version")
        rows = validate_skills(workflow / "skill-packs/manifest.json")
        if not rows or any(row["name"] == "icode" for row in rows):
            raise ValueError("shared skill manifest is empty or collides with icode")
        validate_routes(workflow / "mcp/workflow-gate/skill-routes.json",
                        {row["name"] for row in rows})
        for row in rows:
            for origin, rel in iter_source_publish_files(Path(row["path"]), row["entrypoint"]):
                target = package / "skills" / row["name"] / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, target)
                target.chmod(origin.stat().st_mode & 0o777)
        archives = Path(temporary) / "components"
        if profile == "core":
            split_components(workflow, archives, version.group(1), download_base)
        for host in HOSTS:
            manifest = json.loads((templates / f"{host}.plugin.json.template").read_text(encoding="utf-8"))
            manifest["version"] = version.group(1)
            target = package / f".{host}-plugin/plugin.json"
            target.parent.mkdir()
            target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(templates / "README.md", package / "README.md")
        shutil.copyfile(workflow / "LICENSE", package / "LICENSE")
        sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(package).as_posix()}"
                for p in sorted(package.rglob("*")) if p.is_file()]
        (package / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
        payload = [p for p in package.rglob("*") if p.is_file()]
        size = sum(p.stat().st_size for p in payload)
        if profile == "core" and (len(payload) > CORE_MAX_FILES or size > CORE_MAX_BYTES
                or (workflow / "SKILL.md").stat().st_size > CORE_ENTRY_MAX_BYTES):
            raise ValueError(f"core budget exceeded: files={len(payload)}, bytes={size}")
        # Exclusive creation; no overwrite/merge option. A disk failure leaves
        # an incomplete new output for inspection, never deletes user data.
        output.mkdir()
        shutil.copytree(package, output, dirs_exist_ok=True)
        if profile == "core":
            components_output.mkdir()
            shutil.copytree(archives, components_output, dirs_exist_ok=True)
    return {"output": str(output), "version": version.group(1), "skills": 1 + len(rows),
            "profile": profile, "files": len(payload), "bytes": size,
            "components_output": str(components_output) if components_output else None,
            "validation": "package_structure_only", "host_install_tested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("full", "core"), default="full")
    parser.add_argument("--components-output", type=Path)
    parser.add_argument("--download-base", help="Version-matched GitHub release base; building never contacts it")
    args = parser.parse_args()
    try:
        result = build(args.source, args.output, args.profile, args.components_output, args.download_base)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
