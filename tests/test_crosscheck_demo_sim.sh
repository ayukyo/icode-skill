#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
cp -a "$ROOT/demo" "$TMP_ROOT/demo"

HOME="$TMP_ROOT/home" PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT" "$TMP_ROOT/demo" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
demo = pathlib.Path(sys.argv[2])
tool = root / "tools/icode_crosscheck.py"
ticket = demo / ".icode_output/.icode_output_4"
index = pathlib.Path(os.environ["HOME"]) / ".claude/icode_data/index.json"
index.parent.mkdir(parents=True)
index.write_text('{"schema_version":3,"tickets":[]}\n', encoding="utf-8")
subprocess.run(["git", "init", "-q", str(demo)], check=True)
(demo / ".gitignore").write_text(".icode_output/\n*.o\ncalc_demo\n.all_tests_final/\n.ui_runtime_sim*/\n*.tmp\n", encoding="utf-8")
subprocess.run(["git", "-C", str(demo), "add", "calc.c", "calc.h", "main.c", "Makefile"], check=True)
subprocess.run(["git", "-C", str(demo), "add", ".gitignore"], check=True)
subprocess.run(["git", "-C", str(demo), "add", "log"], check=True)
subprocess.run(["git", "-C", str(demo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "commit", "-qm", "isolated demo source baseline"], check=True)

def digest_tree(path):
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file() and not item.is_symlink():
            digest.update(str(item.relative_to(path)).encode())
            digest.update(item.read_bytes())
    return digest.hexdigest()

def run(*args, expected=0):
    proc = subprocess.run(
        [sys.executable, str(tool), *map(str, args)], cwd=root,
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == expected, proc.stdout + proc.stderr
    return json.loads(proc.stdout)

def payload(number, status="new"):
    return {
        "schema_version": 1,
        "target_ticket_id": "demo-4",
        "round": number,
        "reviewed_at": f"2026-09-14T09:{number:02d}:00+08:00",
        "verdict": "pass_with_suggestions",
        "evidence_boundary": "demo legacy 工单静态模拟；未执行实机验证",
        "summary": "设计与代码链路可读，建议补充一个边界说明",
        "findings": [{
            "finding_id": "DEMO-CC-1",
            "title": "补充边界说明",
            "severity": "suggestion",
            "status": status,
            "category": "documentation",
            "evidence": ["03_plan_final.md"],
            "analysis": "边界可进一步显式化",
            "recommendation": "在后续 patch 中按需补充，不自动修改",
            "requires_change": False,
            "locations": [],
            "evidence_boundary": "仅设计文档建议，未声称源码存在确认缺陷",
        }],
    }

target_before = digest_tree(ticket)
index_before = index.read_bytes()

def read_worklist(directory, number):
    report = json.loads((directory / f"crosscheck_round_{number}.worklist.json").read_text())
    files = {}
    for unit in report["units"]:
        for entry in unit["files"]:
            data = (demo / entry["path"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            files[entry["path"]] = (entry["sha256"], data.decode("utf-8").splitlines(keepends=True))
            read = run("inspection", "--dir", directory, "--round", number, "--phase", "read", "--path", entry["path"])
            assert read["source_sha256"] == entry["sha256"]

    def source_ref(path, marker, count):
        digest, lines = files[path]
        matches = [index for index, line in enumerate(lines) if marker in line]
        assert len(matches) == 1, (path, marker)
        start = matches[0]
        assert start + count <= len(lines)
        return {"kind": "source", "path": path, "start_line": start + 1, "end_line": start + count,
                "source_sha256": digest, "excerpt": "".join(lines[start:start + count])}

    # The lexical product prompt comes from an arithmetic comment, not a
    # device variant. Inspect the complete current C/header scope before NA;
    # adding hardware operations must invalidate these demo-only conclusions.
    sources = "".join("".join(lines) for path, (_, lines) in files.items() if pathlib.Path(path).suffix in (".c", ".h"))
    assert not re.search(r"\b(?:ioctl|mmap|dma|probe|backend|device|reconnect|timestamp)\b", sources, re.I)
    product = source_ref("calc.c", "否则 product = quotient * b", 9)
    power = source_ref("calc.c", "int calc_power(", 27)
    parser = source_ref("calc.c", "ParserState st;", 4)
    interface = source_ref("calc.h", "int calc_power(", 1)
    caller = source_ref("main.c", "rc = calc_power(2, 10, &result);", 2)
    judgments = {
        "target": ("not_applicable", "product 指 calc_lcm 中 quotient*b 的乘积注释，源码没有设备型号目标选择。", [product]),
        "sibling": ("not_applicable", "calc_power 是同一组整数参数的公开接口，没有姊妹产品或型号分派。", [interface, power]),
        "unknown": ("not_applicable", "calc_power 输入是 base/exp/result，没有未知设备身份或能力状态。", [power]),
        "supported": ("not_applicable", "幂运算只检查指数与整数溢出，不探测硬件支持能力。", [power]),
        "unsupported": ("not_applicable", "负指数返回算术 INVALID；该错误不是不支持某设备的能力分类。", [power]),
        "probe_failure": ("not_applicable", "would_overflow_power 是数值溢出预判调用，没有设备探测失败路径。", [power]),
        "read_failure": ("not_applicable", "calc_eval 从调用方字符串创建局部 ParserState，不读取驱动或设备节点。", [parser]),
        "stale": ("not_applicable", "每次 calc_eval 都令局部 st.p=expr，没有跨调用帧缓存、旧序号或旧时间。", [parser]),
        "consumers": ("handled", "静态确认 main.c 调用 calc_power 并打印 rc/result，与 calc.h 声明一致；未运行该程序。", [caller, interface]),
        "memory:mmap": ("not_applicable", "解析状态是局部结构与调用方字符串指针，没有映射内存获取或释放。", [parser]),
        "memory:dma": ("not_applicable", "calc_power 计算普通整数并写 result 指针，没有 DMA 缓冲区或所有权交接。", [power]),
        "first_frame": ("not_applicable", "calc_power 初始化整数结果再累乘，没有首帧采集或流启动。", [power]),
        "steady": ("not_applicable", "幂运算循环由 exp 限定，一次函数调用结束返回，没有持续设备流。", [power]),
        "stop_start": ("not_applicable", "ParserState 每次调用重新初始化，没有 stop/start 生命周期或缓存复位接口。", [parser]),
        "reconnect": ("not_applicable", "公开 calc_power 接口只有数值参数，没有连接句柄、断连或重连分支。", [interface]),
        "build_config:enabled": ("not_applicable", "当前 calc_power 定义无硬件功能宏条件，未声明共享驱动启用变体。", [power]),
        "build_config:disabled": ("not_applicable", "calc.h 始终声明同一算术接口，没有驱动禁用时的备用接口。", [interface]),
        "fallback:old_semantics": ("not_applicable", "calc_power 的入口检查与累乘属于单一算术路径，没有旧驱动语义回退。", [power]),
        "fallback:no_extra_probe": ("not_applicable", "数值溢出预判调用不做硬件 I/O；没有新旧后端或额外设备探测。", [power]),
        "fallback:error_isolation": ("not_applicable", "CALC_ERR_INVALID/OVERFLOW 是本函数返回码，没有共享设备 fallback 错误隔离链。", [power]),
    }
    assert report["required_phases"] == ["fresh"]
    assert report["checks"] and all(check["kind"] == "shared_variant_consumers" for check in report["checks"])
    for check in report["checks"]:
        assert set(check["required_items"]) == set(judgments)
        for phase in report["required_phases"]:
            assessment = {"cells": {item: {"status": status, "reason": reason + " 仅无硬件算术 demo 的当前源码静态模拟，不证明实机行为。",
                                          "evidence": evidence} for item, (status, reason, evidence) in judgments.items()}}
            result = run("inspection", "--dir", directory, "--round", number, "--phase", "assess",
                         "--check-id", check["check_id"], "--assessment-json", json.dumps(assessment, ensure_ascii=False))
            assert result["phase"] == phase
            saved = json.loads((directory / f"crosscheck_round_{number}.worklist.json").read_text())
            recorded = next(item for item in saved["checks"] if item["check_id"] == check["check_id"])
            assert recorded["results"][phase] == assessment

# Round 1: legacy completed ticket, explicit artifact path, full success.
started = run("start", "--workspace", demo, ticket / "03_plan_final.md")
directory = pathlib.Path(started["crosscheck_dir"])
assert directory.parent == demo / ".icode_output/.crosscheck"
(directory / "crosscheck_round_1.fresh.json").write_text(
    json.dumps(payload(1), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
read_worklist(directory, 1)
run("freeze", "--dir", directory, "--round", 1)
(directory / "crosscheck_round_1.json").write_text(
    json.dumps(payload(1), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
run("finish", "--dir", directory, "--round", 1)
assert digest_tree(ticket) == target_before
assert index.read_bytes() == index_before

# Round 2: modify the copied ticket during review; finish must preserve stale_input.
run("start", "--workspace", demo, "--ticket", "demo-4")
(directory / "crosscheck_round_2.fresh.json").write_text(
    json.dumps(payload(2), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
read_worklist(directory, 2)
run("freeze", "--dir", directory, "--round", 2)
(directory / "crosscheck_round_2.json").write_text(
    json.dumps(payload(2, "still_present"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
(ticket / "03_plan_final.md").write_text(
    (ticket / "03_plan_final.md").read_text(encoding="utf-8") + "\nDemo drift.\n",
    encoding="utf-8",
)
stale_baseline = digest_tree(ticket)
stale = run("finish", "--dir", directory, "--round", 2, expected=1)
assert stale["state"] == "stale_input"
assert digest_tree(ticket) == stale_baseline

# Round 3: new stable review compares against the most recent completed round (Round 1).
third = run("start", "--workspace", demo, "--ticket", "demo-4")
assert third["round"] == 3
(directory / "crosscheck_round_3.fresh.json").write_text(
    json.dumps(payload(3), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
read_worklist(directory, 3)
frozen = run("freeze", "--dir", directory, "--round", 3)
assert frozen["previous_round"].endswith("crosscheck_round_1.json")
(directory / "crosscheck_round_3.json").write_text(
    json.dumps(payload(3, "still_present"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
run("finish", "--dir", directory, "--round", 3)
validated = run("validate", "--dir", directory)
assert validated["rounds"] == 3 and validated["completed_rounds"] == 2
assert not list(directory.rglob(".ico_metadata.json"))
assert index.read_bytes() == index_before
print("PASS demo legacy multi-round/stale/resume/zero-write simulation")
PY
