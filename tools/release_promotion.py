#!/usr/bin/env python3
"""Build and optionally publish a bounded ICODE release promotion plan.

The default mode is offline. Network writes require the explicit ``publish
--submit`` combination. Release notes are intentionally not copied into public
posts: only validated release identity and static, reviewed project wording are
used. Credentials are read from the environment and never included in output.

Exit codes: 0 = planned/dry-run/skipped/published; 1 = channel failure;
2 = invalid input or arguments.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_INPUT_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 1_000_000
TIMEOUT_SECONDS = 15
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
TAG_PATTERN = re.compile(r"v[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9.-]+)?")
DNS_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
CHANNELS = ("github", "bluesky", "mastodon")


class InputError(ValueError):
    """Safe, fixed-code input failure."""


class ChannelError(RuntimeError):
    """Safe, fixed-code channel failure."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("invalid_arguments")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _reject_json_constant(value: str):
    raise InputError("invalid_json_constant")


def read_json(path_value: str) -> object:
    path = Path(path_value)
    if ".." in path.parts:
        raise InputError("unsafe_input_path")
    path = path.absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise InputError("unsafe_input_path")
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise InputError("invalid_input_file")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise InputError("unreadable_or_malformed_json") from None


def write_json_atomic(path_value: str, value: object) -> None:
    path = Path(path_value).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _validate_public_base_url(value: object, *, allow_path: bool = True) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.endswith("/"):
        raise InputError("invalid_public_url")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise InputError("invalid_public_url") from None
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
            or port is not None or not parsed.hostname or parsed.query or parsed.fragment):
        raise InputError("invalid_public_url")
    hostname = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.split(".")
        if (len(labels) < 2 or any(not DNS_PATTERN.fullmatch(label) for label in labels)
                or labels[-1].lower() in {"local", "localhost", "internal", "test", "invalid"}):
            raise InputError("invalid_public_url") from None
    else:
        if not address.is_global:
            raise InputError("invalid_public_url")
    if not allow_path and parsed.path != "/":
        raise InputError("invalid_public_url")
    if "//" in parsed.path or any(part in (".", "..") for part in parsed.path.split("/")):
        raise InputError("invalid_public_url")
    return value


def _validate_release(release: object, repository: str) -> dict[str, str]:
    if not isinstance(release, dict):
        raise InputError("invalid_release")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise InputError("non_final_release")
    tag = release.get("tag_name")
    url = release.get("html_url")
    published_at = release.get("published_at")
    if not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag):
        raise InputError("invalid_release_tag")
    expected_url = f"https://github.com/{repository}/releases/tag/{quote(tag, safe='.-_~')}"
    if url != expected_url:
        raise InputError("invalid_release_url")
    if not isinstance(published_at, str) or not published_at.endswith("Z"):
        raise InputError("invalid_published_at")
    try:
        moment = datetime.fromisoformat(published_at[:-1] + "+00:00")
    except ValueError:
        raise InputError("invalid_published_at") from None
    if moment.utcoffset() is None:
        raise InputError("invalid_published_at")
    return {"tag": tag, "url": url, "published_at": published_at}


def build_plan(release: object, repository: str, site_url: str) -> dict[str, Any]:
    """Create deterministic, reviewed posts without copying release body text."""
    if not isinstance(repository, str) or not REPOSITORY_PATTERN.fullmatch(repository):
        raise InputError("invalid_repository")
    site_url = _validate_public_base_url(site_url)
    identity = _validate_release(release, repository)
    seed = f"{repository}\n{identity['tag']}\n{identity['published_at']}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    campaign_id = digest[:24]
    tag = identity["tag"]
    release_url = identity["url"]
    short_text = (
        f"ICODE {tag} 已发布 / is available — 面向 Claude Code、Codex、CodeBuddy "
        f"和 WorkBuddy 的工单驱动 AI 编码工作流。\n{site_url}\n{release_url}"
    )
    github_title = f"ICODE {tag} 已发布 / is available"
    marker = f"<!-- icode-promotion:{campaign_id} -->"
    github_body = (
        f"{marker}\n\n"
        f"ICODE **{tag}** 已正式发布。它提供面向 Claude Code、Codex、CodeBuddy "
        f"和 WorkBuddy 的工单驱动 AI 编码工作流。\n\n"
        f"- 官网 / Website: {site_url}\n"
        f"- Release: {release_url}\n\n"
        "本公告只陈述已发布版本及公开能力，不代表搜索排名、安装量或所有宿主均已完成实机验证。"
    )
    if len(short_text) > 300:
        raise InputError("promotion_text_too_long")
    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "repository": repository,
        "site_url": site_url,
        "release": identity,
        "channels": {
            "github": {"title": github_title, "body": github_body, "marker": marker},
            "bluesky": {
                "text": short_text,
                "created_at": identity["published_at"],
                "rkey": f"icode-{digest[:24]}",
            },
            "mastodon": {
                "status": short_text,
                "idempotency_key": f"icode-{digest[:32]}",
            },
        },
    }


def validate_plan(plan: object) -> dict[str, Any]:
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise InputError("invalid_plan")
    repository = plan.get("repository")
    site_url = plan.get("site_url")
    release = plan.get("release")
    if not isinstance(release, dict):
        raise InputError("invalid_plan")
    rebuilt_release = {
        "tag_name": release.get("tag"), "html_url": release.get("url"),
        "published_at": release.get("published_at"), "draft": False, "prerelease": False,
    }
    expected = build_plan(rebuilt_release, repository, site_url)
    if plan != expected:
        raise InputError("plan_integrity_mismatch")
    return plan


def http_json(method: str, url: str, *, headers: dict[str, str] | None = None,
              payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Perform one bounded JSON request without following redirects."""
    safe_headers = {"Accept": "application/json", "User-Agent": "icode-release-promotion/1"}
    safe_headers.update(headers or {})
    body = None
    if payload is not None:
        if safe_headers.get("Content-Type") == "application/x-www-form-urlencoded":
            body = urlencode(payload).encode("utf-8")
        else:
            safe_headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers=safe_headers, method=method)
    try:
        with build_opener(NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
    except HTTPError as error:
        raise ChannelError(f"http_{error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise ChannelError("network_error") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ChannelError("response_too_large")
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeError, ValueError):
        raise ChannelError("invalid_json_response") from None
    if not isinstance(value, dict):
        raise ChannelError("invalid_json_response")
    return status, value


def _publish_github(plan: dict[str, Any]) -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"channel": "github", "status": "skipped", "reason": "missing_credentials"}
    owner, name = plan["repository"].split("/", 1)
    query = """query($owner:String!,$name:String!){repository(owner:$owner,name:$name){id discussionCategories(first:50){nodes{id name}} discussions(first:100,orderBy:{field:CREATED_AT,direction:DESC}){nodes{url title body}}}}"""
    headers = {"Authorization": f"Bearer {token}"}
    status, response = http_json("POST", "https://api.github.com/graphql", headers=headers,
                                 payload={"query": query, "variables": {"owner": owner, "name": name}})
    if status != 200 or response.get("errors"):
        raise ChannelError("github_query_failed")
    try:
        repository = response["data"]["repository"]
        categories = repository["discussionCategories"]["nodes"]
        discussions = repository["discussions"]["nodes"]
    except (KeyError, TypeError):
        raise ChannelError("github_response_invalid") from None
    marker = plan["channels"]["github"]["marker"]
    for discussion in discussions:
        if isinstance(discussion, dict) and marker in str(discussion.get("body", "")):
            return {"channel": "github", "status": "existing", "url": str(discussion.get("url", ""))}
    requested = os.environ.get("GITHUB_DISCUSSION_CATEGORY", "Announcements")
    category = next((item for item in categories
                     if isinstance(item, dict) and str(item.get("name", "")).casefold() == requested.casefold()), None)
    if not category:
        raise ChannelError("github_category_not_found")
    mutation = """mutation($repositoryId:ID!,$categoryId:ID!,$title:String!,$body:String!){createDiscussion(input:{repositoryId:$repositoryId,categoryId:$categoryId,title:$title,body:$body}){discussion{url}}}"""
    channel = plan["channels"]["github"]
    variables = {"repositoryId": repository["id"], "categoryId": category["id"],
                 "title": channel["title"], "body": channel["body"]}
    status, response = http_json("POST", "https://api.github.com/graphql", headers=headers,
                                 payload={"query": mutation, "variables": variables})
    if status != 200 or response.get("errors"):
        raise ChannelError("github_publish_failed")
    try:
        url = response["data"]["createDiscussion"]["discussion"]["url"]
    except (KeyError, TypeError):
        raise ChannelError("github_response_invalid") from None
    return {"channel": "github", "status": "published", "url": str(url)}


def _publish_bluesky(plan: dict[str, Any]) -> dict[str, str]:
    handle = os.environ.get("BSKY_HANDLE", "")
    password = os.environ.get("BSKY_APP_PASSWORD", "")
    if not handle or not password:
        return {"channel": "bluesky", "status": "skipped", "reason": "missing_credentials"}
    service = "https://bsky.social/xrpc/"
    status, session = http_json("POST", service + "com.atproto.server.createSession",
                                payload={"identifier": handle, "password": password})
    token, did = session.get("accessJwt"), session.get("did")
    if status != 200 or not isinstance(token, str) or not isinstance(did, str):
        raise ChannelError("bluesky_auth_failed")
    channel = plan["channels"]["bluesky"]
    record = {"$type": "app.bsky.feed.post", "text": channel["text"],
              "createdAt": channel["created_at"]}
    payload = {"repo": did, "collection": "app.bsky.feed.post",
               "rkey": channel["rkey"], "record": record, "validate": True}
    status, response = http_json("POST", service + "com.atproto.repo.putRecord",
                                 headers={"Authorization": f"Bearer {token}"}, payload=payload)
    if status != 200 or not isinstance(response.get("uri"), str):
        raise ChannelError("bluesky_publish_failed")
    url = f"https://bsky.app/profile/{quote(handle, safe='.-_~')}/post/{channel['rkey']}"
    return {"channel": "bluesky", "status": "published", "url": url}


def _publish_mastodon(plan: dict[str, Any]) -> dict[str, str]:
    base = os.environ.get("MASTODON_BASE_URL", "")
    token = os.environ.get("MASTODON_ACCESS_TOKEN", "")
    if not base or not token:
        return {"channel": "mastodon", "status": "skipped", "reason": "missing_credentials"}
    base = _validate_public_base_url(base, allow_path=False)
    channel = plan["channels"]["mastodon"]
    headers = {"Authorization": f"Bearer {token}",
               "Idempotency-Key": channel["idempotency_key"],
               "Content-Type": "application/x-www-form-urlencoded"}
    status, response = http_json("POST", base + "api/v1/statuses", headers=headers,
                                 payload={"status": channel["status"], "visibility": "public"})
    if status != 200 or not isinstance(response.get("id"), str):
        raise ChannelError("mastodon_publish_failed")
    result = {"channel": "mastodon", "status": "published"}
    if isinstance(response.get("url"), str) and response["url"].startswith(base):
        result["url"] = response["url"]
    return result


PUBLISHERS = {"github": _publish_github, "bluesky": _publish_bluesky,
              "mastodon": _publish_mastodon}


def publish(plan_value: object, channels: list[str], *, submit: bool) -> tuple[int, dict[str, Any]]:
    plan = validate_plan(plan_value)
    if not channels or len(channels) != len(set(channels)) or any(item not in CHANNELS for item in channels):
        raise InputError("invalid_channels")
    receipts: list[dict[str, str]] = []
    if not submit:
        receipts = [{"channel": channel, "status": "dry-run"} for channel in channels]
        return 0, {"schema_version": 1, "campaign_id": plan["campaign_id"],
                   "status": "dry-run", "channels": receipts}
    for channel in channels:
        try:
            receipts.append(PUBLISHERS[channel](plan))
        except (ChannelError, InputError) as error:
            receipts.append({"channel": channel, "status": "failed", "error": str(error)})
    statuses = {item["status"] for item in receipts}
    if "failed" in statuses:
        overall, code = ("partial" if statuses - {"failed"} else "failed"), 1
    elif statuses <= {"skipped"}:
        overall, code = "skipped", 0
    else:
        overall, code = "published", 0
    return code, {"schema_version": 1, "campaign_id": plan["campaign_id"],
                  "status": overall, "channels": receipts}


def _release_from_args(args) -> object:
    source = read_json(args.event or args.release_json)
    if args.event:
        if not isinstance(source, dict) or "release" not in source:
            raise InputError("invalid_release_event")
        return source["release"]
    return source


def _parse_channels(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main(argv=None) -> int:
    parser = JsonArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", allow_abbrev=False)
    sources = plan_parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--event")
    sources.add_argument("--release-json")
    plan_parser.add_argument("--repository", required=True)
    plan_parser.add_argument("--site-url", required=True)
    plan_parser.add_argument("--output", required=True)
    publish_parser = subparsers.add_parser("publish", allow_abbrev=False)
    publish_parser.add_argument("--plan", required=True)
    publish_parser.add_argument("--channels", default=",".join(CHANNELS))
    publish_parser.add_argument("--submit", action="store_true")
    publish_parser.add_argument("--output")
    try:
        args = parser.parse_args(argv)
        if args.command == "plan":
            result = build_plan(_release_from_args(args), args.repository, args.site_url)
            write_json_atomic(args.output, result)
            summary = {"status": "planned", "campaign_id": result["campaign_id"],
                       "output": str(Path(args.output).name)}
            code = 0
        else:
            code, summary = publish(read_json(args.plan), _parse_channels(args.channels),
                                    submit=args.submit)
            if args.output:
                write_json_atomic(args.output, summary)
    except InputError as error:
        code, summary = 2, {"status": "error", "error": str(error)}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
