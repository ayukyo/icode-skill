#!/usr/bin/env python3
"""Run twenty named local checks and retain logs in a fresh report directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def unit(*names):
    return [PY, '-m', 'unittest', '-v', *names]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', type=Path, required=True)
    args = parser.parse_args()
    # Reports are new generated artifacts only; never replace prior evidence.
    report = args.report_dir.absolute()
    if '..' in report.parts or any(p.is_symlink() for p in (report, *report.parents)):
        parser.error('unsafe report path')
    report.mkdir(parents=True, exist_ok=False)
    case = 'test_public_site.PublicSiteTests.'
    checks = [
        ('01-python-syntax', [PY, '-m', 'py_compile', 'tools/build_public_site.py', 'tools/notify_indexnow.py']),
        ('02-bilingual-public-allowlist', unit(case + 'test_bilingual_pages_and_exact_output_allowlist')),
        ('03-xml-seo-manifest', unit(case + 'test_xml_and_manifest_match_public_pages', case + 'test_public_links_and_fragment_targets_exist')),
        ('04-byte-reproducibility', unit(case + 'test_repeatable_bytes')),
        ('05-private-input-isolation', unit(case + 'test_private_files_not_copied_or_read')),
        ('06-output-preservation', unit(case + 'test_existing_output_never_overwritten', case + 'test_ticket_output_path_rejected')),
        ('07-symlink-rejection', unit(case + 'test_symlink_input_and_parent_output_rejected', case + 'test_broken_symlink_output_rejected')),
        ('08-url-scope', unit(case + 'test_bad_urls_rejected_before_output', case + 'test_custom_domain_root')),
        ('09-content-validation', unit(case + 'test_malformed_content_fails_before_writes', case + 'test_too_few_examples_fail_validation_not_traceback')),
        ('10-html-and-example-semantics', unit(case + 'test_text_is_escaped_not_executed', case + 'test_hero_uses_full_flow_not_design_only_request')),
        ('11-release-source-binding', unit(case + 'test_release_version_mismatch_rejected', case + 'test_missing_content_or_invalid_version_fail_closed')),
        ('12-stdlib-cli', unit(case + 'test_no_optional_packages_required', case + 'test_cli_argument_error_is_concise')),
        ('13-notifier-transport-and-errors', unit('test_notify_indexnow')),
        ('14-real-demo-copy-and-integration', unit('test_public_site_demo')),
        ('15-publication-gates', unit('test_public_site_workflow')),
        ('16-existing-install-doc-contract', ['bash', 'tests/test_open_source_install_docs_contract.sh']),
        ('17-existing-public-command-contract', ['bash', 'tests/test_public_command_names_contract.sh']),
        ('18-existing-verify-behavior', [PY, '-m', 'pytest', '-p', 'no:anyio', 'tests/test_verify_request.py', '-q']),
        ('19-existing-runtime-compatibility', ['bash', 'tests/test_agent_runtime_compat_contract.sh']),
        ('20-final-complete-regression', unit('test_public_site', 'test_public_site_workflow', 'test_public_site_demo', 'test_notify_indexnow')),
    ]
    env = dict(os.environ)
    env['PYTHONPATH'] = str(ROOT / 'tests') + os.pathsep + str(ROOT)
    # Real user notification credentials are neither required nor inherited.
    env.pop('INDEXNOW_KEY', None)
    results = []
    for name, command in checks:
        start = time.monotonic()
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                    text=True, timeout=180)
            code, output = result.returncode, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            code, output = 124, 'check timed out after 180 seconds\n'
        record = {'name': name, 'command': command, 'exit_code': code,
                  'seconds': round(time.monotonic() - start, 3)}
        results.append(record)
        (report / (name + '.txt')).write_text(output, encoding='utf-8')
        print(f"{name}: {'PASS' if code == 0 else 'FAIL'} ({record['seconds']}s)", flush=True)
    (report / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    passed = sum(r['exit_code'] == 0 for r in results)
    summary = ['# Public site: twenty-round local checks', '', f'{passed}/20 passed.', '',
               '| Round | Result | Seconds |', '| --- | --- | --- |']
    summary.extend(f"| {r['name']} | {'PASS' if r['exit_code'] == 0 else 'FAIL'} | {r['seconds']} |" for r in results)
    summary.extend(['', 'These are local automated checks, not proof of live GitHub deployment,',
                    'search indexing, real device testing, or social-media distribution.',
                    'Browser and skills CLI observations are recorded separately.'])
    (report / 'REPORT.md').write_text('\n'.join(summary) + '\n', encoding='utf-8')
    return 0 if passed == len(checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
