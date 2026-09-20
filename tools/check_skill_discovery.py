#!/usr/bin/env python3
"""Observe ICODE in bounded public searches; default is an offline query plan.

No credentials, installs, telemetry events, submissions or retries. These are
point-in-time query results, not a registry-wide absence or recommendation score.
Channel source/stability notes are included in both plans and observations.
"""
import argparse
from datetime import datetime, timezone
from http.client import HTTPException
import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CHANNELS = ('skillsmp', 'skills.sh', 'context7', 'skillhub', 'clawhub', 'smithery')
QUERIES = ('icode',)
MAX_BYTES = 2_000_000
MAX_QUERIES = 5
MAX_REQUESTS = 10
LIMIT = 50
TARGET_REPOSITORY = ('ayukyo', 'icode-skill')
ENDPOINTS = {
    'skillsmp': 'https://skillsmp.com/api/v1/skills/search',
    'skills.sh': 'https://skills.sh/api/search',
    'context7': 'https://context7.com/api/v2/skills',
    'skillhub': 'https://api.skillhub.cn/api/skills',
    'clawhub': 'https://clawhub.ai/api/v1/search',
    'smithery': 'https://api.smithery.ai/skills',
}
# Official docs/source inspected alongside anonymous responses on 2026-09-20.
# These references describe contracts, not proof that ICODE is listed.
CHANNEL_INFO = {
    'skillsmp': ('https://skillsmp.com/docs/api', 'Documented public keyword search; anonymous quotas apply.'),
    'skills.sh': ('https://www.skills.sh/docs/faq', 'Observed website search endpoint; no stable API promise.'),
    'context7': ('https://github.com/upstash/context7/blob/master/packages/cli/src/utils/api.ts',
                 'Observed legacy endpoint; official CLI skill commands are deprecated. No limit parameter.'),
    'skillhub': ('https://github.com/Tencent/skillhub/blob/main/docs/api/skills.md',
                 'Documented public API; upstream_url is an observed optional field, not a stable contract.'),
    'clawhub': ('https://github.com/openclaw/clawhub/blob/main/docs/api.md',
                'Documented public v1 API; ownerHandle is nullable, canonicalUrl/links are observed optional fields.'),
    'smithery': ('https://smithery.ai/docs/api-reference/skills/list-or-search-skills',
                 'Documented API requires a token; anonymous GET worked when inspected, not guaranteed.'),
}


def endpoint(channel, query):
    if channel == 'context7':
        params = {'query': query}
    elif channel == 'skillhub':
        params = {'page': 1, 'pageSize': LIMIT, 'keyword': query}
    elif channel == 'smithery':
        params = {'q': query, 'pageSize': LIMIT}
    else:
        params = {'q': query, 'limit': LIMIT}
    return ENDPOINTS[channel] + '?' + urlencode(params)


def query_plan(channel, query):
    source, stability = CHANNEL_INFO[channel]
    return {'channel': channel, 'query': query, 'url': endpoint(channel, query),
            'limit': None if channel == 'context7' else LIMIT,
            'limit_mode': 'upstream_default' if channel == 'context7' else 'requested',
            'source': source, 'stability': stability}


class BudgetExhausted(Exception):
    """Raised before sending a request beyond the shared HTTP budget."""


class RequestBudget:
    def __init__(self, limit=MAX_REQUESTS):
        self.limit = limit
        self.used = 0

    def consume(self):
        if self.used >= self.limit:
            raise BudgetExhausted(f'HTTP budget exhausted: {self.limit} requests including redirects')
        self.used += 1


class PublicRedirects(HTTPRedirectHandler):
    # Python 3.10 does not dispatch permanent redirects by default.
    http_error_308 = HTTPRedirectHandler.http_error_302

    def __init__(self, budget=None):
        self.budget = budget

    def https_request(self, request):
        # urllib runs this hook for initial requests AND accepted redirects,
        # before invoking the transport. Rejected redirects consume no request.
        if self.budget is not None:
            self.budget.consume()
        return request

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        # Only canonical www/non-www redirects on this exact public API path.
        if (new.scheme != 'https' or new.username or new.password or new.port
                or new.hostname not in {old.hostname, 'www.' + old.hostname.removeprefix('www.')}
                or new.path != old.path or new.query != old.query):
            raise ValueError('unexpected search redirect')
        return super().redirect_request(req, fp, 307 if code == 308 else code, msg, headers, newurl)


def fetch(url, budget=None):
    budget = budget if budget is not None else RequestBudget()
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'ICODE-discovery-check/1.0'})
    with build_opener(PublicRedirects(budget)).open(request, timeout=12) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError('response too large')
    return json.loads(body)


def is_icode(channel, item):
    # Validate every result, including non-matches: schema drift is not absence.
    # Other API fields may be null and are deliberately outside this contract.
    fields = ('name', 'source', 'skillId') if channel == 'skills.sh' else ('name', 'author', 'githubUrl')
    if any(not isinstance(item.get(key), str) or not item[key].strip() for key in fields):
        raise ValueError('search result identity fields changed')
    if item['name'].casefold() != 'icode':
        return False
    if channel == 'skills.sh':
        return item.get('source') == 'ayukyo/icode-skill' and item.get('skillId') == 'icode'
    url = urlsplit(item['githubUrl'])
    return (item.get('author') == 'ayukyo' and url.scheme == 'https'
            and url.netloc == 'github.com' and url.path.rstrip('/') in {
                '/ayukyo/icode-skill', '/ayukyo/icode-skill/tree/main',
                '/ayukyo/icode-skill/blob/main/SKILL.md'})


def text_field(item, key, *, nullable=False, optional=False):
    """Missing required identity keys mean drift; explicit null can mean no source."""
    if key not in item and not optional:
        raise ValueError('search result identity field missing')
    value = item.get(key)
    if value is None and (nullable or optional):
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError('search result identity field changed')
    return value


def github_repository(value):
    """Return a recognized GitHub owner/repo, or None when identity is unknown.

    Case, .git and fragments may describe the SAME repo; failure of the stricter
    match contract below must not turn those spellings into another repository.
    """
    if value is None:
        return None
    # urlsplit strips some controls; browsers may also normalize escapes/slashes.
    # Refuse ambiguous spellings instead of promoting them to source evidence.
    if any(c.isspace() or ord(c) < 32 or c in '%\\' for c in value):
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme != 'https' or url.query:
        return None
    parts = url.path.removeprefix('/').removesuffix('/').split('/')
    if len(parts) < 2 or any(p in ('', '.', '..') for p in parts):
        return None
    if url.netloc == 'github.com':
        if len(parts) != 2 and not (len(parts) >= 4 and parts[2] in ('tree', 'blob')):
            return None
    elif url.netloc != 'raw.githubusercontent.com' or len(parts) < 4:
        return None
    owner, repo = parts[0].casefold(), parts[1].casefold().removesuffix('.git')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', owner) or not re.fullmatch(r'[a-z0-9_.-]+', repo):
        return None
    return owner, repo


def github_source(value):
    """Only the original exact source spelling proves a match; never follow it."""
    if github_repository(value) != TARGET_REPOSITORY:
        return False
    url = urlsplit(value)
    return not url.fragment and url.path.split('/')[1:3] == list(TARGET_REPOSITORY)


def classify(channel, item):
    # Preserve the original channels' strict identity-drift checks.
    if channel in ('skillsmp', 'skills.sh'):
        return 'matched' if is_icode(channel, item) else 'not_in_results'
    if channel == 'context7':
        name = text_field(item, 'name')
        project = text_field(item, 'project', nullable=True)
        source = text_field(item, 'url', nullable=True)
        target = name.casefold() == 'icode'
        matches = (project in (None, '/ayukyo/icode-skill') and github_source(source))
        project_repo = github_repository('https://github.com' + project) if project and project.startswith('/') else None
        known_other = project_repo is not None and project_repo != TARGET_REPOSITORY
    elif channel == 'skillhub':
        name, slug = text_field(item, 'name'), text_field(item, 'slug')
        text_field(item, 'source')
        text_field(item, 'ownerName')
        source = text_field(item, 'upstream_url', optional=True)
        target = name.casefold() == 'icode' or slug == 'icode'
        matches = slug == 'icode' and github_source(source)
        known_other = False
    elif channel == 'smithery':
        text_field(item, 'namespace')
        name, slug = text_field(item, 'displayName'), text_field(item, 'slug')
        source = text_field(item, 'gitUrl', nullable=True)
        target = name.casefold() == 'icode' or slug == 'icode'
        matches = slug == 'icode' and github_source(source)
        known_other = False
    else:  # ClawHub allows missing/null owner information in its search schema.
        name, slug = text_field(item, 'displayName'), text_field(item, 'slug')
        text_field(item, 'ownerHandle', optional=True)
        text_field(item, 'source', optional=True)
        text_field(item, 'canonicalUrl', optional=True)
        links = item.get('links')
        if links is not None and not isinstance(links, dict):
            raise ValueError('search result links changed')
        source = text_field(links or {}, 'source', optional=True)
        target = name.casefold() == 'icode' or slug == 'icode'
        # Platform handles/canonical URLs do not authenticate a GitHub owner.
        matches = slug == 'icode' and github_source(source)
        known_other = False
    source_repo = github_repository(source)
    known_other = known_other or (source_repo is not None and source_repo != TARGET_REPOSITORY)
    if not target:
        return 'not_in_results'
    if matches:
        return 'matched'
    return 'not_in_results' if known_other else 'candidate_unverified'


def search_rows(channel, payload):
    if not isinstance(payload, dict):
        raise ValueError('response object expected')
    if payload.get('error') or payload.get('success', True) is not True:
        raise ValueError('search returned an error envelope')
    if channel == 'skillsmp':
        if payload.get('success') is not True or not isinstance(payload.get('data'), dict):
            raise ValueError('SkillsMP response changed')
        payload = payload['data']
    elif channel == 'skillhub':
        if type(payload.get('code')) is not int or payload['code'] != 0 or not isinstance(payload.get('data'), dict):
            raise ValueError('SkillHub response changed or failed')
        payload = payload['data']
    key = 'results' if channel in ('context7', 'clawhub') else 'skills'
    rows = payload.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('search result array expected')
    return rows


def observe(channel, query, budget=None):
    result = query_plan(channel, query)
    try:
        rows = search_rows(channel, fetch(result['url'], budget=budget))
        # Validate the entire page before declaring success, including nonmatches.
        identities = [classify(channel, row) for row in rows]
        positions = [n for n, state in enumerate(identities, 1) if state == 'matched']
        candidates = [n for n, state in enumerate(identities, 1) if state == 'candidate_unverified']
        result.update(status='matched' if positions else 'candidate_unverified' if candidates else 'not_in_results',
                      returned=len(rows), positions=positions, candidate_positions=candidates)
    except BudgetExhausted as exc:
        result.update(status='budget_exhausted', reason=str(exc))
    except HTTPError as exc:
        result.update(status={401: 'auth_required', 403: 'auth_required', 429: 'rate_limited'}.get(exc.code, 'unavailable'),
                      http_status=exc.code)
    except (URLError, OSError, socket.timeout, HTTPException):
        result['status'] = 'unavailable'
    except (ValueError, TypeError):
        result['status'] = 'invalid_response'
    return result


def main(argv=None, budget=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true', help='make anonymous public GET requests')
    parser.add_argument('--channel', choices=(*CHANNELS, 'all'), default='all')
    parser.add_argument('--query', action='append', help='up to five public search phrases; 10 HTTP requests including redirects (default: icode)')
    args = parser.parse_args(argv)
    queries = args.query or QUERIES
    if len(queries) > MAX_QUERIES or any(not q.strip() or len(q) > 128 or any(ord(c) < 32 for c in q) for q in queries):
        parser.error('use 1–5 nonempty public phrases of at most 128 characters')
    channels = CHANNELS if args.channel == 'all' else (args.channel,)
    if len(channels) * len(queries) > MAX_REQUESTS:
        parser.error('at most 10 search requests per run; split queries into separate --channel runs')
    plan = [query_plan(c, q) for c in channels for q in queries]
    report = {'status': 'observed' if args.online else 'dry-run',
              'checked_at': datetime.now(timezone.utc).isoformat(),
              'boundary': 'Only these bounded queries; not proof of ranking, recommendation, installation or global absence.'}
    budget = budget if budget is not None else RequestBudget()
    report['queries'] = [observe(item['channel'], item['query'], budget=budget) for item in plan] if args.online else plan
    report['http_budget'] = {'limit': budget.limit, 'used': budget.used}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Candidates/misses are valid observations, not verified discovery claims.
    return int(args.online and any(r['status'] not in {'matched', 'not_in_results', 'candidate_unverified'}
                                   for r in report['queries']))


if __name__ == '__main__':
    raise SystemExit(main())
