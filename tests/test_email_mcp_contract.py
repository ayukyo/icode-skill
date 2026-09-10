from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO / "mcp" / "icode-mail-observe" / "server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("icode_mail_observe", SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_message() -> bytes:
    message = EmailMessage()
    message["From"] = "Release Bot <release@example.com>"
    message["To"] = "mower@example.com"
    message["Subject"] = "RL2601 V0.0.8"
    message["Date"] = "Wed, 10 Sep 2026 11:58:00 +0800"
    message["Message-ID"] = "<mail-2@example.com>"
    message["In-Reply-To"] = "<mail-1@example.com>"
    message["References"] = "<mail-1@example.com>"
    message.set_content("test report")
    message.add_attachment(b"log line\n", maintype="text", subtype="plain", filename="device.log")
    return message.as_bytes()


class FakeImap:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls = []
        self.raw = build_message()
        self.__class__.instances.append(self)

    def login(self, username, secret):
        self.calls.append(("login", username, secret))
        return "OK", [b"logged in"]

    def list(self):
        self.calls.append(("list",))
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Sent) "/" "Sent"']

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        command = command.lower()
        if command == "search":
            return "OK", [b"101"]
        if command == "fetch":
            query = str(args[-1])
            if "RFC822.SIZE" in query and "BODY.PEEK" not in query:
                return "OK", [(b"101 (RFC822.SIZE %d)" % len(self.raw), b"")]
            if "HEADER.FIELDS" in query:
                headers = self.raw.split(b"\n\n", 1)[0] + b"\n\n"
                return "OK", [(b"101 (BODY[HEADER.FIELDS] {%d}" % len(headers), headers), b")"]
            if "BODY.PEEK[]" in query:
                return "OK", [(b"101 (BODY[] {%d}" % len(self.raw), self.raw), b")"]
        return "NO", [b"unsupported"]

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", [b"logout"]


class EmailMcpContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_server()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "mail.json"
        self.config.write_text(
            json.dumps(
                {
                    "host": "imap.example.com",
                    "port": 993,
                    "username": "mower@example.com",
                    "mailbox_allowlist": ["INBOX"],
                    "download_root": str(self.root / "downloads"),
                    "max_messages": 20,
                    "max_message_bytes": 1024 * 1024,
                    "max_attachment_bytes": 1024,
                    "timeout_seconds": 3,
                }
            ),
            encoding="utf-8",
        )
        os.environ["ICODE_MAIL_OBSERVE_CONFIG"] = str(self.config)
        os.environ["ICODE_MAIL_OBSERVE_SECRET"] = "fixture-secret"
        FakeImap.instances.clear()

    def tearDown(self):
        os.environ.pop("ICODE_MAIL_OBSERVE_CONFIG", None)
        os.environ.pop("ICODE_MAIL_OBSERVE_SECRET", None)
        self.temp.cleanup()

    def test_surface_is_small_keyless_and_has_no_mailbox_write_tools(self):
        expected = {
            "describe_capabilities",
            "list_mailboxes",
            "search_messages",
            "get_message",
            "get_thread",
            "save_attachment",
        }
        self.assertEqual(set(self.module.TOOLS), expected)
        forbidden = {"send_email", "reply", "delete", "move", "copy", "store", "raw_command", "mark_read"}
        self.assertFalse(expected & forbidden)
        answer = self.module.describe_capabilities()["answer"]
        self.assertFalse(answer["api_key_required"])
        self.assertTrue(answer["network"])
        self.assertTrue(answer["mailbox_read_only"])
        self.assertEqual(answer["credential_sources"], ["ICODE_MAIL_OBSERVE_SECRET", "credential_file"])

    def test_reads_with_peek_readonly_select_and_bounded_search(self):
        with mock.patch.object(self.module.imaplib, "IMAP4_SSL", FakeImap):
            listed = self.module.list_mailboxes()["answer"]
            self.assertEqual([item["path"] for item in listed["mailboxes"]], ["INBOX"])
            search = self.module.search_messages(
                mailbox="INBOX",
                subject_contains="RL2601",
                since="2026-09-01",
                limit=5,
            )["answer"]
            self.assertEqual(search["returned"], 1)
            message = self.module.get_message("INBOX", "101")["answer"]
            self.assertEqual(message["mail"]["headers"]["message_id"], "<mail-2@example.com>")
            thread = self.module.get_thread("INBOX", "101", limit=5)["answer"]
            self.assertEqual(thread["returned"], 1)
            self.assertEqual(thread["resolution_status"], "bounded")
        calls = [call for instance in FakeImap.instances for call in instance.calls]
        self.assertTrue(any(call[:3] == ("select", "INBOX", True) for call in calls))
        fetch_queries = [str(call[-1]) for call in calls if call[:2] == ("uid", "fetch")]
        self.assertTrue(any("BODY.PEEK[]" in query for query in fetch_queries))
        self.assertFalse(any(call[0] in {"store", "copy", "move", "expunge", "append"} for call in calls))

    def test_attachment_write_is_confined_to_configured_managed_root(self):
        with mock.patch.object(self.module.imaplib, "IMAP4_SSL", FakeImap):
            message = self.module.get_message("INBOX", "101")["answer"]["mail"]
            part_id = message["attachments"][0]["part_id"]
            saved = self.module.save_attachment("INBOX", "101", part_id)["answer"]
        target = Path(saved["path"])
        self.assertEqual(target.parent, self.root / "downloads" / "INBOX" / "101")
        self.assertEqual(target.read_bytes(), b"log line\n")

    def test_missing_secret_and_unlisted_mailbox_fail_closed(self):
        os.environ.pop("ICODE_MAIL_OBSERVE_SECRET")
        missing = self.module.get_message("INBOX", "101")
        self.assertEqual(missing["error_code"], "credentials_unavailable")
        os.environ["ICODE_MAIL_OBSERVE_SECRET"] = "fixture-secret"
        with mock.patch.object(self.module.imaplib, "IMAP4_SSL", FakeImap):
            denied = self.module.search_messages("Sent")
        self.assertEqual(denied["error_code"], "mailbox_not_allowed")

    def test_managed_attachment_root_rejects_symlink(self):
        real = self.root / "real-downloads"
        real.mkdir()
        linked = self.root / "linked-downloads"
        linked.symlink_to(real, target_is_directory=True)
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["download_root"] = str(linked)
        self.config.write_text(json.dumps(config), encoding="utf-8")
        with mock.patch.object(self.module.imaplib, "IMAP4_SSL", FakeImap):
            message = self.module.get_message("INBOX", "101")["answer"]["mail"]
            result = self.module.save_attachment("INBOX", "101", message["attachments"][0]["part_id"])
        self.assertEqual(result["error_code"], "attachment_failed")
        self.assertFalse(any(real.iterdir()))


if __name__ == "__main__":
    unittest.main()
