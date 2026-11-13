"""Lossless physical candidate encoding, with no filesystem or recovery actions.

Only validated ledger events define reusable arrays. Documents may reference
that registry, but their inline arrays never acquire ledger authority.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import time


class StorageError(ValueError):
    """Storage is malformed, untrusted, or exceeds an explicit resource limit."""


class StorageKind(str, Enum):
    METADATA = "metadata"
    EVENT = "event"
    TRANSACTION = "transaction"


class ManifestKind(str, Enum):
    FILE_MANIFEST = "file_manifest"
    INDEX_MANIFEST = "index_manifest"


@dataclass(frozen=True)
class StorageLimits:
    max_raw_bytes: int = 4 * 1024 ** 3
    max_events: int = 32768
    max_refs: int = 1000000
    max_definitions: int = 100000
    max_manifest_rows: int = 50000
    max_expanded_bytes: int = 4 * 1024 ** 3
    max_depth: int = 128
    timeout_seconds: float = 120


_MARKER = "$icode_manifest_ref"
_SLOTS = ("manifest", "index_manifest", "untracked_manifest")
_SNAPSHOT_KEYS = frozenset(("identity", "base", "code_files", "scope_contract",
    "inspection_scope", "excluded_side_effects", "control_artifact_exclusions",
    "manifest", "version", "head", "head_tree", "index_manifest",
    "untracked_manifest", "content_id", "candidate_id"))


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise StorageError("storage contains invalid JSON: " + str(exc)) from exc


def manifest_digest(kind, rows):
    try:
        kind = ManifestKind(kind).value
    except (TypeError, ValueError) as exc:
        raise StorageError("unknown manifest kind") from exc
    return _manifest_digest_bytes(kind, _canonical(rows))


def _manifest_digest_bytes(kind, rows_json):
    return hashlib.sha256(b'{"kind":' + _canonical(kind) + b',"rows":' + rows_json + b'}').hexdigest()


@dataclass(frozen=True)
class _Definition:
    rows_json: bytes
    row_count: int
    row_depth: int


class Registry:
    """An invocation-local ledger registry; never deserialized from JSON."""
    def __init__(self):
        self._definitions = {}

    def __len__(self):
        return len(self._definitions)

    def _fork(self):
        result = Registry()
        # Definitions contain immutable canonical bytes, never mutable arrays.
        result._definitions = self._definitions.copy()
        return result


@dataclass
class DecodedLedger:
    events: list
    registry: Registry


def _readonly(value):
    if isinstance(value, dict):
        return _ReadOnlyMapping(value)
    if isinstance(value, list):
        return _ReadOnlySequence(value)
    return value


class _ReadOnlyMapping(Mapping):
    def __init__(self, value):
        self._value = value

    def __len__(self):
        return len(self._value)

    def __iter__(self):
        return iter(self._value)

    def __getitem__(self, key):
        return _readonly(self._value[key])


class _ReadOnlySequence(Sequence):
    """Constant-time prior-history view, recursively readonly and length-bound."""
    def __init__(self, value):
        self._value, self._length = value, len(value)

    def __len__(self):
        return self._length

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(self._length)))
        if index < 0:
            index += self._length
        if not 0 <= index < self._length:
            raise IndexError(index)
        return _readonly(self._value[index])


class _Budget:
    def __init__(self, limits):
        self.limits = limits or StorageLimits()
        for name, value in vars(self.limits).items():
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0
                    or (name != "timeout_seconds" and type(value) is not int)):
                raise StorageError("invalid storage limit: " + name)
        self.started = time.monotonic()
        self.raw = self.expanded = self.refs = 0

    def check(self, name, value):
        if value > getattr(self.limits, name):
            raise StorageError("storage budget exceeded: " + name)
        if time.monotonic() - self.started >= self.limits.timeout_seconds:
            raise StorageError("storage budget exceeded: timeout_seconds")

    def charge(self, count):
        self.expanded += count
        self.check("max_expanded_bytes", self.expanded)

    def raw_object(self, value):
        self.raw += len(_canonical(value))
        self.check("max_raw_bytes", self.raw)


def strict_json_loads(raw, *, limits=None):
    """Parse physical bytes without accepting duplicate keys or nonfinite JSON."""
    budget = _Budget(limits)
    try:
        count = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
        budget.check("max_raw_bytes", count)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise StorageError("storage JSON has duplicate key: " + key)
                result[key] = value
            return result
        def constant(value):
            raise StorageError("storage JSON has nonfinite number: " + value)
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        # Check depth before any later recursive transform or canonicalization.
        stack = [(value, 0)]
        while stack:
            node, depth = stack.pop()
            budget.check("max_depth", depth)
            if isinstance(node, dict): stack.extend((v, depth + 1) for v in node.values())
            elif isinstance(node, list): stack.extend((v, depth + 1) for v in node)
        _canonical(value)  # Also rejects overflow to infinity (for example 1e999).
        budget.check("max_raw_bytes", count)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, StorageError): raise
        raise StorageError("invalid storage JSON: " + str(exc)) from exc


def is_envelope(value):
    return isinstance(value, dict) and "storage_version" in value


def _unwrap(value, kind):
    try:
        kind = StorageKind(kind).value
    except (ValueError, TypeError) as exc:
        raise StorageError("unknown document kind") from exc
    if not isinstance(value, dict):
        raise StorageError("control document must be an object")
    if not is_envelope(value):
        return value, False
    if (set(value) != {"storage_version", "kind", "value"}
            or type(value["storage_version"]) is not int or value["storage_version"] != 2
            or value["kind"] != kind or not isinstance(value["value"], dict)):
        raise StorageError("invalid or unsupported storage envelope for " + kind)
    return value["value"], True


@lru_cache(maxsize=1)
def _candidate_module():
    spec = importlib.util.spec_from_file_location("storage_candidate_identity",
                                                 Path(__file__).with_name("icode_candidate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_snapshot(value):
    module = _candidate_module()
    if not module.snapshot_identity_ok(value):
        raise StorageError("invalid candidate snapshot shape or identity hash")


def _registry(value):
    if type(value) is not Registry:
        raise StorageError("committed registry must come from decoded ledger")
    return value


class _Transform:
    def __init__(self, registry, budget, *, encode=False, define=False, typed=True, require_definitions=False):
        self.registry = _registry(registry)
        self.budget = budget
        self.encode = encode
        self.define = define
        self.typed = typed
        self.require_definitions = require_definitions

    def slot(self, value, name, depth):
        kind = "index_manifest" if name == "index_manifest" else "file_manifest"
        if isinstance(value, dict) and self.typed and not self.encode:
            if set(value) != {_MARKER} or not isinstance(value[_MARKER], dict):
                raise StorageError("invalid manifest reference wrapper")
            ref = value[_MARKER]
            if (set(ref) != {"storage_version", "kind", "sha256"}
                    or type(ref["storage_version"]) is not int or ref["storage_version"] != 2
                    or ref["kind"] != kind or not isinstance(ref["sha256"], str)
                    or re.fullmatch("[0-9a-f]{64}", ref["sha256"]) is None):
                raise StorageError("invalid manifest reference kind, digest or fields")
            self.budget.refs += 1
            self.budget.check("max_refs", self.budget.refs)
            key = (kind, ref["sha256"])
            if key not in self.registry._definitions:
                raise StorageError("missing or forward manifest reference: " + ref["sha256"])
            definition = self.registry._definitions[key]
            self.budget.check("max_manifest_rows", definition.row_count)
            self.budget.check("max_depth", depth + definition.row_depth)
            if _manifest_digest_bytes(kind, definition.rows_json) != ref["sha256"]:
                raise StorageError("corrupt manifest definition")
            self.budget.charge(len(definition.rows_json))
            # Each expansion owns a new ordinary JSON array. Immutable bytes
            # avoid walking/dumping thousands of already validated scalars.
            return json.loads(definition.rows_json)
        if not isinstance(value, list):
            raise StorageError("candidate manifest must be an array or typed reference")
        self.budget.check("max_manifest_rows", len(value))
        required = {"path", "mode", "oid"} if kind == "index_manifest" else \
                   {"path", "kind", "mode", "sha256", "size", "git_oid"}
        if any(type(row) is not dict or set(row) != required or
               any(type(cell) not in (str, int, float, bool, type(None)) for cell in row.values()) for row in value):
            raise StorageError("invalid closed candidate manifest row")
        row_depth = 2 if value else 0
        self.budget.check("max_depth", depth + row_depth)
        rows_json = _canonical(value)
        self.budget.charge(len(rows_json))
        key = (kind, _manifest_digest_bytes(kind, rows_json))
        if self.encode and key in self.registry._definitions:
            self.budget.refs += 1
            self.budget.check("max_refs", self.budget.refs)
            return {_MARKER: {"storage_version": 2, "kind": kind, "sha256": key[1]}}
        if self.require_definitions and key not in self.registry._definitions:
            raise StorageError("candidate manifest definition is absent from the committed/prospective ledger")
        if self.define and key not in self.registry._definitions:
            self.budget.check("max_definitions", len(self.registry) + 1)
            self.registry._definitions[key] = _Definition(rows_json, len(value), row_depth)
        return json.loads(rows_json)

    def walk(self, value, depth=0):
        self.budget.check("max_depth", depth)
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise StorageError("JSON object key must be a string")
            if self.typed and _MARKER in value:
                raise StorageError("reserved manifest marker outside candidate slot")
            snapshot = set(value) == _SNAPSHOT_KEYS
            if snapshot and self.encode: _valid_snapshot(value)
            self.budget.charge(2 + max(0, len(value) - 1))
            result = {}
            # Candidate slots have a fixed order; all other object keys sort.
            keys = sorted(set(value) - set(_SLOTS)) + list(_SLOTS) if snapshot else sorted(value)
            for key in keys:
                self.budget.charge(len(_canonical(key)) + 1)
                result[key] = self.slot(value[key], key, depth + 1) if snapshot and key in _SLOTS \
                              else self.walk(value[key], depth + 1)
            if snapshot and not self.encode: _valid_snapshot(result)
            return result
        if isinstance(value, list):
            self.budget.charge(2 + max(0, len(value) - 1))
            return [self.walk(item, depth + 1) for item in value]
        if value is not None and type(value) not in (str, int, float, bool):
            raise StorageError("unsupported JSON value type")
        self.budget.charge(len(_canonical(value)))
        return value


def decode_events(raw_records, *, limits=None, logical_event_validator):
    """Validate each complete logical event before committing its definitions.

    The required validator receives (event, prior_events), and must check the
    existing event schema, hash, ID, previous hash and ticket identity contract.
    """
    if not callable(logical_event_validator):
        raise StorageError("logical event validator is required")
    budget, registry, events = _Budget(limits), Registry(), []
    for raw in raw_records:
        budget.check("max_events", len(events) + 1)
        budget.raw_object(raw)
        value, typed = _unwrap(raw, StorageKind.EVENT)
        temporary = registry._fork()
        logical = _Transform(temporary, budget, define=True, typed=typed).walk(value)
        try:
            outcome = logical_event_validator(logical, _ReadOnlySequence(events))
            if outcome is False:
                raise ValueError("validator rejected event")
        except Exception as exc:
            raise StorageError("invalid logical ledger event: " + str(exc)) from exc
        budget.check("max_expanded_bytes", budget.expanded)
        registry = temporary
        events.append(logical)
    return DecodedLedger(events, registry)


def decode_document(raw_object, *, kind, registry, limits=None):
    budget = _Budget(limits)
    budget.raw_object(raw_object)
    value, typed = _unwrap(raw_object, kind)
    logical = _Transform(_registry(registry), budget, typed=typed).walk(value)
    budget.check("max_expanded_bytes", budget.expanded)
    return logical


def encode_document(logical_object, *, kind, committed_registry, limits=None, require_definitions=False):
    try:
        kind = StorageKind(kind).value
    except (ValueError, TypeError) as exc:
        raise StorageError("unknown document kind") from exc
    if kind == StorageKind.EVENT.value:
        raise StorageError("events require encode_event")
    if not isinstance(logical_object, dict) or is_envelope(logical_object):
        raise StorageError("logical document must be an unwrapped object")
    budget = _Budget(limits)
    value = _Transform(_registry(committed_registry), budget, encode=True,
                       require_definitions=require_definitions).walk(logical_object)
    result = {"storage_version": 2, "kind": kind, "value": value}
    budget.raw_object(result)
    return result


def encode_event(logical_event, *, committed_registry, limits=None):
    if not isinstance(logical_event, dict) or is_envelope(logical_event):
        raise StorageError("logical event must be an unwrapped object")
    temporary = _registry(committed_registry)._fork()
    budget = _Budget(limits)
    value = _Transform(temporary, budget, encode=True, define=True).walk(logical_event)
    result = {"storage_version": 2, "kind": "event", "value": value}
    budget.raw_object(result)
    return result, temporary
