#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/local/a" "$TMP/local/b"
printf 'first\n' > "$TMP/local/a/same.log"
printf 'second\n' > "$TMP/local/b/same.log"
printf 'shared\n' > "$TMP/local/alpha.log"
printf 'shared\n' > "$TMP/local/beta.log"

cat > "$TMP/meta-single.json" <<'JSON'
{
  "id": "BUG-42",
  "pid": "project-1",
  "lib": "BUG",
  "uniqueId": 42,
  "_id": "task-42",
  "status": "打开",
  "comments": [
    {
      "id": "activity-1",
      "created": "2026-09-08T01:02:03.000Z",
      "content": {
        "comment": "device log attached",
        "files": [
          {"_id": "file-1", "name": "device", "ext": "log", "size": 12,
           "url": "https://tb.example/files/device.log?token=old"}
        ]
      }
    }
  ]
}
JSON

cat > "$TMP/meta-duplicate.json" <<'JSON'
{
  "id": "BUG-42",
  "pid": "project-1",
  "lib": "BUG",
  "uniqueId": 42,
  "_id": "task-42",
  "status": "打开",
  "comments": [
    {
      "id": "activity-1",
      "created": "2026-09-08T01:02:03.000Z",
      "content": {
        "comment": "device log attached",
        "files": [
          {"_id": "file-1", "name": "device", "ext": "log", "size": 12,
           "url": "https://tb.example/files/device.log?token=old"}
        ],
        "attachments": [
          {"fileId": "file-1", "fileName": "device", "fileType": "log", "fileSize": 12,
           "downloadUrl": "https://tb.example/files/device.log?token=new"}
        ]
      }
    }
  ]
}
JSON

python3 "$ROOT/tools/evidence_intake.py" \
  --root "$TMP" --source-json "$TMP/meta-duplicate.json" \
  --path "$TMP/local" --output "$TMP/current.json"

python3 - "$TMP/current.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["schema_version"] == 1 and d["read_only"] is True
assert d["side_effects"] == ["write_output_manifest"]
assert len([a for a in d["artifacts"] if a.get("remote_id") == "file-1"]) == 1
assert any(x["reason"] == "same_remote_id" for x in d["duplicates"])
same_named = [a for a in d["artifacts"] if a["original_name"] == "same.log"]
assert len(same_named) == 2
assert len({a["sha256"] for a in same_named}) == 2
assert any(x["reason"] == "same_sha256" for x in d["duplicates"])
remote = next(a for a in d["artifacts"] if a.get("remote_id") == "file-1")
assert remote["sha256"] is None and remote["content_hash_pending"] is True
assert d["source_identity"]["task_id"] == "task-42"
assert d["coverage_hints"]["timestamp_range"]["start"] == "2026-09-08T01:02:03.000Z"
PY

# 仅状态变化不能伪装成新增语义证据。
python3 "$ROOT/tools/evidence_intake.py" \
  --root "$TMP" --source-json "$TMP/meta-single.json" \
  --path "$TMP/local" --output "$TMP/previous.json"
python3 - "$TMP/meta-single.json" "$TMP/meta-status.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
d["status"] = "已完成"
json.dump(d, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
PY
python3 "$ROOT/tools/evidence_intake.py" \
  --root "$TMP" --source-json "$TMP/meta-status.json" \
  --path "$TMP/local" --previous "$TMP/previous.json" \
  --output "$TMP/status.json"
python3 - "$TMP/status.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["delta"]["status_only"] is True
assert d["delta"]["new_semantic_evidence"] is False
PY

# 新内容文件是语义增量。
printf 'brand new evidence\n' > "$TMP/local/new.log"
python3 "$ROOT/tools/evidence_intake.py" \
  --root "$TMP" --source-json "$TMP/meta-single.json" \
  --path "$TMP/local" --previous "$TMP/previous.json" \
  --output "$TMP/new.json"
python3 - "$TMP/new.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["delta"]["new_semantic_evidence"] is True
assert d["delta"]["new_semantic_ids"]
PY
rm "$TMP/local/new.log"

# 同一远端附件换一种字段表示，只计重复表示，不新增逻辑 artifact。
python3 "$ROOT/tools/evidence_intake.py" \
  --root "$TMP" --source-json "$TMP/meta-duplicate.json" \
  --path "$TMP/local" --previous "$TMP/previous.json" \
  --output "$TMP/duplicate.json"
python3 - "$TMP/previous.json" "$TMP/duplicate.json" <<'PY'
import json, sys
old = json.load(open(sys.argv[1], encoding="utf-8"))
new = json.load(open(sys.argv[2], encoding="utf-8"))
assert len([a for a in old["artifacts"] if a.get("remote_id") == "file-1"]) == 1
assert len([a for a in new["artifacts"] if a.get("remote_id") == "file-1"]) == 1
assert new["delta"]["new_semantic_evidence"] is False
assert new["delta"]["duplicate_representation"] is True
PY

# TB 兼容集成：pull 去掉双字段重复，watch 有 manifest 时按语义比较，无 manifest 保留 legacy。
python3 - "$ROOT" "$TMP/previous.json" "$TMP" <<'PY'
import importlib.util, pathlib, sys
root = pathlib.Path(sys.argv[1])

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

pull = load("tb_pull_contract", root / "tools/tb/scripts/tb_pull.py")
watch = load("tb_watch_contract", root / "tools/tb/scripts/tb_watch.py")
activities = [{
    "_id": "activity-1", "action": "activity.comment.attachments", "created": "2026-09-08",
    "content": {
        "files": [{"_id": "file-1", "name": "device", "ext": "log", "size": 12}],
        "attachments": [{"fileId": "file-1", "fileName": "device", "fileType": "log", "fileSize": 12}],
    },
}]
assert len(pull.collect_files(activities)) == 1
url_alias_activities = [{
    "_id": "activity-alias", "action": "activity.comment.attachments",
    "content": {
        "files": [{"_id": "alias-id", "name": "alias", "ext": "log", "url": "https://tb/x.log?t=1"}],
        "attachments": [{"fileName": "alias", "fileType": "log", "downloadUrl": "https://tb/x.log?t=2"}],
    },
}]
assert len(pull.collect_files(url_alias_activities)) == 1
conflicting_ids = [{
    "_id": "activity-conflict", "action": "activity.comment.attachments",
    "content": {"files": [
        {"_id": "id-one", "name": "one", "url": "https://tb/shared.log"},
        {"_id": "id-two", "name": "two", "url": "https://tb/shared.log"},
    ]},
}]
assert len(pull.collect_files(conflicting_ids)) == 2

previous = __import__("json").load(open(sys.argv[2], encoding="utf-8"))
probe = {
    "uniqueId": 42, "_id": "task-42", "pid": "project-1", "lib": "BUG", "status": "已完成",
    "comments": [{"id": "activity-1", "created": "2026-09-08T01:02:03.000Z", "comment": "device log attached"}],
    "files": [{"remote_id": "file-1", "name": "device", "ext": "log", "size": 12}],
}
detail = watch.compare_update(probe, {"status": "打开", "comments": [], "files": []}, previous)
assert detail["has_update"] is True and detail["status_only"] is False
assert detail["duplicate_representation"] is True
assert detail["new_semantic_evidence"] is False
assert detail["legacy_comparison"] is False
legacy = watch.compare_update(probe, {"status": "已完成", "comments": [], "files": []})
assert legacy["legacy_comparison"] is True
assert watch.has_update(probe, {"status": "已完成", "comments": [], "files": []}) == (True, "新增评论1条、新增附件1个")

# 相对 --out 目录也必须正确生成 manifest，不能把 root 重复拼接。
tmp = pathlib.Path(sys.argv[3])
relative_dir = tmp / "relative-pull"
relative_dir.mkdir()
meta_path = relative_dir / "BUG-7_meta.json"
meta_path.write_text('{"id":"BUG-7","comments":[],"files":[],"downloaded":[]}', encoding="utf-8")
old_cwd = pathlib.Path.cwd()
try:
    __import__("os").chdir(tmp)
    result = pull._write_evidence_manifest("relative-pull", "relative-pull/BUG-7_meta.json", [], "BUG-7")
finally:
    __import__("os").chdir(old_cwd)
assert pathlib.Path(result).is_file()
PY

# 远端 ID/URL 是同一 identity 的别名：一条同时带 ID+URL 的表示必须桥接仅 URL 表示。
cat > "$TMP/meta-alias.json" <<'JSON'
{
  "id": "BUG-43",
  "comments": [{
    "id": "activity-alias", "created": "2026-09-08T02:00:00Z",
    "content": {
      "files": [{"_id": "remote-alias-1", "name": "alias", "ext": "log", "size": 7,
                 "url": "https://tb.example/files/alias.log?token=one"}],
      "attachments": [{"fileName": "alias", "fileType": "log", "fileSize": 7,
                       "downloadUrl": "https://tb.example/files/alias.log?token=two"}]
    }
  }]
}
JSON
python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
  --source-json "$TMP/meta-alias.json" --output "$TMP/alias.json"
python3 - "$TMP/alias.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
remote = [a for a in d["artifacts"] if a.get("remote_id") == "remote-alias-1"]
assert len(remote) == 1, remote
assert len(remote[0]["identity_aliases"]) == 2
assert any(x["reason"] == "same_url_fingerprint" for x in d["duplicates"])
PY
cat > "$TMP/meta-url-only.json" <<'JSON'
{"id":"BUG-43","comments":[{"id":"activity-alias","created":"2026-09-08T02:00:00Z","content":{"files":[{"name":"alias","ext":"log","size":7,"url":"https://tb.example/files/alias.log?token=old"}]}}]}
JSON
python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
  --source-json "$TMP/meta-url-only.json" --output "$TMP/url-only.json"
python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
  --source-json "$TMP/meta-alias.json" --previous "$TMP/url-only.json" \
  --output "$TMP/alias-enriched.json"
python3 - "$TMP/url-only.json" "$TMP/alias-enriched.json" <<'PY'
import json, sys
old, new = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:])
assert new["delta"]["new_semantic_evidence"] is False
assert new["delta"]["removed_or_unavailable"] is False
assert old["semantic_fingerprint"] == new["semantic_fingerprint"]
PY

# activity ID 稳定不代表正文不变：正文编辑必须形成语义增量并改变稳定指纹。
python3 - "$TMP/meta-single.json" "$TMP/meta-edited.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
d["comments"][0]["content"]["comment"] = "edited device log description"
json.dump(d, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
PY
python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
  --source-json "$TMP/meta-edited.json" --path "$TMP/local" \
  --previous "$TMP/previous.json" --output "$TMP/edited.json"
python3 - "$TMP/previous.json" "$TMP/edited.json" <<'PY'
import json, sys
old, new = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:])
assert new["delta"]["new_semantic_evidence"] is True
assert any(x.startswith("activity:") for x in new["delta"]["new_semantic_ids"])
assert old["semantic_fingerprint"] != new["semantic_fingerprint"]
PY

# 同样输入重复执行，generated_at/output 路径不能污染语义指纹。
python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
  --source-json "$TMP/meta-single.json" --path "$TMP/local" --output "$TMP/repeat.json"
python3 - "$TMP/previous.json" "$TMP/repeat.json" <<'PY'
import json, sys
a, b = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:])
assert a["semantic_fingerprint"] == b["semantic_fingerprint"]
PY

# 损坏 JSON 不得吞掉其他有效证据：输出 partial manifest 并定位解析失败。
printf '{broken json' > "$TMP/broken.json"
python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
  --source-json "$TMP/meta-single.json" --source-json "$TMP/broken.json" \
  --output "$TMP/partial.json"
python3 - "$TMP/partial.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["status"] == "partial"
assert d["activities"]
assert any(x.get("input", "").endswith("broken.json") for x in d["coverage_hints"]["parse_failures"])
PY

# 输出不得覆盖 source/previous/local file 输入，拒绝后输入 hash 必须不变。
SOURCE_HASH="$(sha256sum "$TMP/meta-single.json")"
if python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
    --source-json "$TMP/meta-single.json" --output "$TMP/meta-single.json" >/dev/null 2>&1; then
  echo "output unexpectedly overwrote source input" >&2
  exit 1
fi
test "$SOURCE_HASH" = "$(sha256sum "$TMP/meta-single.json")"
LOCAL_HASH="$(sha256sum "$TMP/local/a/same.log")"
if python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
    --source-json "$TMP/meta-single.json" --path "$TMP/local/a/same.log" \
    --output "$TMP/local/a/same.log" >/dev/null 2>&1; then
  echo "output unexpectedly overwrote local evidence" >&2
  exit 1
fi
test "$LOCAL_HASH" = "$(sha256sum "$TMP/local/a/same.log")"
if python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
    --source-json "$TMP/meta-single.json" --path "$TMP/local" \
    --output "$TMP/local/a/same.log" >/dev/null 2>&1; then
  echo "output unexpectedly overwrote a file reached through input directory" >&2
  exit 1
fi
test "$LOCAL_HASH" = "$(sha256sum "$TMP/local/a/same.log")"

printf 'do-not-overwrite\n' > "$TMP/local/evidence_manifest.json"
BYPASS_HASH="$(sha256sum "$TMP/local/evidence_manifest.json")"
if python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
    --path "$TMP/local" --exclude-path "$TMP/local/evidence_manifest.json" \
    --output "$TMP/local/evidence_manifest.json" >/dev/null 2>&1; then
  echo "exclude-path unexpectedly allowed a non-manifest input overwrite" >&2
  exit 1
fi
test "$BYPASS_HASH" = "$(sha256sum "$TMP/local/evidence_manifest.json")"

if python3 "$ROOT/tools/evidence_intake.py" --root / \
    --path /dev/null --output "$TMP/root-scope.json" >/dev/null 2>&1; then
  echo "filesystem root was unexpectedly accepted as evidence root" >&2
  exit 1
fi
test ! -e "$TMP/root-scope.json"

# 根内 symlink 保留链接路径与目标；越界 symlink 只记失败，不读取目标。
mkdir -p "$TMP/links"
printf 'inside target\n' > "$TMP/links/target.log"
ln -s target.log "$TMP/links/inside.log"
ln -s /etc/passwd "$TMP/links/outside.log"
python3 "$ROOT/tools/evidence_intake.py" --root "$TMP" \
  --path "$TMP/links" --output "$TMP/links.json"
python3 - "$TMP/links.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
link = next(a for a in d["artifacts"] if a.get("local_path") == "links/inside.log")
assert link["is_symlink"] is True
assert link["symlink_target"] == "links/target.log"
assert not any(a.get("local_path") == "links/outside.log" for a in d["artifacts"])
assert any(x.get("input", "").endswith("outside.log") for x in d["coverage_hints"]["parse_failures"])
PY

# watch 必须识别证据撤回及跨轮新增表示；manifest symlink 不能逃出 debug 工单。
python3 - "$ROOT" "$TMP/previous.json" "$TMP" <<'PY'
import importlib.util, json, pathlib, sys
root, previous_path, tmp = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
spec = importlib.util.spec_from_file_location("tb_watch_delta_contract", root / "tools/tb/scripts/tb_watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
previous = json.load(open(previous_path, encoding="utf-8"))
url_only_previous = json.load(open(tmp / "url-only.json", encoding="utf-8"))
alias_probe = {
    "id": "BUG-43", "status": None,
    "comments": [{
        "id": "activity-alias", "created": "2026-09-08T02:00:00Z", "comment": "",
        "files": [{"remote_id": "remote-alias-1", "name": "alias", "ext": "log", "size": 7,
                   "url_fingerprint": next(a["url_fingerprint"] for a in url_only_previous["artifacts"])}]
    }]
}
alias_detail = watch.compare_update(alias_probe, {"status": None}, url_only_previous)
assert alias_detail["new_semantic_evidence"] is False
assert alias_detail["removed_or_unavailable"] is False
removed_probe = {
    "uniqueId": 42, "_id": "task-42", "pid": "project-1", "lib": "BUG", "status": "打开",
    "comments": [{"id": "activity-1", "created": "2026-09-08T01:02:03.000Z", "comment": "device log attached"}],
    "files": []
}
removed = watch.compare_update(removed_probe, {"status": "打开"}, previous)
assert removed["has_update"] is True
assert removed["removed_or_unavailable"] is True and removed["removed_semantic_ids"]

representation_probe = {
    "uniqueId": 42, "_id": "task-42", "pid": "project-1", "lib": "BUG", "status": "打开",
    "comments": [{
        "id": "activity-1", "created": "2026-09-08T01:02:03.000Z", "comment": "device log attached",
        "files": [{"remote_id": "file-1", "name": "device", "ext": "log", "size": 12}],
        "attachments": [{"remote_id": "file-1", "name": "device", "ext": "log", "size": 12}]
    }],
    "files": [{"remote_id": "file-1", "name": "device", "ext": "log", "size": 12}]
}
representation = watch.compare_update(representation_probe, {"status": "打开"}, previous)
assert representation["has_update"] is False
assert representation["duplicate_representation"] is True
assert representation["new_representation_ids"]

workticket = tmp / "debug-ticket"
workticket.mkdir()
outside = tmp / "outside-manifest.json"
outside.write_text('{}', encoding="utf-8")
(workticket / "evidence_manifest.json").symlink_to(outside)
assert watch.locate_evidence_manifest(str(workticket), None) is None
PY

# TB 集成扫描整个 ticket 证据树，并用 remote_id 可靠绑定两个同名附件。
python3 - "$ROOT" "$TMP" <<'PY'
import importlib.util, json, pathlib, sys
root, tmp = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("tb_pull_tree_contract", root / "tools/tb/scripts/tb_pull.py")
pull = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pull)
ticket = tmp / "ticket-tree"
(ticket / "extracted").mkdir(parents=True)
(ticket / "same.log").write_text("first remote\n", encoding="utf-8")
(ticket / "same_1.log").write_text("second remote\n", encoding="utf-8")
(ticket / "extracted" / "child.log").write_text("derived child\n", encoding="utf-8")
meta = {
    "id": "BUG-99", "comments": [],
    "files": [
        {"remote_id": "remote-1", "name": "same.log", "ext": "log", "size": 13},
        {"remote_id": "remote-2", "name": "same.log", "ext": "log", "size": 14}
    ],
    # 故意逆序：按名称队列绑定会把两个内容绑反。
    "downloaded": [
        {"remote_id": "remote-2", "name": "same.log", "path": "same_1.log", "size": 14},
        {"remote_id": "remote-1", "name": "same.log", "path": "same.log", "size": 13}
    ]
}
meta_path = ticket / "BUG-99_meta.json"
meta_path.write_text(json.dumps(meta), encoding="utf-8")
manifest_path = pull._write_evidence_manifest(str(ticket), str(meta_path), meta["downloaded"], "BUG-99")
manifest = json.load(open(manifest_path, encoding="utf-8"))
by_id = {a["remote_id"]: a for a in manifest["artifacts"] if a.get("remote_id")}
assert by_id["remote-1"]["local_path"] == "same.log"
assert by_id["remote-2"]["local_path"] == "same_1.log"
assert any(a.get("local_path") == "extracted/child.log" for a in manifest["artifacts"])
assert not any(a.get("local_path", "").endswith("_meta.json") for a in manifest["artifacts"])
first_fingerprint = manifest["semantic_fingerprint"]
manifest_path = pull._write_evidence_manifest(str(ticket), str(meta_path), meta["downloaded"], "BUG-99")
refreshed = json.load(open(manifest_path, encoding="utf-8"))
assert refreshed["semantic_fingerprint"] == first_fingerprint
assert not any("evidence_manifest" in a.get("local_path", "") for a in refreshed["artifacts"])
PY

echo "PASS: evidence intake contract"
