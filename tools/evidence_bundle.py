"""Bounded, read-only raw evidence validation (stdlib only).

Files are relative to an explicit control/evidence root, never to the process
working directory. Hashes establish byte integrity, not device authenticity.
Limits bound files, bytes, JSON and elapsed processing. Elapsed time is checked
before/after I/O returns; a blocking local/network filesystem has no hard I/O
deadline. No claim of an interruptible filesystem timeout is made.
Negative runs may retain partial evidence and unknown required identities;
schema, safe paths, actual hashes and supplied identity bindings still apply.

Measurement v1: each bundle.measurements entry has exactly id, item_id, name,
method, source_ids, unit, value. Its measurement-kind item's JSON contains that
same object. source_ids must also occur on that item and reference complete raw
log/artifact_identity items. sample_rate and sequence_drop_rate read JSON
{schema_version:1, clock:"monotonic", unit:"s", samples:[{timestamp,sequence?}]}
or JSONL (metadata.format="jsonl") with the same header, then one sample/line.
sample_rate=(N-1)/(last-first), units Hz/fps; sequence_drop_rate uses missing
sequence numbers / inclusive sequence span, units ratio/%. Optional header
run_id/device_id/boot_id are checked. Timestamps must be finite, strictly ordered
and inside the bundle's monotonic window; sequences are nonnegative integers.

Review adapters accept one reference dict and return a trusted receipt dict
with outcome="pass", method, item_sha256, run_id, candidate_id. The adapter,
never input JSON, authenticates the receipt. No adapter means fail closed.
Visual integration: validate_visual(bundle, visual_contract, *, items,
read_item, run, review_validator=None); items is id -> item, read_item(id) returns
the exact bytes already hashed under the shared budget. A visual helper must
raise on failure; a boolean/viewed flag is never a substitute for a receipt.
"""
import hashlib
import importlib.util
import json
import math
import ntpath
import os
from pathlib import Path
import re
import stat
import time
from datetime import datetime

DEFAULT_LIMITS = {"max_files": 256, "max_file_bytes": 16 * 1024 * 1024,
                  "max_total_bytes": 64 * 1024 * 1024, "max_json_bytes": 2 * 1024 * 1024,
                  "max_json_nodes": 50000, "max_json_depth": 32, "max_samples": 100000,
                  "max_elapsed_seconds": 10}
_CONTROL = None
PRIVATE_PARTS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".git", ".claude", ".config", ".codex"}


class EvidenceBundleError(ValueError):
    pass


def fail(message):
    raise EvidenceBundleError("evidence_bundle: " + message)


def contract_digest(contract):
    try:
        raw = json.dumps(contract, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        fail("contract invalid: " + str(exc))
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _schema(value, name):
    global _CONTROL
    if _CONTROL is None:
        spec = importlib.util.spec_from_file_location("raw_evidence_schema_control", Path(__file__).with_name("icode_control.py"))
        _CONTROL = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CONTROL)
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / (name + ".schema.json")).read_text(encoding="utf-8"))
    errors = _CONTROL.check_schema(value, schema)
    if errors:
        fail(name + " schema: " + "; ".join(errors[:5]))


def _finite(value, label):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, ValueError):
        finite = False
    if not finite:
        fail(label + " must be a finite number")
    return value


def _unique(values, label):
    if not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values) or len(set(values)) != len(values):
        fail(label + " must be unique nonempty strings (duplicate or invalid)")


class Reader:
    """Budgeted stable-descriptor reads; reject path/parent reparse replacement."""
    def __init__(self, root, limits=None):
        if root is None:
            fail("explicit evidence root required")
        self.root = Path(os.path.abspath(os.fspath(root)))
        if self.root == Path(self.root.anchor) or self.root == Path.home():
            fail("evidence root must be a dedicated control directory")
        if any(part.casefold() in PRIVATE_PARTS for part in self.root.parts):
            fail("private evidence root forbidden")
        protected = [Path(p) for p in (os.environ.get("WINDIR"), os.environ.get("PROGRAMFILES")) if p]
        if os.name != "nt":
            protected += [Path(p) for p in ("/etc", "/proc", "/sys", "/dev")]
        if any(self.root == path or path in self.root.parents for path in protected):
            fail("system evidence root forbidden")
        self.limits = dict(DEFAULT_LIMITS)
        if limits is not None:
            if not isinstance(limits, dict) or set(limits) - self.limits.keys():
                fail("unknown budget limits")
            for name, value in limits.items():
                if type(value) is not int or value <= 0 or value > DEFAULT_LIMITS[name]:
                    fail("invalid budget " + name)
                self.limits[name] = value
        self.total = 0
        self.started = time.monotonic()
        self.facts = {}
        self.cache = {}
        self._parents(self.root)

    def check_budget(self):
        if time.monotonic() - self.started > self.limits["max_elapsed_seconds"]:
            fail("elapsed processing budget exceeded (filesystem I/O is not interruptible)")

    @staticmethod
    def _identity(info, *, cross_source=False):
        identity = info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns
        # Windows path stat and handle fstat can expose different ctime values.
        # Keep ctime for same-source before/after checks, and on POSIX throughout.
        return identity if cross_source and os.name == "nt" else identity + (info.st_ctime_ns,)

    def _parents(self, path):
        facts = []
        for current in [*reversed(path.parents), path]:
            try:
                info = current.lstat()
            except OSError as exc:
                fail("path inaccessible: " + str(exc))
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                fail("symlink/reparse path forbidden: " + str(current))
            facts.append((current, (info.st_dev, info.st_ino, info.st_mode)))
        return facts

    def path(self, raw):
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            fail("invalid evidence path")
        if ntpath.splitdrive(raw)[0] and not ntpath.isabs(raw):
            fail("drive-relative evidence path forbidden")
        parts = raw.replace("\\", "/").split("/")
        if any(part not in ("", ".") and part.endswith((" ", ".")) for part in parts):
            fail("Win32 alias path forbidden")
        if ".." in parts or any(x.casefold() in PRIVATE_PARTS | {".codex"} for x in parts):
            fail("private or parent traversal path forbidden")
        path = Path(raw)
        if (ntpath.isabs(raw) and not path.is_absolute()) or any(":" in x for x in parts[1:]):
            fail("foreign or alternate-stream path forbidden")
        path = path if path.is_absolute() else self.root / path
        path = Path(os.path.abspath(path))
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            fail("path outside evidence root")
        if any(":" in part or part.endswith((" ", ".")) for part in relative.parts):
            fail("alternate-stream or Win32 alias path forbidden")
        self._parents(path.parent)
        return path

    def read(self, raw, expected_hash=None, expected_bytes=None):
        self.check_budget()
        path = self.path(raw)
        if path in self.cache:
            data = self.cache[path]
        else:
            if len(self.facts) >= self.limits["max_files"]:
                fail("file budget exceeded")
            parents = self._parents(path.parent)
            try:
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
                    fail("path is not an ordinary non-symlink file")
                if before.st_size > self.limits["max_file_bytes"] or self.total + before.st_size > self.limits["max_total_bytes"]:
                    fail("byte budget exceeded")
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(fd, "rb") as source:
                    opened = os.fstat(source.fileno())
                    if self._identity(before, cross_source=True) != self._identity(opened, cross_source=True):
                        fail("file replaced before read")
                    data = source.read(min(self.limits["max_file_bytes"], self.limits["max_total_bytes"] - self.total) + 1)
                    if self._identity(opened) != self._identity(os.fstat(source.fileno())):
                        fail("file changed during read")
                if self._identity(before) != self._identity(path.lstat()) or parents != self._parents(path.parent):
                    fail("file or parent replaced during read")
            except OSError as exc:
                fail("evidence file read: " + str(exc))
            if len(data) != before.st_size:
                fail("file byte budget or concurrent modification")
            self.total += len(data)
            self.facts[path] = self._identity(before)
            self.cache[path] = data
        actual_hash = "sha256:" + hashlib.sha256(data).hexdigest()
        if expected_hash is not None and actual_hash != expected_hash:
            fail("file hash mismatch: " + str(path))
        if expected_bytes is not None and len(data) != expected_bytes:
            fail("file bytes mismatch: " + str(path))
        self.check_budget()
        return data

    def json(self, raw):
        self.check_budget()
        if len(raw) > self.limits["max_json_bytes"]:
            fail("JSON byte budget exceeded")
        def pairs(rows):
            result = {}
            for key, value in rows:
                if key in result:
                    fail("duplicate JSON key: " + key)
                result[key] = value
            return result
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                parse_constant=lambda token: fail("non-finite JSON value " + token))
        except (ValueError, UnicodeError, RecursionError) as exc:
            fail("invalid strict JSON: " + str(exc))
        stack, count = [(value, 0)], 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if count > self.limits["max_json_nodes"] or depth > self.limits["max_json_depth"]:
                fail("JSON structure budget exceeded")
            if isinstance(node, (dict, list)):
                stack.extend((x, depth + 1) for x in (node.values() if isinstance(node, dict) else node))
            elif type(node) in (int, float):
                _finite(node, "JSON value")
        self.check_budget()
        return value

    def finish(self):
        self.check_budget()
        for path, expected in self.facts.items():
            self._parents(path.parent)
            try:
                if expected != self._identity(path.lstat()):
                    fail("file replaced after read")
            except OSError as exc:
                fail("file disappeared after read: " + str(exc))
        self.check_budget()


def make_reference(path, evidence_root):
    reader = Reader(evidence_root)
    data = reader.read(str(path))
    reader.finish()
    return {"path": reader.path(str(path)).relative_to(reader.root).as_posix(),
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest()}


def validate_contract(contract):
    evidence = contract.get("evidence_contract") if isinstance(contract, dict) else None
    _schema(evidence, "evidence-contract")
    requirements = evidence["required_items"]
    _unique([r["id"] for r in requirements], "requirement ids")
    _unique(evidence["required_identity"], "required identity fields")
    dimensions = [contract.get(k) for k in ("required_layers", "required_consumers", "required_scenarios")]
    for requirement in requirements:
        key = tuple(requirement[k] for k in ("layer", "consumer", "scenario"))
        if any(not isinstance(values, list) or key[i] not in values for i, values in enumerate(dimensions)):
            fail("requirement outside verification contract cells")
        if contract.get("required_cells") is not None and not any(tuple(row.get(k) for k in ("layer", "consumer", "scenario")) == key for row in contract["required_cells"]):
            fail("requirement outside required cells")
    visual = evidence["visual"]
    if visual is not None:
        fractions = visual["sample_fractions"]
        if fractions != sorted(set(fractions)) or fractions[0] != 0 or fractions[-1] != 1:
            fail("visual sample fractions must be unique ordered full-window endpoints")
    return evidence


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            fail("window host timestamps require timezone")
        return parsed
    except (ValueError, AttributeError) as exc:
        fail("invalid window timestamp: " + str(exc))


def _identity(bundle, run, contract, ticket_id):
    expected = {"ticket_id": ticket_id, "stage_id": run.get("stage_id"), "candidate_id": run.get("candidate_id"),
                "baseline_ref": run.get("baseline_ref"), "contract_sha256": contract_digest(contract)}
    if run.get("run_id") is not None:
        expected["run_id"] = run["run_id"]
    for name, value in expected.items():
        if bundle[name] != value:
            fail(name + " mismatch")
    if bundle["baseline_ref"] != contract.get("baseline_ref"):
        fail("baseline contract mismatch")
    for name in ("device_id", "boot_id"):
        value = run.get(name, run.get("device") if name == "device_id" else None)
        if value is not None and value != bundle["device"][name]:
            fail(name + " mismatch")
    window = bundle["window"]
    if _time(window["host_start"]) >= _time(window["host_end"]) or \
            _finite(window["monotonic_start"], "window start") >= _finite(window["monotonic_end"], "window end"):
        fail("window must be increasing")
    if run.get("window") is not None:
        value = run["window"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                fail("run window must be structured JSON for evidence matching")
        if value != window:
            fail("window mismatch")
    for field in contract["evidence_contract"]["required_identity"] if run.get("outcome") == "pass" else []:
        value = bundle["device"].get(field, bundle.get(field))
        if "." in field:
            value = bundle
            for part in field.split("."):
                value = value.get(part) if isinstance(value, dict) else None
        normalized = re.sub(r"[\s-]+", "_", value.strip().casefold()) if isinstance(value, str) else ""
        if not isinstance(value, str) or not value.strip() or normalized in {
                "unknown", "not_applicable", "n/a", "na", "none", "null", "todo", "pending", "placeholder", "tbd"} or \
                normalized.startswith(("replace_", "placeholder_", "unknown_")) or re.search(r"[<>]|\$\{|\{\{", value):
            fail("required identity is unknown or placeholder: " + field)


def _samples(reader, raw, item, bundle):
    if item["metadata"].get("format") == "jsonl":
        lines = raw.splitlines()
        if len(lines) > reader.limits["max_samples"] + 1 or len(lines) < 3:
            fail("sample count budget or insufficient samples")
        header = reader.json(lines[0])
        if not isinstance(header, dict) or "samples" in header:
            fail("JSONL sample header invalid")
        data = dict(header, samples=[reader.json(line) for line in lines[1:]])
    else:
        data = reader.json(raw)
    if not isinstance(data, dict) or set(data) - {"schema_version", "clock", "unit", "samples", "run_id", "device_id", "boot_id"} or \
            data.get("schema_version") != 1 or type(data.get("schema_version")) is not int or data.get("clock") != "monotonic" or data.get("unit") != "s":
        fail("raw sample schema/clock/unit invalid")
    for name in ("run_id", "device_id", "boot_id"):
        if name in data and data[name] != bundle["device"].get(name, bundle.get(name)):
            fail("raw sample " + name + " mismatch")
    samples = data.get("samples")
    if not isinstance(samples, list) or len(samples) < 2 or len(samples) > reader.limits["max_samples"]:
        fail("sample count budget or insufficient samples")
    previous = None
    for sample in samples:
        if not isinstance(sample, dict) or "timestamp" not in sample or set(sample) - {"timestamp", "sequence"}:
            fail("raw sample structure invalid")
        timestamp = _finite(sample["timestamp"], "sample timestamp")
        if not bundle["window"]["monotonic_start"] <= timestamp <= bundle["window"]["monotonic_end"] or previous is not None and timestamp <= previous:
            fail("sample timestamps outside window or unordered")
        previous = timestamp
        if "sequence" in sample and (type(sample["sequence"]) is not int or sample["sequence"] < 0):
            fail("sample sequence must be nonnegative integer")
    return samples


def _measure(reader, bundle, items, read_item, run, contract):
    if run.get("metrics") is not None and not isinstance(run["metrics"], dict):
        fail("run metrics must be an object")
    result, seen = {}, set()
    for row in bundle["measurements"]:
        if set(row) != {"id", "item_id", "name", "method", "source_ids", "unit", "value"} or \
                any(not isinstance(row[k], str) or not row[k].strip() for k in ("id", "item_id", "name", "method", "unit")):
            fail("measurement structure invalid")
        if row["id"] in seen or row["name"] in result:
            fail("duplicate measurement")
        seen.add(row["id"])
        _unique(row["source_ids"], "measurement sources")
        item = items.get(row["item_id"])
        if not item or item["kind"] != "measurement" or not row["source_ids"] or \
                not set(row["source_ids"]).issubset(item["source_ids"]):
            fail("measurement requires linked raw sources")
        if reader.json(read_item(item["id"])) != row:
            fail("measurement payload mismatch")
        if len(row["source_ids"]) != 1:
            fail("measurement methods require exactly one raw source")
        source = items[row["source_ids"][0]]
        if source["kind"] not in {"log", "artifact_identity"} or source["source_ids"] or source["complete"] is not True:
            fail("measurement requires complete original raw source")
        samples = _samples(reader, read_item(source["id"]), source, bundle)
        if row["method"] == "sample_rate" and row["unit"] in {"Hz", "fps"}:
            value = (len(samples) - 1) / (samples[-1]["timestamp"] - samples[0]["timestamp"])
        elif row["method"] == "sequence_drop_rate" and row["unit"] in {"ratio", "%"}:
            sequences = [s.get("sequence") for s in samples]
            if any(type(x) is not int for x in sequences) or any(a >= b for a, b in zip(sequences, sequences[1:])):
                fail("sequence_drop_rate requires ordered integer sequences")
            span = sequences[-1] - sequences[0] + 1
            value = (span - len(samples)) / span * (100 if row["unit"] == "%" else 1)
        else:
            fail("unknown measurement method/unit")
        _finite(value, "recomputed measurement")
        if not math.isclose(_finite(row["value"], "measurement value"), value, rel_tol=1e-9, abs_tol=1e-12):
            fail("recomputed measurement mismatch: " + row["name"])
        actual = (run.get("metrics") or {}).get(row["name"])
        if not math.isclose(_finite(actual, "run metric"), value, rel_tol=1e-9, abs_tol=1e-12):
            fail("run metric differs from recomputed measurement")
        result[row["name"]] = value
    comparisons = {"lt": lambda a,b:a<b, "lte": lambda a,b:a<=b, "gt": lambda a,b:a>b,
                   "gte": lambda a,b:a>=b, "eq": lambda a,b:a==b}
    for metric in contract.get("required_metrics", []):
        if any(metric.get(k) != run.get(k) for k in ("layer", "consumer", "scenario")):
            continue
        row = next((r for r in bundle["measurements"] if r["name"] == metric["name"]), None)
        if row is None or row["unit"] != metric["unit"]:
            fail("required metric missing raw measurement/unit: " + metric["name"])
        if metric.get("operator") not in comparisons or not comparisons[metric["operator"]](result[metric["name"]], _finite(metric["value"], "threshold")):
            fail("metric threshold failed: " + metric["name"])
    for item in items.values():
        if item["kind"] == "measurement" and not any(row["item_id"] == item["id"] for row in bundle["measurements"]):
            fail("measurement item has no recomputable measurement")
    return result


def validate_visual(bundle, contract_visual, *, items, read_item, run, review_validator=None, requirements=None):
    path = Path(__file__).with_name("visual_evidence.py")
    if not path.is_file():
        fail("visual evidence requires trusted visual helper and review receipt")
    spec = importlib.util.spec_from_file_location("raw_bundle_visual", path)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    return api.validate_visual(bundle, contract_visual, items=items, read_item=read_item,
                               run=run, review_validator=review_validator, requirements=requirements)


def validate_bundle(reference, verification_contract, run, *, ticket_id, evidence_root,
                    visual_validator=None, review_validator=None, limits=None):
    evidence = validate_contract(verification_contract)
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"} or \
            not isinstance(reference.get("sha256"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", reference["sha256"]):
        fail("structured evidence reference required")
    reader = Reader(evidence_root, limits)
    bundle = reader.json(reader.read(reference["path"], reference["sha256"]))
    _schema(bundle, "verification-evidence")
    _identity(bundle, run, verification_contract, ticket_id)
    _unique([item["id"] for item in bundle["items"]], "item ids")
    requirements = {row["id"]: row for row in evidence["required_items"]}
    items = {item["id"]: item for item in bundle["items"]}
    cell = tuple(run.get(k) for k in ("layer", "consumer", "scenario"))
    selected = {key: req for key, req in requirements.items() if tuple(req[k] for k in ("layer", "consumer", "scenario")) == cell}
    if not selected:
        fail("run cell has no evidence requirements")
    paths = set()
    for item in items.values():
        req = selected.get(item["requirement_id"])
        if req is None or item["kind"] != req["kind"]:
            fail("item requirement/kind/cell mismatch")
        if any(key in item["metadata"] and item["metadata"][key] != req[key]
               for key in ("layer", "consumer", "scenario")):
            fail("item scope differs from frozen requirement/run cell")
        if req.get("expected_mode") is not None:
            mode = item["metadata"].get("mode")
            if not isinstance(mode, dict) or any(mode.get(key) != value for key, value in req["expected_mode"].items()):
                fail("item mode differs from frozen expected_mode")
        path = reader.path(item["path"])
        if path in paths:
            fail("duplicate item path cannot satisfy evidence count")
        paths.add(path)
        reader.read(item["path"], item["sha256"], item["bytes"])
        _unique(item["source_ids"], "source ids")
        if any(source not in items for source in item["source_ids"]):
            fail("dangling source reference")
        for key, expected in (("run_id", bundle["run_id"]), ("device_id", bundle["device"]["device_id"]), ("boot_id", bundle["device"]["boot_id"]), ("window", bundle["window"])):
            if key in item["metadata"] and item["metadata"][key] != expected:
                fail("item " + key + " mismatch")
    visiting, visited = set(), set()
    def visit(identifier):
        if identifier in visiting:
            fail("source cycle/self reference")
        if identifier in visited:
            return
        visiting.add(identifier)
        for source in items[identifier]["source_ids"]:
            visit(source)
        visiting.remove(identifier)
        visited.add(identifier)
    for identifier in items:
        visit(identifier)
    complete = run.get("outcome") == "pass"
    if complete:
        for key, req in selected.items():
            matches = [item for item in items.values() if item["requirement_id"] == key]
            if len(matches) < req["min_count"]:
                fail("required item count missing: " + key)
            for item in matches:
                if req["complete"] and item["complete"] is not True:
                    fail("required item incomplete: " + item["id"])
                if item["metadata"].get("raw_truncated") is True:
                    fail("raw evidence truncated")
                visual_review = evidence["visual"] is not None and item["kind"] in {"frame_set", "visual_review"} \
                    and req["method"] != "human_observation"
                if (req["method"] != "machine" or item["kind"] == "visual_review") and not visual_review:
                    if not callable(review_validator):
                        fail("trusted review adapter/receipt required")
                    ref = item["metadata"].get("review_ref")
                    if not isinstance(ref, dict):
                        fail("structured review reference required")
                    try:
                        receipt = review_validator(ref)
                    except Exception as exc:
                        fail("trusted review adapter unavailable: " + type(exc).__name__)
                    expected = {"outcome": "pass", "method": req["method"], "item_sha256": item["sha256"],
                                "run_id": bundle["run_id"], "candidate_id": bundle["candidate_id"],
                                "requirement_id": req["id"], "contract_digest": bundle["contract_sha256"],
                                "cell": {key: run.get(key) for key in ("layer", "consumer", "scenario")}}
                    if not isinstance(receipt, dict) or any(receipt.get(k) != v for k,v in expected.items()):
                        fail("trusted review receipt binding mismatch")
    read_item = lambda identifier: reader.read(items[identifier]["path"], items[identifier]["sha256"], items[identifier]["bytes"])
    measurements = _measure(reader, bundle, items, read_item, run, verification_contract) if complete else {}
    if complete and evidence["visual"] is not None and any(req["kind"] in {"frame_set", "visual_review"} for req in selected.values()):
        visual_result = (visual_validator or validate_visual)(bundle, evidence["visual"], items=items, read_item=read_item,
                                                            run=dict(run, run_id=bundle["run_id"]), review_validator=review_validator,
                                                            requirements=selected)
        if not isinstance(visual_result, dict) or visual_result.get("outcome") != "pass":
            fail("visual validator must return a verified pass report")
    elif complete and evidence["visual"] is not None and not any(req["kind"] in {"frame_set", "visual_review"} for req in requirements.values()):
        fail("visual contract missing frame/review requirements")
    reader.finish()
    return {"bundle": bundle, "items": items, "measurements": measurements,
            "verified_files": [str(path) for path in reader.facts]}
