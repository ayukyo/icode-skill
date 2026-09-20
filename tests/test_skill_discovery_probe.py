"""Bounded read-only observations, never synthetic installs or ranking claims."""
import importlib.util
from http.client import HTTPException, HTTPResponse
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]


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
        self.assertEqual(len(report['queries']), 8)

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
