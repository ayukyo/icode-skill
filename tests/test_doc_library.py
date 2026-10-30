"""Read-only document library behavior using owned temporary fixtures."""
import base64
import hashlib
import http.client
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent_runtime'))
from icode_agent.ticket_catalog import TicketCatalog, _project_id
from icode_agent.ui_server import create_ui_server


def catalog(root, projects=()):
    try:
        module = importlib.import_module('icode_agent.doc_catalog')
    except ModuleNotFoundError:
        pytest.fail('DocCatalog feature is missing')
    tickets = TicketCatalog(root / 'index.json', seed_projects=projects)
    return module.DocCatalog(root, tickets)


def book_fixture(root, relative, body='# 中文概览\n\nneedle text\n', metadata=None):
    path = root / relative
    path.mkdir(parents=True, exist_ok=True)
    (path / '00_overview.md').write_text(body, encoding='utf-8')
    if metadata is not None:
        (path / '_meta.json').write_text(json.dumps(metadata), encoding='utf-8')
    return path


def snapshot(root, version, **extra):
    body = '# 模块说明\n\nSnapshot ' + version + '\n'
    meta = dict(module_key='module-key', snapshot_id=version, source_id='s' * 64,
                branch='main', commit='c' * 40, template_version='v2', scope='full',
                status='verified', source_identity={'branch':'feature/raw', 'url':'https://example.invalid/module.git'},
                chapter_sha256={'00_overview.md': hashlib.sha256(body.encode()).hexdigest()})
    meta.update(extra)
    path = book_fixture(root, 'module_docs/module-key/snapshots/' + version, body, meta)
    meta['chapter_sha256']['00_overview.md'] = hashlib.sha256((path/'00_overview.md').read_bytes()).hexdigest()
    (path/'_meta.json').write_text(json.dumps(meta), encoding='utf-8')
    return path


def test_catalog_discovers_branches_without_tickets_and_matches_exact_project_paths(tmp_path):
    source = tmp_path / 'one' / 'same'
    other = tmp_path / 'two' / 'same'
    book_fixture(tmp_path, 'project_docs/same/main', metadata={'git_root': str(source), 'branch':'main'})
    book_fixture(tmp_path, 'project_docs/same/feature', metadata={'git_root': str(other), 'branch':'feature/raw'})
    books = catalog(tmp_path, [source]).catalog()['books']
    assert len(books) == 2
    assert {b['branch'] for b in books} == {'main','feature/raw'}
    assert next(b for b in books if b['branch']=='main')['linked_project_id'] == _project_id(source.resolve())
    assert next(b for b in books if b['branch']=='feature/raw')['linked_project_id'] is None
    assert str(tmp_path) not in json.dumps(books)
    assert catalog(tmp_path, [source]).catalog()['books'] == books


def test_exact_snapshot_dependency_and_legacy_binding_do_not_choose_latest(tmp_path):
    snapshot(tmp_path, 'a'*64)
    snapshot(tmp_path, 'b'*64)
    book_fixture(tmp_path, 'module_docs/old-key')
    book_fixture(tmp_path, 'project_docs/demo/main', metadata={'module_deps':[
        {'name':'exact','key':'module-key','snapshot_id':'a'*64},
        {'name':'missing','key':'module-key','snapshot_id':'c'*64},
        {'name':'ambiguous','key':'module-key'}, {'name':'old','key':'old-key'}]})
    docs = catalog(tmp_path)
    project = next(b for b in docs.catalog()['books'] if b['kind']=='project')
    detail = docs.book(project['id'])['book']
    deps = {d['name']: d for d in detail['dependencies']}
    assert deps['missing']['book_id'] is None and deps['missing']['status']=='missing'
    assert deps['ambiguous']['status']=='ambiguous'
    assert deps['old']['status']=='legacy'
    bound = docs.book(deps['exact']['book_id'])['book']
    assert bound['metadata']['snapshot_id']=='a'*64
    assert bound['branch']=='feature/raw'
    assert bound['metadata']['source_status']=='unverified'
    assert bound['metadata']['integrity']=='verified'


def test_legacy_header_mapping_links_images_search_and_body_changes(tmp_path):
    source = tmp_path / 'missing-source'
    path = book_fixture(tmp_path, 'project_docs/legacy', '# 中文概览\n> project_path: '+str(source)+'\n\n[下一章](10_more.md#detail)\n![示例](pic.png)\n[源码](code.py)\n[参考][ref]\n\n[ref]: 10_more.md#detail\nneedle text\n')
    (path/'10_more.md').write_text('# 详细章节\n\n## Detail\nneedle\n', encoding='utf-8')
    (path/'pic.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC'))
    docs = catalog(tmp_path, [source])
    public = docs.catalog()['books'][0]
    assert public['status']=='legacy' and public['linked_project_id']==_project_id(source.resolve())
    book = docs.book(public['id'])['book']
    chapter = docs.chapter(public['id'], book['chapters'][0]['id'])['chapter']
    assert chapter['title']=='中文概览'
    assert chapter['links']['10_more.md#detail']['fragment']=='detail'
    assert 'code.py' not in chapter['links']
    asset = chapter['assets']['pic.png']
    data, mime = docs.asset(asset)
    assert mime=='image/png' and data.startswith(b'\x89PNG')
    assert len(docs.search(public['id'], 'needle')['results'])==2
    assert docs.search(public['id'], '')['results']==[]
    assert docs.chapter(public['id'], book['chapters'][0]['id'])['chapter']['sha256']
    snap_path = snapshot(tmp_path, 'd'*64)
    snap = next(b for b in docs.catalog()['books'] if b['kind']=='module')
    (snap_path/'00_overview.md').write_text('# Changed',encoding='utf-8')
    changed = docs.book(snap['id'])['book']
    assert changed['metadata']['integrity']=='modified'
    assert docs.chapter(snap['id'],changed['chapters'][0]['id'])['chapter']['integrity']=='modified'


def test_bad_json_unicode_oversized_and_unknown_ids_are_isolated(tmp_path):
    book_fixture(tmp_path, 'project_docs/good/main')
    bad = book_fixture(tmp_path, 'project_docs/bad/main')
    (bad/'_meta.json').write_text('{oops',encoding='utf-8')
    unicode = book_fixture(tmp_path, 'project_docs/unicode/main')
    (unicode/'00_overview.md').write_bytes(b'\xff')
    huge = book_fixture(tmp_path, 'project_docs/huge/main')
    (huge/'00_overview.md').write_bytes(b'x'*(2*1024*1024+1))
    docs = catalog(tmp_path)
    result = docs.catalog()
    assert len(result['books'])==1 and result['errors']
    assert str(tmp_path) not in json.dumps(result)
    for value in ('../secret', 'unknown', '%2e%2e', ''):
        with pytest.raises(Exception, match='文档'):
            docs.book(value)


def test_snapshot_link_uses_exact_version_and_asset_rejects_nonimages(tmp_path):
    target = snapshot(tmp_path,'a'*64)
    path = book_fixture(tmp_path,'project_docs/demo/main', '[模块](../../../module_docs/module-key/snapshots/'+'a'*64+'/00_overview.md#x)\n![bad](bad.svg)\n![fake](fake.png)')
    (path/'bad.svg').write_text('<svg onload="alert(1)"/>')
    (path/'fake.png').write_bytes(b'not image')
    docs=catalog(tmp_path)
    project=next(b for b in docs.catalog()['books'] if b['kind']=='project')
    detail=docs.book(project['id'])['book']
    chapter=docs.chapter(project['id'],detail['chapters'][0]['id'])['chapter']
    assert len(chapter['links'])==1
    assert chapter['assets']=={}


def test_reparse_junction_escape_and_post_discovery_replacement_are_rejected(tmp_path):
    outside = tmp_path/'outside'
    outside.mkdir()
    (outside/'00_overview.md').write_text('# Secret')
    namespace = tmp_path/'project_docs'
    namespace.mkdir()
    link=namespace/'escape'
    if os.name=='nt':
        run=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)], capture_output=True)
        assert run.returncode==0
    else:
        link.symlink_to(outside,target_is_directory=True)
    docs=catalog(tmp_path)
    assert docs.catalog()['books']==[]
    path=book_fixture(tmp_path,'project_docs/safe/main')
    book=docs.catalog()['books'][0]
    detail=docs.book(book['id'])['book']
    (path/'00_overview.md').unlink()
    if os.name=='nt':
        # File symlinks may lack privilege; the directory-junction assertion above remains native.
        replacement=path.with_name('saved')
        path.rename(replacement)
        assert subprocess.run(['cmd','/c','mklink','/J',str(path),str(outside)],capture_output=True).returncode==0
    else:
        (path/'00_overview.md').symlink_to(outside/'00_overview.md')
    with pytest.raises(Exception, match='文档'):
        docs.chapter(book['id'],detail['chapters'][0]['id'])


def test_http_docs_routes_require_token_and_never_accept_local_paths(tmp_path):
    book_fixture(tmp_path,'project_docs/demo/main')
    server=create_ui_server(None,ROOT/'tools/icode_control.py', index_path=tmp_path/'index.json',
                            settings_path=tmp_path/'settings.json',docs_root=tmp_path)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    def get(path, token=True):
        connection=http.client.HTTPConnection(*server.server_address,timeout=5)
        connection.request('GET',path,headers={'X-ICODE-UI-Token':server.ui_token} if token else {})
        response=connection.getresponse()
        status,body=response.status,response.read()
        connection.close()
        return status,json.loads(body)
    try:
        for endpoint in ('catalog','book?id=bad','chapter?book_id=bad&chapter_id=bad','search?book_id=bad&q=x','asset?id=bad'):
            assert get('/api/v1/docs/'+endpoint,False)[0]==403
        status,payload=get('/api/v1/docs/catalog')
        assert status==200 and len(payload['books'])==1
        book_id=payload['books'][0]['id']
        assert get('/api/v1/docs/book?id='+book_id)[0]==200
        status,payload=get('/api/v1/docs/book?id=../../secret')
        assert status==404 and payload['error_class']=='DocLibraryError'
        assert str(tmp_path) not in json.dumps(payload)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)

def test_image_formats_dimensions_and_size_are_vetted(tmp_path):
    path=book_fixture(tmp_path,'project_docs/demo/main','![fake](fake.png)\n![large](large.png)\n![valid](pixel.gif)')
    (path/'fake.png').write_bytes(b'\x89PNG\r\n\x1a\nmalformed')
    (path/'large.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'x'*(8*1024*1024))
    (path/'pixel.gif').write_bytes(base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'))
    docs=catalog(tmp_path)
    book=docs.book(docs.catalog()['books'][0]['id'])['book']
    chapter=docs.chapter(book['id'],book['chapters'][0]['id'])['chapter']
    assert set(chapter['assets'])=={'pixel.gif'}
    assert docs.asset(chapter['assets']['pixel.gif'])[1]=='image/gif'


def test_dependency_source_mismatch_partial_and_malformed_entries(tmp_path):
    snapshot(tmp_path,'a'*64,scope='partial')
    book_fixture(tmp_path,'project_docs/demo/main',metadata={'module_deps':[
        {'key':'module-key','snapshot_id':'a'*64,'name':'partial'},
        {'key':'module-key','snapshot_id':'a'*64,'source_id':'wrong','name':'wrong'},None]})
    docs=catalog(tmp_path)
    project=next(b for b in docs.catalog()['books'] if b['kind']=='project')
    deps=docs.book(project['id'])['book']['dependencies']
    assert [d['status'] for d in deps]==['partial','source_mismatch','invalid']


def test_real_http_handler_auth_json_and_image_bytes_without_network(tmp_path):
    import io
    from icode_agent.ui_server import AgentUIRequestHandler
    path=book_fixture(tmp_path,'project_docs/demo/main','![pixel](pixel.gif)')
    (path/'pixel.gif').write_bytes(base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'))
    server=create_ui_server(None,ROOT/'tools/icode_control.py',index_path=tmp_path/'index.json',
                            settings_path=tmp_path/'settings.json',docs_root=tmp_path)
    class Wire:
        def __init__(self,request):
            self.input=io.BytesIO(request)
            self.output=bytearray()
        def makefile(self,*args,**kwargs):
            return self.input
        def sendall(self,value):
            self.output.extend(value)
    def request(url,token=True):
        headers=f'Host: 127.0.0.1:{server.server_port}\r\n'
        if token:
            headers+='X-ICODE-UI-Token: '+server.ui_token+'\r\n'
        wire=Wire(('GET '+url+' HTTP/1.0\r\n'+headers+'\r\n').encode())
        AgentUIRequestHandler(wire,('127.0.0.1',1),server)
        head,body=bytes(wire.output).split(b'\r\n\r\n',1)
        return int(head.split()[1]),head,body
    try:
        for endpoint in ('catalog','book?id=x','chapter?book_id=x&chapter_id=y','asset?id=x','search?book_id=x&q=x'):
            assert request('/api/v1/docs/'+endpoint,False)[0]==403
        status,_,raw=request('/api/v1/docs/catalog')
        assert status==200
        bid=json.loads(raw)['books'][0]['id']
        book=json.loads(request('/api/v1/docs/book?id='+bid)[2])['book']
        chapter=json.loads(request('/api/v1/docs/chapter?book_id='+bid+'&chapter_id='+book['chapters'][0]['id'])[2])['chapter']
        asset=chapter['assets']['pixel.gif']
        status,headers,body=request('/api/v1/docs/asset?id='+asset)
        assert status==200 and b'Content-Type: image/gif' in headers
        assert body==(path/'pixel.gif').read_bytes()
        assert request('/api/v1/docs/asset?id='+asset,False)[0]==403
        assert request('/api/v1/docs/book?id=../../secret')[0]==404
        assert request('/api/v1/docs/book?id='+bid+'&path=secret')[0]==400
    finally:
        server.server_close()


def test_legacy_actual_template_header_links_project_without_basename_guess(tmp_path):
    source = tmp_path / 'offline' / 'original'
    book_fixture(tmp_path, 'project_docs/arbitrary',
                 '# 历史工程\n> - 工程本地路径（project_path，工程根绝对路径，段零 3.6 跨工程源码定位 + 冲突检测用）：' +
                 str(source) + '\n> - 分支/提交：main @ a3f2b1c (2026-07-06 12:30)\n')
    book = catalog(tmp_path, [source]).catalog()['books'][0]
    assert book['linked_project_id'] == _project_id(source.resolve())
    assert book['branch'] == 'main'


def test_chapter_response_uses_same_bytes_for_markdown_sha_and_integrity(tmp_path, monkeypatch):
    path = snapshot(tmp_path, 'a'*64)
    docs = catalog(tmp_path)
    book = docs.book(docs.catalog()['books'][0]['id'])['book']
    original = docs._resolve_book if hasattr(docs, '_resolve_book') else lambda bid: docs._find(docs._scan()[0], bid)
    def resolve_then_edit(bid):
        result = original(bid)
        (path/'00_overview.md').write_bytes(b'# Changed after discovery\n')
        return result
    monkeypatch.setattr(docs, '_resolve_book', resolve_then_edit, raising=False)
    chapter = docs.chapter(book['id'], book['chapters'][0]['id'])['chapter']
    assert chapter['markdown'] == '# Changed after discovery\n'
    assert chapter['sha256'] == hashlib.sha256(chapter['markdown'].encode()).hexdigest()
    assert chapter['integrity'] == 'modified'


def test_catalog_reads_headers_and_search_reads_only_selected_book(tmp_path, monkeypatch):
    first = book_fixture(tmp_path, 'project_docs/first/main', '# First\n' + 'x'*100000 + '\nneedle')
    second = book_fixture(tmp_path, 'project_docs/second/main', '# Second\n' + 'y'*100000)
    docs = catalog(tmp_path)
    reads = []
    original = docs._read
    def track(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        reads.append((Path(path), len(result)))
        return result
    monkeypatch.setattr(docs, '_read', track)
    books = docs.catalog()['books']
    assert sum(size for _, size in reads) < 20000
    reads.clear()
    bid = next(b['id'] for b in books if b['name'] == 'first')
    assert docs.search(bid, 'needle')['results']
    assert not any(path.parent == second and size > 8192 for path, size in reads)
    assert any(path.parent == first and size > 8192 for path, size in reads)


def test_asset_after_header_is_readable_without_scanning_other_bodies(tmp_path):
    path = book_fixture(tmp_path, 'project_docs/long/main', '# Long\n' + 'x'*10000 + '\n![p](pixel.gif)')
    (path/'pixel.gif').write_bytes(base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'))
    docs = catalog(tmp_path)
    book = docs.book(docs.catalog()['books'][0]['id'])['book']
    chapter = docs.chapter(book['id'], book['chapters'][0]['id'])['chapter']
    assert docs.asset(chapter['assets']['pixel.gif'])[1] == 'image/gif'


def test_bounded_search_and_malformed_metadata_do_not_break_other_books(tmp_path):
    bad = book_fixture(tmp_path, 'project_docs/bad/main')
    (bad/'_meta.json').write_bytes(b'x'*(32*1024*1024+1))
    array = book_fixture(tmp_path, 'project_docs/array/main')
    (array/'_meta.json').write_text('[]', encoding='utf-8')
    source = book_fixture(tmp_path, 'project_docs/good/main', '# Good\nneedle')
    for index in range(51):
        (source/f'{index+10}_chapter.md').write_text('# Title\nneedle', encoding='utf-8')
    docs = catalog(tmp_path)
    result = docs.catalog()
    assert len(result['books']) == 1 and len(result['errors']) == 2
    bid = result['books'][0]['id']
    search = docs.search(bid, 'needle')
    assert len(search['results']) == 50 and search['truncated']
    with pytest.raises(Exception, match='文档搜索'):
        docs.search(bid, 'x'*201)


def test_large_real_shaped_metadata_is_projected_and_stat_cached(tmp_path, monkeypatch):
    path = snapshot(tmp_path, 'a'*64)
    meta_path = path/'_meta.json'
    meta = json.loads(meta_path.read_text())
    meta['source_identity']['files'] = [{'path':'source/'+str(index), 'fingerprint':'a'*64} for index in range(80000)]
    meta['review'] = {'evidence':['x'*10000]}
    meta_path.write_text(json.dumps(meta), encoding='utf-8')
    assert meta_path.stat().st_size > 8*1024*1024
    docs = catalog(tmp_path)
    original = docs._read
    metadata_reads = []
    def track(path, *args, **kwargs):
        if Path(path) == meta_path:
            metadata_reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(docs, '_read', track)
    result = docs.catalog()
    assert len(result['books']) == 1 and not result['errors']
    bid = result['books'][0]['id']
    book = docs.book(bid)['book']
    assert book['metadata']['source_id'] == 's'*64
    assert len(metadata_reads) == 1
    assert 'files' not in docs._metadata(path)['source_identity']
    assert 'review' not in docs._metadata(path)
    meta['scope'] = 'partial'
    meta_path.write_text(json.dumps(meta), encoding='utf-8')
    assert docs.book(bid)['book']['metadata']['scope'] == 'partial'
    assert len(metadata_reads) == 2


def test_selected_requests_do_not_rescan_library_and_deep_links_work(tmp_path, monkeypatch):
    path = book_fixture(tmp_path, 'project_docs/selected/main', '# Selected\nneedle')
    other = book_fixture(tmp_path, 'project_docs/other/main', '# Other\nprivate')
    docs = catalog(tmp_path)
    bid = next(b['id'] for b in docs.catalog()['books'] if b['name']=='selected')
    original = docs._read
    def reject_other(path, *args, **kwargs):
        assert not Path(path).is_relative_to(other)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(docs, '_read', reject_other)
    book = docs.book(bid)['book']
    assert docs.chapter(bid, book['chapters'][0]['id'])['chapter']['markdown'].startswith('# Selected')
    assert docs.search(bid, 'needle')['results']
    (path/'00_overview.md').write_text('# Changed\nneedle', encoding='utf-8')
    assert docs.chapter(bid, book['chapters'][0]['id'])['chapter']['markdown'].startswith('# Changed')
    assert catalog(tmp_path).book(bid)['book']['id'] == bid


def test_cached_metadata_still_rejects_reparse_replacement(tmp_path):
    path = snapshot(tmp_path, 'a'*64)
    docs = catalog(tmp_path)
    bid = docs.catalog()['books'][0]['id']
    outside = tmp_path/'external-meta'
    outside.mkdir()
    (outside/'_meta.json').write_text('{}', encoding='utf-8')
    metadata = path/'_meta.json'
    metadata.unlink()
    if os.name == 'nt':
        assert subprocess.run(['cmd','/c','mklink','/J',str(metadata),str(outside)], capture_output=True).returncode == 0
    else:
        metadata.symlink_to(outside/'_meta.json')
    with pytest.raises(Exception, match='文档'):
        docs.book(bid)


def test_reviewed_snapshot_without_hash_manifest_is_unverified_but_readable(tmp_path):
    path = snapshot(tmp_path, 'a'*64)
    metadata = json.loads((path/'_meta.json').read_text())
    metadata.pop('chapter_sha256')
    (path/'_meta.json').write_text(json.dumps(metadata), encoding='utf-8')
    docs = catalog(tmp_path)
    public = docs.catalog()['books'][0]
    assert public['status'] == 'unverified'
    book = docs.book(public['id'])['book']
    assert book['metadata']['integrity'] == 'unverified'
    assert docs.chapter(book['id'], book['chapters'][0]['id'])['chapter']['markdown']


@pytest.mark.parametrize('changes', [
    {'snapshot_id':'b'*64},
    {'module_key':'wrong-module'},
    {'source_identity':{'source_id':'different-source'}},
    {'source_identity':{'module_key':'wrong-module'}},
])
def test_snapshot_identity_contradictions_stay_readable_but_cannot_bind(tmp_path, changes):
    snapshot(tmp_path, 'a'*64, **changes)
    book_fixture(tmp_path, 'project_docs/demo/main', metadata={'module_deps':[
        {'name':'module','key':'module-key','snapshot_id':'a'*64}]})
    docs = catalog(tmp_path)
    books = docs.catalog()['books']
    module = next(book for book in books if book['kind']=='module')
    assert module['status'] == 'invalid'
    book = docs.book(module['id'])['book']
    assert docs.chapter(book['id'],book['chapters'][0]['id'])['chapter']['markdown']
    project = next(book for book in books if book['kind']=='project')
    dependency = docs.book(project['id'])['book']['dependencies'][0]
    assert dependency['status'] == 'invalid' and dependency['book_id'] is None


def test_dependency_rechecks_identity_changed_after_catalog_without_full_scan(tmp_path, monkeypatch):
    path = snapshot(tmp_path, 'a'*64)
    book_fixture(tmp_path, 'project_docs/demo/main', metadata={'module_deps':[
        {'name':'module','key':'module-key','snapshot_id':'a'*64}]})
    docs = catalog(tmp_path)
    project = next(book for book in docs.catalog()['books'] if book['kind']=='project')
    meta = json.loads((path/'_meta.json').read_text())
    meta['snapshot_id'] = 'b'*64
    (path/'_meta.json').write_text(json.dumps(meta), encoding='utf-8')
    def no_scan():
        pytest.fail('Selected dependency validation must not scan the entire library')
    monkeypatch.setattr(docs, '_scan', no_scan)
    dep = docs.book(project['id'])['book']['dependencies'][0]
    assert dep == {'name':'module','book_id':None,'status':'invalid'}


def test_duplicate_images_are_read_once_and_distinct_images_obey_budget(tmp_path, monkeypatch):
    pixel = base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')
    body = '# Images\n' + '\n'.join('![same](same.gif)' for _ in range(1000))
    body += '\n![alias](./same.gif)\n![two](two.gif)\n![three](three.gif)'
    path = book_fixture(tmp_path, 'project_docs/images/main', body)
    for name in ('same.gif','two.gif','three.gif'):
        (path/name).write_bytes(pixel)
    docs = catalog(tmp_path)
    bid = docs.catalog()['books'][0]['id']
    book = docs.book(bid)['book']
    calls = []
    original = docs._image
    def track(path):
        calls.append(Path(path).name)
        return original(path)
    monkeypatch.setattr(docs, '_image', track)
    module = importlib.import_module('icode_agent.doc_catalog')
    monkeypatch.setattr(module, 'MAX_CHAPTER_IMAGE_BYTES', len(pixel)*2, raising=False)
    result = docs.chapter(bid, book['chapters'][0]['id'])['chapter']
    assert calls.count('same.gif') == 1
    assert set(result['assets']) == {'same.gif','./same.gif','two.gif'}
    assert calls == ['same.gif','two.gif']
    calls.clear()
    monkeypatch.setattr(module, 'MAX_CHAPTER_IMAGES', 1)
    monkeypatch.setattr(module, 'MAX_CHAPTER_IMAGE_BYTES', len(pixel)*10)
    result = docs.chapter(bid, book['chapters'][0]['id'])['chapter']
    assert set(result['assets']) == {'same.gif','./same.gif'}
    assert calls == ['same.gif']


def test_project_chapter_versions_use_own_sources_and_digests(tmp_path):
    path = book_fixture(tmp_path, 'project_docs/versioned/main', '# First\n')
    (path/'10_second.md').write_bytes(b'# Second\n')
    first_hash = hashlib.sha256((path/'00_overview.md').read_bytes()).hexdigest()
    second_hash = hashlib.sha256((path/'10_second.md').read_bytes()).hexdigest()
    meta = {'head_commit':'head', 'last_generated_commit':'book-baseline',
        'source_id':'whole-book-source', 'template_version':'book-template',
        'chapter_sha256':{'00_overview.md':'0'*64,'10_second.md':second_hash},
        'chapter_versions':{
            '00_overview.md':{'generation_commit':'first-commit','source_id':'first-source',
                              'template_version':'first-template','chapter_sha256':first_hash},
            '10_second.md':{'source_id':'second-source','template_version':'second-template'}},
        'chapter_baselines':{'00_overview.md':{'commit':'wrong-old-baseline'},
                             '10_second.md':{'commit':'second-commit'}}}
    (path/'_meta.json').write_text(json.dumps(meta),encoding='utf-8')
    docs = catalog(tmp_path)
    book = docs.book(docs.catalog()['books'][0]['id'])['book']
    assert book['metadata']['commit'] == 'book-baseline'
    first,second = [docs.chapter(book['id'], c['id'])['chapter'] for c in book['chapters']]
    assert first['integrity'] == second['integrity'] == 'verified'
    assert first['source'] == {'commit':'first-commit','source_id':'first-source','template_version':'first-template'}
    assert second['source'] == {'commit':'second-commit','source_id':'second-source','template_version':'second-template'}
    (path/'00_overview.md').write_bytes(b'# First edited\n')
    assert docs.chapter(book['id'],first['id'])['chapter']['integrity'] == 'modified'
    meta.pop('last_generated_commit')
    (path/'_meta.json').write_text(json.dumps(meta),encoding='utf-8')
    assert docs.book(book['id'])['book']['metadata']['commit'] == 'head'
