"""Bounded real stdio acceptance: run with a Python containing mcp>=1.29."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

LOCAL = Path(__file__).resolve().parents[1]
if '--server' in sys.argv:
    LOCAL = Path(sys.argv[sys.argv.index('--server') + 1]).resolve()


def worker():
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def verify():
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            source = home / 'source'; source.mkdir()
            sample = source / 'sample.txt'; sample.write_text('local needle\n')
            compile_db = source / 'compile_commands.json'; compile_db.write_text('[]')
            mapping = {}
            for module in ['workspace', 'evidence', 'index', 'health', 'policy']:
                config = home / (module + '.json')
                config.write_text(json.dumps({'allowed_roots': [str(source)], 'trusted_server_roots': [], 'data_dir': str(home / 'index-data')}))
                mapping[module] = str(config)
            profile = home / 'profile.json'; profile.write_text(json.dumps({'module_configs': mapping}))
            env = {key: value for key, value in os.environ.items() if key not in {'ICODE_WORKSPACE_CONFIG', 'ICODE_EVIDENCE_CONFIG', 'ICODE_LOCAL_INDEX_CONFIG', 'ICODE_MCP_HEALTH_CONFIG', 'ICODE_MCP_POLICY_CONFIG'}}
            env.update({'HOME': str(home), 'USERPROFILE': str(home), 'ICODE_LOCAL_CONFIG': str(profile), 'PYTHONDONTWRITEBYTECODE': '1'})
            params = StdioServerParameters(command=sys.executable, args=[str(LOCAL / 'server.py')], env=env)
            with anyio.fail_after(20):
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        tools = (await session.list_tools()).tools
                        manifest = json.loads((LOCAL / 'tools_manifest.json').read_text())
                        assert {tool.name for tool in tools} == {row['name'] for row in manifest['tools']}
                        assert len(tools) == 28
                        assert all(tool.outputSchema and tool.annotations for tool in tools)
                        async def call(name, args={}):
                            result = await session.call_tool(name, args)
                            assert not result.isError, (name, result)
                            payload = result.structuredContent
                            assert payload and 'error_code' not in payload, (name, payload)
                            return payload['answer']
                        assert (await call('describe_capabilities'))['lazy_modules']
                        assert (await call('workspace_inspect_build_inputs', {'path': str(source)}))['entry_count'] == 0
                        assert (await call('evidence_read_evidence', {'path': str(sample)}))['text'] == 'local needle'
                        assert (await call('index_build_index', {'root': str(source), 'max_files': 2}))['file_count'] == 2
                        assert (await call('index_query_index', {'root': str(source), 'query': 'needle'}))['count'] == 1
                        assert (await call('health_scan_sensitive_payload', {'payload': {'status': 'ok'}}))['safe']
                        assert (await call('policy_validate_policy'))['valid']
                        blocked = await session.call_tool('health_probe_python_server', {'server_dir': str(sample)})
                        assert blocked.structuredContent['error_code'] == 'untrusted_server'
                        for module in mapping:
                            await call(module + '_describe_capabilities')
            print(json.dumps({'status': 'ok', 'tools': len(tools), 'calls': 13, 'isolated_home': True}))
    anyio.run(verify)


if __name__ == '__main__':
    if '--worker' in sys.argv:
        worker()
    else:
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker',
                                     '--server', str(LOCAL)], timeout=30, text=True, capture_output=True)
        except subprocess.TimeoutExpired:
            print('stdio verification timed out after 30 seconds; check asyncio/socketpair support.', file=sys.stderr)
            raise SystemExit(1)
        print(result.stdout, end='')
        if result.returncode:
            print(result.stderr, file=sys.stderr, end='')
        raise SystemExit(result.returncode)
