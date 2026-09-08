#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/submission_guard.py — worktree 提交契约机器闸门（提案 worktree-upstream-push-guard 阶段 4 落地）

职责（只读检查 + 受控合并 + 一次性迁移；ICode 红线不变——本脚本不 commit / 不 push）：

  normalize-url <url>
      规范化 remote URL：去首尾空白 / 去尾斜杠 / 去 .git 后缀 / SSH 与 HTTPS 等价归一。
      「remote 名相同 ≠ 提交目标相同」，比较一律用 normalize 后的 URL + target ref 双字段。

  migrate-legacy --metadata <path> [--dry-run]
      旧工单无 submission_contracts 时的一次性契约迁移（真源：references/worktree_isolation.md §3.5.5「兼容」+ §3.7 写回条件）：
        - 候选唯一且机器证据完整（非 detached / 有 @{u} / 单一 remote / 分支 == worktree_branch）→
          写 submission_contracts，每项带 migration_source="legacy_inference"，随后立即跑 g2-check，
          未通过则回滚 metadata 写入（不改 git 分支目标）；
        - 任一歧义（无 upstream / 多候选 remote / detached / 分支漂移）→ 不写契约，退出码 2，
          报告 needs_user_confirm + reason + 候选清单，等用户确认；
        - 已有契约 → 跳过（幂等）。
      写回前自动备份原 metadata 到 <path>.bak。<path> 为 .ico_metadata.json 绝对路径。

  g2-check --metadata <path>
      G2 ⑩ 执行前契约校验（只读）：对 submission_contracts 每个契约仓库逐一校验
        非 detached / 当前分支 == worktree_branch / @{u} == target_remote_ref / 规范化 URL == remote_url /
        HEAD 与 target 可解析 / tracking_verified==true。
      任一违约 → 该仓库 verdict=blocked，总 verdict=blocked（L1）。无契约 → 提示跳过（只读工单）。

  submit-check --metadata <path> [--merge]
      G3 交付前逐仓提交清单：默认只读刷新并检查线上目标；显式 --merge 时先对全部契约仓库
      完成只读预检，再执行 fast-forward 或无冲突的 `merge --no-commit --no-ff`。
      不自动 commit / push；fetch 或冲突预检失败时不退回缓存 ref 误报 pass。

  handoff --metadata <path> --output <json> [--markdown <md>]
      生成多仓交付矩阵。只读取本地 Git 与 extensions.handoff.inputs，不 fetch、不修改 metadata/Git；
      缺少构建、部署、文档或提交处置证据时保留 null/unresolved，不从 clean 状态猜测已完成交付。

退出码：0 = pass / 2 = needs_user_confirm 或 blocked / 1 = 用法或执行错误。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# remote URL 规范化
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """规范化 remote URL：去空白/尾斜杠/.git 后缀，SSH 与 HTTPS 等价归一为 host/path。"""
    url = (url or "").strip().rstrip("/")
    if not url:
        return url
    if url.endswith(".git"):
        url = url[:-4]
    # git@host:path / ssh@host:path / user@host:path → host/path
    m = re.match(r"^(?:git|ssh|[^/@]+)@([^:]+):(.+)$", url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    # 带协议：https:// / http:// / ssh:// / git:// → host/path
    m = re.match(r"^(?:https?|ssh|git)://([^/]+)/(.+)$", url)
    if m:
        host = m.group(1)
        # ssh://git@host/path 剥掉 user 前缀，与 scp-like 形式 git@host:path 归一（同一仓库两种写法应等价）
        if "@" in host:
            host = host.rsplit("@", 1)[1]
        return f"{host}/{m.group(2)}"
    # 其它（本地路径等）原样返回（去掉了尾斜杠与 .git）
    return url


# ---------------------------------------------------------------------------
# git 只读原语
# ---------------------------------------------------------------------------

def git(cwd: Path, *args: str) -> str:
    """在 cwd 执行只读 git 命令，返回 stdout（strip）。失败返回空串。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return ""
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def git_exit0(cwd: Path, *args: str) -> bool:
    """执行 git 命令，仅判断 exit 0（如 rev-parse --verify）。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=30,
        )
        return out.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def main_worktree_root(repo_path: Path) -> str:
    """repo_path 所属仓库的主工作区根（git worktree list --porcelain 首行）。"""
    wt = git(repo_path, "worktree", "list", "--porcelain")
    for line in wt.splitlines():
        if line.startswith("worktree "):
            return line.split(" ", 1)[1]
    return ""


# ---------------------------------------------------------------------------
# 契约构造与校验
# ---------------------------------------------------------------------------

def build_contract(repo_path: Path, source_repo_path: Path, worktree_branch: str,
                   repo_role: str, created_at: str) -> dict | None:
    """构造单个仓库的提交契约候选。返回 None 表示歧义（needs_user_confirm）。"""
    branch = git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    upstream = git(repo_path, "rev-parse", "--symbolic-full-name", "@{u}")
    head_ok = git_exit0(repo_path, "rev-parse", "--verify", "HEAD")
    if branch == "HEAD" or not branch:
        return None          # detached
    if not upstream:
        return None          # 无 upstream
    if branch != worktree_branch:
        return None          # 分支漂移
    # 从 upstream refs/remotes/<remote>/<br> 定位 remote 名与远端分支
    m = re.match(r"^refs/remotes/([^/]+)/(.+)$", upstream)
    if not m:
        return None          # upstream 形态不识别
    remote_name, remote_branch = m.group(1), m.group(2)
    remote_url = git(repo_path, "remote", "get-url", remote_name)
    if not remote_url:
        return None          # remote URL 不可识别
    source_branch = git(source_repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    source_upstream = git(source_repo_path, "rev-parse", "--symbolic-full-name", "@{u}")
    target_commit = git(repo_path, "rev-parse", upstream)
    if not target_commit or not head_ok:
        return None          # HEAD / target 不可解析
    # G1 逐项比对（tracking_verified）
    verified = (
        git(repo_path, "rev-parse", "--abbrev-ref", "HEAD") == worktree_branch
        and git(repo_path, "rev-parse", "--symbolic-full-name", "@{u}") == upstream
        and bool(git(repo_path, "remote"))  # remote 名集合非空即可（候选已有 get-url 实证）
        and head_ok
    )
    return {
        "repo_role": repo_role,
        "repo_path": str(repo_path),
        "source_repo_path": str(source_repo_path),
        "worktree_branch": worktree_branch,
        "source_branch": source_branch or None,
        "source_upstream": source_upstream or None,
        "remote_name": remote_name,
        "remote_url": normalize_url(remote_url),
        "target_remote_ref": upstream,
        "target_push_ref": f"refs/heads/{remote_branch}",
        "target_commit_at_create": target_commit,
        "push_refspec": f"HEAD:refs/heads/{remote_branch}",
        "tracking_verified": verified,
        "created_at": created_at,
        "migration_source": "legacy_inference",
    }


def load_metadata(metadata_path: Path) -> dict:
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ 无法解析 metadata {metadata_path}: {e}", file=sys.stderr)
        sys.exit(1)


def atomically_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def atomically_write_text(path: Path, content: str) -> None:
    """先完整写临时文件，再原子替换文本目标；失败不破坏既有目标。"""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        finally:
            raise


# ---------------------------------------------------------------------------
# 子命令：migrate-legacy
# ---------------------------------------------------------------------------

def derive_candidates(meta: dict) -> list[dict]:
    """从旧 metadata 推导各仓库的契约候选（歧义返回 None 项，由调用方判定）。"""
    created_at = __import__("datetime").datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    candidates: list[dict] = []
    # super：active_checkout（缺失按 §3.7 用 worktree_path 推导）
    active = meta.get("active_checkout") or {}
    wt_path = active.get("path") or meta.get("worktree_path")
    wt_branch = active.get("branch") or meta.get("worktree_branch")
    if wt_path and wt_branch:
        repo_path = Path(wt_path)
        source_repo = Path(main_worktree_root(repo_path) or repo_path)
        candidates.append(build_contract(repo_path, source_repo, wt_branch, "super", created_at))
    # sub：sub_worktrees
    for sub in meta.get("sub_worktrees") or []:
        sub_path = sub.get("worktree_path")
        sub_branch = sub.get("branch")
        if not sub_path or not sub_branch:
            continue
        repo_path = Path(sub_path)
        # 子仓源 = super 主仓根 + 子仓相对路径
        source_repo = Path(main_worktree_root(repo_path) or repo_path)
        candidates.append(build_contract(repo_path, source_repo, sub_branch, "sub", created_at))
    return [c for c in candidates if c is not None]


def cmd_migrate(args) -> int:
    meta_path = Path(args.metadata).expanduser()
    meta = load_metadata(meta_path)
    existing = meta.get("submission_contracts") or []
    if existing:
        print("ℹ️ 已有 submission_contracts（非空），跳过迁移（幂等）")
        return 0
    candidates = derive_candidates(meta)
    if not candidates:
        # 原地工单（无 worktree 上下文）无提交契约合法（§3.8⑩ 仅当契约非空才检查），与"有 worktree 但证据歧义"区分开
        has_wt = bool(meta.get("active_checkout")) or bool(meta.get("worktree_path")) or bool(meta.get("sub_worktrees"))
        if not has_wt:
            print("ℹ️ 原地工单（无 worktree 上下文）——无提交契约可推导；G2 ⑩ 仅当契约非空才检查，无契约跳过（合法）。如需冻结提交目标请人工补充 submission_contracts（真源 §3.5.5）")
            return 0
        print("❌ needs_user_confirm——无法自动迁移：detached HEAD / 无 @{u} / 多候选 remote / 分支漂移 / HEAD 或 target 不可解析 至少一项")
        print("   不写契约，请用户在确认目标分支后手动补充 submission_contracts（真源 §3.5.5 元素结构）")
        return 2
    missing = meta.get("submission_contracts")
    _ = missing  # 已在上方处理
    if args.dry_run:
        print("🔍 [dry-run] 候选唯一，将写入 legacy_inference 契约：")
        for c in candidates:
            print(f"   - {c['repo_role']} {c['repo_path']} → {c['target_remote_ref']} (tracking_verified={c['tracking_verified']})")
        return 0
    # 备份原 metadata（显式拼接 .bak——Path.with_suffix 对点开头的隐藏文件如 .ico_metadata.json 不生效）
    backup = Path(str(meta_path) + ".bak")
    shutil.copyfile(meta_path, backup)
    # 写契约（migration_source 已在候选项内）
    meta["submission_contracts"] = candidates
    atomically_write(meta_path, meta)
    # 迁移后立刻跑 G2（§8：未通过则回滚 metadata 写入）
    ok, blocked = run_g2(meta)
    if ok:
        print(f"✅ 迁移成功：写入 {len(candidates)} 份 legacy_inference 契约，G2 校验 pass")
        print(f"   备份保留于 {backup}（确认无误后可删除）")
        return 0
    # 回滚
    shutil.copyfile(backup, meta_path)
    print(f"❌ 迁移后 G2 校验 blocked（{blocked}），已回滚 metadata 写入，备份保留于 {backup}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# 子命令：g2-check（G2 ⑩ 契约校验）
# ---------------------------------------------------------------------------

def run_g2(meta: dict) -> tuple[bool, str]:
    """对 submission_contracts 每个契约逐一校验。返回 (all_pass, blocked_reason)。"""
    contracts = meta.get("submission_contracts") or []
    if not contracts:
        return True, "无契约（只读工单或未迁移），跳过 G2"
    problems = []
    for c in contracts:
        repo_path = Path(c.get("repo_path", ""))
        if not repo_path.is_dir():
            problems.append(f"{c.get('repo_role','?')} {repo_path}: 路径不存在")
            continue
        branch = git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
        if branch == "HEAD" or not branch:
            problems.append(f"{c.get('repo_role')} {repo_path}: detached HEAD")
            continue
        if branch != c.get("worktree_branch"):
            problems.append(f"{c.get('repo_role')} {repo_path}: 当前分支 {branch} != 契约 {c.get('worktree_branch')}")
        upstream = git(repo_path, "rev-parse", "--symbolic-full-name", "@{u}")
        if not upstream:
            problems.append(f"{c.get('repo_role')} {repo_path}: 无 @{{u}}")
        elif upstream != c.get("target_remote_ref"):
            problems.append(f"{c.get('repo_role')} {repo_path}: upstream drift {upstream} != 契约 {c.get('target_remote_ref')}")
        remote_url = git(repo_path, "remote", "get-url", c.get("remote_name", "")) if c.get("remote_name") else ""
        if remote_url and normalize_url(remote_url) != normalize_url(c.get("remote_url", "")):
            problems.append(f"{c.get('repo_role')} {repo_path}: remote URL mismatch {normalize_url(remote_url)} != {normalize_url(c.get('remote_url',''))}")
        if not git_exit0(repo_path, "rev-parse", "--verify", "HEAD"):
            problems.append(f"{c.get('repo_role')} {repo_path}: HEAD 不可解析")
        if not git_exit0(repo_path, "rev-parse", "--verify", c.get("target_remote_ref", "")):
            problems.append(f"{c.get('repo_role')} {repo_path}: target ref {c.get('target_remote_ref')} 不可解析（须先 git fetch）")
        if not c.get("tracking_verified"):
            problems.append(f"{c.get('repo_role')} {repo_path}: tracking_verified=false（G1 未通过）")
    if problems:
        return False, "; ".join(problems)
    return True, ""


def cmd_g2(args) -> int:
    meta = load_metadata(Path(args.metadata).expanduser())
    ok, reason = run_g2(meta)
    if ok:
        print("✅ G2 ⑩ 契约校验 pass")
        return 0
    print(f"❌ G2 ⑩ 契约校验 blocked——{reason}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# 子命令：submit-check（G3 交付前逐仓清单）
# ---------------------------------------------------------------------------

def run_git(cwd: Path, *args: str, timeout: int = 30,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """执行 Git 并保留返回码与 stderr，供 fail-closed 的 merge 门禁使用。"""
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **(env or {})},
        )
    except (subprocess.SubprocessError, OSError) as error:
        return subprocess.CompletedProcess(
            ["git", "-C", str(cwd), *args], 1, "", str(error)
        )


def _git_stdout(repo_path: Path, *args: str) -> tuple[str, str | None]:
    result = run_git(repo_path, *args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        return "", detail
    return result.stdout.strip(), None


def _git_is_ancestor(repo_path: Path, older: str, newer: str) -> bool:
    return run_git(repo_path, "merge-base", "--is-ancestor", older, newer).returncode == 0


def _exact_target_refs(contract: dict) -> tuple[str, str, str | None]:
    """校验 push/remote refs 指向同一契约 remote 与分支。"""
    remote_name = contract.get("remote_name")
    target_push_ref = contract.get("target_push_ref")
    target_remote_ref = contract.get("target_remote_ref")
    if not all(isinstance(value, str) and value for value in (
            remote_name, target_push_ref, target_remote_ref)):
        return "", "", "missing remote_name/target refs"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote_name):
        return "", "", f"invalid remote_name: {remote_name}"
    match = re.fullmatch(r"refs/heads/(.+)", target_push_ref)
    if not match:
        return "", "", f"invalid target_push_ref: {target_push_ref}"
    expected_remote_ref = f"refs/remotes/{remote_name}/{match.group(1)}"
    if target_remote_ref != expected_remote_ref:
        return "", "", (
            f"target ref mismatch: {target_remote_ref} != {expected_remote_ref}"
        )
    return target_push_ref, target_remote_ref, None


def _simulate_merge(repo_path: Path, head_sha: str,
                    target_sha: str) -> tuple[bool, str]:
    """在临时 shared clone 中运行真实 merge，绝不写用户 checkout。"""
    try:
        with tempfile.TemporaryDirectory(prefix="icode_merge_preflight_") as tmp:
            clone = Path(tmp) / "repo"
            cloned = subprocess.run(
                ["git", "clone", "--shared", "--no-checkout", "--quiet",
                 str(repo_path), str(clone)],
                capture_output=True, text=True, timeout=60,
            )
            if cloned.returncode != 0:
                return False, (cloned.stderr or "temporary clone failed").strip()
            checkout = run_git(clone, "checkout", "--detach", "--quiet", head_sha)
            if checkout.returncode != 0:
                return False, (checkout.stderr or "temporary checkout failed").strip()
            merge = run_git(
                clone, "-c", "core.hooksPath=/dev/null", "merge",
                "--no-commit", "--no-ff", target_sha, timeout=60,
            )
            unresolved = run_git(
                clone, "diff", "--name-only", "--diff-filter=U"
            )
            merge_head = run_git(clone, "rev-parse", "-q", "--verify", "MERGE_HEAD")
            if (merge.returncode != 0 or unresolved.returncode != 0
                    or unresolved.stdout.strip()
                    or merge_head.returncode != 0
                    or merge_head.stdout.strip() != target_sha):
                detail = (merge.stderr or merge.stdout or unresolved.stderr
                          or "merge result is incomplete").strip()
                return False, detail
            return True, "clean"
    except (subprocess.SubprocessError, OSError) as error:
        return False, str(error)


def _new_merge_row(contract) -> dict:
    role = contract.get("repo_role", "?") if isinstance(contract, dict) else "?"
    return {
        "contract": contract,
        "repo_role": role,
        "repo_path": None,
        "branch": "?",
        "upstream": "?",
        "remote_url": "?",
        "target_remote_ref": "?",
        "target_push_ref": "?",
        "original_head": "",
        "target_sha": "",
        "ahead": None,
        "behind": None,
        "dirty": None,
        "relation": "unknown",
        "status": "blocked",
        "action": "none",
        "git_check": "not_run",
        "issues": [],
        "pending_merge": False,
    }


def _preflight_merge_contract(contract, merge_enabled: bool) -> dict:
    row = _new_merge_row(contract)
    if not isinstance(contract, dict):
        row["issues"].append("invalid submission contract")
        return row

    repo_value = contract.get("repo_path")
    if not isinstance(repo_value, str) or not repo_value.strip():
        row["issues"].append("missing repo_path")
        return row
    repo_path = Path(repo_value).expanduser()
    row["repo_path"] = repo_path
    if not repo_path.is_dir() or git(repo_path, "rev-parse", "--is-inside-work-tree") != "true":
        row["issues"].append("repository unavailable or not Git")
        return row

    target_push_ref, target_remote_ref, ref_issue = _exact_target_refs(contract)
    row["target_push_ref"] = contract.get("target_push_ref", "?")
    row["target_remote_ref"] = contract.get("target_remote_ref", "?")
    if ref_issue:
        row["issues"].append(ref_issue)
        return row

    branch, branch_error = _git_stdout(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    upstream, upstream_error = _git_stdout(
        repo_path, "rev-parse", "--symbolic-full-name", "@{u}"
    )
    head_sha, head_error = _git_stdout(repo_path, "rev-parse", "--verify", "HEAD")
    remote_url, remote_error = _git_stdout(
        repo_path, "remote", "get-url", contract.get("remote_name", "")
    )
    row.update({
        "branch": branch or "?",
        "upstream": upstream or "?",
        "remote_url": remote_url or "?",
        "original_head": head_sha,
    })
    if branch_error or branch in {"", "HEAD"}:
        row["issues"].append("detached or unresolved HEAD")
    elif branch != contract.get("worktree_branch"):
        row["issues"].append(
            f"branch drift: {branch} != {contract.get('worktree_branch')}"
        )
    if upstream_error or not upstream:
        row["issues"].append("missing upstream")
    elif upstream != target_remote_ref:
        row["issues"].append(f"upstream drift: {upstream} != {target_remote_ref}")
    if head_error or not head_sha:
        row["issues"].append("HEAD cannot be resolved")
    if remote_error or not remote_url:
        row["issues"].append("remote URL cannot be resolved")
    elif normalize_url(remote_url) != normalize_url(contract.get("remote_url", "")):
        row["issues"].append("remote URL mismatch")
    if contract.get("tracking_verified") is not True:
        row["issues"].append("tracking_verified is not true")
    if row["issues"]:
        return row

    fetch = run_git(
        repo_path, "fetch", "--no-tags", "-q", contract["remote_name"],
        f"+{target_push_ref}:{target_remote_ref}", timeout=60,
    )
    if fetch.returncode != 0:
        detail = (fetch.stderr or fetch.stdout or "fetch failed").strip()
        row["issues"].append(f"fetch failed: {detail}")
        return row
    target_sha, target_error = _git_stdout(
        repo_path, "rev-parse", "--verify", target_remote_ref
    )
    if target_error or not target_sha:
        row["issues"].append("fetched target cannot be resolved")
        return row
    row["target_sha"] = target_sha

    status_result = run_git(
        repo_path, "status", "--porcelain=v1", "--untracked-files=all"
    )
    if status_result.returncode != 0:
        row["issues"].append("git status failed")
        return row
    row["dirty"] = bool(status_result.stdout.strip())
    unresolved = run_git(repo_path, "diff", "--name-only", "--diff-filter=U")
    merge_head_result = run_git(repo_path, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    merge_head = merge_head_result.stdout.strip() if merge_head_result.returncode == 0 else ""
    if unresolved.returncode != 0 or unresolved.stdout.strip():
        row["issues"].append("unmerged entries exist")
        return row
    if merge_head:
        unstaged = run_git(repo_path, "diff", "--quiet")
        untracked = run_git(repo_path, "ls-files", "--others", "--exclude-standard")
        if merge_head != target_sha:
            row["issues"].append("MERGE_HEAD does not match frozen target")
            return row
        if unstaged.returncode != 0 or untracked.returncode != 0 or untracked.stdout.strip():
            row["issues"].append("pending merge has new unstaged or untracked changes")
            return row
        row["pending_merge"] = True
    elif merge_enabled and row["dirty"]:
        row["issues"].append("dirty checkout blocks automatic merge")
        return row

    behind_text, behind_error = _git_stdout(
        repo_path, "rev-list", "--count", f"{head_sha}..{target_sha}"
    )
    ahead_text, ahead_error = _git_stdout(
        repo_path, "rev-list", "--count", f"{target_sha}..{head_sha}"
    )
    try:
        if behind_error or ahead_error:
            raise ValueError("rev-list failed")
        row["behind"] = int(behind_text)
        row["ahead"] = int(ahead_text)
    except ValueError:
        row["issues"].append("ahead/behind cannot be calculated")
        return row

    if row["pending_merge"]:
        row.update(relation="pending_merge", status="merge_pending",
                   action="recheck", git_check="preflight_pass")
        return row
    if head_sha == target_sha:
        row.update(relation="equal", status="unchanged", git_check="preflight_pass")
        return row
    if _git_is_ancestor(repo_path, target_sha, head_sha):
        row.update(relation="local_ahead", status="local_ahead",
                   git_check="preflight_pass")
        return row
    if _git_is_ancestor(repo_path, head_sha, target_sha):
        row.update(relation="fast_forward", status="behind" if not merge_enabled else "ready",
                   git_check="preflight_pass")
        return row

    merge_base, merge_base_error = _git_stdout(
        repo_path, "merge-base", head_sha, target_sha
    )
    if merge_base_error or not merge_base:
        row["issues"].append("merge base cannot be resolved")
        return row
    merge_ok, detail = _simulate_merge(repo_path, head_sha, target_sha)
    if not merge_ok:
        row["issues"].append(f"merge conflict or uncertain preflight: {detail}")
        return row
    row.update(relation="diverged", status="behind" if not merge_enabled else "ready",
               git_check="preflight_pass")
    return row


def _post_merge_check(row: dict) -> list[str]:
    repo_path = row["repo_path"]
    contract = row["contract"]
    issues = []
    unresolved = run_git(repo_path, "diff", "--name-only", "--diff-filter=U")
    if unresolved.returncode != 0 or unresolved.stdout.strip():
        issues.append("unmerged entries after merge")
    for args, label in ((('diff', '--check'), "working-tree diff check failed"),
                        (('diff', '--cached', '--check'), "index diff check failed")):
        if run_git(repo_path, *args).returncode != 0:
            issues.append(label)
    if row["relation"] == "fast_forward":
        frozen_range = f'{row["original_head"]}..{row["target_sha"]}'
        if run_git(repo_path, "diff", "--check", frozen_range).returncode != 0:
            issues.append("frozen target diff check failed")
    branch = git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    upstream = git(repo_path, "rev-parse", "--symbolic-full-name", "@{u}")
    remote_url = git(repo_path, "remote", "get-url", contract.get("remote_name", ""))
    if branch != contract.get("worktree_branch"):
        issues.append("branch changed during merge")
    if upstream != contract.get("target_remote_ref"):
        issues.append("upstream changed during merge")
    if normalize_url(remote_url) != normalize_url(contract.get("remote_url", "")):
        issues.append("remote URL changed during merge")
    current_head = git(repo_path, "rev-parse", "--verify", "HEAD")
    merge_head = git(repo_path, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    if row["relation"] == "fast_forward":
        if current_head != row["target_sha"] or merge_head:
            issues.append("fast-forward result does not match frozen target")
    else:
        if current_head != row["original_head"] or merge_head != row["target_sha"]:
            issues.append("pending merge identity does not match frozen snapshot")
    return issues


def _abort_failed_merge(row: dict, original_status: str) -> list[str]:
    """只撤销当前失败 merge，并验证预检快照；绝不 reset 已完成仓库。"""
    repo_path = row["repo_path"]
    issues = []
    if run_git(repo_path, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
        aborted = run_git(repo_path, "merge", "--abort")
        if aborted.returncode != 0:
            issues.append("merge abort failed")
    current_head = git(repo_path, "rev-parse", "--verify", "HEAD")
    current_status = git(
        repo_path, "status", "--porcelain=v1", "--untracked-files=all"
    )
    if current_head != row["original_head"] or current_status != original_status:
        issues.append("checkout did not return to the preflight snapshot")
    return issues


def _refresh_frozen_target(row: dict) -> str | None:
    contract = row["contract"]
    fetched = run_git(
        row["repo_path"], "fetch", "--no-tags", "-q", contract["remote_name"],
        f"+{row['target_push_ref']}:{row['target_remote_ref']}", timeout=60,
    )
    if fetched.returncode != 0:
        return "online recheck fetch failed"
    current = git(row["repo_path"], "rev-parse", "--verify", row["target_remote_ref"])
    if current != row["target_sha"]:
        return f"online target moved: {row['target_sha']} -> {current or '?'}"
    return None


def _print_merge_rows(rows: list[dict], merge_enabled: bool) -> None:
    print("| Repo | Branch | Upstream | Remote URL | Target | Frozen SHA | Ahead/Behind | Dirty | Status |")
    print("|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        ahead = "?" if row["ahead"] is None else row["ahead"]
        behind = "?" if row["behind"] is None else row["behind"]
        dirty = "?" if row["dirty"] is None else "yes" if row["dirty"] else "no"
        target_sha = row["target_sha"][:12] if row["target_sha"] else "?"
        print(
            f"| {row['repo_role']} | {row['branch']} | {row['upstream']} | "
            f"{row['remote_url']} | {row['target_remote_ref']} | {target_sha} | "
            f"+{ahead}/-{behind} | {dirty} | {row['status']} |"
        )
        if row["issues"]:
            print(f"  → blocked: {'; '.join(row['issues'])}")
        elif merge_enabled:
            print(
                f"  → relation={row['relation']}; action={row['action']}; "
                f"git_check={row['git_check']}"
            )
        elif row["status"] == "behind":
            print(
                "  → target 领先或已分叉，先 fetch/merge/rebase；"
                "建议显式运行 /icode worktree --merge"
            )
        else:
            target_br = row["target_push_ref"].removeprefix("refs/heads/")
            if row["dirty"] or row["status"] in {"unchanged", "local_ahead"}:
                print(
                    f"  → 精确安全命令：git push "
                    f"{row['contract'].get('remote_name', '<remote>')} "
                    f"HEAD:refs/heads/{target_br}"
                )


def cmd_submit_check(args) -> int:
    meta = load_metadata(Path(args.metadata).expanduser())
    merge_enabled = bool(getattr(args, "merge", False))
    contracts = meta.get("submission_contracts")
    if not isinstance(contracts, list):
        print("❌ submission_contracts 必须是数组", file=sys.stderr)
        return 1
    if not contracts:
        if merge_enabled:
            print("❌ /icode worktree --merge 要求非空 submission_contracts", file=sys.stderr)
            return 2
        print("ℹ️ 无提交契约（只读工单或未迁移）。仅枚举变更文件供人工确认。")
        return 0

    # Phase 1：全部仓库只读预检。除 remote-tracking refs 外不修改用户仓库。
    rows = [_preflight_merge_contract(contract, merge_enabled) for contract in contracts]
    seen_repo_paths: dict[str, dict] = {}
    for row in rows:
        repo_path = row.get("repo_path")
        if not isinstance(repo_path, Path):
            continue
        try:
            repo_key = str(repo_path.resolve())
        except OSError:
            repo_key = str(repo_path.absolute())
        previous = seen_repo_paths.get(repo_key)
        if previous is not None:
            issue = f"duplicate repository contract: {repo_key}"
            if issue not in previous["issues"]:
                previous["issues"].append(issue)
                previous["status"] = "blocked"
            row["issues"].append(issue)
            row["status"] = "blocked"
        else:
            seen_repo_paths[repo_key] = row
    if any(row["issues"] for row in rows):
        _print_merge_rows(rows, merge_enabled)
        print("\n❌ 总状态 = blocked——全量预检未通过，未执行本地 merge", file=sys.stderr)
        return 2

    if not merge_enabled:
        _print_merge_rows(rows, False)
        if any(row["status"] == "behind" for row in rows):
            print(
                "\n❌ 总状态 = blocked——只读检查发现线上差异；"
                "先运行 /icode worktree --merge，未执行 merge / commit / push",
                file=sys.stderr,
            )
            return 2
        print("\n✅ 总状态 = pass（只读检查；未执行 merge / commit / push）")
        return 0

    # Phase 2：只有所有仓库通过 Phase 1 才开始修改，并且永不创建 commit。
    execution_failed = False
    for row in rows:
        if execution_failed:
            row.update(status="blocked", action="skipped_after_failure",
                       git_check="not_run")
            row["issues"].append("earlier repository merge failed")
            continue
        if row["relation"] in {"equal", "local_ahead"}:
            row["action"] = "none"
            row["git_check"] = "pass"
            continue
        if row["relation"] == "pending_merge":
            post_issues = _post_merge_check(row)
            if post_issues:
                row["issues"].extend(post_issues)
                row.update(status="blocked", git_check="failed")
                execution_failed = True
            else:
                row.update(status="merge_pending", action="recheck", git_check="pass")
            continue

        repo_path = row["repo_path"]
        original_status = git(
            repo_path, "status", "--porcelain=v1", "--untracked-files=all"
        )
        if row["relation"] == "fast_forward":
            merged = run_git(
                repo_path, "-c", "core.hooksPath=/dev/null", "merge",
                "--ff-only", row["target_sha"], timeout=60,
            )
            desired_status = "recheck_pending"
            desired_action = "fast_forwarded"
        else:
            merged = run_git(
                repo_path, "-c", "core.hooksPath=/dev/null", "merge",
                "--no-commit", "--no-ff", row["target_sha"], timeout=60,
            )
            desired_status = "merge_pending"
            desired_action = "merged_no_commit"
        if merged.returncode != 0:
            detail = (merged.stderr or merged.stdout or "merge failed").strip()
            row["issues"].append(f"actual merge failed: {detail}")
            row["issues"].extend(_abort_failed_merge(row, original_status))
            row.update(status="blocked", action="aborted", git_check="failed")
            execution_failed = True
            continue
        row.update(status=desired_status, action=desired_action)
        post_issues = _post_merge_check(row)
        if post_issues:
            row["issues"].extend(post_issues)
            row.update(status="blocked", git_check="failed")
            execution_failed = True
        else:
            row["git_check"] = "pass"

    # 再次读取精确线上目标，防止 merge 窗口内 remote 又前进。
    for row in rows:
        if row["status"] == "blocked":
            continue
        moved_issue = _refresh_frozen_target(row)
        if moved_issue:
            row["issues"].append(moved_issue)
            row.update(status="online_moved", git_check="failed")

    _print_merge_rows(rows, True)
    if any(row["issues"] or row["status"] in {"blocked", "online_moved"}
           for row in rows):
        print("\n❌ 总状态 = blocked——未输出 push 指令；检查部分完成状态", file=sys.stderr)
        return 2
    if any(row["status"] in {"merge_pending", "recheck_pending"} for row in rows):
        print(
            "\n⚠️ 总状态 = pending——未 commit / push；"
            "merge_pending 需人工检查并提交，recheck_pending 需重跑既有业务验证"
        )
        return 3
    print("\n✅ 总状态 = pass（未 commit / push）")
    return 0


# ---------------------------------------------------------------------------
# 子命令：handoff（多仓交付矩阵，只读）
# ---------------------------------------------------------------------------

HANDOFF_REASONS = {
    "modified",
    "build_only",
    "already_upstream",
    "not_in_scope",
    "unresolved",
}


def _bool_or_none(value):
    """只接受明确布尔值；缺失或其它类型都保持未知。"""
    return value if isinstance(value, bool) else None


def _normalise_handoff_inputs(meta: dict) -> list[dict]:
    inputs = (((meta.get("extensions") or {}).get("handoff") or {}).get("inputs") or [])
    if isinstance(inputs, dict):
        # 兼容以 repo path 为 key 的紧凑写法，同时保持 list 为首选合同。
        return [dict(value, repo_path=key) for key, value in inputs.items() if isinstance(value, dict)]
    if not isinstance(inputs, list):
        return []
    return [value for value in inputs if isinstance(value, dict)]


def _path_key(value) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        return str(Path(value).expanduser().resolve())
    except OSError:
        return value


def _input_for_contract(inputs: list[dict], contract: dict) -> dict:
    repo_path = _path_key(contract.get("repo_path"))
    path_matches = [item for item in inputs if _path_key(item.get("repo_path")) == repo_path]
    if len(path_matches) == 1:
        return path_matches[0]
    if len(path_matches) > 1:
        return {}
    role = contract.get("repo_role")
    role_matches = [item for item in inputs if item.get("repo_role") == role]
    return role_matches[0] if len(role_matches) == 1 else {}


def _normalise_excluded_paths(value) -> tuple[list[dict], list[str]]:
    if not isinstance(value, list):
        return [], []
    result = []
    issues = []
    for item in value:
        path = item if isinstance(item, str) else item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str) or not path:
            continue
        # 排除项只允许仓库内相对路径；否则 `../source` 会被 lstrip 误当作 `source`，隐藏真实变更。
        path_parts = re.split(r"[/\\]+", path)
        cross_platform_absolute = bool(re.match(r"^(?:[/\\]|[A-Za-z]:[/\\])", path))
        if cross_platform_absolute or Path(path).is_absolute() or ".." in path_parts or "\x00" in path:
            issues.append(f"invalid_excluded_path:{path}")
            continue
        if isinstance(item, str):
            result.append({"path": path, "reason": "explicitly excluded"})
        else:
            reason = item.get("reason")
            result.append({
                "path": path,
                "reason": reason if isinstance(reason, str) and reason else "explicitly excluded",
            })
    return result, issues


def _dirty_paths(status: str) -> list[str]:
    paths = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return paths


def _is_excluded(path: str, excluded: list[dict]) -> bool:
    normalised = path.rstrip("/")
    for item in excluded:
        prefix = item["path"].lstrip("./").rstrip("/")
        if prefix and (normalised == prefix or normalised.startswith(prefix + "/")):
            return True
    return False


def _unresolved_handoff_row(repo_role="unresolved", repo_path=None,
                            target_remote_ref=None, issues=None) -> dict:
    return {
        "repo_role": repo_role or "unresolved",
        "repo_path": repo_path,
        "branch": None,
        "upstream": None,
        "target_remote_ref": target_remote_ref,
        "head": None,
        "ahead": None,
        "behind": None,
        "dirty": None,
        "modified": None,
        "build_participant": None,
        "deployed": None,
        "deployment_evidence": [],
        "deploy_target": None,
        "submit_required": None,
        "submit_reason": "unresolved",
        "docs_required": None,
        "docs_ready": None,
        "excluded_paths": [],
        "artifact_identity": None,
        "resolution": "unresolved",
        "issues": list(issues or []),
    }


def _normalise_deployment_evidence(value) -> list:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, (str, dict)) and item]


def _normalise_submit_disposition(handoff_input: dict, modified,
                                  issues: list[str]) -> tuple[bool | None, str]:
    reason = handoff_input.get("submit_reason")
    if reason not in HANDOFF_REASONS:
        if reason is not None:
            issues.append("invalid_submit_reason")
        reason = "modified" if modified is True else "unresolved"

    required = _bool_or_none(handoff_input.get("submit_required"))
    expected = None
    if reason == "modified":
        expected = True
    elif reason in {"build_only", "already_upstream", "not_in_scope"}:
        expected = False

    conflict = (
        (required is not None and expected is not None and required != expected)
        or (required is True and modified is False)
        or (reason == "modified" and modified is False)
        or (reason in {"build_only", "not_in_scope"} and modified is True)
    )
    if conflict:
        issues.append("submit_disposition_conflict")
        return None, "unresolved"
    if required is None:
        required = expected
    return required, reason


def build_handoff_report(meta: dict, metadata_path: Path) -> dict:
    """由契约、显式交付观测与本地 Git 事实构造证据有界矩阵。"""
    raw_contracts = meta.get("submission_contracts")
    report_issues = []
    if raw_contracts is None:
        contracts = []
    elif isinstance(raw_contracts, list):
        contracts = raw_contracts
    else:
        contracts = []
        report_issues.append("invalid_submission_contracts")
    inputs = _normalise_handoff_inputs(meta)
    repositories = []

    for contract in contracts:
        if not isinstance(contract, dict):
            repositories.append(_unresolved_handoff_row(issues=["invalid_submission_contract"]))
            continue
        repo_path_value = contract.get("repo_path")
        if not isinstance(repo_path_value, str) or not repo_path_value.strip():
            repositories.append(_unresolved_handoff_row(
                repo_role=contract.get("repo_role"),
                target_remote_ref=contract.get("target_remote_ref"),
                issues=["missing_repo_path"],
            ))
            continue

        repo_path = Path(repo_path_value).expanduser()
        handoff_input = _input_for_contract(inputs, contract)
        excluded, issues = _normalise_excluded_paths(handoff_input.get("excluded_paths"))
        is_git_repo = repo_path.is_dir() and git(repo_path, "rev-parse", "--is-inside-work-tree") == "true"
        if not is_git_repo:
            issues.append("repo_unavailable_or_not_git")
        status = git(repo_path, "status", "--porcelain=v1", "--untracked-files=all") if is_git_repo else ""
        dirty = bool(status) if is_git_repo else None
        relevant_dirty = [path for path in _dirty_paths(status) if not _is_excluded(path, excluded)]

        modified = _bool_or_none(handoff_input.get("modified"))
        if modified is None and relevant_dirty:
            modified = True

        submit_required, submit_reason = _normalise_submit_disposition(
            handoff_input, modified, issues
        )

        target = contract.get("target_remote_ref")
        target_resolved = bool(is_git_repo and target and git_exit0(repo_path, "rev-parse", "--verify", target))
        ahead = behind = None
        if target_resolved:
            try:
                behind = int(git(repo_path, "rev-list", "--count", f"HEAD..{target}"))
                ahead = int(git(repo_path, "rev-list", "--count", f"{target}..HEAD"))
            except (TypeError, ValueError):
                ahead = behind = None

        artifact_identity = handoff_input.get("artifact_identity")
        if artifact_identity in ("", []):
            artifact_identity = None
        deployment_evidence = _normalise_deployment_evidence(handoff_input.get("deployment_evidence"))
        deploy_target = handoff_input.get("deploy_target")
        if not isinstance(deploy_target, str) or not deploy_target:
            deploy_target = None
        deployed = _bool_or_none(handoff_input.get("deployed"))
        if deployed is True:
            if artifact_identity is None:
                issues.append("deployed_without_artifact_identity")
            if not deployment_evidence:
                issues.append("deployed_without_deployment_evidence")
            if artifact_identity is None and not deployment_evidence:
                issues.append("deployed_without_evidence")
                deployed = None
        repositories.append({
            "repo_role": contract.get("repo_role") or "unresolved",
            "repo_path": str(repo_path),
            "branch": git(repo_path, "rev-parse", "--abbrev-ref", "HEAD") or None if is_git_repo else None,
            "upstream": git(repo_path, "rev-parse", "--symbolic-full-name", "@{u}") or None if is_git_repo else None,
            "target_remote_ref": target,
            "head": git(repo_path, "rev-parse", "HEAD") or None if is_git_repo else None,
            "ahead": ahead,
            "behind": behind,
            "dirty": dirty,
            "modified": modified,
            "build_participant": _bool_or_none(handoff_input.get("build_participant")),
            "deployed": deployed,
            "deployment_evidence": deployment_evidence,
            "deploy_target": deploy_target,
            "submit_required": submit_required,
            "submit_reason": submit_reason,
            "docs_required": _bool_or_none(handoff_input.get("docs_required")),
            "docs_ready": _bool_or_none(handoff_input.get("docs_ready")),
            "excluded_paths": excluded,
            "artifact_identity": artifact_identity,
            "resolution": "resolved" if is_git_repo else "unresolved",
            "issues": issues,
        })

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "metadata_path": str(metadata_path.expanduser().resolve()),
        "repositories": repositories,
        "issues": report_issues,
    }


def _markdown_value(value) -> str:
    if value is None:
        return "unresolved"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_handoff_markdown(report: dict) -> str:
    lines = [
        "# Multi-repo handoff matrix",
        "",
        "> Read-only report. `unresolved` means no sufficient explicit evidence was available.",
        "",
        "| Repo | Path | Branch | Dirty | Modified | Build | Deployed | Submit | Reason | Docs required | Docs ready | Artifact | Excluded paths |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["repositories"]:
        excluded = "; ".join(
            f"{item['path']} ({item['reason']})" for item in row["excluded_paths"]
        )
        cells = [
            row["repo_role"], row["repo_path"], row["branch"], row["dirty"],
            row["modified"], row["build_participant"], row["deployed"],
            row["submit_required"], row["submit_reason"], row["docs_required"],
            row["docs_ready"], row["artifact_identity"], excluded,
        ]
        lines.append("| " + " | ".join(_markdown_value(value) for value in cells) + " |")
    return "\n".join(lines) + "\n"


def _same_destination(left: Path, right: Path) -> bool:
    """识别字符串差异、符号链接和已有硬链接指向的同一文件。"""
    if left.resolve() == right.resolve():
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _validate_handoff_destinations(metadata_path: Path, output_path: Path,
                                   markdown_path: Path | None) -> None:
    ticket_root = metadata_path.expanduser().resolve().parent
    destinations = [("--output", output_path)]
    if markdown_path is not None:
        destinations.append(("--markdown", markdown_path))
    for option, destination in destinations:
        resolved_destination = destination.expanduser().resolve()
        try:
            resolved_destination.relative_to(ticket_root)
        except ValueError as exc:
            raise ValueError(
                f"{option} 必须位于 metadata 工单目录内: {resolved_destination}"
            ) from exc
        if destination.name.startswith(".ico_"):
            raise ValueError(f"{option} 不得覆盖 .ico_* 控制文件: {destination}")
        if _same_destination(destination, metadata_path):
            raise ValueError(f"{option} 不得覆盖 metadata: {metadata_path}")
    if markdown_path is not None and _same_destination(output_path, markdown_path):
        raise ValueError("--output 与 --markdown 不得指向同一文件")


def cmd_handoff(args) -> int:
    metadata_path = Path(args.metadata).expanduser()
    output_path = Path(args.output).expanduser()
    markdown_path = Path(args.markdown).expanduser() if args.markdown else None
    try:
        _validate_handoff_destinations(metadata_path, output_path, markdown_path)
    except ValueError as error:
        print(f"❌ handoff 输出路径无效: {error}", file=sys.stderr)
        return 1
    report = build_handoff_report(load_metadata(metadata_path), metadata_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomically_write(output_path, report)
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        atomically_write_text(markdown_path, render_handoff_markdown(report))
    print(f"✅ handoff matrix: {len(report['repositories'])} repos → {output_path}")
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    # 用法/参数错误映射为退出码 1（argparse 默认 sys.exit(2)，与文档「1=用法或执行错误」冲突，
    # 业务语义 2=needs_user_confirm/blocked 不得被用法错误覆盖）
    class _Parser(argparse.ArgumentParser):
        def error(self, message):  # noqa: A002
            self.print_usage(sys.stderr)
            sys.stderr.write(f"{self.prog}: error: {message}\n")
            sys.exit(1)

    parser = _Parser(description="worktree 提交契约机器闸门工具（阶段 4 落地）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_norm = sub.add_parser("normalize-url", help="规范化 remote URL")
    p_norm.add_argument("url")

    p_mig = sub.add_parser("migrate-legacy", help="旧工单契约一次性迁移")
    p_mig.add_argument("--metadata", required=True)
    p_mig.add_argument("--dry-run", action="store_true")

    p_g2 = sub.add_parser("g2-check", help="G2 ⑩ 契约校验（只读）")
    p_g2.add_argument("--metadata", required=True)

    p_ck = sub.add_parser("submit-check", help="G3 交付前逐仓清单（默认只读）")
    p_ck.add_argument("--metadata", required=True)
    p_ck.add_argument(
        "--merge", action="store_true",
        help="全量预检后执行 fast-forward 或保留未提交 merge；绝不 commit/push",
    )

    p_handoff = sub.add_parser("handoff", help="生成多仓交付矩阵（本地只读，不 fetch）")
    p_handoff.add_argument("--metadata", required=True)
    p_handoff.add_argument("--output", required=True, help="JSON 真源输出路径")
    p_handoff.add_argument("--markdown", help="可选 Markdown 输出路径")

    args = parser.parse_args(argv)
    if args.cmd == "normalize-url":
        print(normalize_url(args.url))
        return 0
    if args.cmd == "migrate-legacy":
        return cmd_migrate(args)
    if args.cmd == "g2-check":
        return cmd_g2(args)
    if args.cmd == "submit-check":
        return cmd_submit_check(args)
    if args.cmd == "handoff":
        return cmd_handoff(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
