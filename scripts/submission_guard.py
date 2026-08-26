#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/submission_guard.py — worktree 提交契约机器闸门（提案 worktree-upstream-push-guard 阶段 4 落地）

职责（只读检查 + 一次性迁移；ICode 红线不变——本脚本不 commit / 不 push / 不改 git 分支目标）：

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

  submit-check --metadata <path>
      G3 交付前逐仓提交清单（只读）：枚举 super + 全部契约子仓（不只看 code_files），输出逐仓表格，
      对有变更/含 ticket commit 的仓库给出精确安全 push 命令
      `git push <remote_name> HEAD:refs/heads/<target-branch>`；target 前进 → 标 behind 不给出可直接 push。

退出码：0 = pass / 2 = needs_user_confirm 或 blocked / 1 = 用法或执行错误。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
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

def cmd_submit_check(args) -> int:
    meta = load_metadata(Path(args.metadata).expanduser())
    contracts = meta.get("submission_contracts") or []
    if not contracts:
        print("ℹ️ 无提交契约（只读工单或未迁移）。仅枚举变更文件供人工确认。")
        return 0
    print("| Repo | Branch | Upstream | Remote URL | Target(remote branch) | Ahead/Behind | Dirty | Verdict |")
    print("|---|---|---|---|---|---|---|---|")
    overall_blocked = False
    overall_behind = False
    for c in contracts:
        repo_path = Path(c.get("repo_path", ""))
        row = c.get("repo_role", "?")
        # G3 规则 4：behind 判定前先 fetch 目标分支取在线状态（防本地 fetch 过时误报 +0/-0，
        # 与 G4 规则 1「不用本地缓存 ref」一致）；fetch 失败（远程不可达）→ 降级本地 ref 并标注
        m = re.match(r"^refs/heads/(.+)$", c.get("target_push_ref", ""))
        fb = m.group(1) if m else None
        fetch_ok = True
        if fb:
            fetch_ok = git_exit0(repo_path, "fetch", "-q", c.get("remote_name", ""), f"refs/heads/{fb}")
        branch = git(repo_path, "rev-parse", "--abbrev-ref", "HEAD") or "?"
        upstream = git(repo_path, "rev-parse", "--symbolic-full-name", "@{u}") or "?"
        remote_url = git(repo_path, "remote", "get-url", c.get("remote_name", "")) or "?"
        target = c.get("target_remote_ref", "?")
        if not fetch_ok:
            target += " (本地缓存)"
        dirty = bool(git(repo_path, "status", "--porcelain"))
        # ahead/behind：target 领先本地 → behind；本地领先 target → ahead（仅展示，不影响 verdict）
        # 注意 `git rev-list --count A..B`（B 领先 A 数）；漏掉 `..` 会变成"共同可达数"，误报（真源 §3.10 G3 规则 4）
        behind = 0
        ahead = 0
        try:
            behind = int(git(repo_path, "rev-list", "--count", f"HEAD..{target}") or 0)
            ahead = int(git(repo_path, "rev-list", "--count", f"{target}..HEAD") or 0)
        except ValueError:
            behind = ahead = 0
        verdict = "pass"
        if branch == "HEAD" or branch != c.get("worktree_branch"):
            verdict = "blocked"
        elif not upstream or upstream != c.get("target_remote_ref"):
            verdict = "blocked"
        elif remote_url and normalize_url(remote_url) != normalize_url(c.get("remote_url", "")):
            verdict = "blocked"
        elif not c.get("tracking_verified"):
            verdict = "blocked"
        elif behind > 0:
            verdict = "behind"
        if verdict == "blocked":
            overall_blocked = True
        elif verdict == "behind":
            overall_behind = True
        ab = f"+{ahead}/-{behind}"
        print(f"| {row} | {branch} | {upstream} | {remote_url} | {target} | {ab} | {'yes' if dirty else 'no'} | {verdict} |")
        # 目标分支来自契约 target_push_ref 的远端分支部分
        m = re.match(r"^refs/heads/(.+)$", c.get("target_push_ref", ""))
        target_br = m.group(1) if m else "?"
        if verdict == "blocked":
            print(f"  → 未给出 push 指令（契约违约，先跑 g2-check 或由用户显式确认目标）")
        elif verdict == "behind":
            print(f"  → target 领先本地 {behind} commit，先 fetch/merge/rebase，由用户决定，ICode 不自动改历史")
        elif dirty or verdict == "pass":
            print(f"  → 精确安全命令：git push {c.get('remote_name','<remote>')} HEAD:refs/heads/{target_br}")
    if overall_blocked:
        print("\n❌ 总 verdict = blocked——存在契约违约仓库，不宣称“可以提交”", file=sys.stderr)
        return 2
    if overall_behind:
        print("\n⚠️ 总 verdict = behind——存在落后仓库，先 fetch/merge/rebase 后再提交（未执行任何 push；红线不变：ICode 不 commit / 不 push）")
        return 0
    print("\n✅ 总 verdict = pass（未执行任何 push；红线不变：ICode 不 commit / 不 push）")
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

    p_ck = sub.add_parser("submit-check", help="G3 交付前逐仓清单（只读）")
    p_ck.add_argument("--metadata", required=True)

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
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
