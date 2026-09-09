#!/usr/bin/env python3
"""Validate an embedded baseline and derive a read-only verification plan.

The input is data only: this tool never executes values found in the baseline.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path


LAYERS = {
    "static", "unit", "build", "host", "deploy", "delivery", "consumption",
    "settings_preview", "operation_view", "closed_client", "physical",
}
OPERATORS = {"lt", "lte", "gt", "gte", "eq"}
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
PROTECTED_NAMES = {
    ".ico_metadata.json", ".ico_events.jsonl", "index.json",
    "archive_manifest.json", "ticket_snapshot.json",
}


class BaselineError(Exception):
    pass


def fail(message):
    raise BaselineError(message)


def exact_object(value, path, required):
    if not isinstance(value, dict):
        fail(f"{path} 必须是对象")
    missing = set(required) - set(value)
    unknown = set(value) - set(required)
    if missing:
        fail(f"{path} 缺少字段: {sorted(missing)}")
    if unknown:
        fail(f"{path} 含未知字段: {sorted(unknown)}")


def text(value, path):
    if not isinstance(value, str) or not value.strip():
        fail(f"{path} 必须是非空字符串")


def named(value, path):
    text(value, path)
    if not NAME_RE.fullmatch(value):
        fail(f"{path} 不符合 {NAME_RE.pattern}")


def load_baseline(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        fail(f"baseline 必须是非符号链接普通文件: {path}")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or resolved.parent == Path(resolved.anchor):
        fail("baseline 不得位于文件系统根目录")
    try:
        raw = resolved.read_bytes()
        data = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"非有限数 {value}")),
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        fail(f"baseline 读取失败: {exc}")
    validate(data)
    return resolved, data, "sha256:" + hashlib.sha256(raw).hexdigest()


def validate(data):
    top = ["schema_version", "project_profile", "identity", "software",
           "components", "cameras", "verification"]
    exact_object(data, "$", top)
    if data["schema_version"] != 1:
        fail("$.schema_version 必须为 1")
    if data["project_profile"] not in {"embedded", "camera"}:
        fail("$.project_profile 必须为 embedded 或 camera")

    identity_keys = ["device_id", "board_model", "board_revision", "soc"]
    exact_object(data["identity"], "$.identity", identity_keys)
    for key in identity_keys:
        text(data["identity"][key], f"$.identity.{key}")

    software_keys = ["bootloader", "kernel", "dtb", "rootfs", "bsp", "toolchain"]
    exact_object(data["software"], "$.software", software_keys)
    for key in software_keys:
        text(data["software"][key], f"$.software.{key}")

    components = data["components"]
    if not isinstance(components, list) or not components:
        fail("$.components 必须是非空数组")
    component_keys = ["name", "kind", "identity", "artifact_path", "hash",
                      "build_id", "loaded_evidence", "required"]
    component_names = set()
    for index, component in enumerate(components):
        path = f"$.components[{index}]"
        exact_object(component, path, component_keys)
        named(component["name"], f"{path}.name")
        if component["name"] in component_names:
            fail(f"{path}.name 重复: {component['name']}")
        component_names.add(component["name"])
        for key in component_keys[1:-1]:
            text(component[key], f"{path}.{key}")
        if not isinstance(component["required"], bool):
            fail(f"{path}.required 必须是布尔值")

    cameras = data["cameras"]
    if not isinstance(cameras, list):
        fail("$.cameras 必须是数组")
    if data["project_profile"] == "camera" and not cameras:
        fail("camera profile 的 $.cameras 至少需要一个摄像头身份")
    camera_keys = ["name", "sensor", "module", "lens", "eeprom_sn",
                   "calibration_hash", "tuning_hash", "media_nodes"]
    camera_names = set()
    for index, camera in enumerate(cameras):
        path = f"$.cameras[{index}]"
        exact_object(camera, path, camera_keys)
        named(camera["name"], f"{path}.name")
        if camera["name"] in camera_names:
            fail(f"{path}.name 重复: {camera['name']}")
        camera_names.add(camera["name"])
        for key in camera_keys[1:-1]:
            text(camera[key], f"{path}.{key}")
        nodes = camera["media_nodes"]
        if not isinstance(nodes, list) or not nodes:
            fail(f"{path}.media_nodes 必须是非空数组")
        for node_index, node in enumerate(nodes):
            text(node, f"{path}.media_nodes[{node_index}]")

    verification = data["verification"]
    exact_object(verification, "$.verification", ["scenarios", "safety"])
    safety = verification["safety"]
    exact_object(safety, "$.verification.safety",
                 ["read_only_probe_default", "mutations_require_explicit_authorization"])
    if safety["read_only_probe_default"] is not True \
            or safety["mutations_require_explicit_authorization"] is not True:
        fail("$.verification.safety 两项都必须显式为 true")

    scenarios = verification["scenarios"]
    if not isinstance(scenarios, list) or not scenarios:
        fail("$.verification.scenarios 必须是非空数组")
    scenario_keys = ["id", "layer", "consumer", "required_evidence", "metrics"]
    scenario_ids = set()
    metric_cells = set()
    for index, scenario in enumerate(scenarios):
        path = f"$.verification.scenarios[{index}]"
        exact_object(scenario, path, scenario_keys)
        named(scenario["id"], f"{path}.id")
        if scenario["id"] in scenario_ids:
            fail(f"{path}.id 重复: {scenario['id']}")
        scenario_ids.add(scenario["id"])
        if scenario["layer"] not in LAYERS:
            fail(f"{path}.layer 非法: {scenario['layer']!r}")
        text(scenario["consumer"], f"{path}.consumer")
        evidence = scenario["required_evidence"]
        if not isinstance(evidence, list) or not evidence:
            fail(f"{path}.required_evidence 必须是非空数组")
        for evidence_index, item in enumerate(evidence):
            text(item, f"{path}.required_evidence[{evidence_index}]")
        metrics = scenario["metrics"]
        if not isinstance(metrics, list):
            fail(f"{path}.metrics 必须是数组")
        for metric_index, metric in enumerate(metrics):
            metric_path = f"{path}.metrics[{metric_index}]"
            exact_object(metric, metric_path, ["name", "unit", "operator", "value"])
            named(metric["name"], f"{metric_path}.name")
            text(metric["unit"], f"{metric_path}.unit")
            if metric["operator"] not in OPERATORS:
                fail(f"{metric_path}.operator 非法")
            value = metric["value"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                fail(f"{metric_path}.value 必须是有限数值")
            metric_cell = (scenario["layer"], scenario["consumer"],
                           scenario["id"], metric["name"])
            if metric_cell in metric_cells:
                fail(f"{metric_path}.name 在同一验证单元重复: {metric['name']}")
            metric_cells.add(metric_cell)


def ordered_unique(values):
    return list(dict.fromkeys(values))


def make_plan(source, data, baseline_digest):
    scenarios = data["verification"]["scenarios"]
    metrics = []
    for scenario in scenarios:
        for metric in scenario["metrics"]:
            metrics.append({
                **metric,
                "layer": scenario["layer"],
                "consumer": scenario["consumer"],
                "scenario": scenario["id"],
            })
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_baseline": source.name,
        "source_baseline_sha256": baseline_digest,
        "project_profile": data["project_profile"],
        "read_only": True,
        "verification_contract": {
            "required": True,
            "profile": data["project_profile"],
            "baseline_ref": baseline_digest,
            "required_layers": ordered_unique(item["layer"] for item in scenarios),
            "required_consumers": ordered_unique(item["consumer"] for item in scenarios),
            "required_scenarios": ordered_unique(item["id"] for item in scenarios),
            "required_cells": [
                {"layer": item["layer"], "consumer": item["consumer"], "scenario": item["id"]}
                for item in scenarios
            ],
            "required_metrics": metrics,
        },
        "scenarios": scenarios,
        "safety": data["verification"]["safety"],
    }


def markdown_plan(plan):
    lines = [
        "# Embedded verification plan", "",
        f"- Profile: `{plan['project_profile']}`",
        f"- Source baseline: `{plan['source_baseline']}`",
        "- Execution: read-only plan; no command from the baseline is executed.", "",
        "## Scenarios", "",
        "| Scenario | Layer | Consumer | Required evidence | Metrics |",
        "|---|---|---|---|---|",
    ]
    for scenario in plan["scenarios"]:
        metric_text = ", ".join(
            f"{m['name']} {m['operator']} {m['value']} {m['unit']}"
            for m in scenario["metrics"]) or "none"
        evidence_text = "; ".join(scenario["required_evidence"])
        lines.append(
            f"| {scenario['id']} | {scenario['layer']} | {scenario['consumer']} | "
            f"{evidence_text} | {metric_text} |")
    lines.extend([
        "", "## Safety", "",
        "Read-only probing is the default. Any mutation or fault injection requires explicit authorization.",
        "",
    ])
    return "\n".join(lines)


def safe_output(path, root, baseline):
    raw = Path(path)
    if raw.is_symlink():
        fail(f"输出不得是符号链接: {raw}")
    resolved = raw.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        fail(f"输出必须位于 baseline 目录内: {resolved}")
    if resolved.parent != root:
        fail(f"输出必须直接位于 baseline 目录内: {resolved}")
    if resolved == baseline:
        fail("输出不得覆盖 baseline")
    lowered = resolved.name.lower()
    if resolved.name in PROTECTED_NAMES or lowered.startswith(".ico_") \
            or "manifest" in lowered:
        fail(f"输出名受保护: {resolved.name}")
    if resolved.exists() and not resolved.is_file():
        fail(f"输出存在但不是普通文件: {resolved}")
    if resolved.exists() and os.path.samefile(resolved, baseline):
        fail("输出不得与 baseline 指向同一文件")
    return resolved


def stage_bytes(path, content):
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return Path(temporary)


def stage(path, content):
    return stage_bytes(path, content.encode("utf-8"))


def restore(path, previous):
    if previous is None:
        if path.exists():
            path.unlink()
        return
    temporary = stage_bytes(path, previous)
    os.replace(temporary, path)


def write_pair(json_path, json_content, markdown_path, markdown_content):
    previous_json = json_path.read_bytes() if json_path.exists() else None
    previous_markdown = markdown_path.read_bytes() if markdown_path.exists() else None
    staged_json = stage(json_path, json_content)
    staged_markdown = stage(markdown_path, markdown_content)
    try:
        os.replace(staged_json, json_path)
        os.replace(staged_markdown, markdown_path)
    except BaseException:
        for temporary in (staged_json, staged_markdown):
            if temporary.exists():
                temporary.unlink()
        restore(json_path, previous_json)
        restore(markdown_path, previous_markdown)
        raise


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate_parser = sub.add_parser("validate", help="validate a baseline without writing")
    validate_parser.add_argument("--baseline", required=True)
    plan_parser = sub.add_parser("plan", help="derive JSON and Markdown verification plans")
    plan_parser.add_argument("--baseline", required=True)
    plan_parser.add_argument("--output", required=True)
    plan_parser.add_argument("--markdown", required=True)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        source, data, baseline_digest = load_baseline(args.baseline)
        if args.command == "validate":
            result = {"ok": True, "baseline": str(source), "profile": data["project_profile"]}
        else:
            root = source.parent
            json_path = safe_output(args.output, root, source)
            markdown_path = safe_output(args.markdown, root, source)
            if json_path == markdown_path:
                fail("JSON 与 Markdown 输出不得是同一路径")
            plan = make_plan(source, data, baseline_digest)
            json_content = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
            write_pair(json_path, json_content, markdown_path, markdown_plan(plan))
            result = {"ok": True, "baseline": str(source), "profile": data["project_profile"],
                      "output": str(json_path), "markdown": str(markdown_path)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BaselineError, OSError, UnicodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
