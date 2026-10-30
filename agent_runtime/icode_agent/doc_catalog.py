"""Bounded, read-only projections of the historical DOC namespaces.

Source checkout availability is deliberately independent of document integrity.
Only discovered chapters and their vetted image references can be read by ID.
"""
from __future__ import annotations

from dataclasses import dataclass
import codecs
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from urllib.parse import unquote, urlsplit

from .ticket_catalog import CatalogError, _project_id

MAX_CHAPTER_BYTES = 2 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_BOOKS = 1000
MAX_CHAPTERS = 256
MAX_SEARCH_BYTES = 16 * 1024 * 1024
MAX_BOOK_BYTES = 16 * 1024 * 1024
HEADER_BYTES = 8192
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 16000
MAX_CHAPTER_IMAGES = 32
MAX_CHAPTER_IMAGE_BYTES = 32 * 1024 * 1024
MAX_RESULTS = 50
REPARSE_POINT = 0x400


class DocLibraryError(RuntimeError):
    """A concise, path-free failure suitable for an HTTP response."""

    def __init__(self, message='文档不存在或不可安全读取', status=404):
        super().__init__(message)
        self.status = status


def _id(kind, *parts):
    digest = hashlib.sha256('\0'.join(map(str, parts)).encode('utf-8')).hexdigest()
    return kind + '-' + digest[:32]


def _text(value, default=''):
    return value if isinstance(value, str) else default


@dataclass
class _Book:
    path: Path
    public: dict
    meta: dict
    chapters: list
    bodies: dict


class DocCatalog:
    def __init__(self, docs_root: Path, ticket_catalog=None):
        # abspath preserves links so every component can be rejected before resolve.
        self.root = Path(os.path.abspath(os.path.expanduser(str(docs_root))))
        self.ticket_catalog = ticket_catalog
        self._lock = threading.RLock()
        self._registry = {}
        self._metadata_cache = {}
        self._initialized = False
        self._projects = {}

    def _safe(self, path):
        path = Path(os.path.abspath(str(path)))
        try:
            path.relative_to(self.root)
            for component in (*reversed(path.parents), path):
                info = component.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & REPARSE_POINT:
                    raise DocLibraryError()
            if not path.resolve().is_relative_to(self.root.resolve()):
                raise DocLibraryError()
        except (OSError, ValueError, RuntimeError) as exc:
            if isinstance(exc, DocLibraryError):
                raise
            raise DocLibraryError() from exc
        return path

    def _read(self, path, limit, *, prefix=None):
        path = self._safe(path)
        try:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise DocLibraryError('文档内容过大或类型不受支持', 413)
            flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                    raise DocLibraryError()
                body = stream.read(prefix if prefix is not None else limit + 1)
                after = os.fstat(stream.fileno())
            self._safe(path)
            current = path.stat()
            if len(body) > limit:
                raise DocLibraryError('文档内容过大或类型不受支持', 413)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                    current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) or (
                    opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise DocLibraryError('文档读取期间发生变化，请重试', 409)
            return body
        except OSError as exc:
            raise DocLibraryError() from exc

    def _children(self, path):
        try:
            self._safe(path)
            # Limit both enumeration and the work caused by a malformed namespace.
            with os.scandir(path) as entries:
                children = []
                for entry in entries:
                    if len(children) >= MAX_BOOKS:
                        break
                    children.append(Path(entry.path))
            return sorted(children, key=lambda item: item.name.casefold())
        except (OSError, DocLibraryError):
            return []

    def _size(self, path):
        try:
            return self._safe(path).stat().st_size
        except OSError as exc:
            raise DocLibraryError() from exc

    def _metadata(self, path):
        target = path / '_meta.json'
        if not target.exists() and not target.is_symlink():
            self._metadata_cache.pop(target, None)
            return {}
        try:
            self._safe(target)
            info = target.stat()
            signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            cached = self._metadata_cache.get(target)
            if cached is not None and cached[0] == signature:
                return cached[1]
            value = json.loads(self._read(target, MAX_METADATA_BYTES).decode('utf-8-sig'))
            if not isinstance(value, dict):
                raise ValueError()
            # Historical source_identity.files may contain megabytes of source inventory.
            # Preserve only fields used by the reader, never this full inventory or review.
            projected = {key: value[key] for key in (
                'branch', 'commit', 'source_id', 'snapshot_id', 'template_version', 'scope',
                'name', 'project_name', 'git_root', 'project_path', 'status', 'module_key', 'url',
                'head_commit', 'last_generated_commit', 'chapter_sha256', 'module_deps') if key in value}
            identity = value.get('source_identity')
            if isinstance(identity, dict):
                projected['source_identity'] = {key: identity[key] for key in (
                    'branch', 'url', 'commit', 'source_id', 'snapshot_id', 'module_key', 'template_version', 'scope')
                    if key in identity}
            if isinstance(projected.get('module_deps'), list):
                projected['module_deps'] = [
                    {key: dep[key] for key in ('key', 'module_key', 'name', 'snapshot_id', 'source_id') if key in dep}
                    if isinstance(dep, dict) else None for dep in projected['module_deps'][:MAX_BOOKS]]
            for section in ('chapter_versions', 'chapter_baselines'):
                records = value.get(section)
                if isinstance(records, dict):
                    projected[section] = {
                        name: {key: record[key] for key in ('generation_commit', 'commit', 'source_id',
                                                           'template_version', 'chapter_sha256') if key in record}
                        for name, record in list(records.items())[:MAX_CHAPTERS] if isinstance(record, dict)}
            self._metadata_cache[target] = (signature, projected)
            return projected
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise DocLibraryError('文档元信息无效', 409) from exc
        except OSError as exc:
            raise DocLibraryError() from exc

    @staticmethod
    def _header(body, key):
        for line in body.splitlines()[:50]:
            match = re.match(r'^\s*>?\s*(?:[-*]\s*)?(?:\*\*)?' + re.escape(key) +
                             r'(?:\*\*)?\s*[:：]\s*(.*?)\s*$', line)
            if match:
                return match.group(1).strip().strip('`')
        return ''

    def _trusted_projects(self):
        if self.ticket_catalog is None:
            return {}
        try:
            paths = self.ticket_catalog._project_paths(self.ticket_catalog._entries())
        except (CatalogError, OSError, ValueError):
            paths = list(self.ticket_catalog.seed_projects)
        return {os.path.normcase(os.path.normpath(str(p.resolve()))): _project_id(p) for p in paths}

    @staticmethod
    def _chapter_record(meta, section, name):
        records = meta.get(section)
        record = records.get(name) if isinstance(records, dict) else None
        return record if isinstance(record, dict) else {}

    @staticmethod
    def _expected_hash(meta, name):
        version = DocCatalog._chapter_record(meta, 'chapter_versions', name)
        if 'chapter_sha256' in version:
            return version['chapter_sha256']
        hashes = meta.get('chapter_sha256')
        if isinstance(hashes, dict) and name in hashes:
            return hashes[name]
        return DocCatalog._chapter_record(meta, 'chapter_baselines', name).get('chapter_sha256')

    @staticmethod
    def _book_commit(meta):
        return (_text(meta.get('commit')) or _text(meta.get('last_generated_commit'))
                or _text(meta.get('head_commit')))

    @staticmethod
    def _chapter_source(meta, name):
        version = DocCatalog._chapter_record(meta, 'chapter_versions', name)
        baseline = DocCatalog._chapter_record(meta, 'chapter_baselines', name)
        has_record = any(isinstance(meta.get(section), dict) and name in meta[section]
                         for section in ('chapter_versions', 'chapter_baselines'))
        if has_record:
            return {
                'commit': _text(version.get('generation_commit')) or _text(version.get('commit'))
                          or _text(baseline.get('commit')),
                'source_id': _text(version.get('source_id')) or _text(baseline.get('source_id')),
                'template_version': _text(version.get('template_version')) or _text(baseline.get('template_version')),
            }
        identity = meta.get('source_identity')
        identity = identity if isinstance(identity, dict) else {}
        return {'commit': DocCatalog._book_commit(meta) or _text(identity.get('commit')),
                'source_id': _text(meta.get('source_id')) or _text(identity.get('source_id')),
                'template_version': _text(meta.get('template_version')) or _text(identity.get('template_version'))}

    @staticmethod
    def _recorded_status(meta, kind, family, version, chapters):
        """Recorded review, document hashes and live-source matching are separate claims."""
        if version == 'legacy':
            return 'legacy'
        identity = meta.get('source_identity')
        identity = identity if isinstance(identity, dict) else {}
        if kind == 'module':
            for field, expected in (('snapshot_id', version), ('module_key', family)):
                if any(record.get(field) not in (None, '', expected) for record in (meta, identity)):
                    return 'invalid'
            if meta.get('source_id') and identity.get('source_id') and meta['source_id'] != identity['source_id']:
                return 'invalid'
        hashes = meta.get('chapter_sha256')
        expected = [DocCatalog._expected_hash(meta, chapter['name']) for chapter in chapters]
        complete_manifest = bool(expected) and all(
            isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) for value in expected)
        if kind == 'module' and isinstance(hashes, dict) and set(hashes) != {chapter['name'] for chapter in chapters}:
            complete_manifest = False
        status = _text(meta.get('status'), 'unverified')
        if status == 'verified':
            if not complete_manifest:
                return 'unverified'
            if kind == 'module' and not all(meta.get(key) for key in ('snapshot_id', 'module_key', 'source_id')):
                return 'unverified'
        if meta.get('scope') == 'partial':
            return 'partial'
        return status

    def _load(self, path, kind, family, version, projects):
        self._safe(path)
        if not path.is_dir():
            return None
        files = [p for p in self._children(path) if p.suffix.lower() == '.md']
        if not files:
            return None
        if len(files) > MAX_CHAPTERS:
            raise DocLibraryError('文档章节数量超限', 413)
        meta = self._metadata(path)
        bodies, chapters = {}, []
        bid = _id('book', path.relative_to(self.root).as_posix())
        for file in files:
            try:
                raw = self._read(file, MAX_CHAPTER_BYTES, prefix=HEADER_BYTES)
                # A prefix can end in the middle of a valid multibyte character.
                decoder = codecs.getincrementaldecoder('utf-8-sig')()
                body = decoder.decode(raw, final=len(raw) < HEADER_BYTES)
            except UnicodeError as exc:
                raise DocLibraryError('文档正文不是有效 UTF-8', 409) from exc
            title = re.search(r'^#\s+(.+?)\s*#*\s*$', body, re.M)
            cid = _id('chapter', bid, file.name)
            chapters.append({'id': cid, 'name': file.name,
                             'title': title.group(1) if title else file.stem})
            bodies[cid] = (file, body)
        overview = next((b for p,b in bodies.values() if p.name=='00_overview.md'), '')
        identity = meta.get('source_identity')
        identity = identity if isinstance(identity, dict) else {}
        branch = _text(identity.get('branch')) or _text(meta.get('branch'))
        if not branch:
            branch = self._header(overview, 'branch') or self._header(overview, '分支/提交').split('@')[0].strip()
        branch = branch or (version if kind=='project' and version!='legacy' else 'unknown')
        linked = None
        if kind == 'project':
            raw = _text(meta.get('git_root')) or _text(meta.get('project_path'))
            raw = raw or self._header(overview, 'project_path') or self._header(overview, '工程本地路径（project_path）')
            if not raw:
                # Older templates include an explanatory suffix inside these parentheses.
                for line in overview.splitlines()[:50]:
                    match = re.match(r'^\s*>?\s*(?:[-*]\s*)?工程本地路径[（(]project_path[^）)]*[）)]\s*[:：]\s*(.*?)\s*$', line)
                    if match:
                        raw = match.group(1).strip().strip('`')
                        break
            if raw and Path(raw).is_absolute():
                normalized = os.path.normcase(os.path.normpath(str(Path(raw).resolve())))
                linked = projects.get(normalized)
        url = _text(identity.get('url')) or _text(meta.get('url'))
        module_name = Path(urlsplit(url).path).name.removesuffix('.git') if url else family
        name = _text(meta.get('name')) or _text(meta.get('project_name')) or (module_name if kind=='module' else family)
        status = self._recorded_status(meta, kind, family, version, chapters)
        public = dict(id=bid,kind=kind,name=name,branch=branch,version=version,
                      project_id=family if kind=='project' else None,linked_project_id=linked,
                      module_key=family if kind=='module' else None,status=status,chapter_count=len(chapters))
        return _Book(path, public, meta, chapters, bodies)

    def _discover(self):
        books, errors = {}, []
        projects = self._trusted_projects()
        def add(path, kind, family, version):
            if len(books) >= MAX_BOOKS:
                if '文档集数量超限，目录已截断' not in errors:
                    errors.append('文档集数量超限，目录已截断')
                return
            try:
                book = self._load(path,kind,family,version,projects)
                if book:
                    books[book.public['id']] = book
            except (DocLibraryError, ValueError, OSError, UnicodeError, RecursionError):
                errors.append('文档集不可读或元信息无效：' + family[:100] + '（' + version[:12] + '）')
        for family in self._children(self.root/'project_docs'):
            add(family,'project',family.name,'legacy')
            for branch in self._children(family):
                if branch.is_dir():
                    add(branch,'project',family.name,branch.name)
        for family in self._children(self.root/'module_docs'):
            add(family,'module',family.name,'legacy')
            for version in self._children(family/'snapshots'):
                if version.is_dir() and re.fullmatch(r'[0-9a-f]{64}',version.name):
                    add(version,'module',family.name,version.name)
        self._projects = projects
        return books, list(dict.fromkeys(errors))[:100]

    def _scan(self):
        with self._lock:
            books, errors = self._discover()
            self._registry = books
            self._initialized = True
            # Drop stale projected metadata instead of growing a persistent pathname cache.
            visible_meta = {book.path / '_meta.json' for book in books.values()}
            self._metadata_cache = {path: entry for path, entry in self._metadata_cache.items()
                                    if path in visible_meta}
            return books, errors

    def _resolve_book(self, bid):
        if not isinstance(bid, str) or not re.fullmatch(r'book-[0-9a-f]{32}', bid):
            raise DocLibraryError()
        with self._lock:
            if not self._initialized:
                self._scan()
            registered = self._find(self._registry, bid)
            public = registered.public
            # A registered ID is only a descriptor, never authority to skip fresh path checks.
            book = self._load(registered.path, public['kind'],
                              public['module_key'] or public['project_id'], public['version'], self._projects)
            if book is None:
                raise DocLibraryError()
            self._registry[bid] = book
            return book

    def catalog(self):
        books, errors = self._scan()
        return {'ok':True,'books':[book.public for book in books.values()],'errors':errors}

    @staticmethod
    def _find(books, bid):
        if not isinstance(bid,str) or bid not in books:
            raise DocLibraryError()
        return books[bid]

    def _integrity(self, book):
        hashes = book.meta.get('chapter_sha256')
        expected = [self._expected_hash(book.meta, chapter['name']) for chapter in book.chapters]
        if not any(value is not None for value in expected):
            return 'unverified'
        if isinstance(hashes, dict) and set(hashes) - {c['name'] for c in book.chapters}:
            return 'modified'
        states = []
        total = 0
        for chapter in book.chapters:
            path = self._safe(book.bodies[chapter['id']][0])
            total += self._size(path)
            if total > MAX_BOOK_BYTES:
                return 'unverified'
            states.append(self._chapter_status(book, chapter))
        if 'modified' in states:
            return 'modified'
        return 'verified' if all(s=='verified' for s in states) else 'unverified'

    def _chapter_status(self, book, chapter, raw=None):
        expected = self._expected_hash(book.meta, chapter['name'])
        if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):
            return 'unverified'
        if raw is None:
            raw = self._read(book.bodies[chapter['id']][0],MAX_CHAPTER_BYTES)
        return 'verified' if hashlib.sha256(raw).hexdigest()==expected else 'modified'

    def _dependencies(self, book, books):
        deps = book.meta.get('module_deps',[])
        if not isinstance(deps,list):
            return [{'name':'依赖元信息无效','book_id':None,'status':'invalid'}]
        result=[]
        for dep in deps[:MAX_BOOKS]:
            if not isinstance(dep,dict):
                result.append({'name':'未知模块','book_id':None,'status':'invalid'})
                continue
            key = _text(dep.get('key')) or _text(dep.get('module_key'))
            name = _text(dep.get('name')) or key or '未知模块'
            bound = dep.get('snapshot_id')
            matches=[b for b in books.values() if b.public['kind']=='module' and b.public['module_key']==key]
            if bound:
                matches=[b for b in matches if b.public['version']==bound]
            else:
                legacy=[b for b in matches if b.public['version']=='legacy']
                if legacy:
                    matches=legacy
                elif matches:
                    # Unbound historical dependencies never silently acquire an exact version.
                    result.append({'name':name,'book_id':None,'status':'ambiguous'})
                    continue
            if len(matches)!=1:
                result.append({'name':name,'book_id':None,'status':'ambiguous' if len(matches)>1 else 'missing'})
                continue
            target=matches[0]
            try:
                self._safe(target.path)
                target_meta = self._metadata(target.path)
                status = self._recorded_status(target_meta, 'module', key, target.public['version'], target.chapters)
            except DocLibraryError:
                result.append({'name':name,'book_id':None,'status':'invalid'})
                continue
            if status == 'invalid':
                result.append({'name':name,'book_id':None,'status':'invalid'})
                continue
            source = _text(dep.get('source_id'))
            if source and source != target_meta.get('source_id'):
                result.append({'name':name,'book_id':None,'status':'source_mismatch'})
                continue
            result.append({'name':name,'book_id':target.public['id'],'status':status})
        return result

    def book(self, bid):
        book=self._resolve_book(bid)
        books=self._registry.copy()
        identity=book.meta.get('source_identity')
        identity=identity if isinstance(identity,dict) else {}
        metadata={key:_text(book.meta.get(key)) or _text(identity.get(key)) for key in (
            'commit','source_id','snapshot_id','template_version','scope')}
        metadata['commit'] = self._book_commit(book.meta) or _text(identity.get('commit'))
        metadata.update(branch=book.public['branch'],source_status='unverified',integrity=self._integrity(book))
        return {'ok':True,'book':{**book.public,'chapters':book.chapters,
                'dependencies':self._dependencies(book,books),'metadata':metadata}}

    @staticmethod
    def _targets(markdown):
        # Remove fenced code; it contains examples, not navigable document references.
        body=re.sub(r'(^```[^\n]*\n.*?^```\s*$|^~~~[^\n]*\n.*?^~~~\s*$)','',markdown,flags=re.M|re.S)
        refs={m.group(1).strip().casefold():m.group(2) for m in re.finditer(
            r'^\s{0,3}\[([^\[\]\n]+)\]:\s*<?([^\s>]+)>?',body,re.M)}
        found=[]
        for m in re.finditer(r'(!?)\[([^\[\]\n]*)\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+[^)\n]*)?\)',body):
            found.append((bool(m.group(1)),m.group(3) or m.group(4)))
        for m in re.finditer(r'(!?)\[([^\[\]\n]+)\](?:\[([^\[\]\n]*)\])?',body):
            label=(m.group(3) or m.group(2)).strip().casefold()
            if label in refs:
                found.append((bool(m.group(1)),refs[label]))
        return found

    def _target_path(self, book, raw):
        try:
            target=urlsplit(raw)
            if target.scheme or target.netloc or target.query or '\\' in raw or '\x00' in raw:
                return None
            decoded=unquote(target.path)
            if '\\' in decoded or '\x00' in decoded or Path(decoded).is_absolute():
                return None
            if not decoded:
                return None
            # Library-qualified links and relative links are accepted only within DOC namespaces.
            path=self.root/decoded if decoded.startswith(('module_docs/','project_docs/')) else book.path/decoded
            return self._safe(path),unquote(target.fragment)
        except (DocLibraryError,ValueError,OSError):
            return None

    @staticmethod
    def _image_dimensions(body, mime):
        """Validate container structure and bound decoded dimensions without dependencies."""
        if mime == 'image/png':
            if len(body) < 45 or body[8:16] != b'\x00\x00\x00\x0dIHDR':
                return None
            position, kinds = 8, []
            while position + 12 <= len(body):
                length = int.from_bytes(body[position:position + 4], 'big')
                kind = body[position + 4:position + 8]
                if position + 12 + length > len(body):
                    return None
                kinds.append(kind)
                position += length + 12
                if kind == b'IEND':
                    break
            if position != len(body) or kinds[-1:] != [b'IEND'] or b'IDAT' not in kinds:
                return None
            return int.from_bytes(body[16:20], 'big'), int.from_bytes(body[20:24], 'big')
        if mime == 'image/gif':
            if len(body) < 14 or body[-1:] != b';':
                return None
            return int.from_bytes(body[6:8], 'little'), int.from_bytes(body[8:10], 'little')
        if mime == 'image/jpeg':
            if body[-2:] != b'\xff\xd9':
                return None
            position = 2
            while position + 4 <= len(body):
                if body[position] != 255:
                    return None
                while position < len(body) and body[position] == 255:
                    position += 1
                if position + 3 > len(body):
                    return None
                marker = body[position]
                length = int.from_bytes(body[position + 1:position + 3], 'big')
                if length < 2 or position + 1 + length > len(body):
                    return None
                if marker in {192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207}:
                    if length < 8:
                        return None
                    return int.from_bytes(body[position + 6:position + 8], 'big'), int.from_bytes(body[position + 4:position + 6], 'big')
                position += length + 1
            return None
        if mime == 'image/webp':
            if len(body) < 30 or int.from_bytes(body[4:8], 'little') + 8 != len(body):
                return None
            kind, data = body[12:16], body[20:]
            length = int.from_bytes(body[16:20], 'little')
            if length > len(data):
                return None
            if kind == b'VP8X' and length >= 10:
                return 1 + int.from_bytes(data[4:7], 'little'), 1 + int.from_bytes(data[7:10], 'little')
            if kind == b'VP8 ' and length >= 10 and data[3:6] == b'\x9d\x01\x2a':
                return int.from_bytes(data[6:8], 'little') & 16383, int.from_bytes(data[8:10], 'little') & 16383
            if kind == b'VP8L' and length >= 5 and data[0] == 47:
                bits = int.from_bytes(data[1:5], 'little')
                return (bits & 16383) + 1, ((bits >> 14) & 16383) + 1
        return None

    def _image(self, path):
        body = self._read(path, MAX_IMAGE_BYTES)
        mime = None
        if path.suffix.lower() == '.png' and body.startswith(b'\x89PNG\r\n\x1a\n'):
            mime = 'image/png'
        elif path.suffix.lower() in {'.jpg', '.jpeg'} and body.startswith(b'\xff\xd8\xff'):
            mime = 'image/jpeg'
        elif path.suffix.lower() == '.gif' and body[:6] in {b'GIF87a', b'GIF89a'}:
            mime = 'image/gif'
        elif path.suffix.lower() == '.webp' and body[:4] == b'RIFF' and body[8:12] == b'WEBP':
            mime = 'image/webp'
        dimensions = self._image_dimensions(body, mime) if mime else None
        if not dimensions or min(dimensions) < 1 or max(dimensions) > MAX_IMAGE_DIMENSION or dimensions[0] * dimensions[1] > MAX_IMAGE_PIXELS:
            raise DocLibraryError('文档图片无效或格式不受支持；仅支持 PNG、JPEG、GIF、WebP', 415)
        return body, mime

    def _references(self,book,chapter,books):
        links,assets={},{}
        image_cache = {}
        image_targets = set()
        image_bytes = 0
        image_count = 0
        files={str(p): (b.public['id'],c['id']) for b in books.values() for c in b.chapters
               for p,_ in [b.bodies[c['id']]]}
        for image,raw in self._targets(book.bodies[chapter['id']][1]):
            if image and raw in image_targets:
                continue
            if image:
                image_targets.add(raw)
            if raw.startswith('#') and not image:
                links[raw]={'book_id':book.public['id'],'chapter_id':chapter['id'],'fragment':unquote(raw[1:])}
                continue
            resolved=self._target_path(book,raw)
            if resolved is None:
                continue
            path,fragment=resolved
            if image:
                # Images belong to their book; a DOC link cannot expose arbitrary library files.
                if not path.is_relative_to(book.path):
                    continue
                if path in image_cache:
                    if image_cache[path] is not None:
                        assets[raw] = image_cache[path]
                    continue
                image_cache[path] = None
                try:
                    size = self._size(path)
                    if image_count >= MAX_CHAPTER_IMAGES or image_bytes + size > MAX_CHAPTER_IMAGE_BYTES or size > MAX_IMAGE_BYTES:
                        continue
                    image_count += 1
                    image_bytes += size
                    self._image(path)
                except DocLibraryError:
                    continue
                image_cache[path] = self._asset_id(book, path)
                assets[raw]=image_cache[path]
            elif str(path) in files:
                bid,cid=files[str(path)]
                links[raw]={'book_id':bid,'chapter_id':cid,'fragment':fragment}
        return links,assets

    def chapter(self,bid,cid):
        book=self._resolve_book(bid)
        books=self._registry.copy()
        chapter=next((c for c in book.chapters if c['id']==cid),None)
        if chapter is None:
            raise DocLibraryError()
        raw=self._read(book.bodies[cid][0],MAX_CHAPTER_BYTES)
        try:
            markdown = raw.decode('utf-8-sig')
        except UnicodeError as exc:
            raise DocLibraryError('文档正文不是有效 UTF-8', 409) from exc
        book.bodies[cid] = (book.bodies[cid][0], markdown)
        links,assets=self._references(book,chapter,books)
        return {'ok':True,'chapter':{**chapter,'markdown':markdown,
            'sha256':hashlib.sha256(raw).hexdigest(),'integrity':self._chapter_status(book,chapter,raw),
            'links':links,'assets':assets,'source':self._chapter_source(book.meta,chapter['name'])}}

    def search(self,bid,query):
        book=self._resolve_book(bid)
        if not isinstance(query,str) or len(query)>200:
            raise DocLibraryError('文档搜索词最长 200 字符',400)
        needle=query.strip().casefold()
        results=[]
        size=0
        truncated=False
        if needle:
            for chapter in book.chapters:
                path = self._safe(book.bodies[chapter['id']][0])
                size += self._size(path)
                if size>MAX_SEARCH_BYTES or len(results)>=MAX_RESULTS:
                    truncated=True
                    break
                try:
                    body = self._read(path, MAX_CHAPTER_BYTES).decode('utf-8-sig')
                except (DocLibraryError, UnicodeError):
                    truncated = True
                    continue
                at=body.casefold().find(needle)
                if at>=0 or needle in chapter['title'].casefold():
                    start=max(0,at-70)
                    results.append({'book_id':bid,'chapter_id':chapter['id'],'title':chapter['title'],
                                    'snippet':body[start:start+220]})
        return {'ok':True,'results':results,'truncated':truncated}

    @staticmethod
    def _asset_id(book, path):
        # Embed only the opaque book digest so resolving one image never scans other bodies.
        suffix = _id('asset', path.relative_to(book.path).as_posix()).split('-', 1)[1]
        return 'asset-' + book.public['id'].split('-', 1)[1] + '-' + suffix

    def asset(self,aid):
        if not isinstance(aid,str) or not re.fullmatch(r'asset-[0-9a-f]{32}-[0-9a-f]{32}',aid):
            raise DocLibraryError()
        book = self._resolve_book('book-' + aid.split('-')[1])
        total = 0
        for chapter in book.chapters:
            chapter_path = self._safe(book.bodies[chapter['id']][0])
            total += self._size(chapter_path)
            if total > MAX_BOOK_BYTES:
                raise DocLibraryError('文档资源检索总量超限', 413)
            try:
                markdown = self._read(chapter_path, MAX_CHAPTER_BYTES).decode('utf-8-sig')
            except UnicodeError as exc:
                raise DocLibraryError('文档正文不是有效 UTF-8', 409) from exc
            for image,raw in self._targets(markdown):
                if not image:
                    continue
                resolved=self._target_path(book,raw)
                if resolved is None:
                    continue
                path,_=resolved
                if path.is_relative_to(book.path) and self._asset_id(book,path)==aid:
                    return self._image(path)
        raise DocLibraryError()
