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
VERIFICATION_RE = re.compile(r'[A-Za-z0-9_-]{8,128}')
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
FIELDS |= {'motion_label', 'diagram_input', 'diagram_ticket', 'diagram_output',
           'flow_details_label', 'delivery_details_label', 'install_details_label'}
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
PUBLIC_ASSETS = (
    'assets/icode-ticket-hex.svg',
    'assets/icode-ticket-hex-128.png',
    'assets/icode-ticket-hex-512.png',
)
GUIDE_SLUGS = ('ai-coding-workflow', 'multi-model-code-review')
GUIDE_FIELDS = {'title', 'description', 'intro', 'sections', 'commands'}


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


def normalize_source_lastmod(value):
    """Accept only a truthful, date-only source revision timestamp."""
    if value in (None, ''):
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('source lastmod must use YYYY-MM-DD')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError('source lastmod must be a valid date') from None
    if parsed.isoformat() != value:
        raise ValueError('source lastmod must use canonical YYYY-MM-DD')
    return value


def normalize_verification(value, provider):
    """Validate public ownership tokens before placing them in HTML metadata."""
    if value in (None, ''):
        return None
    if not isinstance(value, str) or not VERIFICATION_RE.fullmatch(value):
        raise ValueError('invalid ' + provider + ' site verification value')
    return value


def no_symlinks(path):
    path = Path(path)
    # Reject lexical traversal before resolve() can erase it.
    if '..' in path.parts:
        raise ValueError('path traversal is not accepted')
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink paths are not accepted')
    return path


def checked_public_path(root, relative):
    path = no_symlinks(root / relative)
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('missing or unsafe public input: ' + relative)
    if path.stat().st_size > 1_000_000:
        raise ValueError('public input exceeds 1 MB limit')
    return path


def public_input(root, relative):
    return checked_public_path(root, relative).read_text(encoding='utf-8')


def public_asset(root, relative):
    if relative not in PUBLIC_ASSETS:
        raise ValueError('public asset is not in allowlist')
    return checked_public_path(root, relative).read_bytes()


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
    if not isinstance(data, dict) or set(data) != {'locales', 'guides', 'updates'}:
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
    guides = data['guides']
    if not isinstance(guides, dict) or tuple(guides) != GUIDE_SLUGS:
        raise ValueError('invalid guide slugs or order')
    guide_titles = set()
    for slug in GUIDE_SLUGS:
        localized = guides[slug]
        if not isinstance(localized, dict) or set(localized) != {'zh-CN', 'en'}:
            raise ValueError('both guide locales are required')
        for guide in localized.values():
            if not isinstance(guide, dict) or set(guide) != GUIDE_FIELDS:
                raise ValueError('invalid guide fields')
            if any(not nonempty_text(guide[field])
                   for field in ('title', 'description', 'intro')):
                raise ValueError('guide strings must be nonempty')
            if guide['title'] in guide_titles:
                raise ValueError('guide titles must be unique')
            guide_titles.add(guide['title'])
            sections = guide['sections']
            if (not isinstance(sections, list) or len(sections) < 3
                    or any(not isinstance(section, dict)
                           or set(section) != {'title', 'body'}
                           or any(not nonempty_text(value) for value in section.values())
                           for section in sections)):
                raise ValueError('each guide requires at least three complete sections')
            commands = guide['commands']
            if (not isinstance(commands, list) or not commands
                    or any(not nonempty_text(command) for command in commands)):
                raise ValueError('each guide requires commands')
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


# Authored geometry only: no SVG or URLs are taken from content.json.
ICON_PATHS = {
    'plan': ('M12 5h17l7 7v30H12Z M29 5v9h7', 'M18 22h12 M18 28h12 M18 34h8'),
    'review': ('M31 21a12 12 0 1 1-24 0 12 12 0 0 1 24 0Z M28 30l13 13', 'M13 21h12 M19 15v12'),
    'merge': ('M12 6v24c0 6 5 10 12 10h12 M36 6v14c0 6-5 10-12 10H12', 'M7 6h10 M31 6h10 M31 35l5 5-5 5'),
    'code': ('M16 12 4 24l12 12 M32 12l12 12-12 12', 'M27 7 21 41'),
    'deepcheck': ('M24 4 40 10v12c0 10-7 17-16 22C15 39 8 32 8 22V10Z', 'M16 24l6 6 12-13'),
    'audit': ('M11 5h26v38H11Z M17 5v6h14V5', 'M17 22l3 3 5-6 M28 22h4 M17 34h15'),
    'logs': ('M5 8h38v32H5Z M5 15h38', 'M10 29h6l4-8 7 14 4-6h7'),
    'verify': ('M6 7h36v27H6Z M17 42h14 M24 34v8', 'M15 21l6 6 12-13'),
    'docs': ('M6 12h25v31H6Z M16 5h20l6 6v25 M36 5v8h6', 'M12 22h13 M12 29h13 M12 36h8'),
    'manage': ('M4 8h40v33H4Z M4 16h40 M17 16v25 M31 16v25', 'M8 23h5 M8 30h5 M21 23h6 M35 23h5 M35 30h5'),
    'pending': ('M43 24a19 19 0 1 1-38 0 19 19 0 0 1 38 0Z', 'M24 12v13l8 5'),
    'request': ('M5 8h38v26H20L10 43v-9H5Z', 'M12 17h24 M12 25h16'),
}


def icon(name, cls):
    return (f'<svg class="{cls}" viewBox="0 0 48 48" aria-hidden="true" focusable="false" '
            'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            + ''.join('<path d="' + d + '"></path>' for d in ICON_PATHS[name]) + '</svg>')


def render_diagram(t):
    e = escape
    nodes = ''.join(f'<li class="diagram-node node-{n}" data-stage="{stage}">'
                    + icon(stage, 'diagram-icon') + f'<span class="diagram-number">0{n}</span>'
                    + '<b>' + e(label) + '</b></li>'
                    for n, (stage, label) in enumerate(zip(STAGE_DOCS, t['flow']), 1))
    return ('<aside class="terminal workflow-illustration"><p class="diagram-caption">'
            + e(t['illustration_label']) + '</p><input type="checkbox" id="motion-toggle" checked>'
            + '<label class="motion-control" for="motion-toggle">' + e(t['motion_label']) + '</label>'
            + '<div class="workflow-canvas"><div class="request-node">' + icon('request', 'diagram-icon')
            + '<span class="diagram-kicker">' + e(t['diagram_input']) + '</span><p class="prompt">'
            + e(t['examples'][1][1]) + '</p></div><div class="diagram-ticket">' + e(t['diagram_ticket'])
            + '</div><ol class="diagram-stages">' + nodes + '</ol><div class="diagram-delivery">'
            + icon('audit', 'diagram-icon') + '<span>' + e(t['diagram_output']) + '</span></div></div></aside>')


def render_page(data, lang, base, version, google_site_verification=None,
                bing_site_verification=None):
    t = data['locales'][lang]
    e = escape
    english = lang == 'en'
    relative = '../' if english else ''
    canonical = base + ('en/' if english else '')
    favicon = relative + PUBLIC_ASSETS[0]
    brand_image = relative + PUBLIC_ASSETS[1]
    social_image = base + PUBLIC_ASSETS[2]
    switch = ('<a href="../?lang=zh-CN" lang="zh-CN">中文</a>' if english
              else '<a href="en/?lang=en" lang="en">English</a>')
    docs = REPO + '/blob/main/' + ('README.md' if english else 'README.zh-CN.md')
    flow = ''.join('<li>' + e(label) + '</li>' for label in t['flow'])
    features = ''.join('<article class="card"><h3>' + e(title) + '</h3><p>' + e(body) + '</p></article>'
                       for title, body in t['features'])
    examples = ''.join('<article><h3>' + e(title) + '</h3><code>' + e(command) + '</code></article>'
                       for title, command in t['examples'])
    guide_links = ''.join('<article class="card"><h3><a href="' + slug + '/">'
                          + e(data['guides'][slug][lang]['title']) + '</a></h3><p>'
                          + e(data['guides'][slug][lang]['description']) + '</p></article>'
                          for slug in GUIDE_SLUGS)
    stages = []
    for n, (stage, label) in enumerate(zip(t['stages'], t['flow']), 1):
        stage_id = stage['id']
        fields = ''.join('<dt>' + e(t[key + '_label']) + '</dt><dd>' + e(stage[key]) + '</dd>'
                         for key in ('input', 'output', 'checkpoint'))
        stages.append(f'<details class="stage" id="stage-{stage_id}">'
                      + f'<summary><span class="step-number">{n:02}</span><strong>{e(label)}</strong>'
                      + f'<code>{stage_id}</code></summary><div class="stage-body"><p>{e(stage["purpose"])}</p>'
                      + f'<dl>{fields}</dl><a href="{REPO}/blob/main/steps/{STAGE_DOCS[stage_id]}">'
                      + e(t['step_docs_label']) + '</a></div></details>')
    paths = ''.join('<article class="card"><h3>' + e(p['title']) + '</h3><p>' + e(p['body'])
                    + '</p></article>' for p in t['paths'])
    scene_icons = {'develop': 'code', 'logs': 'logs', 'review': 'review',
                   'verify': 'verify', 'docs': 'docs', 'manage': 'manage'}
    scenes = ''.join('<article class="card">' + icon(scene_icons[s['id']], 'scene-art')
                     + '<h3>' + e(s['title']) + '</h3><p>' + e(s['body'])
                     + '</p><details><summary>' + e(t['more_label'])
                     + '</summary><code class="example-command">' + e(s['command'])
                     + '</code><pre><code>' + e(s['more']) + '</code></pre><p class="note">'
                     + e(s['note']) + '</p></details></article>' for s in t['scenes'])
    delivery_icons = {'design': 'plan', 'code': 'code', 'evidence': 'audit', 'pending': 'pending'}
    delivery = ''.join('<article class="card' + (' pending' if p['id'] == 'pending' else '')
                       + '">' + icon(delivery_icons[p['id']], 'delivery-icon')
                       + '<h3>' + e(p['title']) + '</h3></article>'
                       for p in t['delivery'])
    delivery_details = ''.join('<h3>' + e(p['title']) + '</h3><p>' + e(p['body']) + '</p>'
                               for p in t['delivery'])
    updates = ''.join('<article class="update" id="note-' + e(item['version']) + '"><h3>' + e(item['version'])
                      + '<time datetime="' + e(item['reviewed_on']) + '">' + e(item['reviewed_on'])
                      + '</time></h3><p>' + e(item[lang]) + '</p></article>' for item in data['updates'])
    skip = 'Skip to content' if english else '跳到正文'
    verification = ''
    if google_site_verification:
        verification += ('<meta name="google-site-verification" content="'
                         + e(google_site_verification) + '">')
    if bing_site_verification:
        verification += ('<meta name="msvalidate.01" content="'
                         + e(bing_site_verification) + '">')
    return f'''<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{verification}<title>ICODE — {e(t['title'])}</title><meta name="description" content="{e(t['description'])}">
<link rel="canonical" href="{e(canonical)}"><link rel="alternate" hreflang="zh-CN" href="{e(base)}">
<link rel="alternate" hreflang="en" href="{e(base)}en/"><link rel="alternate" hreflang="x-default" href="{e(base)}">
<meta property="og:title" content="ICODE — {e(t['title'])}"><meta property="og:description" content="{e(t['description'])}">
<meta property="og:url" content="{e(canonical)}"><meta property="og:type" content="website"><meta property="og:image" content="{e(social_image)}"><meta property="og:image:alt" content="ICODE">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="ICODE — {e(t['title'])}"><meta name="twitter:description" content="{e(t['description'])}"><meta name="twitter:image" content="{e(social_image)}"><meta name="twitter:image:alt" content="ICODE">
<link rel="icon" type="image/svg+xml" href="{favicon}">
<link rel="alternate" type="application/rss+xml" title="ICODE version notes" href="{relative}feed.xml">
<link rel="alternate" type="text/markdown" href="index.md"><link rel="describedby" type="text/plain" href="{relative}llms.txt">
<script src="{relative}locale.js"></script><link rel="stylesheet" href="{relative}style.css"></head><body itemscope itemtype="https://schema.org/SoftwareSourceCode">
<meta itemprop="name" content="ICODE"><a class="skip" href="#content">{skip}</a><header><a class="brand" href="{relative or './'}"><img src="{brand_image}" alt="ICODE" width="40" height="40"></a>
<nav aria-label="{'Navigation' if english else '导航'}"><a href="#flow">{e(t['flow_cta'])}</a><a href="#capabilities">{e(t['scenes_title'])}</a><a href="#install">{e(t['install_cta'])}</a>{switch}<a href="{REPO}">GitHub ↗</a></nav></header>
<main id="content"><section class="hero"><div><p class="eyebrow">{e(t['eyebrow'])}</p><h1>{e(t['title'])}</h1>
<p class="lead" itemprop="description">{e(t['intro'])}</p><ul class="hosts"><li>Claude Code</li><li>Codex</li><li>CodeBuddy</li></ul>
<div class="actions"><a class="button" href="#install">{e(t['install_cta'])} →</a><a href="#flow">{e(t['flow_cta'])} ↗</a></div>
<p class="version">{e(t['source_label'])} · <span itemprop="version">{e(version)}</span></p></div>
{render_diagram(t)}</section>
<section class="section" id="flow"><h2>{e(t['flow_title'])}</h2><p class="note">{e(t['flow_intro'])}</p>
<details class="workflow-details"><summary>{e(t['flow_details_label'])}</summary><ol class="flow">{flow}</ol>
<div class="stage-grid">{''.join(stages)}</div></details><details class="optional-details"><summary>{e(t['optional_title'])}</summary><div class="optional-grid">{paths}</div></details></section>
<section class="section" id="capabilities"><h2>{e(t['scenes_title'])}</h2><p class="note">{e(t['examples_note'])}</p><div class="scenes">{scenes}</div>
<details class="principles"><summary>{e(t['principles_label'])}</summary><h3>{e(t['features_title'])}</h3><div class="grid">{features}</div></details></section>
<section class="section" id="guides"><h2>{'Focused guides' if english else '专题指南'}</h2><div class="grid">{guide_links}</div></section>
<section class="section" id="delivery"><h2>{e(t['delivery_title'])}</h2><div class="delivery-grid">{delivery}</div><p class="note">{e(t['delivery_note'])}</p>
<details class="delivery-details"><summary>{e(t['delivery_details_label'])}</summary><div>{delivery_details}</div></details></section>
<section class="section" id="install"><h2>{e(t['install_title'])}</h2><div class="install-grid"><div><h3>{e(t['terminal_label'])}</h3>
<pre><code>{e(INSTALL)}</code></pre><details class="install-note-details"><summary>{e(t['install_details_label'])}</summary><p class="note">{e(t['install_note'])}</p></details></div><div><h3>{e(t['host_label'])}</h3>
<pre><code>/icode help\n{e(t['examples'][0][1])}</code></pre><details class="install-note-details"><summary>{e(t['install_details_label'])}</summary><p class="note">{e(t['host_note'])}</p></details><a href="{docs}">{e(t['docs_label'])} ↗</a></div></div></section>
<section class="section" id="examples"><details class="more-examples"><summary>{e(t['examples_title'])}</summary><p class="note">{e(t['examples_note'])}</p><div class="examples">{examples}</div></details>
<p class="boundary note">{e(t['boundary'])}</p></section>
<section class="section" id="updates"><details class="updates-details"><summary>{e(t['updates_title'])}</summary>{updates}</details></section></main>
<footer><p>{e(t['privacy'])}</p><a itemprop="codeRepository" href="{REPO}">GitHub</a><a href="{relative}feed.xml">RSS</a><a href="{relative}llms.txt">llms.txt</a><a href="index.md">Markdown</a><a itemprop="license" href="{REPO}/blob/main/LICENSE">MIT</a></footer>
</body></html>\n'''


def render_guide(data, slug, lang, base, google_site_verification=None,
                 bing_site_verification=None):
    """Render one reviewed, crawlable guide with the fixed local locale script."""
    guide = data['guides'][slug][lang]
    e = escape
    english = lang == 'en'
    relative = '../../' if english else '../'
    home_relative = '../'
    localized_prefix = 'en/' if english else ''
    canonical = base + localized_prefix + slug + '/'
    zh_url = base + slug + '/'
    en_url = base + 'en/' + slug + '/'
    social_image = base + PUBLIC_ASSETS[2]
    verification = ''
    if google_site_verification:
        verification += ('<meta name="google-site-verification" content="'
                         + e(google_site_verification) + '">')
    if bing_site_verification:
        verification += ('<meta name="msvalidate.01" content="'
                         + e(bing_site_verification) + '">')
    sections = ''.join('<section class="guide-section"><h2>' + e(section['title'])
                       + '</h2><p>' + e(section['body']) + '</p></section>'
                       for section in guide['sections'])
    commands = ''.join('<pre><code>' + e(command) + '</code></pre>'
                       for command in guide['commands'])
    back = 'Back to ICODE' if english else '返回 ICODE 首页'
    language_switch = (f'<a href="{zh_url}?lang=zh-CN" lang="zh-CN">中文</a>' if english
                       else f'<a href="{en_url}?lang=en" lang="en">English</a>')
    return f'''<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{verification}<title>{e(guide['title'])} — ICODE</title><meta name="description" content="{e(guide['description'])}">
<link rel="canonical" href="{e(canonical)}"><link rel="alternate" hreflang="zh-CN" href="{e(zh_url)}">
<link rel="alternate" hreflang="en" href="{e(en_url)}"><link rel="alternate" hreflang="x-default" href="{e(zh_url)}">
<meta property="og:title" content="{e(guide['title'])} — ICODE"><meta property="og:description" content="{e(guide['description'])}">
<meta property="og:url" content="{e(canonical)}"><meta property="og:type" content="article"><meta property="og:image" content="{e(social_image)}"><meta property="og:image:alt" content="ICODE">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="{e(guide['title'])} — ICODE"><meta name="twitter:description" content="{e(guide['description'])}"><meta name="twitter:image" content="{e(social_image)}"><meta name="twitter:image:alt" content="ICODE">
<link rel="icon" type="image/svg+xml" href="{relative}{PUBLIC_ASSETS[0]}"><script src="{relative}locale.js"></script><link rel="stylesheet" href="{relative}style.css"></head>
<body><a class="skip" href="#content">{'Skip to content' if english else '跳到正文'}</a><header><a class="brand" href="{home_relative}"><img src="{relative}{PUBLIC_ASSETS[1]}" alt="ICODE" width="40" height="40"></a>
<nav aria-label="{'Navigation' if english else '导航'}"><a href="{home_relative}">{back}</a>{language_switch}<a href="{REPO}">GitHub ↗</a></nav></header>
<main id="content" class="guide-page" itemscope itemtype="https://schema.org/TechArticle"><article><p class="eyebrow">ICODE · Claude Code · Codex · CodeBuddy · WorkBuddy</p><h1 itemprop="headline">{e(guide['title'])}</h1>
<p class="lead" itemprop="description">{e(guide['intro'])}</p>{sections}<section class="guide-section"><h2>{'Commands' if english else '命令示例'}</h2>{commands}</section>
<p class="boundary note">{'Examples explain workflow behavior; they are not execution evidence.' if english else '示例用于说明工作流行为，不代表已经执行或验证。'}</p></article></main>
<footer><a href="{home_relative}">{back}</a><a href="{REPO}">GitHub</a><a href="{REPO}/blob/main/LICENSE">MIT</a></footer></body></html>\n'''


def markdown_text(value):
    """Render public prose as text, never injected HTML, headings or links."""
    return re.sub(r'([\\`*_{}\[\]()#+!|~])', r'\\\1', escape(' '.join(value.split()), quote=False))


def markdown_code(value):
    # A public command containing backticks must not close its own code block.
    fence = '`' * max(3, 1 + max((len(run) for run in re.findall(r'`+', value)), default=0))
    return fence + '\n' + value + '\n' + fence


def render_markdown(data, lang, base, version):
    """Text counterpart of the page, derived from the same reviewed content."""
    t, m = data['locales'][lang], markdown_text
    docs = REPO + '/blob/main/' + ('README.md' if lang == 'en' else 'README.zh-CN.md')
    parts = ['# ICODE — ' + m(t['title']), '> ' + m(t['description']),
             m(t['intro']), 'Claude Code · Codex · CodeBuddy',
             m(t['source_label']) + ': ' + version,
             '[Website](' + base + ('en/' if lang == 'en' else '') + ')',
             '## ' + m(t['flow_title']), m(t['flow_intro'])]
    for label, stage in zip(t['flow'], t['stages']):
        parts.extend(['### ' + m(label) + ' / ' + stage['id'], m(stage['purpose'])])
        parts.extend('- ' + m(t[key + '_label']) + ': ' + m(stage[key])
                     for key in ('input', 'output', 'checkpoint'))
        parts.append('[' + m(t['step_docs_label']) + '](' + REPO + '/blob/main/steps/'
                     + STAGE_DOCS[stage['id']] + ')')
    parts.append('## ' + m(t['optional_title']))
    for record in t['paths']:
        parts.extend(['### ' + m(record['title']), m(record['body'])])
    parts.extend(['## ' + m(t['scenes_title']), m(t['examples_note'])])
    for scene in t['scenes']:
        parts.extend(['### ' + m(scene['title']), m(scene['body']), markdown_code(scene['command']),
                      markdown_code(scene['more']), m(scene['note'])])
    parts.append('## ' + m(t['features_title']))
    for title, body in t['features']:
        parts.extend(['### ' + m(title), m(body)])
    parts.extend(['## ' + m(t['delivery_title']), m(t['delivery_note'])])
    for record in t['delivery']:
        parts.extend(['### ' + m(record['title']), m(record['body'])])
    parts.extend(['## ' + m(t['install_title']), '### ' + m(t['terminal_label']),
                  markdown_code(INSTALL), m(t['install_note']), '### ' + m(t['host_label']),
                  markdown_code('/icode help\n' + t['examples'][0][1]), m(t['host_note']),
                  '[' + m(t['docs_label']) + '](' + docs + ')', '## ' + m(t['examples_title'])])
    for title, command in t['examples']:
        parts.extend(['### ' + m(title), markdown_code(command)])
    parts.extend([m(t['boundary']), '## ' + m(t['updates_title'])])
    for note in data['updates']:
        parts.extend(['### ' + note['version'] + ' / ' + note['reviewed_on'], m(note[lang])])
    parts.extend([m(t['privacy']), '[MIT](' + REPO + '/blob/main/LICENSE)'])
    return '\n\n'.join(parts) + '\n'


def render_llms(data, base, version):
    """A voluntary, path-scoped reading index; not a registry or ranking signal."""
    raw = 'https://raw.githubusercontent.com/ayukyo/icode-skill/main/'
    return '\n\n'.join([
        '# ICODE', '> ' + markdown_text(data['locales']['en']['description']),
        markdown_text(data['locales']['zh-CN']['description']),
        'Source version: ' + version + '. MIT licensed. Maintained installation adapters: Claude Code, Codex, CodeBuddy.',
        'Use only when the user names ICODE or resumes a bound ICODE ticket. Discovery does not imply marketplace listing, '
        'recommendation, complete installation, or validation on other hosts. Reports do not prove device verification.',
        'The source install.sh sets up shared skills and host dependencies. A generic skill download is not the full installer. '
        'This public site never reads private tickets and contains no model credentials.',
        '## Overview and installation',
        '- [English workflow and examples](' + base + 'en/index.md): purpose, stages, use cases, installation and limits.\n'
        '- [中文流程与示例](' + base + 'index.md): 用途、步骤、场景、安装与证据边界。\n'
        '- [AI coding workflow guide](' + base + 'en/ai-coding-workflow/): resumable stages and verification evidence.\n'
        '- [Multi-model code review guide](' + base + 'en/multi-model-code-review/): independent crosscheck boundaries.\n'
        '- [English README](' + raw + 'README.md): complete source installation.\n'
        '- [中文 README](' + raw + 'README.zh-CN.md): 完整安装与用法。',
        '## Skill and compatibility',
        '- [Canonical SKILL.md](' + raw + 'SKILL.md): the single workflow entry and trigger boundary.\n'
        '- [Discovery coverage](' + raw + 'docs/agent-skill-discovery.md): dated channel observations, not universal support.\n'
        '- [Distribution guide](' + raw + 'docs/skill-distribution.md): packaging versus runtime support.',
        '## Optional',
        '- [Source repository](' + REPO + '): review code and issues before installation.\n'
        '- [Public updates](' + base + 'feed.xml): reviewed source version notes.',
    ]) + '\n'


def xml_bytes(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'


def build(source_root, output, base_url, indexnow_key=None, expected_version=None,
          source_lastmod=None, google_site_verification=None,
          bing_site_verification=None):
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
    source_lastmod = normalize_source_lastmod(source_lastmod)
    google_site_verification = normalize_verification(
        google_site_verification, 'Google')
    bing_site_verification = normalize_verification(
        bing_site_verification, 'Bing')
    data = content_checked(json.loads(public_input(root, 'site/content.json')))
    skill = public_input(root, 'SKILL.md')
    match = re.search(r'^\*\*版本\*\*:\s*(' + VERSION_RE + r')\s*$', skill, re.MULTILINE)
    if not match:
        raise ValueError('missing or invalid source version')
    version = match.group(1)
    if expected_version and expected_version != version:
        raise ValueError('release/source version mismatch')
    files = {'index.html': render_page(data, 'zh-CN', base, version,
                                      google_site_verification, bing_site_verification).encode(),
             'en/index.html': render_page(data, 'en', base, version,
                                         google_site_verification, bing_site_verification).encode(),
             'style.css': public_input(root, 'site/style.css').encode(),
             'locale.js': public_input(root, 'site/locale.js').encode(), '.nojekyll': b'',
             'llms.txt': render_llms(data, base, version).encode(),
             'index.md': render_markdown(data, 'zh-CN', base, version).encode(),
             'en/index.md': render_markdown(data, 'en', base, version).encode()}
    for slug in GUIDE_SLUGS:
        files[slug + '/index.html'] = render_guide(
            data, slug, 'zh-CN', base, google_site_verification,
            bing_site_verification).encode()
        files['en/' + slug + '/index.html'] = render_guide(
            data, slug, 'en', base, google_site_verification,
            bing_site_verification).encode()
    files.update((relative, public_asset(root, relative)) for relative in PUBLIC_ASSETS)
    urls = [base, base + 'en/']
    for slug in GUIDE_SLUGS:
        urls.extend((base + slug + '/', base + 'en/' + slug + '/'))
    sitemap = ET.Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    for url in urls:
        entry = ET.SubElement(sitemap, 'url')
        ET.SubElement(entry, 'loc').text = url
        if source_lastmod:
            ET.SubElement(entry, 'lastmod').text = source_lastmod
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
    if source_lastmod:
        manifest['lastmod'] = source_lastmod
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
    parser.add_argument('--source-lastmod', help='source revision date in YYYY-MM-DD')
    parser.add_argument('--google-site-verification', help='public Google ownership token')
    parser.add_argument('--bing-site-verification', help='public Bing ownership token')
    args = parser.parse_args()
    try:
        result = build(args.source_root, args.output, args.base_url,
                       os.environ.get('INDEXNOW_KEY'), args.expected_version,
                       args.source_lastmod, args.google_site_verification,
                       args.bing_site_verification)
    except (ValueError, OSError) as exc:
        print('site build rejected: ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({'status': 'built', **result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
