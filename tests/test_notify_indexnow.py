"""IndexNow contract tests; only DNS/socket/TLS transport is replaced."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import socket
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "notify_indexnow.py"
BASE = "https://pages.example.org/project/"
KEY = "test-key-12345678"
PUBLIC_IP = "93.184.216.34"


class WireSocket:
    """A byte-stream peer for the real http.client request/response parser."""

    def __init__(self, response):
        status, body, headers = response
        header_lines = [f"HTTP/1.1 {status} Test", f"Content-Length: {len(body)}",
                        "Connection: close"]
        header_lines.extend(f"{name}: {value}" for name, value in headers.items())
        self.response = ("\r\n".join(header_lines) + "\r\n\r\n").encode() + body
        self.sent = bytearray()
        self.closed = False

    def sendall(self, data):
        self.sent.extend(data)

    def makefile(self, mode):
        return io.BytesIO(self.response)

    def setsockopt(self, *args):
        pass

    def close(self):
        self.closed = True


def reply(status=200, body=b"", **headers):
    return status, body, headers


class NotifyIndexNowTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "IndexNow notifier CLI has not been implemented")
        spec = importlib.util.spec_from_file_location("notify_indexnow", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        temp = tempfile.TemporaryDirectory(prefix=".indexnow-test-", dir=ROOT)
        self.addCleanup(temp.cleanup)
        self.manifest_path = Path(temp.name) / "manifest.json"
        self.manifest = {"schema_version": 1, "base_url": BASE,
                         "urls": [BASE, BASE + "en/"]}
        self.outcomes = []
        self.wires = []
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"INDEXNOW_KEY": KEY}))
        self.dns = self.stack.enter_context(patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))]))
        self.connect = self.stack.enter_context(patch("socket.create_connection",
                                                     side_effect=self.open_socket))
        self.tls = self.stack.enter_context(patch("ssl.SSLContext.wrap_socket",
                                                 side_effect=lambda sock, **kwargs: sock))
        # Any unanticipated lower-level network route is an immediate test failure.
        self.stack.enter_context(patch("socket.socket", side_effect=AssertionError("real network forbidden")))

    def open_socket(self, *args, **kwargs):
        self.assertTrue(self.outcomes, "unexpected extra HTTP attempt")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        wire = outcome if isinstance(outcome, WireSocket) else WireSocket(outcome)
        self.wires.append(wire)
        return wire

    def invoke(self, submit=False, raw=None, argv=None):
        content = json.dumps(self.manifest).encode() if raw is None else raw
        self.manifest_path.write_bytes(content)
        args = ["--manifest", str(self.manifest_path)]
        if submit:
            args.append("--submit")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.module.main(args if argv is None else argv)
        text = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(KEY, text)
        self.assertEqual(stderr.getvalue(), "")
        return code, json.loads(stdout.getvalue())

    def assert_no_network(self):
        self.dns.assert_not_called()
        self.connect.assert_not_called()
        self.tls.assert_not_called()

    def assert_invalid(self, **kwargs):
        code, result = self.invoke(submit=True, **kwargs)
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "error")
        self.assertIn("error", result)
        self.assert_no_network()

    def requests(self):
        return [bytes(wire.sent) for wire in self.wires]

    def test_default_dry_run_has_zero_network_even_with_key(self):
        code, result = self.invoke()
        self.assertEqual((code, result["status"]), (0, "dry-run"))
        self.assertEqual(result["url_count"], 2)
        self.assert_no_network()

    def test_optional_manifest_version_is_accepted(self):
        self.manifest["version"] = "release-1"
        self.assertEqual(self.invoke()[0], 0)
        self.assert_no_network()

    def test_workflow_manifest_contract_is_accepted(self):
        base = "https://ayukyo.github.io/icode-skill/"
        self.manifest = {"schema_version": 1, "base_url": base,
                         "version": "v1.2.3", "urls": [base, base + "en/"]}
        self.outcomes = [reply(body=KEY.encode()), reply()]
        self.assertEqual(self.invoke(submit=True)[0], 0)
        self.assertTrue(self.requests()[0].startswith(f"GET /icode-skill/{KEY}.txt ".encode()))

    def test_script_entry_point_exits_zero_in_offline_default(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        stdout = io.StringIO()
        with patch.object(sys, "argv", [str(SCRIPT), "--manifest", str(self.manifest_path)]):
            with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(SCRIPT), run_name="__main__")
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "dry-run")
        self.assertNotIn(KEY, stdout.getvalue())
        self.assert_no_network()

    def test_root_base_and_single_known_page_are_valid(self):
        for base in ("https://pages.example.org/", "https://pages.example.org/a/b/"):
            with self.subTest(base=base):
                self.manifest.update(base_url=base, urls=[base + "en/"])
                self.assertEqual(self.invoke()[0], 0)
        self.assert_no_network()

    def test_missing_or_empty_key_skips_even_submit(self):
        for key in (None, ""):
            for submit in (False, True):
                with self.subTest(key=key, submit=submit):
                    os.environ.pop("INDEXNOW_KEY", None)
                    if key is not None:
                        os.environ["INDEXNOW_KEY"] = key
                    code, result = self.invoke(submit=submit)
                    self.assertEqual((code, result["status"]), (0, "skipped"))
                    self.assertEqual(result["reason"], "missing_key")
        self.assert_no_network()

    def test_invalid_key_is_rejected_without_network(self):
        for key in ("short", "a" * 129, "abc_defgh", "abcdefgh\n", "abcdefgh/", "ä" * 8, " key-12345"):
            with self.subTest(key=key):
                os.environ["INDEXNOW_KEY"] = key
                self.assert_invalid()

    def test_key_length_boundaries_are_accepted(self):
        for key in ("a" * 8, "Z" * 128, "-" * 8):
            with self.subTest(length=len(key)):
                os.environ["INDEXNOW_KEY"] = key
                self.assertEqual(self.invoke()[0], 0)
        self.assert_no_network()

    def test_malformed_json_or_encoding_is_rejected(self):
        for raw in (b"{", b"", b"\xff", b"{} trailing"):
            with self.subTest(raw=raw):
                self.assert_invalid(raw=raw)

    def test_nonstandard_json_constants_are_rejected(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                raw = json.dumps(self.manifest)[:-1] + ', "extra": ' + constant + '}'
                self.assert_invalid(raw=raw.encode())

    def test_non_object_manifest_is_rejected(self):
        for value in (None, [], "manifest", True, 1):
            with self.subTest(value=value):
                self.manifest = value
                self.assert_invalid()

    def test_missing_required_fields_are_rejected(self):
        for field in ("schema_version", "base_url", "urls"):
            with self.subTest(field=field):
                value = self.manifest.pop(field)
                self.assert_invalid()
                self.manifest[field] = value

    def test_schema_version_must_be_integer_one(self):
        for version in (True, False, 1.0, "1", None, 0, 2):
            with self.subTest(version=version):
                self.manifest["schema_version"] = version
                self.assert_invalid()

    def test_unsafe_base_urls_are_rejected(self):
        bases = [None, 123, "", "http://pages.example.org/project/",
                 "https://user:secret@pages.example.org/project/",
                 "https://@pages.example.org/project/", "https://user@pages.example.org/project/",
                 "https://pages.example.org:443/project/", "https://pages.example.org:/project/",
                 "https://pages.example.org/project", "https://pages.example.org/project/?x=1",
                 "https://pages.example.org/project/?", "https://pages.example.org/project/#x",
                 "https://pages.example.org/project/#", "https://pages.example.org/project\\evil/",
                 "https://pages.example.org/project/%2e%2e/", "https://pages.example.org/a/../project/",
                 "https://pages.example.org/a/./", "https://pages.example.org//project/",
                 "https://pages.example.org/pro ject/", "https://pages.example.org/\tproject/",
                 "https://pages.example.org/\nproject/", "https://pages.example.org/\x00project/",
                 "https://pages.example.org/\x7fproject/", " https://pages.example.org/project/",
                 "https://pages.example.org/项目/", "https://pages.example.org./project/",
                 "https://-bad.example.org/", "https://bad-.example.org/", "https://bad_host.example.org/",
                 "https://a..b/", "https://a.-bad.org/", "https://a.bad-.org/",
                 "https://" + "x" * 64 + ".example.org/"]
        bases.extend(BASE + char + "/" for char in '<>"{}|^`[]')
        for base in bases:
            with self.subTest(base=base):
                self.manifest.update(base_url=base, urls=[base])
                self.assert_invalid()

    def test_ip_literals_and_local_hosts_are_rejected(self):
        for host in ("localhost", "printer", "printer.local", "printer.localhost", "server.internal",
                     "server.lan", "router.home", "host.test", "host.invalid", "host.example",
                     "localhost.localdomain",
                     "foo.onion", "127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.0.2",
                     "8.8.8.8", "[::1]", "[2001:4860:4860::8888]", "127.1", "2130706433",
                     "0x7f000001", "0177.0.0.1"):
            with self.subTest(host=host):
                base = f"https://{host}/project/"
                self.manifest.update(base_url=base, urls=[base])
                self.assert_invalid()

    def test_url_collection_shape_duplicates_and_limit_are_rejected(self):
        for urls in (None, "https://pages.example.org/project/", {}, [], [None], [123],
                     [BASE, BASE], [BASE + "en/"] * 10001):
            with self.subTest(kind=type(urls).__name__, count=len(urls) if isinstance(urls, list) else None):
                self.manifest["urls"] = urls
                self.assert_invalid()

    def test_urls_must_exactly_match_known_public_pages(self):
        urls = ["https://other.example.org/project/", "https://pages.example.org/",
                "https://pages.example.org/project-other/", BASE + "private/", BASE + "en",
                BASE + "en/deep/", BASE + "en/?q=x", BASE + "en/#fragment", BASE + "./",
                BASE + "%65n/", BASE + "en/../", BASE + "en/\n", BASE + KEY + ".txt",
                "http://pages.example.org/project/", "https://pages.example.org:443/project/"]
        for url in urls:
            with self.subTest(url=url):
                self.manifest["urls"] = [url]
                self.assert_invalid()

    def test_argument_errors_return_json_and_exit_two(self):
        for args in ([], ["--manifest"], ["--manifest", str(self.manifest_path), "--unknown"]):
            with self.subTest(args=args):
                self.assert_invalid(argv=args)

    def test_unreadable_manifest_returns_json(self):
        self.assert_invalid(argv=["--manifest", str(self.manifest_path.parent / "absent.json")])

    def test_manifest_file_symlink_rejected_before_read_even_without_key(self):
        alias = self.manifest_path.parent / 'alias.json'
        alias.symlink_to(self.manifest_path)
        os.environ.pop('INDEXNOW_KEY', None)
        with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected manifest read')):
            self.assert_invalid(argv=['--manifest', str(alias)])

    def test_manifest_parent_symlink_rejected_before_read(self):
        alias = self.manifest_path.parent / 'alias'
        alias.symlink_to(self.manifest_path.parent, target_is_directory=True)
        with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected manifest read')):
            self.assert_invalid(argv=['--manifest', str(alias / 'manifest.json')])

    def test_manifest_traversal_rejected_before_read(self):
        child = self.manifest_path.parent / 'child'
        child.mkdir()
        with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected manifest read')):
            self.assert_invalid(argv=['--manifest', str(child / '../manifest.json')])

    def test_oversized_manifest_rejected_before_read(self):
        raw = json.dumps(self.manifest).encode().ljust(1_000_001, b' ')
        with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected manifest read')):
            self.assert_invalid(raw=raw)

    def test_manifest_size_limit_inclusive(self):
        raw = json.dumps(self.manifest).encode().ljust(1_000_000, b' ')
        self.assertEqual(self.invoke(raw=raw)[0], 0)
        self.assert_no_network()

    def test_ownership_get_precedes_exact_post_and_received_mapping(self):
        self.outcomes = [reply(body=(" \n" + KEY + "\r\n").encode()), reply()]
        code, result = self.invoke(submit=True)
        self.assertEqual((code, result["status"]), (0, "received"))
        self.assertEqual(result["http_status"], 200)
        get, post = self.requests()
        self.assertTrue(get.startswith(f"GET /project/{KEY}.txt HTTP/1.1\r\n".encode()))
        self.assertIn(b"Host: pages.example.org\r\n", get)
        self.assertTrue(post.startswith(b"POST /indexnow HTTP/1.1\r\n"))
        self.assertIn(b"Host: api.indexnow.org\r\n", post)
        self.assertIn(b"Content-Type: application/json\r\n", post)
        body = json.loads(post.split(b"\r\n\r\n", 1)[1])
        self.assertEqual(body, {"host": "pages.example.org", "key": KEY,
                                "keyLocation": BASE + KEY + ".txt", "urlList": self.manifest["urls"]})
        self.assertEqual([c.kwargs["server_hostname"] for c in self.tls.call_args_list],
                         ["pages.example.org", "api.indexnow.org"])
        for call in self.connect.call_args_list:
            self.assertEqual(call.args[0], (PUBLIC_IP, 443))
            self.assertEqual(call.kwargs.get("timeout", call.args[1] if len(call.args) > 1 else None), 10)
        self.assertTrue(all(wire.closed for wire in self.wires))
        self.assertNotIn("indexed", json.dumps(result).lower())

    def test_post_202_is_pending_not_indexed(self):
        self.outcomes = [reply(body=KEY.encode()), reply(202)]
        code, result = self.invoke(submit=True)
        self.assertEqual((code, result["status"], result["http_status"]), (0, "pending", 202))
        self.assertNotIn("indexed", json.dumps(result).lower())

    def test_unexpected_success_status_fails_closed(self):
        self.outcomes = [reply(body=KEY.encode()), reply(204)]
        self.assertEqual(self.invoke(submit=True)[0], 1)
        self.assertEqual(self.connect.call_count, 2)

    def test_wrong_key_never_posts(self):
        for body in (b"", b"wrong-key", KEY.encode() + b"suffix", b"\xff" + KEY.encode(), b"\xef\xbb\xbf" + KEY.encode()):
            with self.subTest(body=body):
                self.outcomes = [reply(body=body)]
                code, result = self.invoke(submit=True)
                self.assertEqual((code, result["status"]), (1, "failed"))
                self.assertEqual(result["stage"], "ownership")
                self.assertTrue(self.requests()[-1].startswith(b"GET "))
        self.assertEqual(self.connect.call_count, 5)

    def test_oversized_ownership_body_fails_closed(self):
        self.outcomes = [reply(body=KEY.encode() + b" " * 10000)]
        self.assertEqual(self.invoke(submit=True)[0], 1)
        self.assertEqual(self.connect.call_count, 1)

    def test_truncated_ownership_response_never_posts(self):
        wire = WireSocket(reply(body=KEY.encode()))
        wire.response = (f"HTTP/1.1 200 OK\r\nContent-Length: {len(KEY) + 10}\r\n"
                         "Connection: close\r\n\r\n" + KEY).encode()
        self.outcomes = [wire, reply()]
        code, result = self.invoke(submit=True)
        self.assertEqual((code, result["status"], result.get("stage")), (1, "failed", "ownership"))
        self.assertEqual(self.connect.call_count, 1)
        self.assertTrue(wire.closed)

    def test_invalid_http_response_is_json_failure_without_key(self):
        wire = WireSocket(reply())
        wire.response = ("invalid-http-" + KEY + "\r\n\r\n").encode()
        self.outcomes = [wire]
        code, result = self.invoke(submit=True)
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertEqual(self.connect.call_count, 1)
        self.assertTrue(wire.closed)

    def test_tls_verification_failure_closes_socket_without_retry(self):
        self.outcomes = [reply(body=KEY.encode())]
        self.tls.side_effect = ssl.SSLCertVerificationError(KEY)
        code, result = self.invoke(submit=True)
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertEqual(self.connect.call_count, 1)
        self.assertTrue(self.wires[0].closed)

    def test_chunked_ownership_proof_is_validated(self):
        wire = WireSocket(reply())
        wire.response = ("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
                         "Connection: close\r\n\r\n"
                         f"{len(KEY):x}\r\n{KEY}\r\n0\r\n\r\n").encode()
        self.outcomes = [wire, reply(202)]
        self.assertEqual(self.invoke(submit=True)[0], 0)

    def test_unterminated_chunked_proof_fails_without_post(self):
        wire = WireSocket(reply())
        wire.response = ("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
                         "Connection: close\r\n\r\n"
                         f"{len(KEY):x}\r\n{KEY}\r\n").encode()
        self.outcomes = [wire]
        self.assertEqual(self.invoke(submit=True)[0], 1)
        self.assertEqual(self.connect.call_count, 1)

    def test_get_requires_http_200(self):
        for status in (201, 202, 204, 400, 401, 403, 404, 410):
            with self.subTest(status=status):
                self.outcomes = [reply(status, KEY.encode())]
                code, result = self.invoke(submit=True)
                self.assertEqual((code, result["status"]), (1, "failed"))
                self.assertEqual(result["stage"], "ownership")
                self.assertEqual(result["http_status"], status)
        self.assertEqual(self.connect.call_count, 8)

    def test_get_redirects_are_not_followed(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.outcomes = [reply(status, KEY.encode(), Location="https://attacker.example.org/key")]
                self.assertEqual(self.invoke(submit=True)[0], 1)
        self.assertEqual(self.connect.call_count, 5)
        self.assertTrue(all(c.args[0] == "pages.example.org" for c in self.dns.call_args_list))

    def test_post_redirects_are_not_followed(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.outcomes = [reply(body=KEY.encode()), reply(status, Location="https://attacker.example.org/")]
                self.assertEqual(self.invoke(submit=True)[0], 1)
        self.assertEqual(self.connect.call_count, 10)
        self.assertTrue(all(c.args[0] in ("pages.example.org", "api.indexnow.org") for c in self.dns.call_args_list))

    def test_get_retries_timeout_429_and_5xx_at_most_three_times(self):
        for failure in (TimeoutError("secret " + KEY), reply(429), reply(500), reply(503), reply(599)):
            with self.subTest(failure=str(failure)):
                self.connect.reset_mock()
                self.outcomes = [failure, failure, failure]
                code, result = self.invoke(submit=True)
                self.assertEqual((code, result["status"]), (1, "failed"))
                self.assertEqual(result["stage"], "ownership")
                self.assertEqual(self.connect.call_count, 3)

    def test_post_retries_timeout_429_and_5xx_at_most_three_times(self):
        for failure in (TimeoutError(KEY), reply(429), reply(500), reply(503), reply(599)):
            with self.subTest(failure=str(failure)):
                self.connect.reset_mock()
                self.outcomes = [reply(body=KEY.encode()), failure, failure, failure]
                code, result = self.invoke(submit=True)
                self.assertEqual((code, result["status"]), (1, "failed"))
                self.assertEqual(result["stage"], "submission")
                self.assertEqual(self.connect.call_count, 4)

    def test_get_recovers_on_third_attempt(self):
        self.outcomes = [TimeoutError(KEY), reply(429), reply(body=KEY.encode()), reply()]
        self.assertEqual(self.invoke(submit=True)[0], 0)
        self.assertEqual(self.connect.call_count, 4)

    def test_post_recovers_on_third_attempt(self):
        self.outcomes = [reply(body=KEY.encode()), reply(500), reply(429), reply(202)]
        code, result = self.invoke(submit=True)
        self.assertEqual((code, result["status"]), (0, "pending"))
        self.assertEqual(self.connect.call_count, 4)

    def test_permanent_post_4xx_is_not_retried(self):
        for status in (400, 401, 403, 404, 422):
            with self.subTest(status=status):
                self.connect.reset_mock()
                self.outcomes = [reply(body=KEY.encode()), reply(status, KEY.encode())]
                code, result = self.invoke(submit=True)
                self.assertEqual((code, result["status"], result["http_status"]), (1, "failed", status))
                self.assertEqual(self.connect.call_count, 2)

    def test_network_and_tls_failures_are_json_without_secrets_or_retries(self):
        for failure in (OSError(KEY), ConnectionResetError(KEY), ssl.SSLError(KEY)):
            with self.subTest(failure=type(failure).__name__):
                self.connect.reset_mock()
                self.outcomes = [failure]
                code, result = self.invoke(submit=True)
                self.assertEqual((code, result["status"]), (1, "failed"))
                self.assertIn("error", result)
                self.assertEqual(self.connect.call_count, 1)

    def test_dns_failure_is_json_without_key(self):
        self.dns.side_effect = socket.gaierror(KEY)
        code, result = self.invoke(submit=True)
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertIn("error", result)
        self.connect.assert_not_called()

    def test_private_or_mixed_dns_answers_never_connect(self):
        for ip in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "224.0.0.1", "0.0.0.0",
                   "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1", "192.0.2.1"):
            with self.subTest(ip=ip):
                family = socket.AF_INET6 if ":" in ip else socket.AF_INET
                self.dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443)),
                                         (family, socket.SOCK_STREAM, 6, "", (ip, 443))]
                self.assertEqual(self.invoke(submit=True)[0], 1)
                self.connect.assert_not_called()

    def test_fixed_api_destination_also_checks_public_dns(self):
        self.dns.side_effect = [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]]
        self.outcomes = [reply(body=KEY.encode())]
        code, result = self.invoke(submit=True)
        self.assertEqual((code, result["stage"]), (1, "submission"))
        self.assertEqual(self.connect.call_count, 1)

    def test_empty_dns_answer_fails_without_connecting(self):
        self.dns.return_value = []
        self.assertEqual(self.invoke(submit=True)[0], 1)
        self.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
