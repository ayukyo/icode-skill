#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/icode_payload_profile.py — 指令载荷画像（只读）

职责：从 Claude Code 会话转录里统计**实际进入上下文的指令字节**，回答
"每个步骤到底读了多少规则、占多大比例"，为文档瘦身提供数据而不是猜测。

测量口径：
  - 只统计 Read 工具的 tool_result 文本长度（含 offset/limit 分段读，即真实进上下文的量）；
  - 路径落在 skill root 内的 `steps/`、`references/`、`SKILL.md` 记为指令，其余记为工作；
  - 同一文件重复读会重复计数——这正是要暴露的浪费之一。

用法：
  python3 scripts/icode_payload_profile.py                       # 默认扫描 ~/.claude/projects
  python3 scripts/icode_payload_profile.py --min-instruction-kb 50
  python3 scripts/icode_payload_profile.py --skill-root /path/to/skill --projects-root /path/to/projects

只读：不写任何文件，不改任何工单。
"""
import argparse
import collections
import functools
import json
import os
import sys
from pathlib import Path

DEFAULT_PROJECTS = Path.home() / ".claude" / "projects"
DEFAULT_SKILL_ROOTS = [Path(__file__).resolve().parents[1],
                       Path.home() / ".claude" / "skills" / "icode"]

# 步骤路由里标注为"按需加载"的引用；读得多说明条件门没有被遵守。
CONDITIONAL = {
    "media_routing.md", "serial_evidence_stages.md", "first_activation_path.md",
    "worktree_isolation.md", "debug_mode.md", "crosscheck_mode.md", "adversarial.md",
    "inspection_worklist.md", "inspection_evidence.md", "doc_template.md",
    "docx_authoring.md", "outsource_contract.md", "host_adapters.md",
    "wsl_build.md", "project_docs_retrieval.md",
}

SKILL_ROOTS = []


@functools.lru_cache(maxsize=65536)
def classify(raw):
    path = Path(raw)
    for root in SKILL_ROOTS:
        try:
            rel = path.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        parts = rel.parts
        if parts[0] == "references":
            return "reference", rel.name
        if parts[0] == "steps":
            return "step", rel.name
        if rel.name == "SKILL.md":
            return "router", "SKILL.md"
        return "skill", "/".join(parts[-2:])
    return "work", path.name


def text_length(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(item.get("text") or "") for item in content
                   if isinstance(item, dict) and item.get("type") == "text")
    return 0


def analyse(path):
    pending = {}
    reads = collections.defaultdict(int)
    read_counts = collections.Counter()
    edits = collections.Counter()
    writes = collections.Counter()
    out_tokens = ctx_tokens = turns = 0
    touched_skill = False

    with open(path, encoding="utf-8", errors="replace") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            message = event.get("message") or {}
            role, content = message.get("role"), message.get("content")
            if role == "assistant":
                turns += 1
                usage = message.get("usage") or {}
                out_tokens += usage.get("output_tokens", 0) or 0
                ctx_tokens += (usage.get("cache_read_input_tokens", 0) or 0) + (usage.get("input_tokens", 0) or 0)
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use":
                    name = item.get("name")
                    data = item.get("input") or {}
                    target = data.get("file_path")
                    if not target:
                        continue
                    if name == "Read":
                        pending[item.get("id")] = Path(str(target))
                    elif name == "Edit":
                        edits[classify(str(target))] += 1
                    elif name == "Write":
                        writes[classify(str(target))] += 1
                elif item.get("type") == "tool_result":
                    target = pending.pop(item.get("tool_use_id"), None)
                    if target is None:
                        continue
                    size = text_length(item.get("content"))
                    kind, name = classify(str(target))
                    if kind != "work":
                        touched_skill = True
                    reads[(kind, name)] += size
                    read_counts[(kind, name)] += 1
    if not touched_skill:
        return None
    return {
        "path": str(path), "turns": turns, "out_tokens": out_tokens,
        "avg_ctx": int(ctx_tokens / turns) if turns else 0,
        "instruction_bytes": sum(v for (k, _), v in reads.items() if k != "work"),
        "work_bytes": sum(v for (k, _), v in reads.items() if k == "work"),
        "reads": dict(reads), "read_counts": dict(read_counts),
        "edits": dict(edits), "writes": dict(writes),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only ICODE instruction payload profile.")
    parser.add_argument("--projects-root", default=str(DEFAULT_PROJECTS))
    parser.add_argument("--skill-root", action="append", default=None,
                        help="可重复；缺省为仓库根与 ~/.claude/skills/icode")
    parser.add_argument("--min-instruction-kb", type=int, default=20)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args(argv)

    for root in (args.skill_root or [str(p) for p in DEFAULT_SKILL_ROOTS]):
        if Path(root).is_dir():
            SKILL_ROOTS.append(Path(root))
    if not SKILL_ROOTS:
        parser.error("no usable skill root")
    projects = Path(args.projects_root)
    if not projects.is_dir():
        parser.error(f"projects root not found: {projects}")

    sessions = []
    for project in sorted(projects.iterdir()):
        if not project.is_dir():
            continue
        for file in project.glob("*.jsonl"):
            try:
                row = analyse(file)
            except OSError:
                continue
            if row and row["instruction_bytes"] >= args.min_instruction_kb * 1024:
                row["project"] = project.name
                sessions.append(row)
    sessions.sort(key=lambda r: -r["instruction_bytes"])

    print(f"sessions touching a skill root: {len(sessions)}\n")
    print(f'{"project":50s} {"instr_KB":>9s} {"work_KB":>8s} {"share":>6s} {"turns":>6s} {"out_tok":>9s} {"avg_ctx":>8s}')
    for row in sessions[:args.top]:
        total = row["instruction_bytes"] + row["work_bytes"]
        share = row["instruction_bytes"] / total if total else 0
        print(f'{row["project"][:50]:50s} {row["instruction_bytes"]/1024:9.0f} {row["work_bytes"]/1024:8.0f} '
              f'{share*100:5.0f}% {row["turns"]:6d} {row["out_tokens"]:9d} {row["avg_ctx"]:8d}')

    totals, counts = collections.Counter(), collections.Counter()
    for row in sessions:
        for key, value in row["reads"].items():
            totals[key] += value
            counts[key] += row["read_counts"][key]
    print("\n== instruction files actually read ==")
    print(f'{"kind":10s} {"file":34s} {"total_KB":>9s} {"reads":>6s} {"sessions":>8s}  conditional?')
    for (kind, name), value in sorted(totals.items(), key=lambda kv: -kv[1]):
        if kind == "work":
            continue
        hits = sum(1 for row in sessions if (kind, name) in row["reads"])
        print(f"{kind:10s} {name[:34]:34s} {value/1024:9.0f} {counts[(kind, name)]:6d} {hits:8d}  "
              f"{'yes' if name in CONDITIONAL else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())