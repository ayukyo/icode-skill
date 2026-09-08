#!/usr/bin/env python3
"""在显式 Git 根内解析 runtime/analysis/verification 三基线；从不 fetch。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
HASH_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{7,40})(?![0-9a-fA-F])")
VERSION_CONTEXT_RE = re.compile(r"\b(commit|git|hash|revision|rev|version|build[-_ ]?id)\b", re.I)


class BaselineError(RuntimeError):
    """输入范围、Git 根或输出格式错误。"""


PROTECTED_OUTPUT_NAMES = {
    ".ico_metadata.json",
    ".ico_events.jsonl",
    ".ico_txn.json",
    ".active_ticket.json",
    "evidence_manifest.json",
    "debug_catalog.json",
    "index.json",
}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _same_file(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _validate_output_path(root: Path, value: str | Path, inputs: list[Path]) -> Path:
    path = Path(value).resolve()
    if not _inside(path, root):
        raise BaselineError(f"输出必须位于 --root 内: {path}")
    relative = path.relative_to(root)
    if ".git" in relative.parts:
        raise BaselineError(f"输出不得覆盖 Git 控制文件: {path}")
    if path.name.startswith(".ico_") or path.name in PROTECTED_OUTPUT_NAMES:
        raise BaselineError(f"输出不得覆盖 ICODE 证据/控制文件: {path}")
    if any(_same_file(path, source) for source in inputs):
        raise BaselineError(f"输出不得覆盖输入证据: {path}")
    return path


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _validate_repo(root: Path, value: str | Path) -> Path:
    repo = Path(value).resolve()
    if not _inside(repo, root):
        raise BaselineError(f"repo 必须位于 --root 内: {repo}")
    probe = _run_git(repo, "rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        raise BaselineError(f"不是可读 Git 工作树: {repo}")
    return repo


def resolve_commit(repo: str | Path, candidate: str | None) -> dict[str, Any]:
    """仅在指定 repo 内把候选解析为唯一 commit，不触发任何远端操作。"""
    repo_path = Path(repo).resolve()
    raw = (candidate or "").strip()
    if not raw:
        return {"candidate": raw or None, "resolved": False, "commit": None, "reason": "missing"}
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", raw):
        return {"candidate": raw, "resolved": False, "commit": None, "reason": "invalid_hash"}
    result = _run_git(repo_path, "rev-parse", "--verify", f"{raw}^{{commit}}")
    if result.returncode != 0:
        reason = "ambiguous_or_unreachable" if "ambiguous" in result.stderr.lower() else "unreachable"
        return {"candidate": raw, "resolved": False, "commit": None, "reason": reason}
    commit = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
    if not commit or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return {"candidate": raw, "resolved": False, "commit": None, "reason": "not_a_commit"}
    return {"candidate": raw, "resolved": True, "commit": commit, "reason": "resolved_in_repo"}


def _head(repo: Path) -> dict[str, Any]:
    result = _run_git(repo, "rev-parse", "--verify", "HEAD^{commit}")
    if result.returncode != 0:
        return {"resolved": False, "commit": None, "reason": "head_unresolved"}
    return {"resolved": True, "commit": result.stdout.strip(), "reason": "analysis_head"}


def compare_to_head(repo: str | Path, runtime_commit: str | None) -> str:
    """比较现场提交与分析 HEAD，返回保守的四值关系。"""
    if not runtime_commit:
        return "unresolved"
    repo_path = Path(repo).resolve()
    head = _head(repo_path)
    if not head["resolved"]:
        return "unresolved"
    if runtime_commit == head["commit"]:
        return "same_as_head"
    ancestor = _run_git(repo_path, "merge-base", "--is-ancestor", runtime_commit, head["commit"])
    if ancestor.returncode == 0:
        return "ancestor_of_head"
    if ancestor.returncode == 1:
        return "ahead_or_forked"
    return "unresolved"


def _parse_assignments(values: list[str], option: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise BaselineError(f"{option} 必须使用 module=value: {value}")
        module, payload = value.split("=", 1)
        module = module.strip()
        payload = payload.strip()
        if not module or not payload:
            raise BaselineError(f"{option} 的 module/value 不能为空: {value}")
        if module in result and result[module] != payload:
            raise BaselineError(f"{option} module 重复且值冲突: {module}")
        result[module] = payload
    return result


def _candidate_from_logs(module: str, log_paths: list[Path]) -> tuple[str | None, list[str], list[str]]:
    found: list[tuple[str, str]] = []
    failures = []
    for path in log_paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for number, line in enumerate(stream, 1):
                    if module.lower() not in line.lower() or not VERSION_CONTEXT_RE.search(line):
                        continue
                    for match in HASH_RE.findall(line):
                        found.append((match, f"{path}:{number}"))
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    unique = sorted({candidate.lower() for candidate, _ in found})
    refs = sorted({ref for _, ref in found})
    if len(unique) == 1:
        return unique[0], refs, failures
    if len(unique) > 1:
        failures.append(f"{module}: multiple runtime candidates: {', '.join(unique)}")
    return None, refs, failures


def _manifest_candidates(paths: list[Path]) -> tuple[dict[str, list[str]], list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    failures = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{path}: {exc}")
            continue
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            failures.append(f"{path}: unsupported evidence manifest schema")
            continue
        hints = value.get("coverage_hints") or {}
        raw = hints.get("runtime_version_candidates") or {}
        if isinstance(raw, dict):
            for module, candidates in raw.items():
                if isinstance(candidates, str):
                    candidates = [candidates]
                if isinstance(candidates, list):
                    result[str(module)].extend(str(item) for item in candidates)
    return result, failures


def build_baseline(
    root: str | Path,
    repo_map: dict[str, str | Path],
    candidates: dict[str, str] | None = None,
    evidence_manifests: list[str | Path] | None = None,
    logs: list[str | Path] | None = None,
    verification: dict[str, str] | None = None,
) -> dict[str, Any]:
    """为每个显式映射模块生成三基线，不跨 Git 根猜测版本。"""
    root_path = Path(root).resolve()
    if not root_path.is_dir() or root_path == Path(root_path.anchor):
        raise BaselineError(f"--root 必须是已存在的非根目录: {root_path}")
    repos = {module: _validate_repo(root_path, path) for module, path in repo_map.items()}
    if not repos:
        raise BaselineError("至少需要一个 --repo-map")
    explicit = dict(candidates or {})
    verification_values = dict(verification or {})
    unknown_modules = sorted((set(explicit) | set(verification_values)) - set(repos))
    if unknown_modules:
        raise BaselineError(
            "candidate/verification module 缺少 --repo-map: " + ", ".join(unknown_modules)
        )
    log_paths = [Path(path).resolve() for path in logs or []]
    manifest_paths = [Path(path).resolve() for path in evidence_manifests or []]
    for path in log_paths + manifest_paths:
        if not _inside(path, root_path) or not path.is_file():
            raise BaselineError(f"输入证据必须是 --root 内的文件: {path}")
    manifest_candidates, unresolved = _manifest_candidates(manifest_paths)

    rows = []
    for module, repo in sorted(repos.items()):
        evidence_refs = []
        candidate = explicit.get(module)
        source = "explicit" if candidate else None
        if not candidate:
            values = sorted(set(manifest_candidates.get(module) or []))
            if len(values) == 1:
                candidate = values[0]
                source = "evidence_manifest"
                evidence_refs.extend(str(path) for path in manifest_paths)
            elif len(values) > 1:
                unresolved.append(f"{module}: multiple manifest candidates: {', '.join(values)}")
        if not candidate:
            candidate, refs, failures = _candidate_from_logs(module, log_paths)
            evidence_refs.extend(refs)
            unresolved.extend(failures)
            if candidate:
                source = "log_context"
        runtime = resolve_commit(repo, candidate)
        runtime["source"] = source
        runtime["evidence_refs"] = sorted(set(evidence_refs))
        analysis = _head(repo)
        verification_row = resolve_commit(repo, verification_values.get(module))
        verification_row["source"] = "explicit_verification" if module in verification_values else None
        rows.append(
            {
                "module": module,
                "repo": str(repo),
                "runtime": runtime,
                "analysis": analysis,
                "verification": verification_row,
                "relation": compare_to_head(repo, runtime.get("commit")),
                "confidence": "high" if runtime["resolved"] and source == "explicit" else (
                    "medium" if runtime["resolved"] else "unresolved"
                ),
            }
        )
        if not runtime["resolved"]:
            unresolved.append(f"{module}: runtime {runtime['reason']}")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_scope": {
            "root": str(root_path),
            "repo_map": {module: str(repo) for module, repo in sorted(repos.items())},
            "candidates": dict(sorted(explicit.items())),
            "verification": dict(sorted(verification_values.items())),
            "evidence_manifests": [str(path) for path in manifest_paths],
            "logs": [str(path) for path in log_paths],
        },
        "modules": rows,
        "unresolved": sorted(set(unresolved)),
        "status": "partial" if unresolved else "complete",
        "read_only": True,
        "git_side_effects": [],
        "next_action": "主代理复核后再通过控制面写入 metadata；工具本身不写 metadata。",
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ICODE 三基线矩阵",
        "",
        "| 模块 | Git 根 | Runtime | Analysis | Verification | 关系 | 置信度 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["modules"]:
        runtime = row["runtime"].get("commit") or row["runtime"].get("candidate") or "unresolved"
        analysis = row["analysis"].get("commit") or "unresolved"
        verification = row["verification"].get("commit") or "unresolved"
        lines.append(
            f"| {row['module']} | `{row['repo']}` | `{runtime}` | `{analysis}` | "
            f"`{verification}` | {row['relation']} | {row['confidence']} |"
        )
    if report["unresolved"]:
        lines.extend(["", "## 未解决项", ""])
        lines.extend(f"- {item}" for item in report["unresolved"])
    lines.extend(["", "> 本工具不执行 fetch/checkout/commit/push，也不直接写 metadata。", ""])
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ICODE runtime/analysis/verification 三基线解析器")
    parser.add_argument("--root", required=True)
    parser.add_argument("--repo-map", action="append", default=[], metavar="MODULE=PATH")
    parser.add_argument("--candidate", action="append", default=[], metavar="MODULE=HASH")
    parser.add_argument("--verification", action="append", default=[], metavar="MODULE=HASH")
    parser.add_argument("--evidence-manifest", action="append", default=[])
    parser.add_argument("--log", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = Path(args.root).resolve()
        repo_map = _parse_assignments(args.repo_map, "--repo-map")
        candidates = _parse_assignments(args.candidate, "--candidate")
        verification = _parse_assignments(args.verification, "--verification")
        report = build_baseline(
            root,
            repo_map,
            candidates=candidates,
            evidence_manifests=args.evidence_manifest,
            logs=args.log,
            verification=verification,
        )
        inputs = [
            Path(path).resolve()
            for path in [*args.evidence_manifest, *args.log]
        ]
        output = _validate_output_path(root, args.output, inputs)
        markdown = _validate_output_path(root, args.markdown, inputs) if args.markdown else None
        if markdown is not None and _same_file(output, markdown):
            raise BaselineError("--output 与 --markdown 不得指向同一文件")
        _atomic_write(output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if markdown is not None:
            _atomic_write(markdown, _render_markdown(report))
    except BaselineError as exc:
        print(f"runtime_baseline: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"runtime_baseline: 文件系统错误: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "output": str(output), "modules": len(report["modules"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
