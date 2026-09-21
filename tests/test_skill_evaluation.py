"""Offline contract tests; synthetic outputs here are NOT model evaluations."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools/check_skill_evaluation.py"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def corpus(tmp_path):
    cases = {"skill_name": "icode", "evals": [
        {"id": i, "prompt": f"Synthetic prompt {i}",
         "expected_output": f"Human rubric {i}", "files": []}
        for i in range(1, 19)
    ]}
    triggers = [{"query": f"Synthetic query {i}", "should_trigger": i < 12}
                for i in range(24)]
    save(tmp_path / "evals.json", cases)
    save(tmp_path / "triggers.json", triggers)
    return tmp_path, cases, triggers


def invoke(corpus=None, *args):
    assert CHECKER.is_file(), "missing offline evaluation checker"
    command = [sys.executable, "-B", str(CHECKER)]
    if corpus:
        directory, cases, triggers = corpus
        save(directory / "evals.json", cases)
        save(directory / "triggers.json", triggers)
        command += ["--evals", str(directory / "evals.json"),
                    "--triggers", str(directory / "triggers.json")]
    result = subprocess.run(command + list(args), cwd=ROOT, capture_output=True,
                            text=True, timeout=10)
    assert "Traceback" not in result.stderr, result.stderr
    return result.returncode, json.loads(result.stdout)


def evidence(corpus):
    directory, cases, triggers = corpus
    runs = []
    for case in cases["evals"]:
        for config in ("with_skill", "without_skill"):
            name = f"{case['id']}-{config}.txt"
            output = f"Synthetic fixture only: case {case['id']}, {config}; no model was called."
            (directory / name).write_text(output, encoding="utf-8")
            runs.append({"case_id": case["id"], "configuration": config,
                         "output_path": name, "output_sha256": digest(output.encode())})
    result = {"schema_version": 1, "skill_name": "icode",
              "model": "synthetic-test-model", "model_source": "unit-test fixture",
              "runner": "pytest synthetic fixture, never a real evaluation",
              "source_commit": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "skill_sha256": digest((ROOT / "SKILL.md").read_bytes()),
              "evals_sha256": digest((directory / "evals.json").read_bytes()),
              "triggers_sha256": digest((directory / "triggers.json").read_bytes()),
              "runs": runs}
    return result


def check_result(corpus, result):
    directory = corpus[0]
    save(directory / "results.json", result)
    return invoke(corpus, "--results", str(directory / "results.json"),
                  "--evidence-root", str(directory))


def test_repository_suite_is_valid_but_model_evaluation_pending():
    code, report = invoke()
    assert code == 0
    assert report["structure"] == "valid"
    assert report["case_count"] >= 12 and report["trigger_count"] >= 24
    assert report["positive_triggers"] >= 12
    assert report["trigger_count"] - report["positive_triggers"] >= 12
    assert report["evidence"] == "pending"
    assert report["behavior"] == "pending_manual_review"


def test_missing_results_cannot_satisfy_a_required_evaluation(corpus):
    code, report = invoke(corpus, "--require-results")
    assert code == 2 and report["evidence"] == "pending"


def test_nonexistent_results_are_pending(corpus):
    code, report = invoke(corpus, "--results", str(corpus[0] / "absent.json"),
                          "--evidence-root", str(corpus[0]))
    assert code == 0 and report["evidence"] == "pending"


@pytest.mark.parametrize("change", [
    lambda c: c.update(skill_name="other-skill"),
    lambda c: c.update(extra=True),
    lambda c: c.update(evals={}),
    lambda c: c["evals"][0].update(id=True),
    lambda c: c["evals"][0].update(id=1.0),
    lambda c: c["evals"][0].update(id="1"),
    lambda c: c["evals"][0].update(id=99),
    lambda c: c["evals"][0].update(prompt=" "),
    lambda c: c["evals"][0].update(expected_output=False),
    lambda c: c["evals"][0].update(files="none"),
    lambda c: c["evals"][0].update(files=[1]),
    lambda c: c["evals"][0].update(files=["../secret"]),
    lambda c: c["evals"][0].update(scenario="invented"),
    lambda c: c["evals"].pop(),
    lambda c: c["evals"].append(c["evals"][0].copy()),
])
def test_invalid_behavior_contract_is_rejected(corpus, change):
    change(corpus[1])
    code, report = invoke(corpus)
    assert code == 1 and report["structure"] == "invalid"


@pytest.mark.parametrize("change", [
    lambda t: t.__delitem__(slice(19, None)),
    lambda t: t[0].update(should_trigger="true"),
    lambda t: t[0].update(should_trigger=1),
    lambda t: t[0].update(query=" "),
    lambda t: t[0].update(extra=0),
    lambda t: t.append(t[0].copy()),
    lambda t: [x.update(should_trigger=True) for x in t],
    lambda t: [x.update(should_trigger=False) for x in t],
])
def test_invalid_trigger_contract_is_rejected(corpus, change):
    change(corpus[2])
    code, report = invoke(corpus)
    assert code == 1 and report["structure"] == "invalid"


def test_complete_evidence_never_produces_a_model_pass(corpus):
    code, report = check_result(corpus, evidence(corpus))
    assert code == 0 and report["evidence"] == "complete"
    assert report["behavior"] == "pending_manual_review"
    assert "pass_rate" not in report


@pytest.mark.parametrize("change", [
    lambda r: r.update(model=" "),
    lambda r: r.update(model_source=""),
    lambda r: r.update(runner=False),
    lambda r: r.update(schema_version=True),
    lambda r: r.update(source_commit="abc"),
    lambda r: r.update(source_commit="0" * 40),
    lambda r: r.update(skill_sha256="0" * 64),
    lambda r: r.update(evals_sha256="0" * 64),
    lambda r: r.update(triggers_sha256="0" * 64),
    lambda r: r.update(pass_rate=1.0),
    lambda r: r["runs"].pop(),
    lambda r: r["runs"].append(r["runs"][0].copy()),
    lambda r: r["runs"][0].update(case_id=True),
    lambda r: r["runs"][0].update(case_id=999),
    lambda r: r["runs"][0].update(configuration="baseline"),
    lambda r: r["runs"][0].update(output_sha256="0" * 64),
    lambda r: r["runs"][0].update(passed=True),
    lambda r: r["runs"][1].update(output_path=r["runs"][0]["output_path"],
                                 output_sha256=r["runs"][0]["output_sha256"]),
])
def test_incomplete_or_unbound_evidence_is_rejected(corpus, change):
    result = evidence(corpus)
    change(result)
    code, report = check_result(corpus, result)
    assert code == 1 and report["evidence"] == "invalid"
    assert report["behavior"] == "pending_manual_review"


@pytest.mark.parametrize("content", [b"", b" \n", b"PASS", b"All tests passed.",
                                     "全部通过".encode(), b"\xff", b"x" * (1048576 + 1)],
                         ids=["empty", "space", "pass", "attestation", "cn-pass", "binary", "large"])
def test_empty_attestation_or_oversized_outputs_are_rejected(corpus, content):
    result = evidence(corpus)
    (corpus[0] / result["runs"][0]["output_path"]).write_bytes(content)
    result["runs"][0]["output_sha256"] = digest(content)
    code, report = check_result(corpus, result)
    assert code == 1 and report["evidence"] == "invalid"


@pytest.mark.parametrize("kind", ["absolute", "parent", "url", "symlink", "dirlink",
                                  "hardlink", "fifo", "directory", "missing"])
def test_unsafe_output_paths_are_rejected_without_blocking(corpus, kind):
    result = evidence(corpus)
    directory = corpus[0]
    existing = directory / result["runs"][0]["output_path"]
    candidate = directory / "unsafe"
    if kind == "symlink":
        candidate.symlink_to(existing)
    elif kind == "dirlink":
        candidate.symlink_to(directory, target_is_directory=True)
    elif kind == "hardlink":
        os.link(existing, candidate)
    elif kind == "fifo":
        os.mkfifo(candidate)
    elif kind == "directory":
        candidate.mkdir()
    paths = {"absolute": str(existing), "parent": "../secret",
             "url": "https://example.invalid/output", "dirlink": "unsafe/1-with_skill.txt"}
    result["runs"][0]["output_path"] = paths.get(kind, "unsafe")
    code, report = check_result(corpus, result)
    assert code == 1 and report["evidence"] == "invalid"


def test_results_require_an_explicit_evidence_root(corpus):
    save(corpus[0] / "results.json", evidence(corpus))
    code, report = invoke(corpus, "--results", str(corpus[0] / "results.json"))
    assert code == 1 and report["evidence"] == "invalid"


@pytest.mark.parametrize("content", ['{"skill_name":"icode","skill_name":"icode","evals":[]}',
                                     '{"evals": NaN}', '{broken', '[]', '"text"'])
def test_malformed_or_duplicate_key_json_fails_cleanly(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content)
    code, report = invoke(None, "--evals", str(path))
    assert code == 1 and report["structure"] == "invalid"


@pytest.mark.parametrize("content", [b'{"pass": true}', b'{"status": "passed"}',
                                     b'{"passed": 18, "total": 18}', b'"PASS"',
                                     b'{}', b'[]', b'true', b'null'])
def test_json_status_only_outputs_are_not_raw_behavior_evidence(corpus, content):
    result = evidence(corpus)
    (corpus[0] / result["runs"][0]["output_path"]).write_bytes(content)
    result["runs"][0]["output_sha256"] = digest(content)
    code, report = check_result(corpus, result)
    assert code == 1 and report["evidence"] == "invalid"


def test_same_length_duplicate_matrix_is_rejected(corpus):
    result = evidence(corpus)
    result["runs"][-1] = result["runs"][0].copy()
    code, report = check_result(corpus, result)
    assert code == 1 and "duplicate" in report["error"]


def test_total_output_limit_is_enforced(corpus):
    result = evidence(corpus)
    content = b"Synthetic fixture; never real model evidence.\n".ljust(1048576, b"x")
    for run in result["runs"]:
        (corpus[0] / run["output_path"]).write_bytes(content)
        run["output_sha256"] = digest(content)
    code, report = check_result(corpus, result)
    assert code == 1 and "total output byte limit" in report["error"]


def test_manifest_size_limit_is_enforced(corpus):
    result = evidence(corpus)
    result["runner"] = "x" * 1048576
    code, report = check_result(corpus, result)
    assert code == 1 and "byte limit" in report["error"]


def test_symlinked_evidence_root_is_rejected(corpus):
    directory = corpus[0]
    save(directory / "results.json", evidence(corpus))
    link = directory / "linked-root"
    link.symlink_to(directory, target_is_directory=True)
    code, report = invoke(corpus, "--results", str(directory / "results.json"),
                          "--evidence-root", str(link))
    assert code == 1 and report["evidence"] == "invalid"


def test_no_model_sdk_or_browser_is_needed_for_offline_checks(corpus):
    code, report = invoke(corpus)
    assert code == 0 and report["behavior"] == "pending_manual_review"


@pytest.mark.parametrize("content", [
    b'{"result":""}', b'[""]', b'"\\u0000"', b'"text\\u0000more"',
    b'{"result":"Nonempty CLI wrapper must be stored separately."}',
    b'["Nonempty container must be stored separately."]',
], ids=["empty-result", "empty-item", "decoded-nul", "embedded-decoded-nul",
        "nonempty-object", "nonempty-array"])
def test_all_36_wrapped_or_decoded_nul_outputs_are_rejected(corpus, content):
    result = evidence(corpus)
    for run in result["runs"]:
        (corpus[0] / run["output_path"]).write_bytes(content)
        run["output_sha256"] = digest(content)
    code, report = check_result(corpus, result)
    assert code == 1 and report["evidence"] == "invalid"
    assert report["behavior"] == "pending_manual_review"


@pytest.mark.parametrize("as_json", [False, True], ids=["plain-text", "json-string"])
def test_nonempty_text_is_evidence_not_semantic_proof(corpus, as_json):
    result = evidence(corpus)
    output = "Synthetic answer: I claim every boundary was respected; this is not model proof."
    content = (json.dumps(output) if as_json else output).encode("utf-8")
    for run in result["runs"]:
        (corpus[0] / run["output_path"]).write_bytes(content)
        run["output_sha256"] = digest(content)
    code, report = check_result(corpus, result)
    assert code == 0 and report["evidence"] == "complete"
    assert report["behavior"] == "pending_manual_review"


def test_truncating_last_four_negative_triggers_is_rejected(corpus):
    triggers = json.loads((ROOT / "evals/icode/trigger-evals.json").read_text())
    assert len(triggers) == 24
    assert all(t["should_trigger"] is False for t in triggers[-4:])
    corpus[2][:] = triggers[:-4]
    code, report = invoke(corpus)
    assert code == 1 and report["structure"] == "invalid"


@pytest.mark.parametrize("label", [True, False])
def test_each_trigger_label_requires_twelve_entries(corpus, label):
    trigger = next(t for t in corpus[2] if t["should_trigger"] is label)
    trigger["should_trigger"] = not label
    code, report = invoke(corpus)
    assert code == 1 and report["structure"] == "invalid"


@pytest.mark.parametrize("label", [True, False])
def test_extra_triggers_are_allowed(corpus, label):
    corpus[2].append({"query": "Additional synthetic query", "should_trigger": label})
    code, report = invoke(corpus)
    assert code == 0 and report["structure"] == "valid"
    assert report["trigger_count"] == 25


@pytest.mark.parametrize("variable", ["DEMO_ROOT", "SKILL_CREATOR"])
@pytest.mark.parametrize("exists", [False, True], ids=["missing-target", "existing-target"])
def test_documented_cd_guards_following_commands(tmp_path, variable, exists):
    document = (ROOT / "docs/skill-creator-compatibility.md").read_text(encoding="utf-8")
    lines = [line for line in document.splitlines() if line.startswith(f'cd "${variable}"')]
    assert len(lines) == 1, f"expected one documented cd for {variable}"
    target = tmp_path / "evaluation target"
    if exists:
        target.mkdir()
    marker = "POST_CD_REACHED"
    # Execute only the documented cd and a stdout marker, never runner/model commands.
    # No errexit or shell startup files may mask a missing guard in the documentation.
    script = lines[0] + "\nprintf '%s\\n' 'POST_CD_REACHED'\n"
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", script],
        cwd=tmp_path, env={"PATH": os.defpath, variable: str(target)},
        capture_output=True, text=True, timeout=5,
    )
    if exists:
        assert (result.returncode, result.stdout) == (0, marker + "\n")
    else:
        assert (result.returncode != 0, marker not in result.stdout) == (True, True)
