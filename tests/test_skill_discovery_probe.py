"""Bounded read-only observations, never synthetic installs or ranking claims."""
import importlib.util
from copy import deepcopy
from email.message import Message
from http.client import HTTPException, HTTPResponse
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, build_opener as urllib_build_opener
from urllib.response import addinfourl

ROOT = Path(__file__).resolve().parents[1]

# Independent, reduced public-response fixtures observed anonymously 2026-09-20.
# Only parser-relevant fields retained; no user IDs, descriptions or images.
PUBLIC_ROWS = {
    'context7': {'name': 'context7-mcp', 'project': '/upstash/context7',
                 'url': 'https://raw.githubusercontent.com/upstash/context7/refs/heads/master/plugins/claude/context7/skills/context7-mcp/SKILL.md'},
    'skillhub': {'name': 'slide-maker', 'slug': 'slides-maker', 'source': 'community',
                 'ownerName': 'fixture-owner', 'upstream_url': None,
                 'upstream_owner_login': None, 'tags': None},
    'clawhub': {'slug': 'smart-code-review', 'displayName': 'Code Review',
                'ownerHandle': 'caingao', 'source': 'clawhub',
                'canonicalUrl': '/caingao/skills/smart-code-review',
                'links': {'source': None}, 'version': None},
    'smithery': {'namespace': 'bgauryy', 'slug': 'octocode-local-search',
                 'displayName': 'octocode-local-search',
                 'gitUrl': 'https://github.com/bgauryy/octocode-mcp/tree/main/packages/octocode-cli/skills/octocode-local-search'},
}

# Synthetic positive identities test contracts; they are NOT live listing proof.
MATCH_ROWS = {
    'context7': {'name': 'icode', 'project': '/ayukyo/icode-skill',
                 'url': 'https://raw.githubusercontent.com/ayukyo/icode-skill/refs/heads/main/SKILL.md'},
    'skillhub': {'name': 'icode', 'slug': 'icode', 'source': 'community',
                 'ownerName': 'fixture-owner',
                 'upstream_url': 'https://github.com/ayukyo/icode-skill'},
    'clawhub': {'slug': 'icode', 'displayName': 'ICODE', 'source': 'clawhub',
                'ownerHandle': 'ayukyo', 'canonicalUrl': '/ayukyo/skills/icode',
                'links': {'source': 'https://github.com/ayukyo/icode-skill'}},
    'smithery': {'namespace': 'ayukyo', 'slug': 'icode', 'displayName': 'ICODE',
                 'gitUrl': 'https://github.com/ayukyo/icode-skill/tree/main'},
}


def envelope(channel, rows):
    if channel == 'skillhub':
        return {'code': 0, 'message': 'success', 'data': {'skills': rows, 'total': len(rows)}}
    if channel in ('context7', 'clawhub'):
        return {'results': rows}
    return {'skills': rows, 'pagination': {'currentPage': 1, 'pageSize': 50}}


class RedirectTransport(HTTPSHandler):
    """Replace only the transport; urllib must process every 308 itself."""
    def __init__(self, redirects=1):
        super().__init__()
        self.redirects = redirects
        self.requests = []
        self.visits = {}

    def https_open(self, request):
        self.requests.append(request.full_url)
        url = urlsplit(request.full_url)
        host = url.hostname.removeprefix('www.')
        self.visits[host] = self.visits.get(host, 0) + 1
        headers = Message()
        if self.visits[host] <= self.redirects:
            headers['Location'] = url._replace(netloc='www.' + host).geturl()
            response = addinfourl(io.BytesIO(b''), headers, request.full_url, 308)
            response.msg = 'Permanent Redirect'
        else:
            pages = {
                'skillsmp.com': {'success': True, 'data': {'skills': []}},
                'skills.sh': {'skills': []},
                'context7.com': {'results': []},
                'api.skillhub.cn': {'code': 0, 'data': {'skills': []}},
                'clawhub.ai': {'results': []},
                'api.smithery.ai': {'skills': []},
            }
            headers['Content-Type'] = 'application/json'
            response = addinfourl(io.BytesIO(json.dumps(pages[host]).encode()), headers, request.full_url, 200)
            response.msg = 'OK'
        return response


class ProbeTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('probe', ROOT / 'tools/check_skill_discovery.py')
        self.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.probe)

    def test_default_is_zero_network_query_plan(self):
        with patch.object(self.probe, 'fetch', side_effect=AssertionError('offline')), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(self.probe.main([]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report['status'], 'dry-run')
        self.assertEqual(len(report['queries']), 6)
        self.assertEqual({r['query'] for r in report['queries']}, {'icode'})
        self.assertEqual({r['channel'] for r in report['queries']},
                         {'skillsmp', 'skills.sh', 'context7', 'skillhub', 'clawhub', 'smithery'})

    def test_new_channels_use_verified_query_parameters(self):
        expected = {
            'context7': ('context7.com', '/api/v2/skills', {'query': ['工单 & code']}),
            'skillhub': ('api.skillhub.cn', '/api/skills',
                         {'keyword': ['工单 & code'], 'page': ['1'], 'pageSize': ['50']}),
            'clawhub': ('clawhub.ai', '/api/v1/search', {'q': ['工单 & code'], 'limit': ['50']}),
            'smithery': ('api.smithery.ai', '/skills', {'q': ['工单 & code'], 'pageSize': ['50']}),
        }
        for channel, (host, path, params) in expected.items():
            with self.subTest(channel=channel):
                self.assertIn(channel, self.probe.CHANNELS)
                url = urlsplit(self.probe.endpoint(channel, '工单 & code'))
                self.assertEqual((url.scheme, url.netloc, url.path), ('https', host, path))
                self.assertEqual(parse_qs(url.query), params)

    def test_new_channels_match_miss_and_preserve_null_unrelated_fields(self):
        for channel in PUBLIC_ROWS:
            with self.subTest(channel=channel):
                self.assertIn(channel, self.probe.CHANNELS)
                rows = [PUBLIC_ROWS[channel], {**MATCH_ROWS[channel], 'description': None, 'score': None}]
                with patch.object(self.probe, 'fetch', return_value=envelope(channel, rows)):
                    result = self.probe.observe(channel, 'icode')
                self.assertEqual(result['status'], 'matched')
                self.assertEqual(result['positions'], [2])
                self.assertEqual(result['returned'], 2)
                for miss_rows in ([], [PUBLIC_ROWS[channel]]):
                    with patch.object(self.probe, 'fetch', return_value=envelope(channel, miss_rows)):
                        result = self.probe.observe(channel, 'icode')
                    self.assertEqual(result['status'], 'not_in_results')
                    self.assertEqual(result['returned'], len(miss_rows))

    def test_source_less_same_name_is_candidate_not_a_miss(self):
        candidates = {
            'context7': {**MATCH_ROWS['context7'], 'project': None, 'url': None},
            'skillhub': {**MATCH_ROWS['skillhub'], 'upstream_url': None},
            'clawhub': {'slug': 'icode', 'displayName': 'ICODE'},
            'smithery': {**MATCH_ROWS['smithery'], 'gitUrl': None},
        }
        for channel, row in candidates.items():
            with self.subTest(channel=channel):
                self.assertIn(channel, self.probe.CHANNELS)
                with patch.object(self.probe, 'fetch', return_value=envelope(channel, [row])), \
                        patch('sys.stdout', new_callable=io.StringIO) as output:
                    self.assertEqual(self.probe.main(['--online', '--channel', channel]), 0)
                result = json.loads(output.getvalue())['queries'][0]
                self.assertEqual(result['status'], 'candidate_unverified')
                self.assertEqual(result['positions'], [])
                self.assertEqual(result['candidate_positions'], [1])
                with patch.object(self.probe, 'fetch', return_value=envelope(channel, [row, MATCH_ROWS[channel]])):
                    result = self.probe.observe(channel, 'icode')
                self.assertEqual(result['status'], 'matched')
                self.assertEqual(result['positions'], [2])
                self.assertEqual(result['candidate_positions'], [1])

    def test_collisions_never_match(self):
        collisions = {
            'context7': {**MATCH_ROWS['context7'], 'project': '/other/icode-skill',
                         'url': 'https://raw.githubusercontent.com/other/icode-skill/main/SKILL.md'},
            'skillhub': {**MATCH_ROWS['skillhub'], 'upstream_url': 'https://github.com/other/icode-skill'},
            'clawhub': {**MATCH_ROWS['clawhub'], 'ownerHandle': 'other',
                        'canonicalUrl': '/other/skills/icode',
                        'links': {'source': 'https://github.com/other/icode-skill'}},
            'smithery': {**MATCH_ROWS['smithery'], 'namespace': 'other',
                         'gitUrl': 'https://github.com/other/icode-skill'},
        }
        for channel, row in collisions.items():
            with self.subTest(channel=channel):
                self.assertIn(channel, self.probe.CHANNELS)
                with patch.object(self.probe, 'fetch', return_value=envelope(channel, [row])):
                    self.assertEqual(self.probe.observe(channel, 'icode')['status'], 'not_in_results')

    def test_github_provenance_rejects_url_spoofing_and_normalization(self):
        bad_urls = [
            'https://evil.example/ayukyo/icode-skill',
            'https://github.com.evil.example/ayukyo/icode-skill',
            'https://user@github.com/ayukyo/icode-skill',
            'https://github.com:443/ayukyo/icode-skill',
            'https://github.com/ayukyo/icode-skill-other',
            'http://github.com/ayukyo/icode-skill',
            'https://github.com/ayukyo/icode-skill?repo=other',
            'https://github.com/ayukyo/icode-skill#other',
            'https://github.com/ayukyo/icode-skill/tree/main/../../other',
            'https://github.com/ayukyo/icode-skill/tree/main/%2e%2e/%2e%2e/other',
            'https://github.com/ayukyo/icode-skill/tree/main\\..\\other',
            '\nhttps://github.com/ayukyo/icode-skill',
            'https://github.com//ayukyo/icode-skill',
            'https://raw.githubusercontent.com.evil.example/ayukyo/icode-skill/main/SKILL.md',
        ]
        for channel, field in (('context7', 'url'), ('skillhub', 'upstream_url'), ('smithery', 'gitUrl')):
            for url in bad_urls:
                with self.subTest(channel=channel, url=url):
                    row = {**MATCH_ROWS[channel], field: url}
                    with patch.object(self.probe, 'fetch', return_value=envelope(channel, [row])):
                        self.assertNotEqual(self.probe.observe(channel, 'icode')['status'], 'matched')

    def test_clawhub_platform_identity_alone_is_unverified(self):
        # Platform handles, even "ayukyo", do not prove the GitHub owner's identity.
        for owner in ('ayukyo', 'other'):
            for prefix in ('', 'https://clawhub.ai'):
                row = {**MATCH_ROWS['clawhub'], 'ownerHandle': owner,
                       'canonicalUrl': f'{prefix}/{owner}/skills/icode',
                       'links': {'source': None}}
                with self.subTest(owner=owner, prefix=prefix), \
                        patch.object(self.probe, 'fetch', return_value=envelope('clawhub', [row])), \
                        patch('sys.stdout', new_callable=io.StringIO) as output:
                    self.assertEqual(self.probe.main(['--online', '--channel', 'clawhub']), 0)
                    result = json.loads(output.getvalue())['queries'][0]
                    self.assertEqual(result['status'], 'candidate_unverified')
                    self.assertEqual(result['positions'], [])
                    self.assertEqual(result['candidate_positions'], [1])
        for field in ('ownerHandle', 'canonicalUrl', 'source'):
            row = {**MATCH_ROWS['clawhub'], 'links': {'source': None}}
            del row[field]
            with self.subTest(field=field), patch.object(self.probe, 'fetch', return_value=envelope('clawhub', [row])):
                self.assertEqual(self.probe.observe('clawhub', 'icode')['status'], 'candidate_unverified')
        row = {'slug': 'icode', 'displayName': 'ICODE',
               'links': {'source': 'https://github.com/ayukyo/icode-skill/blob/main/SKILL.md'}}
        with patch.object(self.probe, 'fetch', return_value=envelope('clawhub', [row])):
            self.assertEqual(self.probe.observe('clawhub', 'icode')['status'], 'matched')
        row = {**MATCH_ROWS['clawhub'], 'links': {'source': 'https://github.com/other/icode-skill'}}
        with patch.object(self.probe, 'fetch', return_value=envelope('clawhub', [row])):
            self.assertEqual(self.probe.observe('clawhub', 'icode')['status'], 'not_in_results')

    def test_unknown_sources_and_target_repo_aliases_remain_candidates(self):
        urls = ['https://icode.example.org',
                'https://github.com/ayukyo/icode-skill.git',
                'https://github.com/ayukyo/icode-skill/#readme',
                'https://github.com/ayukyo/icode-skill.git/#readme',
                'https://github.com/AYUKYO/ICODE-SKILL',
                'https://github.com.evil.example/other/repo']
        for channel in PUBLIC_ROWS:
            for url in urls:
                row = deepcopy(MATCH_ROWS[channel])
                if channel == 'clawhub':
                    row['links']['source'] = url
                else:
                    field = {'context7': 'url', 'skillhub': 'upstream_url', 'smithery': 'gitUrl'}[channel]
                    row[field] = url
                with self.subTest(channel=channel, url=url), \
                        patch.object(self.probe, 'fetch', return_value=envelope(channel, [row])) as fetch:
                    result = self.probe.observe(channel, 'icode')
                    self.assertEqual(result['status'], 'candidate_unverified')
                    self.assertEqual(result['positions'], [])
                    self.assertEqual(result['candidate_positions'], [1])
                    self.assertEqual(fetch.call_count, 1)  # Never follow a result's source link.

    def test_context7_unknown_project_is_not_another_repository(self):
        for project in (None, 'unknown', '/ayukyo/icode-skill.git', '/AYUKYO/ICODE-SKILL'):
            row = {**MATCH_ROWS['context7'], 'project': project, 'url': 'https://icode.example.org'}
            with self.subTest(project=project), patch.object(self.probe, 'fetch', return_value=envelope('context7', [row])):
                self.assertEqual(self.probe.observe('context7', 'icode')['status'], 'candidate_unverified')
        row = {**MATCH_ROWS['context7'], 'project': '/other/icode-skill', 'url': None}
        with patch.object(self.probe, 'fetch', return_value=envelope('context7', [row])):
            self.assertEqual(self.probe.observe('context7', 'icode')['status'], 'not_in_results')

    def test_only_explicit_other_github_repositories_are_excluded(self):
        for channel in PUBLIC_ROWS:
            for url in ('https://github.com/other/icode-skill',
                        'https://github.com/ayukyo/other-skill.git/#readme',
                        'https://raw.githubusercontent.com/other/icode-skill/main/SKILL.md'):
                row = deepcopy(MATCH_ROWS[channel])
                if channel == 'clawhub':
                    row['links']['source'] = url
                else:
                    field = {'context7': 'url', 'skillhub': 'upstream_url', 'smithery': 'gitUrl'}[channel]
                    row[field] = url
                with self.subTest(channel=channel, url=url), patch.object(self.probe, 'fetch', return_value=envelope(channel, [row])):
                    self.assertEqual(self.probe.observe(channel, 'icode')['status'], 'not_in_results')

    def test_real_urllib_redirects_share_the_run_http_budget(self):
        for redirects, completed in ((1, 5), (2, 3)):
            transport = RedirectTransport(redirects)
            def opener(*handlers):
                return urllib_build_opener(ProxyHandler({}), transport, *handlers)
            with self.subTest(redirects=redirects), \
                    patch.object(self.probe, 'build_opener', side_effect=opener), \
                    patch('socket.socket', side_effect=AssertionError('real network forbidden')), \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                code = self.probe.main(['--online'])
                report = json.loads(output.getvalue())
                self.assertEqual(len(transport.requests), 10)
                self.assertEqual(code, 1)
                self.assertEqual(report['http_budget'], {'limit': 10, 'used': 10})
                self.assertEqual(len(report['queries']), 6)
                self.assertEqual([r['status'] for r in report['queries']],
                                 ['not_in_results'] * completed + ['budget_exhausted'] * (6 - completed))
                for result in report['queries'][completed:]:
                    self.assertIn('redirect', result['reason'])
                    self.assertIn('10', result['reason'])
                self.assertTrue(any(urlsplit(url).hostname.startswith('www.') for url in transport.requests))

    def test_offline_never_opens_urllib_transport(self):
        with patch.object(self.probe, 'build_opener', side_effect=AssertionError('offline')), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(self.probe.main([]), 0)
        self.assertEqual(json.loads(output.getvalue()).get('http_budget'), {'limit': 10, 'used': 0})

    def test_optional_source_type_drift_is_not_a_candidate(self):
        for channel, field in (('skillhub', 'upstream_url'), ('clawhub', 'ownerHandle'),
                               ('clawhub', 'source'), ('clawhub', 'canonicalUrl'), ('clawhub', 'links')):
            for value in ('', ' ', 1, False, []):
                with self.subTest(channel=channel, field=field, value=value):
                    row = {**PUBLIC_ROWS[channel], field: value}
                    with patch.object(self.probe, 'fetch', return_value=envelope(channel, [row])):
                        self.assertEqual(self.probe.observe(channel, 'icode')['status'], 'invalid_response')
        for channel, field in (('context7', 'name'), ('skillhub', 'slug'), ('skillhub', 'name'),
                               ('skillhub', 'source'), ('skillhub', 'ownerName'), ('clawhub', 'slug'),
                               ('clawhub', 'displayName'), ('smithery', 'namespace'),
                               ('smithery', 'slug'), ('smithery', 'displayName')):
            with self.subTest(channel=channel, field=field):
                row = {**PUBLIC_ROWS[channel], field: None}
                with patch.object(self.probe, 'fetch', return_value=envelope(channel, [row])):
                    self.assertEqual(self.probe.observe(channel, 'icode')['status'], 'invalid_response')

    def test_default_online_batch_has_six_queries_and_continues_after_failure(self):
        responses = [HTTPException('truncated'), {'skills': []},
                     envelope('context7', []), envelope('skillhub', []),
                     envelope('clawhub', []), envelope('smithery', [])]
        with patch.object(self.probe, 'fetch', side_effect=responses) as fetch, \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(self.probe.main(['--online']), 1)
        self.assertEqual(fetch.call_count, 6)
        self.assertEqual([r['status'] for r in json.loads(output.getvalue())['queries']],
                         ['unavailable'] + ['not_in_results'] * 5)

    def test_invalid_query_is_rejected_offline_and_online(self):
        for online in ([], ['--online']):
            for phrase in ('', ' ', 'x' * 129, 'a\nb', '\0'):
                with self.subTest(online=online, phrase=phrase), \
                        patch.object(self.probe, 'fetch', side_effect=AssertionError('no network')), \
                        patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as caught:
                    self.probe.main(online + ['--query', phrase])
                self.assertEqual(caught.exception.code, 2)

    def test_new_channel_schema_drift_is_invalid_even_after_a_match(self):
        required = {'context7': ('name', 'project', 'url'),
                    'skillhub': ('name', 'slug', 'source', 'ownerName'),
                    'clawhub': ('slug', 'displayName'),
                    'smithery': ('namespace', 'slug', 'displayName', 'gitUrl')}
        for channel, fields in required.items():
            with self.subTest(channel=channel):
                self.assertIn(channel, self.probe.CHANNELS)
                bad_payloads = [{}, [], {'items': []}, envelope(channel, [None]), envelope(channel, {})]
                for field in fields:
                    row = deepcopy(PUBLIC_ROWS[channel])
                    del row[field]
                    bad_payloads.append(envelope(channel, [MATCH_ROWS[channel], row]))
                    for value in ('', ' ', 123, False, [], {}):
                        row = {**PUBLIC_ROWS[channel], field: value}
                        bad_payloads.append(envelope(channel, [row]))
                for payload in bad_payloads:
                    with self.subTest(payload=payload), patch.object(self.probe, 'fetch', return_value=payload):
                        self.assertEqual(self.probe.observe(channel, 'icode')['status'], 'invalid_response')

    def test_new_channels_error_envelopes_and_http_errors(self):
        for channel in PUBLIC_ROWS:
            with self.subTest(channel=channel):
                self.assertIn(channel, self.probe.CHANNELS)
                for error in ({'error': 'failed'}, {'success': False}):
                    with patch.object(self.probe, 'fetch', return_value={**envelope(channel, []), **error}):
                        self.assertEqual(self.probe.observe(channel, 'icode')['status'], 'invalid_response')
                for code, status in ((401, 'auth_required'), (403, 'auth_required'), (429, 'rate_limited'), (503, 'unavailable')):
                    with patch.object(self.probe, 'fetch', side_effect=HTTPError('https://public.example', code, '', {}, None)), \
                            patch('sys.stdout', new_callable=io.StringIO) as output:
                        self.assertEqual(self.probe.main(['--online', '--channel', channel]), 1)
                    self.assertEqual(json.loads(output.getvalue())['queries'][0]['status'], status)
                for error in (URLError('redacted'), HTTPException('truncated')):
                    with patch.object(self.probe, 'fetch', side_effect=error):
                        self.assertEqual(self.probe.observe(channel, 'icode')['status'], 'unavailable')
        for code in (None, False, '0', 1):
            self.assertIn('skillhub', self.probe.CHANNELS)
            with patch.object(self.probe, 'fetch', return_value={**envelope('skillhub', []), 'code': code}):
                self.assertEqual(self.probe.observe('skillhub', 'icode')['status'], 'invalid_response')

    def test_source_and_limit_metadata_are_honest_in_plan_and_observation(self):
        with patch.object(self.probe, 'fetch', side_effect=AssertionError('offline')), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.probe.main([])
        for item in json.loads(output.getvalue())['queries']:
            with self.subTest(channel=item['channel']):
                self.assertTrue(item.get('source', '').startswith('https://'))
                self.assertTrue(item.get('stability'))
                expected = None if item['channel'] == 'context7' else 50
                self.assertEqual(item['limit'], expected)
                self.assertEqual(item['limit_mode'], 'upstream_default' if expected is None else 'requested')
        self.assertIn('context7', self.probe.CHANNELS)
        with patch.object(self.probe, 'fetch', return_value=envelope('context7', [PUBLIC_ROWS['context7']] * 20)):
            result = self.probe.observe('context7', 'icode')
        self.assertIsNone(result['limit'])
        self.assertEqual(result['returned'], 20)
        self.assertEqual(result['limit_mode'], 'upstream_default')

    def test_request_budget_fails_before_network_and_tells_user_to_split_channels(self):
        for online in ([], ['--online']):
            with self.subTest(online=online), patch.object(self.probe, 'fetch', side_effect=AssertionError('no network')), \
                    patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit) as caught:
                    self.probe.main(online + ['--channel', 'all', '--query', 'icode', '--query', 'code review'])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn('10', err.getvalue())
                self.assertIn('--channel', err.getvalue())

    def test_single_channel_five_queries_pass_but_six_are_rejected_before_network(self):
        phrases = ['icode', 'code review', 'workflow', '工单', 'testing']
        args = ['--channel', 'skills.sh']
        for phrase in phrases:
            args.extend(['--query', phrase])
        for online in ([], ['--online']):
            with self.subTest(online=online), patch.object(self.probe, 'fetch', return_value={'skills': []}) as fetch, \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(self.probe.main(online + args), 0)
            self.assertEqual([r['query'] for r in json.loads(output.getvalue())['queries']], phrases)
            self.assertEqual(fetch.call_count, 5 if online else 0)
            with self.subTest(online=online, rejected=6), \
                    patch.object(self.probe, 'fetch', return_value={'skills': []}) as fetch, \
                    patch('sys.stdout', new_callable=io.StringIO) as output, \
                    patch('sys.stderr', new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit) as caught:
                    self.probe.main(online + args + ['--query', 'debug'])
                self.assertEqual(caught.exception.code, 2)
                fetch.assert_not_called()
                self.assertEqual(output.getvalue(), '')
                self.assertIn('5', err.getvalue())

    def test_matching_requires_exact_source_and_skill_identity(self):
        cases = [
            ('skillsmp', {'success': True, 'data': {'skills': [{'name': 'icode', 'author': 'ayukyo', 'githubUrl': 'https://github.com/ayukyo/icode-skill/tree/main'}]}}),
            ('skills.sh', {'skills': [{'name': 'icode', 'skillId': 'icode', 'source': 'ayukyo/icode-skill'}]}),
        ]
        for channel, payload in cases:
            with self.subTest(channel=channel):
                with patch.object(self.probe, 'fetch', return_value=payload):
                    result = self.probe.observe(channel, 'icode')
                self.assertEqual(result['status'], 'matched')
                self.assertEqual(result['positions'], [1])
        for url in ['https://evil.example/ayukyo/icode-skill', 'https://github.com/ayukyo/icode-skill-other', 'https://user@github.com/ayukyo/icode-skill']:
            payload = {'success': True, 'data': {'skills': [{'name': 'icode', 'author': 'ayukyo', 'githubUrl': url}]}}
            with patch.object(self.probe, 'fetch', return_value=payload):
                self.assertEqual(self.probe.observe('skillsmp', 'icode')['status'], 'not_in_results')

    def test_empty_is_query_limited_not_a_global_absence_claim(self):
        with patch.object(self.probe, 'fetch', return_value={'skills': []}):
            result = self.probe.observe('skills.sh', 'code review')
        self.assertEqual(result['status'], 'not_in_results')
        self.assertEqual(result['returned'], 0)
        self.assertEqual(result['limit'], 50)
        self.assertIn('query', result)

    def test_schema_drift_and_errors_are_not_not_found(self):
        for payload in [{}, {'skills': None}, {'skills': [None]}, {'skills': 'wrong'}]:
            with patch.object(self.probe, 'fetch', return_value=payload):
                self.assertEqual(self.probe.observe('skills.sh', 'icode')['status'], 'invalid_response')
        for code, status in [(401, 'auth_required'), (403, 'auth_required'), (429, 'rate_limited'), (500, 'unavailable')]:
            with patch.object(self.probe, 'fetch', side_effect=HTTPError('https://skills.sh', code, 'error', {}, None)):
                self.assertEqual(self.probe.observe('skills.sh', 'icode')['status'], status)
        with patch.object(self.probe, 'fetch', side_effect=URLError('redacted')):
            self.assertEqual(self.probe.observe('skills.sh', 'icode')['status'], 'unavailable')

    def test_endpoint_encodes_queries_and_does_not_filter_owner(self):
        url = self.probe.endpoint('skillsmp', '工单 & code')
        self.assertIn('%E5%B7%A5%E5%8D%95', url)
        self.assertNotIn('owner=', self.probe.endpoint('skills.sh', 'icode'))

    def test_identity_schema_drift_is_not_a_search_miss(self):
        # Identity fields observed in anonymous public results on 2026-09-20.
        identities = {
            'skillsmp': {'name': 'icode', 'author': 'ayukyo',
                         'githubUrl': 'https://github.com/ayukyo/icode-skill/tree/main'},
            'skills.sh': {'name': 'icode', 'source': 'ayukyo/icode-skill', 'skillId': 'icode'},
        }
        for channel, identity in identities.items():
            for field in identity:
                for value in (None, '', ' ', 123, False, [], {}):
                    row = {**identity, field: value}
                    payload = {'skills': [row]}
                    if channel == 'skillsmp':
                        payload = {'success': True, 'data': payload}
                    with self.subTest(channel=channel, field=field, value=value), \
                            patch.object(self.probe, 'fetch', return_value=payload):
                        self.assertEqual(self.probe.observe(channel, 'icode')['status'], 'invalid_response')
            row = {**identity, 'description': None, 'installs': None}
            payload = {'skills': [row]}
            if channel == 'skillsmp':
                payload = {'success': True, 'data': payload}
            with patch.object(self.probe, 'fetch', return_value=payload):
                self.assertEqual(self.probe.observe(channel, 'icode')['status'], 'matched')
        for row in ({}, {'name': 'icode', 'repository': 'ayukyo/icode-skill'}):
            with patch.object(self.probe, 'fetch', return_value={'skills': [row]}):
                self.assertEqual(self.probe.observe('skills.sh', 'icode')['status'], 'invalid_response')

    def test_error_envelope_is_not_successful_empty_results(self):
        for payload in ({'success': False, 'skills': []}, {'error': 'backend failure', 'skills': []}):
            with patch.object(self.probe, 'fetch', return_value=payload), \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(self.probe.main(['--online', '--channel', 'skills.sh', '--query', 'icode']), 1)
                self.assertEqual(json.loads(output.getvalue())['queries'][0]['status'], 'invalid_response')

    def test_redirect_query_cannot_change_or_disappear(self):
        from urllib.request import Request
        request = Request('https://skills.sh/api/search?q=icode&limit=50')
        for query in ('q=other&limit=50', 'q=icode&limit=5000', '', 'q=icode&limit=50&q=other'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.probe.PublicRedirects().redirect_request(
                    request, None, 308, 'move', {}, 'https://www.skills.sh/api/search?' + query)

    def test_truncated_chunked_http_response_is_isolated(self):
        class Socket:
            def makefile(self, *args):
                return io.BytesIO(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n20\r\n{"skills":[')
        response = HTTPResponse(Socket())
        response.begin()
        with patch.object(self.probe, 'build_opener') as opener:
            opener.return_value.open.side_effect = [response, io.BytesIO(b'{"skills":[]}')]
            with patch('sys.stdout', new_callable=io.StringIO) as output:
                try:
                    code = self.probe.main(['--online', '--channel', 'skills.sh', '--query', 'icode', '--query', 'review'])
                except HTTPException as exc:
                    self.fail('HTTP read failure escaped the query: ' + type(exc).__name__)
            self.assertEqual(code, 1)
            self.assertEqual([r['status'] for r in json.loads(output.getvalue())['queries']],
                             ['unavailable', 'not_in_results'])

    def test_other_http_protocol_errors_continue_the_batch(self):
        with patch.object(self.probe, 'fetch', side_effect=[HTTPException('bad status'), {'skills': []}]), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            try:
                code = self.probe.main(['--online', '--channel', 'skills.sh', '--query', 'icode', '--query', 'review'])
            except HTTPException:
                self.fail('HTTP protocol error escaped the query')
        self.assertEqual(code, 1)
        self.assertEqual([r['status'] for r in json.loads(output.getvalue())['queries']],
                         ['unavailable', 'not_in_results'])

    def test_response_size_is_bounded(self):
        class Response(io.BytesIO):
            pass
        class Opener:
            def open(inner, request, timeout):
                self.assertEqual(timeout, 12)
                self.assertNotIn('Authorization', request.headers)
                return Response(b'x' * (self.probe.MAX_BYTES + 1))
        with patch.object(self.probe, 'build_opener', return_value=Opener()):
            with self.assertRaises(ValueError):
                self.probe.fetch('https://skillsmp.com/api/v1/skills/search?q=icode')

    def test_redirects_cannot_leave_the_verified_public_endpoint(self):
        from urllib.request import Request
        handler = self.probe.PublicRedirects()
        request = Request('https://skills.sh/api/search?q=icode')
        allowed = handler.redirect_request(request, None, 308, 'move', {}, 'https://www.skills.sh/api/search?q=icode')
        self.assertEqual(allowed.full_url, 'https://www.skills.sh/api/search?q=icode')
        for url in ['http://skills.sh/api/search', 'https://evil.example/api/search', 'https://skills.sh/login', 'https://u@skills.sh/api/search']:
            with self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, 'move', {}, url)


if __name__ == '__main__':
    unittest.main()
