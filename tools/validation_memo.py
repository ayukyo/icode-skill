"""Bounded reuse for pure Boolean checks of complete, ordinary JSON values.

Never use this helper for I/O, authorization, filesystem identity or current
evidence qualification. Each instance binds one fixed implementation; all of
that implementation's variable rules must be supplied as ``rules`` content.
"""
from collections import OrderedDict
import json
import math
import marshal
import re
import threading
import time

_JSON_SURROGATE = re.compile('[\ud800-\udfff]')


def _typed_key_safe():
    """Enable the C-level prefilter only when this runtime preserves types.

    Version 2 avoids reference/interning flags making equal ordinary values
    have different keys. This is process-local data, never a portable format;
    no externally supplied marshal is loaded or trusted.
    """
    try:
        examples = [1, 1.5, 'value', [1], {'key': 1}]
        for value in examples:
            subtype = type('ValidationKeyProbe', (type(value),), {})
            try:
                if marshal.dumps(subtype(value), 2) == marshal.dumps(value, 2):
                    return False
            except (ValueError, TypeError):
                pass  # Rejecting a subclass also preserves the distinction.
        pairs = [(True, 1), (1, 1.0), ('value', b'value'), ([1], (1,)), ({'1': 1}, {1: 1})]
        return all(marshal.dumps(a, 2) != marshal.dumps(b, 2) for a, b in pairs)
    except Exception:
        # Alternative runtimes/audit policies retain the original JSON path.
        return False


_TYPED_KEY_SAFE = _typed_key_safe()


def _ordinary_json(value, max_nodes):
    """Decline coercions, custom objects and cycles; the caller keeps its semantics."""
    stack = [(value, False)]
    active = set()
    remaining = max_nodes
    while stack:
        item, leaving = stack.pop()
        kind = type(item)
        if leaving:
            active.remove(id(item))
            continue
        remaining -= 1
        if remaining < 0:
            return False
        if kind in (str, int, bool, type(None)):
            if kind is str and _JSON_SURROGATE.search(item):
                return False  # JSON decoding can combine surrogate code points.
            continue
        if kind is float:
            if not math.isfinite(item):
                return False
            continue
        if kind not in (dict, list) or id(item) in active:
            return False
        if kind is dict and any(type(key) is not str or _JSON_SURROGATE.search(key) for key in item):
            return False
        active.add(id(item))
        stack.append((item, True))
        stack.extend((child, False) for child in (item.values() if kind is dict else item))
    return True


class PureJsonMemo:
    """Cache successful pure checks only, with complete keys and fixed budgets."""

    def __init__(self, validator, *, max_entries=32, max_key_bytes=8*1024*1024,
                 max_item_bytes=2*1024*1024):
        if not callable(validator) or any(type(value) is not int or value < 1
                for value in (max_entries, max_key_bytes, max_item_bytes)):
            raise ValueError('Pure validation requires a checker and positive integer budgets')
        self._validator = validator
        self._max_entries = max_entries
        self._max_key_bytes = max_key_bytes
        self._max_item_bytes = min(max_item_bytes, max_key_bytes)
        self._entries = OrderedDict()
        self._key_bytes = 0
        self._lock = threading.Lock()
        self._use_typed_key = _TYPED_KEY_SAFE
        self._counts = dict(hits=0, misses=0, bypasses=0, evictions=0, key_seconds=0.0)

    def check(self, value, rules=None):
        started = time.perf_counter()
        key = None
        try:
            # Only exact built-in JSON types may round-trip into a frozen
            # copy. Tuple/list, integer subclasses and key coercion must not
            # share a cache identity with an input the old checker accepted.
            if self._use_typed_key:
                encoded = marshal.dumps([value, rules], 2)
                if len(encoded) <= self._max_item_bytes:
                    key = encoded
            elif _ordinary_json([value, rules], self._max_item_bytes):
                encoded = json.dumps([value, rules], sort_keys=False, ensure_ascii=True,
                                     separators=(',', ':'), allow_nan=False).encode('ascii')
                if len(encoded) <= self._max_item_bytes:
                    key = encoded
        except (TypeError, ValueError, OverflowError, RecursionError):
            pass  # Unsupported input still reaches the original checker.
        with self._lock:
            self._counts['key_seconds'] += time.perf_counter()-started
            if key is None:
                self._counts['bypasses'] += 1
            elif key in self._entries:
                self._counts['hits'] += 1
                self._entries.move_to_end(key)
                return True
            else:
                self._counts['misses'] += 1
        if key is None:
            return self._validator(value, rules)
        if self._use_typed_key:
            # A typed key can hit only a complete ordinary JSON value/rules
            # pair validated on an earlier miss. Unsupported types have
            # distinct encodings (or fail encoding) and never enter the cache.
            started = time.perf_counter()
            eligible = _ordinary_json([value, rules], self._max_item_bytes)
            encoded = None
            if eligible:
                try:
                    encoded = json.dumps([value, rules], sort_keys=False, ensure_ascii=True,
                        separators=(',', ':'), allow_nan=False).encode('ascii')
                    eligible = len(encoded) <= self._max_item_bytes
                    if eligible:
                        # Preserve the checker's input order and bind this copy
                        # to the key, even if caller-owned input changed meanwhile.
                        frozen_value, frozen_rules = json.loads(encoded)
                        eligible = marshal.dumps([frozen_value, frozen_rules], 2) == key
                except (TypeError, ValueError, OverflowError, RecursionError):
                    eligible = False
            with self._lock:
                self._counts['key_seconds'] += time.perf_counter()-started
                if not eligible:
                    self._counts['bypasses'] += 1
            if not eligible:
                return self._validator(value, rules)
        else:
            frozen_value, frozen_rules = json.loads(key)
        # No caller-owned mutable object or validator work is held under the
        # cache lock. Concurrent misses may compute twice; they cannot grant
        # any dynamic permission or expose cached mutable state.
        result = self._validator(frozen_value, frozen_rules)
        if result is True:
            with self._lock:
                if key not in self._entries:
                    self._entries[key] = True
                    self._key_bytes += len(key)
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries or self._key_bytes > self._max_key_bytes:
                    old, _ = self._entries.popitem(last=False)
                    self._key_bytes -= len(old)
                    self._counts['evictions'] += 1
        return result

    def stats(self):
        with self._lock:
            return dict(self._counts, entries=len(self._entries), key_bytes=self._key_bytes,
                        typed_key=self._use_typed_key)
