#!/usr/bin/env python3
"""
lint_mcp_coverage.py —— 检查 icode 工单产物文件是否含「MCP 调用记录」段

按 anti_laziness.md 第 21 条 + SKILL.md v2.2 强证据二元化要求，每个步骤产物文件必须含：
- 「MCP 调用记录」段（标题级别 ## 或更小）
- 至少包含本步骤无条件 🟢 MCP（sequential-thinking）的调用结果记录

注：v2.2 二元化后，非 sequential-thinking MCP 的 🟢\* 取决于强证据场景（有可索引源码/涉及
第三方库/用户给图/前端工程/有历史工单），lint 无法静态判定，其强制由 A 层（执行步骤内嵌）
+ B 层（thinking_core gate）承载，不在 lint。lint 只做最低形式兜底：段存在 + sequential-thinking 出现。

用法:
    python3 tools/lint_mcp_coverage.py <out_dir>
    python3 tools/lint_mcp_coverage.py <out_dir> --strict  # 严格模式：缺段直接报错退出
    python3 tools/lint_mcp_coverage.py <out_dir> --json    # 输出 JSON 报告

退出码:
    0 = 所有检查通过
    1 = 有违规（缺段或 MCP 调用记录不全）
    2 = 参数错误

实现:
- 解析 step 文件名 → 推荐 MCP 矩阵
- 解析产物文件 → 检查「MCP 调用记录」段存在 + MCP 名称出现
- 输出 Markdown 报告 / JSON
"""
import sys
import re
import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional


# 矩阵映射（v2.2 二元化：只静态强制无条件 🟢 的 sequential-thinking）
# 其他 MCP（serena/context7/vision-bridge/playwright/memory）的 🟢* 取决于强证据场景，
# lint 无法静态判定 -> 强制由 A 层（执行步骤内嵌）+ B 层（thinking_core gate）承载。
# lint 只保证「段存在 + sequential-thinking 出现」，真实调用覆盖由 A+B 层 + audit 终审保障。
MATRIX = {
    "00_init.md": ["sequential-thinking"],
    "log.md": ["sequential-thinking"],
    "doc.md": ["sequential-thinking"],
    "01_plan.md": ["sequential-thinking"],
    "02_review.md": ["sequential-thinking"],
    "03_merge.md": ["sequential-thinking"],
    "04_code.md": ["sequential-thinking"],
    "05_deepcheck.md": ["sequential-thinking"],
    "06_audit.md": ["sequential-thinking"],
    "07_readme.md": ["sequential-thinking"],
    "fast.md": ["sequential-thinking"],
    "install.md": ["sequential-thinking"],
    "status.md": ["sequential-thinking"],
    "list.md": ["sequential-thinking"],
}

# 反向映射：产物文件名 -> 步骤名（用于定位推荐 MCP）
OUTPUT_TO_STEP = {
    "00_init.md": "00_init.md",
    "log_analysis.md": "log.md",
    "01_plan.md": "01_plan.md",
    "02_review.md": "02_review.md",
    "03_plan_final.md": "03_merge.md",
    "04_code_review_fix.md": "04_code.md",
    "05_deepcheck.md": "05_deepcheck.md",
    "06_audit.md": "06_audit.md",
    "README.md": "07_readme.md",
}

# 可选产物文件：缺这些文件不报违规（步骤 7 readme 是可选后置步骤）
OPTIONAL_OUTPUTS = {"README.md"}


def find_section(content: str, section_name: str) -> Optional[Tuple[int, int]]:
    """找 ## 标题，返回 (start_line, end_line)"""
    pattern = re.compile(rf"^##\s+{re.escape(section_name)}", re.MULTILINE)
    m = pattern.search(content)
    if not m:
        return None
    start = m.start()
    # 下一个 ## 标题
    next_section = re.compile(r"^##\s+", re.MULTILINE)
    next_m = next_section.search(content, m.end())
    end = next_m.start() if next_m else len(content)
    return (start, end)


def check_file(filepath: Path, required_mcps: List[str]) -> Dict:
    """检查单个产物文件，返回报告"""
    report = {
        "file": str(filepath.name),
        "has_section": False,
        "section_content": "",
        "missing_mcps": [],
        "present_mcps": [],
        "issues": [],
    }
    if not filepath.exists():
        # 可选产物文件（如 README.md）缺失不报违规
        if filepath.name in OPTIONAL_OUTPUTS:
            report["issues"].append("可选文件未生成（步骤 7 readme 是可选后置步骤）")
        else:
            report["issues"].append("文件不存在")
        return report

    content = filepath.read_text(encoding="utf-8")
    section = find_section(content, "MCP 调用记录")
    if not section:
        report["issues"].append("缺「MCP 调用记录」段（反偷懒第 21 条违规）")
        return report

    report["has_section"] = True
    report["section_content"] = content[section[0]:section[1]].strip()

    for mcp in required_mcps:
        if mcp.lower() in report["section_content"].lower():
            report["present_mcps"].append(mcp)
        else:
            report["missing_mcps"].append(mcp)

    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="lint icode 工单产物 MCP 调用覆盖率")
    ap.add_argument("out_dir", help="工单目录路径（如 demo/.icode_output/.icode_output_10）")
    ap.add_argument("--strict", action="store_true", help="严格模式：缺段或 MCP 缺失直接报错退出")
    ap.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_dir():
        print(f"❌ 目录不存在: {out_dir}", file=sys.stderr)
        return 2

    reports = []
    for out_name, step_name in OUTPUT_TO_STEP.items():
        filepath = out_dir / out_name
        required = MATRIX.get(step_name, [])
        if not required:
            continue
        rep = check_file(filepath, required)
        rep["required_mcps"] = required
        # 标记可选文件缺失（不计入违规）
        rep["optional_missing"] = (
            not filepath.exists() and out_name in OPTIONAL_OUTPUTS
        )
        reports.append(rep)

    # 统计：可选文件缺失不计入 failed
    passed = sum(
        1 for r in reports
        if r["has_section"] and not r["missing_mcps"]
    )
    failed = sum(
        1 for r in reports
        if not (r["has_section"] and not r["missing_mcps"]) and not r["optional_missing"]
    )
    skipped_optional = sum(1 for r in reports if r["optional_missing"])

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 1 if (failed > 0 and args.strict) else (1 if failed > 0 else 0)

    # Markdown 报告
    print(f"\n# MCP 调用覆盖率检查报告\n")
    print(f"工单目录: `{out_dir}`\n")
    skip_note = f" / {skipped_optional} 可选缺失" if skipped_optional else ""
    print(f"结果: **{passed} 通过 / {failed} 违规{skip_note}**\n")
    print(f"| 文件 | 段存在 | 推荐 MCP | 已记录 | 缺失 | 问题 |")
    print(f"|------|--------|----------|--------|------|------|")
    for r in reports:
        miss = ",".join(r["missing_mcps"]) if r["missing_mcps"] else "-"
        issues = "; ".join(r["issues"]) if r["issues"] else "-"
        if r["optional_missing"]:
            issues = "可选文件未生成（不计违规）"
        print(f"| {r['file']} | {'✅' if r['has_section'] else '➖' if r['optional_missing'] else '❌'} | {len(r['required_mcps'])} | {len(r['present_mcps'])} | {miss} | {issues} |")

    if failed > 0:
        print(f"\n⚠️ {failed} 个文件违规")
        if args.strict:
            return 1
        return 1
    print(f"\n✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())