"""A pending pathname blocks publication even when its link target is absent."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

from test_delivery_inventory import inventory_repo

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("delivery_pending_path_fixture", ROOT / "tools/icode_delivery.py")
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


def test_dangling_pending_link_is_reported_and_refuses_all_persistence(inventory_repo):
    _, metadata_path, _ = inventory_repo
    directory = metadata_path.parent
    pending = directory / delivery.ctl.TXN_NAME
    try:
        pending.symlink_to(directory / "missing-pending-target-fixture")
    except (OSError, NotImplementedError):
        pytest.skip("host cannot create the required real dangling file symlink")
    original = metadata_path.read_bytes()
    original_target = os.readlink(pending)
    original_names = sorted(path.name for path in directory.iterdir())
    with contextlib.redirect_stdout(io.StringIO()) as stream:
        code = delivery.main(["--dir", str(directory)])
    assert code == 0
    assert "pending_transaction" in json.loads(stream.getvalue())["issues"]
    for option, filename in (("--markdown", "delivery_validation_report.md"),
                             ("--output", "delivery_validation_report.json")):
        output = directory / filename
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            code = delivery.main(["--dir", str(directory), option, str(output)])
        assert code == 1 and json.loads(stream.getvalue())["read_only"] is True
        assert not output.exists()
    assert metadata_path.read_bytes() == original
    assert os.readlink(pending) == original_target
    assert sorted(path.name for path in directory.iterdir()) == original_names
