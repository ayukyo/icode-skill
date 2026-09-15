#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 verify 主动作，保留自然语言供主代理结合 LIMIT/工程上下文理解。

只解析参数，不选择或执行构建命令，不连接设备，不写工单。
"""
import argparse
import json
import shlex


def parse_request(request):
    tokens = shlex.split(request)
    if tokens[:2] == ["/icode", "verify"]:
        tokens = tokens[2:]
    elif tokens and tokens[0] == "/icode":
        raise ValueError("请求必须是 /icode verify")
    action = None
    values = {}
    intent = []
    index = 0
    actions = {"--build": "build", "--plan": "plan", "--deploy": "deploy",
               "--listen": "listen", "--test": "device_test"}
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            intent = tokens[index + 1:]
            break
        if token in actions:
            if action is not None:
                raise ValueError("--build/--plan/--deploy/--listen/--test 主动作互斥")
            action = actions[token]
            if token != "--test":
                index += 1
                continue
            name = "target"
        elif token in {"--ticket", "--reuse"}:
            name = token[2:]
            if name in values:
                raise ValueError(f"{token} 不可重复")
        else:
            if token.startswith("--"):
                raise ValueError(f"未知 verify 选项: {token}；脚本参数请放在自然语言之后或 -- 后")
            # 从首个意图词开始，后续 -j6、--module 等均属于构建意图，
            # 而非 verify 选项；raw_request 同时保留原始引号和换行。
            intent = tokens[index:]
            break
        index += 1
        if index >= len(tokens) or not tokens[index].strip() or tokens[index].startswith("--"):
            raise ValueError(f"{token} 缺少参数")
        values[name] = tokens[index]
        index += 1
    action = action or "default"
    if action in {"build", "plan"} and "reuse" in values:
        raise ValueError(f"--{action} 不可与 --reuse 组合")
    return {"action": action, "ticket": values.get("ticket"),
            "reuse": values.get("reuse"), "target": values.get("target"),
            "natural_language": " ".join(intent), "raw_request": request}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="verify 原始参数或完整 /icode verify 请求")
    args = parser.parse_args()
    try:
        result = parse_request(args.request)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
