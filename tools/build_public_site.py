#!/usr/bin/env python3
"""Build an offline static site from explicit public inputs, never ticket data."""
import argparse
from datetime import date
from html import escape
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

REPO = 'https://github.com/ayukyo/icode-skill'
DEFAULT_BASE = 'https://ayukyo.github.io/icode-skill/'
VERSION_RE = r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?'
# XML 1.0 character ranges; TAB, LF and CR remain valid public text.
INVALID_XML_CHARS = re.compile(r'[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')
LOCAL_SUFFIXES = {'localhost', 'local', 'localdomain', 'internal', 'lan', 'home',
                  'test', 'invalid', 'example', 'onion', 'alt', 'arpa'}
INSTALL = ('git clone https://github.com/ayukyo/icode-skill ~/icode-skill\n'
           'cd ~/icode-skill\n./install.sh --dry-run --client all\n'
           './install.sh --client all')
FIELDS = {'title', 'description', 'eyebrow', 'intro', 'install_cta', 'flow_title',
          'features_title', 'install_title', 'terminal_label', 'host_label',
          'install_note', 'host_note', 'examples_title', 'examples_note', 'boundary',
          'updates_title', 'source_label', 'docs_label', 'privacy', 'flow_cta',
          'flow_intro', 'input_label', 'output_label', 'checkpoint_label',
          'step_docs_label', 'optional_title', 'more_label', 'scenes_title',
          'principles_label', 'illustration_label', 'delivery_title', 'delivery_note'}
STAGE_DOCS = dict(zip(('plan', 'review', 'merge', 'code', 'deepcheck', 'audit'),
                     ('01_plan.md', '02_review.md', '03_merge.md', '04_code.md',
                      '05_deepcheck.md', '06_audit.md')))
# IDs/order are fixed presentation contracts, not a second workflow engine.
RECORDS = {
    'stages': (tuple(STAGE_DOCS), {'id', 'purpose', 'input', 'output', 'checkpoint'}),
    'paths': (('intake', 'verify', 'crosscheck'), {'id', 'title', 'body'}),
    'scenes': (('develop', 'logs', 'review', 'verify', 'docs', 'manage'),
               {'id', 'title', 'body', 'command', 'more', 'note'}),
    'delivery': (('design', 'code', 'evidence', 'pending'), {'id', 'title', 'body'}),
}


def nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def checked_records(value, ids, fields):
    if not isinstance(value, list) or len(value) != len(ids):
        raise ValueError('invalid structured content count')
    for record, expected_id in zip(value, ids):
        if (not isinstance(record, dict) or set(record) != fields
                or record['id'] != expected_id
                or any(not nonempty_text(v) for v in record.values())):
            raise ValueError('invalid structured content fields, order or text')


def normalize_base_url(value):
    """Canonical HTTPS public host and conservative, unencoded project path."""
    if not isinstance(value, str) or re.search(r'[\s\\%?#\x7f]', value):
        raise ValueError('base URL contains unsafe characters')
    url = urlsplit(value)
    host = url.hostname or ''
    if (url.scheme != 'https' or '@' in url.netloc or ':' in url.netloc
            or len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                     for label in host.split('.'))
            or '.' not in host or not re.fullmatch(r'[a-z][a-z0-9-]*', host.split('.')[-1])
            or host.split('.')[-1] in LOCAL_SUFFIXES):
        raise ValueError('base URL must use an HTTPS public DNS hostname without credentials or port')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('IP address base URLs are not supported')
    path = url.path or '/'
    if (not re.fullmatch(r'/[A-Za-z0-9_./-]*', path) or '//' in path
            or any(part in ('.', '..') for part in path.split('/'))):
        raise ValueError('invalid base URL path')
    return 'https://' + host + path.rstrip('/') + '/'


def no_symlinks(path):
    path = Path(path)
    # Reject lexical traversal before resolve() can erase it.
    if '..' in path.parts:
        raise ValueError('path traversal is not accepted')
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink paths are not accepted')
    return path


def public_input(root, relative):
    path = no_symlinks(root / relative)
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('missing or unsafe public input: ' + relative)
    if path.stat().st_size > 1_000_000:
        raise ValueError('public input exceeds 1 MB limit')
    return path.read_text(encoding='utf-8')


def validate_xml_strings(data):
    """Check every JSON string, including nested values and object keys."""
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if INVALID_XML_CHARS.search(value):
                raise ValueError('public content contains invalid XML characters')
        elif isinstance(value, dict):
            pending.extend(value)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def content_checked(data):
    validate_xml_strings(data)
    if not isinstance(data, dict) or set(data) != {'locales', 'updates'}:
        raise ValueError('invalid content root')
    locales = data['locales']
    if not isinstance(locales, dict) or set(locales) != {'zh-CN', 'en'}:
        raise ValueError('both locales are required')
    for text in locales.values():
        if not isinstance(text, dict) or set(text) != FIELDS | {'flow', 'features', 'examples'} | set(RECORDS):
            raise ValueError('invalid locale fields')
        if any(not isinstance(text[k], str) or not text[k].strip() for k in FIELDS):
            raise ValueError('locale strings must be nonempty')
        if (not isinstance(text['flow'], list) or len(text['flow']) != 6
                or any(not isinstance(v, str) or not v for v in text['flow'])):
            raise ValueError('six flow labels are required')
        for key in ('features', 'examples'):
            if (not isinstance(text[key], list) or not text[key]
                    or any(not isinstance(pair, list) or len(pair) != 2
                           or any(not isinstance(s, str) or not s for s in pair) for pair in text[key])):
                raise ValueError('invalid feature or example pair')
        if len(text['examples']) < 2:
            raise ValueError('design-only and full-flow examples are required')
        for field, (ids, fields) in RECORDS.items():
            checked_records(text[field], ids, fields)
    if not isinstance(data['updates'], list) or not data['updates']:
        raise ValueError('at least one reviewed update is required')
    seen = set()
    for item in data['updates']:
        if (not isinstance(item, dict) or set(item) != {'version', 'reviewed_on', 'zh-CN', 'en'}
                or any(not isinstance(v, str) or not v for v in item.values())
                or not re.fullmatch(VERSION_RE, item['version'])):
            raise ValueError('invalid reviewed update')
        date.fromisoformat(item['reviewed_on'])
        if item['version'] in seen:
            raise ValueError('duplicate update version')
        seen.add(item['version'])
    return data


def render_page(data, lang, base, version):
    t = data['locales'][lang]
    e = escape
    english = lang == 'en'
    relative = '../' if english else ''
    canonical = base + ('en/' if english else '')
    switch = '<a href="../" lang="zh-CN">中文</a>' if english else '<a href="en/" lang="en">English</a>'
    docs = REPO + '/blob/main/' + ('README.md' if english else 'README.zh-CN.md')
    flow = ''.join('<li>' + e(label) + '</li>' for label in t['flow'])
    features = ''.join('<article class="card"><h3>' + e(title) + '</h3><p>' + e(body) + '</p></article>'
                       for title, body in t['features'])
    examples = ''.join('<article><h3>' + e(title) + '</h3><code>' + e(command) + '</code></article>'
                       for title, command in t['examples'])
    stages = []
    for n, (stage, label) in enumerate(zip(t['stages'], t['flow']), 1):
        stage_id = stage['id']
        fields = ''.join('<dt>' + e(t[key + '_label']) + '</dt><dd>' + e(stage[key]) + '</dd>'
                         for key in ('input', 'output', 'checkpoint'))
        stages.append(f'<details class="stage" id="stage-{stage_id}"' + (' open' if n == 1 else '')
                      + f'><summary><span class="step-number">{n:02}</span><strong>{e(label)}</strong>'
                      + f'<code>{stage_id}</code></summary><div class="stage-body"><p>{e(stage["purpose"])}</p>'
                      + f'<dl>{fields}</dl><a href="{REPO}/blob/main/steps/{STAGE_DOCS[stage_id]}">'
                      + e(t['step_docs_label']) + '</a></div></details>')
    paths = ''.join('<article class="card"><h3>' + e(p['title']) + '</h3><p>' + e(p['body'])
                    + '</p></article>' for p in t['paths'])
    scenes = ''.join('<article class="card"><h3>' + e(s['title']) + '</h3><p>' + e(s['body'])
                     + '</p><code class="example-command">' + e(s['command']) + '</code><details><summary>'
                     + e(t['more_label']) + '</summary><pre><code>' + e(s['more']) + '</code></pre><p class="note">'
                     + e(s['note']) + '</p></details></article>' for s in t['scenes'])
    delivery = ''.join('<article class="card' + (' pending' if p['id'] == 'pending' else '')
                       + '"><h3>' + e(p['title']) + '</h3><p>' + e(p['body']) + '</p></article>'
                       for p in t['delivery'])
    updates = ''.join('<article class="update" id="note-' + e(item['version']) + '"><h3>' + e(item['version'])
                      + '<time datetime="' + e(item['reviewed_on']) + '">' + e(item['reviewed_on'])
                      + '</time></h3><p>' + e(item[lang]) + '</p></article>' for item in data['updates'])
    skip = 'Skip to content' if english else '跳到正文'
    # The terminal is a labeled usage illustration, never a fake successful run.
    trace = ''.join('<li><b>' + e(label) + '</b><span class="tag">' + str(n) + '/6</span></li>'
                    for n, label in enumerate(t['flow'], 1))
    return f'''<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ICODE — {e(t['title'])}</title><meta name="description" content="{e(t['description'])}">
<link rel="canonical" href="{e(canonical)}"><link rel="alternate" hreflang="zh-CN" href="{e(base)}">
<link rel="alternate" hreflang="en" href="{e(base)}en/"><link rel="alternate" hreflang="x-default" href="{e(base)}">
<meta property="og:title" content="ICODE — {e(t['title'])}"><meta property="og:description" content="{e(t['description'])}">
<meta property="og:url" content="{e(canonical)}"><meta property="og:type" content="website">
<link rel="alternate" type="application/rss+xml" title="ICODE version notes" href="{relative}feed.xml">
<link rel="stylesheet" href="{relative}style.css"></head><body>
<a class="skip" href="#content">{skip}</a><header><a class="brand" href="{relative or './'}">I<span>CODE</span></a>
<nav aria-label="{'Navigation' if english else '导航'}"><a href="#flow">{e(t['flow_cta'])}</a><a href="#capabilities">{e(t['scenes_title'])}</a><a href="#install">{e(t['install_cta'])}</a>{switch}<a href="{REPO}">GitHub ↗</a></nav></header>
<main id="content"><section class="hero"><div><p class="eyebrow">{e(t['eyebrow'])}</p><h1>{e(t['title'])}</h1>
<p class="lead">{e(t['intro'])}</p><ul class="hosts"><li>Claude Code</li><li>Codex</li><li>CodeBuddy</li></ul>
<div class="actions"><a class="button" href="#install">{e(t['install_cta'])} →</a><a href="#flow">{e(t['flow_cta'])} ↗</a></div>
<p class="version">{e(t['source_label'])} · {e(version)}</p></div>
<aside class="terminal" aria-label="{'Workflow example' if english else '流程示例'}"><div class="terminal-head">{e(t['illustration_label'])}</div>
<div class="terminal-body"><p class="prompt">{e(t['examples'][1][1])}</p><ul class="trace">{trace}</ul></div></aside></section>
<section class="section" id="flow"><h2>{e(t['flow_title'])}</h2><p class="note">{e(t['flow_intro'])}</p><ol class="flow">{flow}</ol>
<div class="stage-grid">{''.join(stages)}</div><h3>{e(t['optional_title'])}</h3><div class="optional-grid">{paths}</div></section>
<section class="section" id="capabilities"><h2>{e(t['scenes_title'])}</h2><p class="note">{e(t['examples_note'])}</p><div class="scenes">{scenes}</div>
<details class="principles"><summary>{e(t['principles_label'])}</summary><h3>{e(t['features_title'])}</h3><div class="grid">{features}</div></details></section>
<section class="section" id="delivery"><h2>{e(t['delivery_title'])}</h2><p class="note">{e(t['delivery_note'])}</p><div class="delivery-grid">{delivery}</div></section>
<section class="section" id="install"><h2>{e(t['install_title'])}</h2><div class="install-grid"><div><h3>{e(t['terminal_label'])}</h3>
<pre><code>{e(INSTALL)}</code></pre><p class="note">{e(t['install_note'])}</p></div><div><h3>{e(t['host_label'])}</h3>
<pre><code>/icode help\n{e(t['examples'][0][1])}</code></pre><p class="note">{e(t['host_note'])}</p><a href="{docs}">{e(t['docs_label'])} ↗</a></div></div></section>
<section class="section" id="examples"><details class="more-examples"><summary>{e(t['examples_title'])}</summary><p class="note">{e(t['examples_note'])}</p><div class="examples">{examples}</div></details>
<p class="boundary note">{e(t['boundary'])}</p></section>
<section class="section" id="updates"><h2>{e(t['updates_title'])}</h2>{updates}</section></main>
<footer><p>{e(t['privacy'])}</p><a href="{REPO}">GitHub</a><a href="{relative}feed.xml">RSS</a><a href="{REPO}/blob/main/LICENSE">MIT</a></footer>
</body></html>\n'''


def xml_bytes(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'


def build(source_root, output, base_url, indexnow_key=None, expected_version=None):
    """Validate and render fully before creating a fresh output directory."""
    base = normalize_base_url(base_url)
    root = no_symlinks(source_root).resolve()
    out = no_symlinks(output)
    if out.exists():
        raise ValueError('output already exists; choose a fresh directory')
    if any(part in {'.git', '.icode_output', '.claude', '.agents', '.codex', '.codebuddy'} for part in out.parts):
        raise ValueError('output must not be a ticket or host configuration directory')
    # In-repository output is allowed only at the dedicated build directory.
    if out.resolve().is_relative_to(root) and out.resolve() != root / '_site':
        raise ValueError('in-repository output must be the dedicated _site directory')
    if indexnow_key and not re.fullmatch(r'[A-Za-z0-9-]{8,128}', indexnow_key):
        raise ValueError('invalid IndexNow key')
    data = content_checked(json.loads(public_input(root, 'site/content.json')))
    skill = public_input(root, 'SKILL.md')
    match = re.search(r'^\*\*版本\*\*:\s*(' + VERSION_RE + r')\s*$', skill, re.MULTILINE)
    if not match:
        raise ValueError('missing or invalid source version')
    version = match.group(1)
    if expected_version and expected_version != version:
        raise ValueError('release/source version mismatch')
    files = {'index.html': render_page(data, 'zh-CN', base, version).encode(),
             'en/index.html': render_page(data, 'en', base, version).encode(),
             'style.css': public_input(root, 'site/style.css').encode(), '.nojekyll': b''}
    urls = [base, base + 'en/']
    sitemap = ET.Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    for url in urls:
        ET.SubElement(ET.SubElement(sitemap, 'url'), 'loc').text = url
    files['sitemap.xml'] = xml_bytes(sitemap)
    rss = ET.Element('rss', version='2.0')
    channel = ET.SubElement(rss, 'channel')
    for tag, text in [('title', 'ICODE version notes'), ('link', base),
                      ('description', 'Reviewed public version notes / 经审阅的公开版本说明')]:
        ET.SubElement(channel, tag).text = text
    for note in data['updates']:
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = 'ICODE ' + note['version']
        url = base + '#note-' + note['version']
        ET.SubElement(item, 'link').text = url
        ET.SubElement(item, 'guid', isPermaLink='true').text = url
        ET.SubElement(item, 'description').text = note['zh-CN'] + '\n' + note['en']
    files['feed.xml'] = xml_bytes(rss)
    manifest = {'schema_version': 1, 'base_url': base, 'version': version, 'urls': urls}
    files['public-manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    if indexnow_key:
        files[indexnow_key + '.txt'] = (indexnow_key + '\n').encode()
    out.mkdir(parents=True, exist_ok=False)
    for relative, content in files.items():
        file = out / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True, help='new directory; never overwritten')
    parser.add_argument('--base-url', default=DEFAULT_BASE)
    parser.add_argument('--expected-version', help='release tag must exactly match source version')
    args = parser.parse_args()
    try:
        result = build(args.source_root, args.output, args.base_url, os.environ.get('INDEXNOW_KEY'), args.expected_version)
    except (ValueError, OSError) as exc:
        print('site build rejected: ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({'status': 'built', **result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
