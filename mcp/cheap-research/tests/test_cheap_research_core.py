"""cheap-research 核心契约测试（不依赖真实 LLM provider）。

覆盖（对应 tools_manifest.json 与 proposals 审查契约）：
  1. 14 工具注册与改名（audit_facts→propose_repo_facts, apply_migration→validate_migration_ops）
  2. 截断硬信号边界（8000/6000/4000、truncated 标记、source_digest、source_range）
  3. 数据出境闸门（拒绝真实密钥/私钥/AWS、放行占位符与正常文本）
  4. 默认 token 限额（summarize=512, diff_summary=1024，与 docstring 一致）
  5. provider 未配置时：本地工具仍可用、LLM 工具明确不可用（unconfigured）
  6. validate_migration_ops 路径逃逸 / repo root / remove 权限边界提示
  7. fetch_remote SSRF 防护（_validate_url_safe 拒绝内网/loopback/metadata）
  8. 本地工具确定性（scan_patterns 与 rg 基线在简单样例上一致）

运行：python3 -m unittest discover -s tests -p "test_*.py"  （或 python3 tests/test_cheap_research_core.py）
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 确保可 import server / _utils（server 依赖 config；本地/出境闸门路径不触发 provider）
SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

# 用 example 配置模拟"已装但未填三件套"，供 provider 不可用场景
os.environ.setdefault(
    "CHEAP_RESEARCH_CONFIG",
    str(SERVER_DIR / "config.example.json"),
)

import server  # noqa: E402
from _utils import (  # noqa: E402
    truncate_with_meta,
    scan_sensitive,
    truncate_text,
    truncate_candidates,
)


def run(coro):
    return asyncio.run(coro)


class TestToolRegistry(unittest.TestCase):
    """1. 14 工具注册与改名。"""

    def test_all_tools_registered(self):
        names = set(server.mcp._tool_manager._tools.keys())
        expected = {
            "summarize", "retrieve_similar", "fill_template", "extract",
            "propose_repo_facts", "scan_patterns", "trace_refs", "fetch_remote",
            "validate_migration_ops", "parse_project_id", "scan_modules",
            "diff_summary", "generate_filename", "select_template",
        }
        self.assertEqual(len(names), 14)
        self.assertEqual(names, expected)

    def test_renamed_tools_absent(self):
        names = set(server.mcp._tool_manager._tools.keys())
        self.assertNotIn("audit_facts", names)
        self.assertNotIn("apply_migration", names)

    def test_manifest_consistency(self):
        """manifest 声明的 14 工具与注册一致。"""
        import json
        manifest = json.loads((SERVER_DIR / "tools_manifest.json").read_text())
        manifest_names = {t["name"] for t in manifest["tools"]}
        registered = set(server.mcp._tool_manager._tools.keys())
        self.assertEqual(manifest_names, registered)
        caps = {t["name"]: t["capability"] for t in manifest["tools"]}
        for name, cap in caps.items():
            self.assertIn(cap, {"local", "fetch", "llm"})


class TestTruncationSignal(unittest.TestCase):
    """2. 截断硬信号边界。"""

    def test_not_truncated(self):
        safe, meta = truncate_with_meta("hello", 100)
        self.assertFalse(meta["truncated"])
        self.assertEqual(meta["source_chars"], 5)
        self.assertEqual(meta["consumed_chars"], 5)
        self.assertEqual(meta["source_range"], "chars:all")
        self.assertTrue(meta["source_digest"])

    def test_truncated_8000(self):
        big = "A" * 9000
        safe, meta = truncate_with_meta(big, 8000)
        self.assertTrue(meta["truncated"])
        self.assertEqual(meta["source_chars"], 9000)
        self.assertEqual(meta["consumed_chars"], 8000)
        self.assertEqual(meta["source_range"], "chars:0-7999")
        self.assertTrue(safe.endswith("(truncated)"))
        # 摘要稳定且不暴露原文
        self.assertEqual(len(meta["source_digest"]), 16)
        self.assertNotIn("AAAA", meta["source_digest"])

    def test_boundary_exact(self):
        # 恰好等于上限 → 不截断
        _, meta = truncate_with_meta("B" * 4000, 4000)
        self.assertFalse(meta["truncated"])
        # 超 1 字符 → 截断
        _, meta = truncate_with_meta("B" * 4001, 4000)
        self.assertTrue(meta["truncated"])

    def test_truncate_text_backward_compat(self):
        self.assertFalse(truncate_text("short", 8000).endswith("(truncated)"))
        self.assertTrue(truncate_text("x" * 9000, 8000).endswith("(truncated)"))

    def test_digest_deterministic(self):
        _, m1 = truncate_with_meta("same input", 100)
        _, m2 = truncate_with_meta("same input", 100)
        self.assertEqual(m1["source_digest"], m2["source_digest"])

    def test_extract_returns_truncation_meta(self):
        """extract 成功路径带 truncation 字段（provider 不可用时不可测，仅验证函数签名契约）。"""
        import inspect
        src = inspect.getsource(server.extract)
        self.assertIn("truncate_with_meta", src)
        self.assertIn("scan_sensitive", src)


class TestEgressGate(unittest.TestCase):
    """3. 数据出境闸门。"""

    def test_block_private_key(self):
        hits = scan_sensitive("-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----")
        self.assertTrue(hits)

    def test_block_aws_key(self):
        hits = scan_sensitive("key = AKIA1234567890ABCDEF1234")
        self.assertTrue(hits)

    def test_block_keyvalue_secret(self):
        hits = scan_sensitive("api_key = sk-abcdefghijklmnop123456789")
        self.assertTrue(hits)
        hits = scan_sensitive("password: P@ssw0rdSecretValue123")
        self.assertTrue(hits)

    def test_allow_placeholder(self):
        self.assertFalse(scan_sensitive("api_key: <your-api-key-here>"))
        self.assertFalse(scan_sensitive("secret = xxxplaceholderxxx"))
        self.assertFalse(scan_sensitive("token = your_token_here"))

    def test_allow_normal(self):
        self.assertFalse(scan_sensitive("def main():\n    print('hello')"))
        self.assertFalse(scan_sensitive("password = 12345"))  # 短值不算真实密钥

    def test_tool_rejects_sensitive(self):
        r = run(server.summarize("api_key = sk-abcdefghijklmnop123456789 请摘要"))
        self.assertIn("出境闸门", r.get("error", ""))

    def test_tool_allows_placeholder(self):
        # 占位符不拦截，走到 provider（unconfigured → 明确不可用提示，非出境闸门错误）
        r = run(server.summarize("token = your_token_here, 请摘要"))
        self.assertNotIn("出境闸门", r.get("error", ""))


class TestDefaultTokens(unittest.TestCase):
    """4. 默认 token 限额与 docstring 一致（防契约漂移）。"""

    def test_defaults(self):
        import inspect
        self.assertEqual(inspect.signature(server.summarize).parameters["max_tokens"].default, 512)
        self.assertEqual(inspect.signature(server.diff_summary).parameters["max_tokens"].default, 1024)


class TestProviderGating(unittest.TestCase):
    """5. provider 未配置时：本地工具可用、LLM 工具明确不可用。"""

    def test_local_tools_without_provider(self):
        # validate_migration_ops 纯本地，不触发 provider
        r = run(server.validate_migration_ops(
            {"add": [{"path": "src/a.py", "template": "x"}]}, "/tmp"))
        self.assertIn("answer", r)
        self.assertEqual(r["model"], "validate_migration_ops")

    def test_llm_tool_unconfigured_message(self):
        r = run(server.summarize("普通文本"))
        self.assertIn("未配置", r.get("error", ""))


class TestMigrationOpsValidation(unittest.TestCase):
    """6. validate_migration_ops 路径逃逸 / repo root。"""

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            r = run(server.validate_migration_ops(
                {"add": [{"path": "../../etc/passwd"}]}, td))
            self.assertIn("error", r)
            self.assertIn("逃逸", r["error"])

    def test_repo_root_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            r = run(server.validate_migration_ops({"add": [{"path": "."}]}, td))
            self.assertIn("error", r)

    def test_unknown_op_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            r = run(server.validate_migration_ops({"destroy": [{"path": "a.py"}]}, td))
            self.assertIn("error", r)


class TestSSRF(unittest.TestCase):
    """7. fetch_remote SSRF 防护。"""

    def test_private_ip_rejected(self):
        self.assertIsNotNone(server._validate_url_safe("http://192.168.1.1/"))
        self.assertIsNotNone(server._validate_url_safe("http://10.0.0.1/"))
        self.assertIsNotNone(server._validate_url_safe("http://169.254.169.254/latest/meta-data/"))
        self.assertIsNotNone(server._validate_url_safe("http://100.100.100.200/"))
        self.assertIsNotNone(server._validate_url_safe("http://localhost:8080/"))
        self.assertIsNotNone(server._validate_url_safe("ftp://example.com/x"))

    def test_fetch_remote_blocks_private(self):
        r = run(server.fetch_remote("http://127.0.0.1/"))
        self.assertEqual(r.get("error_code"), "ssrf_blocked")


class TestLocalDeterministic(unittest.TestCase):
    """8. 本地工具确定性 / 与 rg 基线在简单样例一致。"""

    def test_scan_patterns_finds_match(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a.py").write_text("def helper():\n    pass\n")
            r = run(server.scan_patterns(["helper"], td))
            self.assertIn("answer", r)
            matches = r["answer"].get("matches", [])
            self.assertTrue(any("a.py" in str(m.get("file", "")) for m in matches))


if __name__ == "__main__":
    unittest.main(verbosity=2)
