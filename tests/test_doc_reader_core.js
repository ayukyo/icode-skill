"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const readerPath = path.join(__dirname, "../agent_runtime/icode_agent/ui_assets/doc-reader.js");
assert.ok(fs.existsSync(readerPath), "文档库需要独立阅读器模块");
const reader = require(readerPath);

const slug = reader.createSlugger();
assert.equal(slug("中文 标题！"), "中文-标题");
assert.equal(slug("中文 标题！"), "中文-标题-1");
assert.equal(slug("中文 标题！"), "中文-标题-2");
assert.equal(slug("⚙"), "section");
assert.equal(slug("中文-标题-1"), "中文-标题-1-1");

const books = [
  {id: "a", kind: "project", name: "same", project_id: "p1", branch: "main"},
  {id: "b", kind: "project", name: "same", project_id: "p1", branch: "dev"},
  {id: "c", kind: "project", name: "same", project_id: "p2", branch: "main"},
  {id: "d", kind: "module", module_key: "cam", version: "v1"},
  {id: "e", kind: "module", module_key: "cam", version: "v2"},
];
assert.equal(reader.bookFamily(books[0]), reader.bookFamily(books[1]));
assert.notEqual(reader.bookFamily(books[0]), reader.bookFamily(books[2]));
assert.equal(reader.bookFamily(books[3]), reader.bookFamily(books[4]));
assert.notEqual(reader.bookFamily({kind: "project", name: "same", id: "unknown1"}), reader.bookFamily({kind: "project", name: "same", id: "unknown2"}));

const location = reader.routeURL("http://localhost/?ticket=kept", {bookId: "a b", chapterId: "中文", fragment: "同一标题"});
const route = reader.parseRoute(location);
assert.deepEqual(route, {active: true, bookId: "a b", chapterId: "中文", fragment: "同一标题"});
assert.equal(new URL(location).searchParams.get("ticket"), "kept");
assert.equal(reader.parseRoute("http://localhost/#%ZZ").fragment, "");
assert.equal(reader.safeExternalLink("javascript:alert(1)"), false);
assert.equal(reader.safeExternalLink("data:text/html,test"), false);
assert.equal(reader.safeExternalLink("https://example.com/"), true);
assert.equal(reader.safeExternalLink("//example.com/"), false);
assert.equal(reader.scrollKey({id: "a", version: "v1"}, "c"), reader.scrollKey({id: "a", version: "v1"}, "c"));
assert.notEqual(reader.scrollKey({id: "a", version: "v1"}, "c"), reader.scrollKey({id: "a", version: "v2"}, "c"));
assert.equal(reader.isMetadataLabel("项目元信息"), true);
assert.equal(reader.isMetadataLabel("KEYS"), true);
assert.equal(reader.isMetadataLabel("业务说明"), false);
assert.equal(reader.versionLabel({kind: "module", branch: "dev", version: "1234567890abcdef1234567890abcdef", status: "legacy"}), "dev · 快照 1234567890ab");
assert.deepEqual(reader.mappedValue({"next.md#目标": {chapter_id: "next"}}, "next.md#%E7%9B%AE%E6%A0%87"), {chapter_id: "next"});
assert.equal(reader.mappedValue({"a/b.md": "target"}, "a%2Fb.md"), undefined);
assert.equal(typeof reader.integrityText, "function", "integrity labels must be separate from recorded review status");
assert.equal(reader.integrityText("verified"), "摘要一致");
assert.equal(reader.integrityText("modified"), "正文已变化");
assert.equal(reader.integrityText("unverified"), "缺少摘要或未核验");
assert.equal(typeof reader.chapterSourceText, "function", "chapter provenance must use only chapter source records");
assert.equal(reader.chapterSourceText(), "章节基线未记录");
assert.equal(reader.chapterSourceText({commit: "1234567890abcdef", source_id: "abcdef1234567890"}), "章节基线 1234567890ab · 来源 abcdef123456");
console.log("Document reader core behavior checks passed");
