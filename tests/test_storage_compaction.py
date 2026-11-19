"""Legacy inline tickets re-encode to storage v2 without changing their content.

The physical envelope is maintenance only: the logical object and its metadata
hash must be identical before and after, and a dry run must not touch the file.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def control_module():
    spec = importlib.util.spec_from_file_location("compaction_control", ROOT / "tools/icode_control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CTL = control_module()


def call(*argv):
    parsed = CTL.build_parser().parse_args(list(argv))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = parsed.func(parsed)
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text.startswith("{") else text)


@pytest.fixture
def legacy_ticket(tmp_path):
    out_dir = tmp_path / "ws" / ".icode_output/.icode_output_1"
    out_dir.mkdir(parents=True)
    seed = {"mode": "full", "code_files": [], "semantic_decisions": [], "requirement_deltas": []}
    code, payload = call("create", "--dir", str(out_dir), "--birth", "plan",
                         "--ticket-id", "legacy-compact", "--requirement", "fixture",
                         "--metadata-json", json.dumps(seed))
    assert code == 0, payload
    # Rewrite the issued ticket in the historical inline format.
    path = out_dir / CTL.METADATA_NAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw.get("storage_version") == 2
    path.write_text(json.dumps(raw["value"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_dir


def test_dry_run_reports_without_writing(legacy_ticket):
    path = legacy_ticket / CTL.METADATA_NAME
    before = path.read_bytes()
    code, report = call("compact", "--dir", str(legacy_ticket))
    assert code == 0, report
    assert report["applied"] is False
    assert report["before_bytes"] == len(before)
    assert report["after_bytes"] < report["before_bytes"]
    assert report["saved_ratio"] > 0
    assert isinstance(report["event_bytes"], int) and report["event_bytes"] >= 0
    assert path.read_bytes() == before, "dry run must not touch the ticket"


def test_apply_preserves_the_logical_object_and_hash(legacy_ticket):
    path = legacy_ticket / CTL.METADATA_NAME
    before_logical = CTL.load_metadata(legacy_ticket)
    before_hash = CTL.metadata_hash(before_logical)
    before_bytes = path.stat().st_size

    code, report = call("compact", "--dir", str(legacy_ticket), "--apply")
    assert code == 0, report
    assert report["applied"] is True
    assert Path(report["backup"]).is_file()
    assert path.stat().st_size < before_bytes
    assert json.loads(path.read_text(encoding="utf-8"))["storage_version"] == 2

    after_logical = CTL.load_metadata(legacy_ticket)
    assert after_logical == before_logical
    assert CTL.metadata_hash(after_logical) == before_hash
    code, validated = call("validate", "--dir", str(legacy_ticket))
    assert code == 0, validated
    assert validated["ok"] is True


def test_second_apply_is_a_no_op(legacy_ticket):
    call("compact", "--dir", str(legacy_ticket), "--apply")
    path = legacy_ticket / CTL.METADATA_NAME
    settled = path.read_bytes()
    code, report = call("compact", "--dir", str(legacy_ticket), "--apply")
    assert code == 0, report
    assert report["already_compact"] is True
    assert report["applied"] is False
    assert path.read_bytes() == settled


def test_ledger_dominated_ticket_requires_force(legacy_ticket):
    """A ledger at least as large as the metadata makes v2 reads slower."""
    path = legacy_ticket / CTL.METADATA_NAME
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")),
                               ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(CTL.ControlError) as error:
        call("compact", "--dir", str(legacy_ticket), "--apply")
    assert error.value.extra.get("gate_id") == "compact_ledger_dominates"
    assert path.read_bytes() == before, "refusal must not touch the ticket"

    code, report = call("compact", "--dir", str(legacy_ticket), "--apply", "--force")
    assert code == 0, report
    assert report["applied"] is True


def test_pending_transaction_blocks_compaction(legacy_ticket):
    (legacy_ticket / CTL.TXN_NAME).write_text("{}\n", encoding="utf-8")
    with pytest.raises(CTL.ControlError) as error:
        call("compact", "--dir", str(legacy_ticket), "--apply")
    assert error.value.extra.get("gate_id") == "compact_pending_transaction"