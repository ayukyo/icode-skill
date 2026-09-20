#!/usr/bin/env python3
"""Bounded IndexNow notification for the public manifest's two known pages.

Usage: python3 tools/notify_indexnow.py --manifest FILE [--submit]
INDEXNOW_KEY is optional. The default is offline dry-run, including no DNS.
Missing/empty keys skip submission, but the manifest must still be valid.

Exit codes: 0 = dry-run/skipped/received/pending; 1 = ownership/network/HTTP
failure; 2 = invalid arguments, key, or manifest. Results are one JSON object
on stdout (except --help); inputs, keys, response bodies, and exception text
are never logged. Received/pending do not establish search-engine indexing.

Each GET or POST has at most three attempts, with a 10-second socket timeout.
Only timeouts, HTTP 429, and HTTP 5xx are retried. Redirects are never followed.
The OS resolver has its own timeout; 10 seconds is not a whole-process deadline.
"""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
from urllib.parse import urlsplit


API_HOST = "api.indexnow.org"
API_PATH = "/indexnow"
TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 3
MAX_URLS = 10000
MAX_PROOF_BYTES = 4096
MAX_MANIFEST_BYTES = 1_000_000
KEY_PATTERN = re.compile(r"[A-Za-z0-9-]{8,128}")
DNS_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
PATH_PATTERN = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@/-]*")
LOCAL_SUFFIXES = {"localhost", "local", "localdomain", "internal", "lan", "home", "test",
                  "invalid", "example", "onion", "alt", "arpa"}


class InputError(ValueError):
    """A fixed error code, safe to emit without exposing input values."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes user arguments; those may contain a key.
        raise InputError("invalid_arguments")


def _reject_json_constant(value: str):
    # Python otherwise accepts NaN/Infinity, which are not valid JSON values.
    raise InputError("invalid_json_constant")


def read_manifest(value: str) -> object:
    """Reject unsafe paths and oversized files before reading manifest content."""
    path = Path(value)
    # Do not normalize away traversal or follow a linked file/parent directory.
    if '..' in path.parts:
        raise InputError('unsafe_manifest_path')
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise InputError('unsafe_manifest_path')
    if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise InputError('invalid_manifest_file')
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=_reject_json_constant)


def validate_manifest(manifest: object) -> tuple[str, str, list[str]]:
    """Validate offline, returning the exact base URL, DNS host, and page list."""
    if not isinstance(manifest, dict):
        raise InputError("invalid_manifest")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise InputError("invalid_schema_version")
    base = manifest.get("base_url")
    if (not isinstance(base, str) or not base.startswith("https://")
            or not base.endswith("/") or not base.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in base)
            or any(char in base for char in "\\%?#")):
        raise InputError("invalid_base_url")
    try:
        parsed = urlsplit(base)
    except ValueError:
        raise InputError("invalid_base_url") from None
    # A DNS-only authority excludes credentials, every port spelling, and IPv6.
    authority = parsed.netloc
    labels = authority.split(".")
    if (len(authority) > 253 or len(labels) < 2
            or not all(DNS_LABEL_PATTERN.fullmatch(label) for label in labels)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", labels[-1])
            or labels[-1].lower() in LOCAL_SUFFIXES):
        raise InputError("invalid_public_host")
    path = parsed.path
    if (not PATH_PATTERN.fullmatch(path) or "//" in path
            or any(segment in (".", "..") for segment in path.split("/"))):
        raise InputError("invalid_base_path")
    urls = manifest.get("urls")
    allowed = {base, base + "en/"}
    if (not isinstance(urls, list) or not 1 <= len(urls) <= MAX_URLS
            or any(not isinstance(url, str) or url not in allowed for url in urls)
            or len(set(urls)) != len(urls)):
        raise InputError("invalid_urls")
    return base, authority.lower(), urls


def _connect_public(address, timeout=TIMEOUT_SECONDS, source_address=None):
    """Resolve once, reject non-public answers, and connect to a checked IP.

    Pinning the numeric address prevents a second hostname lookup from changing
    the destination after validation. HTTPSConnection still verifies TLS and SNI
    against the original DNS hostname. Proxy environment variables are unused.
    """
    host, port = address
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers:
        raise OSError("empty_dns_answer")
    for answer in answers:
        try:
            ip = ipaddress.ip_address(answer[4][0])
        except ValueError:
            raise OSError("invalid_dns_answer") from None
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise OSError("non_public_dns_answer")
    # One connection per HTTP attempt keeps retries bounded even for many A/AAAA
    # records. An unavailable first address can therefore cause a safe failure.
    return socket.create_connection((answers[0][4][0], port), timeout=timeout,
                                    source_address=source_address)


def request(method: str, host: str, path: str,
            body: bytes | None = None) -> tuple[int | None, bytes, str | None]:
    """Return (status, proof, error); never return raw network error text."""
    for attempt in range(MAX_ATTEMPTS):
        connection = http.client.HTTPSConnection(host, timeout=TIMEOUT_SECONDS)
        # Keep the standard library's TLS handshake and certificate validation.
        connection._create_connection = _connect_public
        try:
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=body, headers=headers)
            with connection.getresponse() as response:
                status = response.status
                if status == 429 or 500 <= status <= 599:
                    if attempt + 1 < MAX_ATTEMPTS:
                        continue
                # No need to read arbitrary server bodies except ownership proof.
                proof = b""
                if method == "GET" and status == 200:
                    proof = response.read(MAX_PROOF_BYTES + 1)
                    # read(size) does not raise on early EOF with Content-Length;
                    # an incomplete proof must never authorize the POST.
                    if response.length not in (None, 0):
                        return status, b"", "incomplete_ownership_response"
                return status, proof, None
        except TimeoutError:
            if attempt + 1 == MAX_ATTEMPTS:
                return None, b"", "network_timeout"
        except (OSError, http.client.HTTPException):
            return None, b"", "network_error"
        finally:
            connection.close()


def notify(base: str, host: str, urls: list[str], key: str) -> tuple[int, dict[str, object]]:
    key_location = base + key + ".txt"
    status, proof, error = request("GET", host, urlsplit(key_location).path)
    if error or status != 200:
        return 1, {"status": "failed", "stage": "ownership",
                   "error": error or "ownership_http_status", "http_status": status}
    if len(proof) > MAX_PROOF_BYTES or proof.strip() != key.encode("ascii"):
        return 1, {"status": "failed", "stage": "ownership", "error": "ownership_key_mismatch"}
    payload = json.dumps({"host": host, "key": key, "keyLocation": key_location,
                          "urlList": urls}).encode("utf-8")
    status, _, error = request("POST", API_HOST, API_PATH, payload)
    if error or status not in (200, 202):
        return 1, {"status": "failed", "stage": "submission",
                   "error": error or "submission_http_status", "http_status": status}
    return 0, {"status": "received" if status == 200 else "pending",
               "http_status": status, "url_count": len(urls)}


def main(argv=None) -> int:
    parser = JsonArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--manifest", required=True, help="Public manifest JSON file")
    parser.add_argument("--submit", action="store_true", help="Permit ownership GET and IndexNow POST")
    try:
        args = parser.parse_args(argv)
        try:
            manifest = read_manifest(args.manifest)
        except (OSError, ValueError, RecursionError):
            raise InputError("unreadable_or_malformed_manifest") from None
        base, host, urls = validate_manifest(manifest)
        key = os.environ.get("INDEXNOW_KEY", "")
        if not key:
            code, result = 0, {"status": "skipped", "reason": "missing_key"}
        elif not KEY_PATTERN.fullmatch(key):
            raise InputError("invalid_key")
        elif not args.submit:
            code, result = 0, {"status": "dry-run", "url_count": len(urls)}
        else:
            code, result = notify(base, host, urls, key)
    except InputError as error:
        code, result = 2, {"status": "error", "error": str(error)}
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
