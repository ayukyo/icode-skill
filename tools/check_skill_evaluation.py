#!/usr/bin/env python3
"""Validate ICODE evaluation structure/evidence offline, never model semantics.

Exit 0: requested structural checks valid (evidence may be pending).
Exit 1: invalid data or evidence. Exit 2: --require-results but no results.
No model invocation, output generation, grading, or workflow state mutations.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MAX_JSON_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MIN_TRIGGER_CASES = 24
MIN_TRIGGERS_PER_LABEL = 12
# IDs are a coverage registry, not a keyword classifier or semantic evaluator.
# Update this registry, fixtures and the documented rubric together for new cases.
SCENARIOS = {
    1: "explicit_zh_plan", 2: "explicit_en_log", 3: "ordinary_request",
    4: "consultation_only", 5: "listen_no_build", 6: "test_implicit_build",
    7: "crosscheck_no_writeback", 8: "explicit_path_priority",
    9: "bound_ticket_resume", 10: "debug_isolation", 11: "opt_out_zh",
    12: "quoted_instruction", 13: "unknown_command", 14: "conflicting_flags",
    15: "completed_stop_point", 16: "zero_write", 17: "build_only",
    18: "opt_out_en",
}
CONFIGURATIONS = {"with_skill", "without_skill"}


class Invalid(ValueError):
    """Input violates the offline evaluation contract."""


def require(condition, message):
    if not condition:
        raise Invalid(message)


def fields(value, expected, label):
    require(type(value) is dict, f"{label}: expected object")
    require(set(value) == set(expected), f"{label}: missing or unknown fields")


def nonempty(value, label):
    require(type(value) is str and bool(value.strip()), f"{label}: expected nonempty string")


def relative_parts(value):
    nonempty(value, "path")
    parts = value.split("/")
    require(not any(p in ("", ".", "..") for p in parts)
            and "\\" not in value and ":" not in value and "\x00" not in value,
            "path must be a relative local path without traversal or URL")
    return parts


def read_bounded(root, relative, limit, evidence=False):
    """Open each component relative to a held fd, rejecting links and special files.

    O_NONBLOCK prevents a malicious FIFO from hanging before fstat. Holding directory
    descriptors and using O_NOFOLLOW avoids resolve-then-open symlink races on Linux.
    """
    parts = relative_parts(relative)
    root = Path(root).absolute()
    directory_fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (*root.parts[1:], *parts[:-1]):
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                          dir_fd=directory_fd)
        with os.fdopen(file_fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode), "expected regular file")
            require(not evidence or before.st_nlink == 1, "hard-linked evidence is forbidden")
            require(before.st_size <= limit, f"file exceeds {limit} byte limit")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            require(len(data) <= limit, f"file exceeds {limit} byte limit")
            require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                    "file changed during evidence read")
            return data
    finally:
        os.close(directory_fd)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON object key")
        result[key] = value
    return result


def invalid_constant(_value):
    raise Invalid("non-finite JSON number")


def load_json(path):
    path = Path(path).absolute()
    data = read_bounded(path.parent, path.name, MAX_JSON_BYTES)
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant), hashlib.sha256(data).hexdigest()


def validate_cases(cases):
    fields(cases, {"skill_name", "evals"}, "evals")
    require(cases["skill_name"] == "icode", "skill_name must be icode")
    require(type(cases["evals"]) is list, "evals must be an array")
    ids = set()
    prompts = set()
    for case in cases["evals"]:
        fields(case, {"id", "prompt", "expected_output", "files"}, "case")
        case_id = case["id"]
        require(type(case_id) is int, "case id must be integer, not boolean or float")
        require(case_id in SCENARIOS, f"unknown scenario ID {case_id}")
        require(case_id not in ids, f"duplicate case ID {case_id}")
        ids.add(case_id)
        for key in ("prompt", "expected_output"):
            nonempty(case[key], key)
        require(case["prompt"].strip() not in prompts, "duplicate behavior prompt")
        prompts.add(case["prompt"].strip())
        require(type(case["files"]) is list, "files must be an array")
        paths = set()
        for path in case["files"]:
            relative_parts(path)
            require(path not in paths, "duplicate input file path")
            paths.add(path)
    missing = sorted(set(SCENARIOS) - ids)
    require(not missing, f"missing scenarios: {[SCENARIOS[i] for i in missing]}")
    return ids


def validate_triggers(triggers):
    require(type(triggers) is list and len(triggers) >= MIN_TRIGGER_CASES,
            f"trigger set must be an array with at least {MIN_TRIGGER_CASES} entries")
    label_counts = {True: 0, False: 0}
    queries = set()
    for trigger in triggers:
        fields(trigger, {"query", "should_trigger"}, "trigger")
        nonempty(trigger["query"], "query")
        require(type(trigger["should_trigger"]) is bool, "should_trigger must be boolean")
        query = trigger["query"].strip()
        require(query not in queries, "duplicate trigger query")
        queries.add(query)
        label_counts[trigger["should_trigger"]] += 1
    require(all(count >= MIN_TRIGGERS_PER_LABEL for count in label_counts.values()),
            f"trigger set must contain at least {MIN_TRIGGERS_PER_LABEL} entries per label")


def validate_results(result, evidence_root, ids, evals_hash, triggers_hash):
    fields(result, {"schema_version", "skill_name", "model", "model_source", "runner",
                    "source_commit", "skill_sha256", "evals_sha256", "triggers_sha256",
                    "runs"}, "results")
    require(type(result["schema_version"]) is int and result["schema_version"] == 1,
            "schema_version must be integer 1")
    require(result["skill_name"] == "icode", "result skill_name must be icode")
    for key in ("model", "model_source", "runner", "source_commit", "skill_sha256",
                "evals_sha256", "triggers_sha256"):
        nonempty(result[key], key)
    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", result["source_commit"]),
            "source_commit must be a full Git object ID")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                            capture_output=True, check=True, timeout=5).stdout.strip()
    require(result["source_commit"] == commit, "source_commit differs from checker checkout HEAD")
    skill_hash = hashlib.sha256(read_bounded(ROOT, "SKILL.md", MAX_JSON_BYTES)).hexdigest()
    for key, expected in (("skill_sha256", skill_hash), ("evals_sha256", evals_hash),
                          ("triggers_sha256", triggers_hash)):
        require(result[key] == expected, f"{key} does not match current source/dataset bytes")
    require(type(result["runs"]) is list, "runs must be an array")
    expected_runs = {(case_id, config) for case_id in ids for config in CONFIGURATIONS}
    require(len(result["runs"]) == len(expected_runs), "missing or extra case/configuration runs")
    seen = set()
    output_paths = set()
    total_bytes = 0
    for run in result["runs"]:
        fields(run, {"case_id", "configuration", "output_path", "output_sha256"}, "run")
        require(type(run["case_id"]) is int, "run case_id must be integer")
        nonempty(run["configuration"], "configuration")
        pair = (run["case_id"], run["configuration"])
        require(pair in expected_runs, "unknown case/configuration")
        require(pair not in seen, "duplicate case/configuration")
        seen.add(pair)
        relative_parts(run["output_path"])
        require(run["output_path"] not in output_paths, "output path reused across runs")
        output_paths.add(run["output_path"])
        nonempty(run["output_sha256"], "output_sha256")
        require(re.fullmatch(r"[0-9a-f]{64}", run["output_sha256"]), "invalid output_sha256")
        data = read_bounded(evidence_root, run["output_path"], MAX_OUTPUT_BYTES, evidence=True)
        total_bytes += len(data)
        require(total_bytes <= MAX_TOTAL_BYTES, "total output byte limit exceeded")
        require(hashlib.sha256(data).hexdigest() == run["output_sha256"], "output digest mismatch")
        output = data.decode("utf-8").strip()
        require(bool(output) and "\x00" not in output, "empty or binary output")
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = output
        # CLI envelopes belong in sidecar evidence; only the actual answer is accepted.
        require(type(payload) is str, "raw output must be plain text or a JSON string")
        output = payload.strip()
        require(bool(output) and "\x00" not in output, "empty or NUL-containing decoded output")
        # Only reject trivial status-only evidence; do not infer semantics from words.
        status_only = output.casefold().strip(" .!。！\r\n\t")
        require(status_only not in {"pass", "passed", "ok", "true", "success",
                                    "all tests passed", "通过", "全部通过", "测试通过"},
                "status-only attestation is not raw output evidence")
    require(seen == expected_runs, "missing case/configuration evidence")
    return len(seen)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evals", type=Path, default=ROOT / "evals/icode/evals.json")
    parser.add_argument("--triggers", type=Path, default=ROOT / "evals/icode/trigger-evals.json")
    parser.add_argument("--results", type=Path, help="optional evidence manifest, not grading.json")
    parser.add_argument("--evidence-root", type=Path, help="explicit allowed root for raw outputs")
    parser.add_argument("--require-results", action="store_true", help="exit 2 if evidence pending")
    args = parser.parse_args(argv)
    report = {"structure": "invalid", "evidence": "pending", "behavior": "pending_manual_review"}
    phase = "structure"
    try:
        cases, evals_hash = load_json(args.evals)
        triggers, triggers_hash = load_json(args.triggers)
        ids = validate_cases(cases)
        validate_triggers(triggers)
        report.update(structure="valid", case_count=len(ids), trigger_count=len(triggers),
                      positive_triggers=sum(t["should_trigger"] for t in triggers))
        phase = "evidence"
        if args.results is not None:
            require(args.evidence_root is not None, "--results requires --evidence-root")
            try:
                result, _ = load_json(args.results)
            except FileNotFoundError:
                report["note"] = "results absent; no model evaluation is established"
            else:
                report["run_count"] = validate_results(result, args.evidence_root, ids,
                                                      evals_hash, triggers_hash)
                report["evidence"] = "complete"
        exit_code = 2 if args.require_results and report["evidence"] == "pending" else 0
    except (ValueError, OSError, RecursionError, subprocess.SubprocessError) as error:
        report[phase] = "invalid"
        report["error"] = str(error)
        exit_code = 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
