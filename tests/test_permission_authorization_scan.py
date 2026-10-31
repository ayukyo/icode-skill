"""Credentials are rejected; natural permission/security prose is not a credential."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_candidate_inspection_generations import candidate
from test_candidate_freshness import ctl

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools/lint_workflow_contract.py"
SPEC = importlib.util.spec_from_file_location("permission_note_lint", PATH)
lint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lint)

# Supersede the old sentence whitelist assertions explicitly: negation, sentence
# order, language, quoting and placement cannot distinguish prose from secrets.
ORDINARY_NOTES = (
    "No deployment authorization was requested.",
    "Deployment requires authorization; this record only documents host checks.",
    "Authorization is pending. Password policy and cookie security were reviewed.",
    "No errors, request authorization.", "No errors, enable authorization.",
    "No password. No authorization. No cookie.",
    "No new builds, service changes, visual review or deployment authorization.",
    'Checks: complete. "No authorization." Checks completed.',
    "未请求部署授权；本次仅记录软件验证，不代表实机验收。",
    "已检查 authorization 和 access_token 的保护策略，没有记录实际凭据。",
    "Password rotation is required.\nCookie retention policy was checked.",
)
SENSITIVE_NOTES = (
    "Authorization: Bearer synthetic-credential-value",
    "Authorization: Basic c3ludGhldGljOnRlc3Q=", "Cookie: session=synthetic-cookie-value",
    "Set-Cookie: session=synthetic-cookie-value", "password=synthetic-password-value",
    "passwd: synthetic-password-value", "api_key=synthetic-api-value",
    "apikey: synthetic-api-value", "client_secret=synthetic-secret-value",
    "access_token=synthetic-access-value", "refresh_token: synthetic-refresh-value",
    'JSON {"access_token": "synthetic-access-value"}',
    "token.v1: synthetic-token-value. No authorization.",
    "token /v1: synthetic-token-value. No authorization.",
    "credential(v1): synthetic-credential-value", "Bearer synthetic-bearer-value",
    "-----BEGIN RSA PRIVATE KEY-----\nsynthetic-material\n-----END RSA PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----\nsynthetic-material",
    "sk-abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.synthetic_signature",
)


def frozen(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize("note", ORDINARY_NOTES)
def test_permission_and_security_prose_is_not_a_credential(note):
    for value, path, direct in (({"note": note}, "$", False),
            ({"note": note}, "$.verification_runs[12]", True),
            ({"details": {"note": note}}, "$.verification_runs", True),
            ({"verification_runs.note": note}, "$", False),
            ({"evidence": note}, "$.verification_runs", False),
            (json.dumps({"note": note}, ensure_ascii=False), "$", False)):
        assert lint.scan_sensitive(value, path, direct_verification_run=direct) == []


@pytest.mark.parametrize("note", SENSITIVE_NOTES)
def test_actual_material_is_rejected_in_every_note_shape(note):
    for value in ({"note": note}, {"details": {"note": note}},
                  {"verification_runs.note": note}, {"note": [note]}, {"evidence": note}):
        for direct in (False, True):
            assert lint.scan_sensitive(value, "$.verification_runs", direct_verification_run=direct)


@pytest.mark.parametrize("key", [*lint.SENSITIVE_KEYWORDS, "access_token", "refresh_token",
                                "token", "credential", "nested.authorization", "clientSecret"])
@pytest.mark.parametrize("value", [None, "placeholder", "No authorization."])
def test_sensitive_field_names_stay_strict_at_nested_or_forged_paths(key, value):
    for data in ({key: value}, {"details": {key: value}}, {"note": {key: value}},
                 {"verification_runs[0].note": {key: value}}):
        assert lint.scan_sensitive(data, "$.verification_runs", direct_verification_run=True)


@pytest.mark.parametrize("character", ["\0", "\x01", "\x1b", "\x7f", "\x85", "\u202e", "\u200b"])
def test_hidden_control_characters_are_rejected(character):
    assert lint.scan_sensitive({"note": "Checks" + character + " complete. No authorization."})


@pytest.mark.parametrize("label", ["API key", "private key", "密码", "口令", "密钥", "令牌"])
def test_credential_labels_accept_spacing_and_localized_field_names(label):
    assert lint.scan_sensitive({"note": label + ": synthetic-value"})
    assert lint.scan_sensitive({"nested": {label: "placeholder"}})


def test_hidden_controls_in_keys_are_not_a_field_name_bypass():
    assert lint.scan_sensitive({"nested": {"pass\u200bword": "synthetic-value"}})


def test_duplicate_artifact_json_keys_cannot_hide_original_credential_material():
    for material in ("password=synthetic-value", "-----BEGIN PRIVATE KEY-----"):
        identity = '{"note":' + json.dumps(material) + ',"note":"clean"}'
        assert lint.scan_sensitive({"artifact_identity": identity})


def test_deep_finite_input_is_rejected_without_uncontrolled_recursion():
    data = "No authorization."
    for _ in range(1500):
        data = [data]
    assert lint.scan_sensitive(data)


def test_exact_length_budget_and_large_inputs_are_bounded():
    note = "No authorization.".ljust(lint.MAX_EVIDENCE_VALUE_CHARS)
    assert lint.scan_sensitive({"note": note}) == []
    assert lint.scan_sensitive({"note": note + " "})
    program = ("import importlib.util,sys; "
        "s=importlib.util.spec_from_file_location('finite_scan',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m); "
        "assert m.scan_sensitive({'note':'authorization. '*16000}); "
        "assert m.scan_sensitive({'evidence':('token.'*16000)+': value'})")
    result = subprocess.run([sys.executable, "-B", "-c", program, str(PATH)],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("note,sensitive", [(n, False) for n in ORDINARY_NOTES]
                         + [(n, True) for n in SENSITIVE_NOTES])
def test_historical_notes_use_current_policy_without_rewriting(tmp_path, note, sensitive):
    data = {"ticket_id": "generic-note-scan", "workflow_gate_schema_version": 1,
            "verification_runs": [{"note": note}]}
    before = copy.deepcopy(data)
    catalog, error = lint.find_gates_catalog()
    assert error is None
    report = lint.build_report(tmp_path, "verify", data, catalog, legacy=False, strict=True)
    assert bool(report["sensitive_data"]) is sensitive
    assert data == before and list(tmp_path.iterdir()) == []
    (tmp_path / ".ico_metadata.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    snapshot = frozen(tmp_path)
    result = subprocess.run([sys.executable, "-B", str(PATH), str(tmp_path), "--step", "verify",
                             "--strict", "--json"], capture_output=True, timeout=15)
    assert result.returncode == int(sensitive), result.stderr
    assert bool(json.loads(result.stdout)["sensitive_data"]) is sensitive
    assert frozen(tmp_path) == snapshot


def record_args(candidate, note, request="verification-note", **options):
    args = ["record-verification", "--dir", candidate.ticket, "--kind", "listen",
            "--outcome", "inconclusive", "--evidence", "fixture.log", "--note", note,
            "--environment", "host", "--candidate-id",
            ctl.candidate_snapshot(candidate.ticket, ctl.load_metadata(candidate.ticket))["candidate_id"],
            "--request-id", request]
    for name, value in options.items():
        args.extend(["--" + name.replace("_", "-"), value])
    return args


@pytest.mark.parametrize("note", [ORDINARY_NOTES[0], ORDINARY_NOTES[1], ORDINARY_NOTES[8]])
def test_native_writer_accepted_note_remains_lintable_and_immutable_on_replay(candidate, note):
    candidate.create()
    args = record_args(candidate, note)
    assert candidate.call(*args)["ok"]
    meta = ctl.load_metadata(candidate.ticket)
    assert meta["verification_runs"][-1]["note"] == note
    assert not ctl.verify_event_chain(candidate.ticket, meta)[1]
    snapshot = frozen(candidate.ticket)
    assert candidate.call(*args)["already_applied"]
    assert frozen(candidate.ticket) == snapshot
    result = subprocess.run([sys.executable, "-B", str(PATH), str(candidate.ticket),
                             "--step", "verify", "--strict", "--json"], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["sensitive_data"] == 0
    assert frozen(candidate.ticket) == snapshot
    protected = candidate.call("metadata-update", "--dir", candidate.ticket, "--set-json",
                               '{"verification_runs":[]}', ok=False)
    assert protected["gate_id"] == "metadata_update_protected"
    assert frozen(candidate.ticket) == snapshot


@pytest.mark.parametrize("note,options", [
    ("access_token=synthetic-must-not-echo", {}),
    (ORDINARY_NOTES[0], {"evidence": "Authorization: Bearer synthetic-must-not-echo"}),
    (ORDINARY_NOTES[0], {"artifact_identity": '{"nested":{"access_token":"synthetic-must-not-echo"}}'}),
    (ORDINARY_NOTES[0], {"reuse_json": '{"nested":{"access_token":"synthetic-must-not-echo"}}'}),
    ("-----BEGIN PRIVATE KEY-----\nsynthetic-must-not-echo", {}),
])
def test_native_writer_rejects_credentials_before_any_persistent_write(candidate, note, options):
    candidate.create()
    snapshot = frozen(candidate.ticket)
    result = candidate.call(*record_args(candidate, note, **options), ok=False)
    assert result["gate_id"] == "verification_sensitive_data"
    assert "synthetic-must-not-echo" not in json.dumps(result)
    assert frozen(candidate.ticket) == snapshot
    assert not ctl.verify_event_chain(candidate.ticket, ctl.load_metadata(candidate.ticket))[1]
