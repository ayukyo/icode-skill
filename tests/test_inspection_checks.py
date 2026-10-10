"""Special review records use anonymous source fixtures, never automatic lint passes."""
import copy
import hashlib

import pytest

from test_inspection_worklist import api, build, commit, files, put, read_all, repo


CLOCK_SOURCE = "#include <stdint.h>\nuint64_t stamp(void) { int64_t clock_ns = -1; return (uint64_t)clock_ns; }\n"
SHARED_SOURCE = "int shared_backend(int product_pid) { return product_pid == 101; } /* sibling probe failure */\n"
TIMESTAMP_ITEMS = {"signedness", "domain", "unit", "epoch", "negative_zero_max", "sentinel",
                   "overflow_underflow", "projection", "frame_window", "interval_fallback"}
STATIC_REVIEW = {
    "signedness": "The fixture converts int64_t clock_ns=-1 to uint64_t; that risk is recorded, not repaired.",
    "domain": "The fixture contains a constant, with no monotonic/realtime clock API; real clock domain remains untested.",
    "unit": "The variable name states ns but no conversion or scale is implemented in the fixture.",
    "epoch": "No epoch offset exists in this constant-return fixture; the source does not establish a real clock epoch.",
    "negative_zero_max": "Negative -1 is present; zero/max inputs are static boundary obligations, not executed device tests.",
    "sentinel": "-1 has no check before the unsigned cast, so the invalid clock sentinel escapes as a large unsigned value.",
    "overflow_underflow": "The unsigned projection wraps the negative fixture value modulo the destination width.",
    "projection": "The int64_t to uint64_t cast is the sole projection and has no sign guard.",
    "frame_window": "The minimal source has no frame-window filtering; this does not prove any caller window correct.",
    "interval_fallback": "No interval/fallback path exists in the minimal fixture; downstream behavior is untested.",
    "target": "The shared backend accepts only product_pid==101; target selection is visible in the source.",
    "sibling": "All other PIDs return false; a sibling reusing this selector needs its own capability decision.",
    "unknown": "Unknown PIDs also return false; no speculative probe appears in this minimal source.",
    "supported": "Support is represented only by a boolean equality to 101; physical capability is not verified.",
    "unsupported": "The boolean false branch covers non-101 PIDs, with no physical probing in the fixture.",
    "probe_failure": "No real probe is implemented; the sibling probe failure comment is a review signal, not evidence of a test.",
    "read_failure": "The fixture performs no hardware read, so read error propagation is outside its static proof boundary.",
    "stale": "The selector has no stored capability cache; stale-cache handling is absent from the minimal source.",
    "consumers": "No caller is declared in this fixture; the association header/test is reviewed, with external consumers unproven.",
    "first_frame": "The stateless selector has no frame callback or first-frame special state.",
    "steady": "Repeated calls use the same PID equality without cached state; runtime repetition is not tested.",
    "stop_start": "No retained state appears, so the selector itself has no stop/start lifecycle cleanup path.",
    "reconnect": "No connection state appears; actual transport reconnect remains outside this fixture.",
    "build_config:enabled": "The fixture has no preprocessor build guard; its selector is always present in the source.",
    "build_config:disabled": "No disabled build configuration exists in this minimal source.",
    "fallback:old_semantics": "The fixed PID equality is the existing fixture behavior; this test makes no code repair claim.",
    "fallback:no_extra_probe": "The function performs no probe and introduces no hardware access.",
    "fallback:error_isolation": "There is no hardware error path in the selector; cross-consumer error isolation is not runtime proven.",
}


def check(report, kind="timestamp_contract"):
    return next(c for c in report["checks"] if c["kind"] == kind)


def assessment(report, item, root):
    """Every cell cites the real, read fixture and states the static evidence limit."""
    path = item["paths"][0]
    entry = files(report)[path]
    text = (root / path).read_text()
    location = dict(kind="source", path=path, start_line=1, end_line=len(text.splitlines()),
                    source_sha256=entry["sha256"], excerpt=text)
    cells = {}
    for required in item["required_items"]:
        if required.startswith("memory:"):
            status, reason = "not_applicable", "Fixture has no mmap or DMA allocation or release path."
        else:
            status, reason = "handled", STATIC_REVIEW[required]
        cells[required] = dict(status=status, reason=reason, evidence=[copy.deepcopy(location)])
    return dict(cells=cells)


def reviewed(api, root, **kwargs):
    report = read_all(build(api, root, **kwargs))
    for item in report["checks"]:
        for phase in report["required_phases"]:
            api.apply_assessment(report, root, item["check_id"], phase, assessment(report, item, root))
    return report


def test_new_reports_are_marked_even_without_triggers(api, repo):
    report = read_all(build(api, repo))
    assert report["schema_version"] == 1
    assert report.get("checks_version") == 1 and report.get("checks") == []
    assert api.validate_worklist(report, repo, required_checks_version=1) == []


def test_clock_minus_one_unsigned_requires_all_dimensions_and_phases(api, repo):
    put(repo, "src/feature.c", CLOCK_SOURCE)
    report = read_all(build(api, repo))
    item = check(report)
    assert set(item["required_items"]) == TIMESTAMP_ITEMS
    assert item["results"] == {}  # Neither prepare nor Read implies assessment.
    assert api.validate_worklist(report, repo)
    api.apply_assessment(report, repo, item["check_id"], "reverse", assessment(report, item, repo))
    assert api.validate_worklist(report, repo)  # Fixed and Free need independent conclusions.
    for phase in ("fixed", "free"):
        api.apply_assessment(report, repo, item["check_id"], phase, assessment(report, item, repo))
    assert api.validate_worklist(report, repo) == []


def test_shared_sibling_pid_requires_product_capability_lifecycle_matrix(api, repo):
    put(repo, "src/feature.c", SHARED_SOURCE)
    report = read_all(build(api, repo))
    item = check(report, "shared_variant_consumers")
    expected = {"target", "sibling", "unknown", "supported", "unsupported", "probe_failure", "read_failure", "stale",
                "consumers", "memory:mmap", "memory:dma", "first_frame", "steady", "stop_start", "reconnect",
                "build_config:enabled", "build_config:disabled", "fallback:old_semantics",
                "fallback:no_extra_probe", "fallback:error_isolation"}
    assert expected == set(item["required_items"])
    assert api.validate_worklist(report, repo)
    assert api.validate_worklist(reviewed(api, repo), repo) == []


@pytest.mark.parametrize("source", ["int64_t duration_ms = 3;\n", "int interval_us = 1;\n",
                                   "long epoch = 0;\n", "int ns = 0;\n",
                                   "int x = (uint32_t)raw;\n", "auto x = static_cast<unsigned long>(raw);\n"])
def test_explicit_timestamp_and_integer_conversion_signals(api, repo, source):
    put(repo, "src/feature.c", source)
    assert check(build(api, repo))


def test_sdk_documentation_does_not_trigger_source_checks(api, repo):
    put(repo, "docs/sdk.md", "SDK timestamp clock epoch ns us ms shared backend sibling capability\n")
    report = api.build_worklist(repo, ["docs/sdk.md"], step="audit", ticket_id="docs", attempt=1)
    assert report["checks"] == []


def test_deleted_lines_and_deleted_file_preserve_trigger(api, repo):
    put(repo, "src/feature.c", CLOCK_SOURCE)
    baseline = commit(repo)
    put(repo, "src/feature.c", "int feature(void) { return 1; }\n")
    assert check(build(api, repo, baselines={".": baseline}))
    (repo / "src/feature.c").unlink()
    assert check(build(api, repo, baselines={".": baseline}))


def test_related_consumers_share_identity_and_are_not_lost(api, repo):
    put(repo, "src/feature.c", SHARED_SOURCE)
    report = build(api, repo, related=["elsewhere/other.c"])
    item = check(report, "shared_variant_consumers")
    assert "elsewhere/other.c" in item["paths"]
    audit = api.build_worklist(repo, ["src/feature.c"], related=["elsewhere/other.c"],
                               step="audit", ticket_id="test-1", attempt=2)
    assert check(audit, "shared_variant_consumers")["check_id"] == item["check_id"]


@pytest.mark.parametrize("mutation", ["missing_item", "empty_reason", "missing_evidence", "bad_excerpt",
                                      "foreign_phase", "identity", "bool_version", "missing_checks", "unknown_field"])
def test_assessment_and_derived_identity_fail_closed(api, repo, mutation):
    put(repo, "src/feature.c", CLOCK_SOURCE)
    report = reviewed(api, repo)
    item = check(report)
    cells = item["results"]["reverse"]["cells"]
    cell = cells["signedness"]
    if mutation == "missing_item":
        cells.pop("signedness")
    elif mutation == "empty_reason":
        cell.update(status="not_applicable", reason="  ")
    elif mutation == "missing_evidence":
        cell["evidence"] = []
    elif mutation == "bad_excerpt":
        cell["evidence"][0]["excerpt"] = "invented evidence\n"
    elif mutation == "foreign_phase":
        item["results"]["audit"] = item["results"].pop("reverse")
    elif mutation == "identity":
        item["required_items"].remove("sentinel")
    elif mutation == "bool_version":
        item["version"] = True
    elif mutation == "missing_checks":
        report.pop("checks")
    else:
        cell["invented"] = True
    assert api.validate_worklist(report, repo, allow_incomplete=True)


@pytest.mark.parametrize("status", ["pending", "fail"])
def test_unfinished_or_failed_cell_requires_debt_and_cannot_success(api, repo, status):
    put(repo, "src/feature.c", CLOCK_SOURCE)
    report = reviewed(api, repo)
    check(report)["results"]["reverse"]["cells"]["sentinel"]["status"] = status
    assert api.validate_worklist(report, repo)
    report.update(coverage_status="partial", debt_reason="Sentinel handling still needs independent review.")
    assert api.validate_worklist(report, repo, allow_incomplete=True) == []


def test_assessment_requires_actual_read_and_is_atomic_on_rejection(api, repo):
    put(repo, "src/feature.c", CLOCK_SOURCE)
    report = build(api, repo)
    before = copy.deepcopy(report)
    item = check(report)
    with pytest.raises(ValueError, match="Read"):
        api.apply_assessment(report, repo, item["check_id"], "reverse", assessment(report, item, repo))
    assert report == before


def test_old_v1_read_only_compatibility_and_external_marker_enforcement(api, repo):
    report = read_all(build(api, repo))
    report.pop("checks_version")
    report.pop("checks")
    assert api.validate_worklist(report, repo) == []
    assert api.validate_worklist(report, repo, required_checks_version=1)


@pytest.mark.parametrize("version", [0, True, 2])
def test_required_checks_version_cannot_silently_accept_unknown_protocol(api, repo, version):
    assert api.validate_worklist(read_all(build(api, repo)), repo, required_checks_version=version)


@pytest.mark.parametrize("item", ["sibling", "unknown", "probe_failure", "read_failure", "stale", "stop_start", "memory:mmap"])
def test_shared_dimension_omission_cannot_be_hidden_by_static_evidence(api, repo, item):
    put(repo, "src/feature.c", SHARED_SOURCE)
    report = reviewed(api, repo)
    check(report, "shared_variant_consumers")["results"]["reverse"]["cells"].pop(item)
    assert api.validate_worklist(report, repo)


def test_baseline_trigger_truncation_retains_explicit_debt(api, repo, monkeypatch):
    original = api._git
    def truncated(repo_root, args, limit=8192):
        if args[0] == "diff" and "--unified=0" in args:
            return b"-int64_t clock_ns = -1;\n", True
        return original(repo_root, args, limit)
    monkeypatch.setattr(api, "_git", truncated)
    report = read_all(build(api, repo))
    assert check(report)
    assert report["coverage_status"] == "partial"
    assert api.validate_worklist(report, repo)
    assert api.validate_worklist(report, repo, allow_incomplete=True) == []


def test_baseline_trigger_timeout_is_debt_not_silent_completion(api, repo, monkeypatch):
    original = api._git
    def bounded(repo_root, args, limit=8192):
        if args[0] == "diff" and "--unified=0" in args:
            raise api.InspectionError("git timeout")
        return original(repo_root, args, limit)
    monkeypatch.setattr(api, "_git", bounded)
    report = read_all(build(api, repo))
    assert report["coverage_status"] == "partial"
    assert any("trigger" in debt["debt_reason"] and "timeout" in debt["debt_reason"] for debt in report["unobserved"])
    assert api.validate_worklist(report, repo)


def test_simulation_artifact_requires_explicit_boundary_and_current_hash(api, repo):
    put(repo, "src/feature.c", CLOCK_SOURCE)
    artifact = put(repo, "evidence/clock.txt", "simulated clock -1 boundary fixture; no physical hardware\n")
    report = reviewed(api, repo)
    cell = check(report)["results"]["reverse"]["cells"]["sentinel"]
    cell["evidence"] = [dict(kind="simulation", path="evidence/clock.txt",
        sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), simulated=True,
        evidence_boundary="Synthetic clock error injection only; physical hardware untested.")]
    assert api.validate_worklist(report, repo) == []
    cell["evidence"][0]["simulated"] = False
    assert api.validate_worklist(report, repo)


@pytest.mark.parametrize("kind", ["test", "simulation", "runtime"])
def test_control_sidecar_artifact_is_valid_evidence_but_not_source(api, repo, kind):
    put(repo, "src/feature.c", CLOCK_SOURCE)
    artifact = put(repo, ".icode_output/review/clock.txt", "clock boundary review artifact; no semantic proof claimed\n")
    report = reviewed(api, repo)
    cell = check(report)["results"]["reverse"]["cells"]["sentinel"]
    cell["evidence"] = [dict(kind=kind, path=".icode_output/review/clock.txt",
        sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), simulated=kind == "simulation",
        evidence_boundary="Fixture artifact boundary only; no physical validation inferred.")]
    assert api.validate_worklist(report, repo) == []
    cell["evidence"] = [dict(kind="source", path=".icode_output/review/clock.txt", start_line=1, end_line=1,
        source_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), excerpt=artifact.read_text())]
    assert api.validate_worklist(report, repo)


@pytest.mark.parametrize("path", ["../escape.txt", ".git/config", ".icode_output/id_rsa", ".icode_output/.env"])
def test_artifact_evidence_still_rejects_escape_git_and_private_paths(api, repo, path):
    put(repo, "src/feature.c", CLOCK_SOURCE)
    report = reviewed(api, repo)
    cell = check(report)["results"]["reverse"]["cells"]["sentinel"]
    cell["evidence"] = [dict(kind="test", path=path, sha256="0" * 64, simulated=False,
        evidence_boundary="Untrusted artifact fixture.")]
    assert api.validate_worklist(report, repo)
