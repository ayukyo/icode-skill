#!/usr/bin/env python3
"""Observe ICODE in bounded public searches; default is an offline query plan.

No credentials, installs, telemetry events, submissions or retries. These are
point-in-time query results, not a registry-wide absence or recommendation score.
The skills.sh search route is a public website endpoint, not a stable API promise.
"""
import argparse
from datetime import datetime, timezone
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CHANNELS = ('skillsmp', 'skills.sh')
QUERIES = ('icode', 'AI coding workflow', 'code review', '工单')
MAX_BYTES = 2_000_000
LIMIT = 50
ENDPOINTS = {
    'skillsmp': 'https://skillsmp.com/api/v1/skills/search',
    'skills.sh': 'https://skills.sh/api/search',
}


def endpoint(channel, query):
    return ENDPOINTS[channel] + '?' + urlencode({'q': query, 'limit': LIMIT})


class PublicRedirects(HTTPRedirectHandler):
    # Python 3.10 does not dispatch permanent redirects by default.
    http_error_308 = HTTPRedirectHandler.http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        # Only canonical www/non-www redirects on this exact public API path.
        if (new.scheme != 'https' or new.username or new.password or new.port
                or new.hostname not in {old.hostname, 'www.' + old.hostname.removeprefix('www.')}
                or new.path != old.path):
            raise ValueError('unexpected search redirect')
        return super().redirect_request(req, fp, 307 if code == 308 else code, msg, headers, newurl)


def fetch(url):
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'ICODE-discovery-check/1.0'})
    with build_opener(PublicRedirects()).open(request, timeout=12) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError('response too large')
    return json.loads(body)


def is_icode(channel, item):
    if str(item.get('name', '')).casefold() != 'icode':
        return False
    if channel == 'skills.sh':
        return item.get('source') == 'ayukyo/icode-skill' and item.get('skillId') == 'icode'
    url = urlsplit(str(item.get('githubUrl', '')))
    return (item.get('author') == 'ayukyo' and url.scheme == 'https'
            and url.netloc == 'github.com' and url.path.rstrip('/') in {
                '/ayukyo/icode-skill', '/ayukyo/icode-skill/tree/main',
                '/ayukyo/icode-skill/blob/main/SKILL.md'})


def observe(channel, query):
    result = {'channel': channel, 'query': query, 'url': endpoint(channel, query), 'limit': LIMIT}
    try:
        payload = fetch(result['url'])
        if not isinstance(payload, dict):
            raise ValueError('response object expected')
        if channel == 'skillsmp':
            if payload.get('success') is not True or not isinstance(payload.get('data'), dict):
                raise ValueError('SkillsMP response changed')
            payload = payload['data']
        rows = payload.get('skills')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('search result array expected')
        positions = [n for n, row in enumerate(rows, 1) if is_icode(channel, row)]
        result.update(status='matched' if positions else 'not_in_results', returned=len(rows), positions=positions)
    except HTTPError as exc:
        result.update(status={401: 'auth_required', 403: 'auth_required', 429: 'rate_limited'}.get(exc.code, 'unavailable'),
                      http_status=exc.code)
    except (URLError, OSError, socket.timeout):
        result['status'] = 'unavailable'
    except (ValueError, TypeError):
        result['status'] = 'invalid_response'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true', help='make anonymous public GET requests')
    parser.add_argument('--channel', choices=(*CHANNELS, 'all'), default='all')
    parser.add_argument('--query', action='append', help='up to five public search phrases (default: four common uses)')
    args = parser.parse_args(argv)
    queries = args.query or QUERIES
    if len(queries) > 5 or any(not q.strip() or len(q) > 128 or any(ord(c) < 32 for c in q) for q in queries):
        parser.error('use 1–5 nonempty public phrases of at most 128 characters')
    channels = CHANNELS if args.channel == 'all' else (args.channel,)
    plan = [{'channel': c, 'query': q, 'url': endpoint(c, q)} for c in channels for q in queries]
    report = {'status': 'observed' if args.online else 'dry-run',
              'checked_at': datetime.now(timezone.utc).isoformat(),
              'boundary': 'Only these bounded queries; not proof of ranking, recommendation, installation or global absence.'}
    report['queries'] = [observe(item['channel'], item['query']) for item in plan] if args.online else plan
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # A miss is a valid observation. Infrastructure/schema errors are not misses.
    return int(args.online and any(r['status'] not in {'matched', 'not_in_results'} for r in report['queries']))


if __name__ == '__main__':
    raise SystemExit(main())
