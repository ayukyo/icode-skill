"""cheap-research 核心契约测试（不依赖真实 LLM provider）。

覆盖（对应 tools_manifest.json 与 proposals 审查契约）：
  1. 15 工具注册、能力发现与改名（audit_facts→propose_repo_facts, apply_migration→validate_migration_ops）
  2. 截断硬信号边界（8000/6000/4000、truncated 标记、source_digest、source_range）
  3. 数据出境闸门（拒绝真实密钥/私钥/AWS、放行占位符与正常文本）
  4. 默认 token 限额（summarize=512, diff_summary=1024，与 docstring 一致）
  5. provider 未配置时：本地工具仍可用、LLM 工具明确不可用（unconfigured）
  6. validate_migration_ops 路径逃逸 / repo root / remove 权限边界提示
  7. fetch_remote SSRF 防护（拒绝内网并固定已验证 IP，阻断 DNS rebinding）
  8. 本地工具确定性、allowed_roots 与 symlink containment
  9. MCP outputSchema / read-only / open-world annotations

运行：python3 -m unittest discover -s tests -p "test_*.py"  （或 python3 tests/test_cheap_research_core.py）
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    """1. 15 工具注册、能力发现与改名。"""

    def test_all_tools_registered(self):
        names = set(server.mcp._tool_manager._tools.keys())
        expected = {
            "describe_capabilities",
            "summarize", "retrieve_similar", "fill_template", "extract",
            "propose_repo_facts", "scan_patterns", "trace_refs", "fetch_remote",
            "validate_migration_ops", "parse_project_id", "scan_modules",
            "diff_summary", "generate_filename", "select_template",
        }
        self.assertEqual(len(names), 15)
        self.assertEqual(names, expected)

    def test_renamed_tools_absent(self):
        names = set(server.mcp._tool_manager._tools.keys())
        self.assertNotIn("audit_facts", names)
        self.assertNotIn("apply_migration", names)

    def test_manifest_consistency(self):
        """manifest 声明的 15 工具与注册一致。"""
        import json
        manifest = json.loads((SERVER_DIR / "tools_manifest.json").read_text())
        manifest_names = {t["name"] for t in manifest["tools"]}
        registered = set(server.mcp._tool_manager._tools.keys())
        self.assertEqual(manifest_names, registered)
        caps = {t["name"]: t["capability"] for t in manifest["tools"]}
        for name, cap in caps.items():
            self.assertIn(cap, {"local", "fetch", "llm"})
        for tool in manifest["tools"]:
            self.assertIn("trust_level", tool)
            self.assertIn("conclusion_ceiling", tool)

    def test_capability_profile_is_non_secret(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.json"
            cfg.write_text(json.dumps({
                "provider": "openai_compat",
                "base_url": "https://example.com/v1",
                "api_key": "must-not-leak-secret",
                "model": "small-model",
            }))
            with patch.dict(os.environ, {"CHEAP_RESEARCH_CONFIG": str(cfg)}):
                result = run(server.describe_capabilities())
        self.assertEqual(result["answer"]["tool_count"], 15)
        self.assertEqual(
            result["answer"]["capability_counts"],
            {"local": 6, "fetch": 1, "llm": 8},
        )
        serialized = str(result)
        self.assertNotIn("must-not-leak-secret", serialized)
        self.assertIn("configured", result["answer"]["provider"])
        boundary = result["answer"]["data_scope_boundary"]
        self.assertEqual(boundary["corporate_email_remote_default"], "deny")
        self.assertEqual(
            boundary["override_requirement"],
            "explicit_provider_and_disclosure_scope",
        )

    def test_mcp_metadata_present(self):
        for name, tool in server.mcp._tool_manager._tools.items():
            self.assertIsNotNone(tool.output_schema, name)
            properties = tool.output_schema.get("properties", {})
            self.assertTrue(
                {"answer", "error_code", "error", "model"}.issubset(properties),
                f"{name}: {tool.output_schema}",
            )
            self.assertTrue(tool.annotations.readOnlyHint, name)
            self.assertFalse(tool.annotations.destructiveHint, name)
            if name in {"fetch_remote", "summarize", "retrieve_similar", "fill_template",
                        "extract", "propose_repo_facts", "diff_summary",
                        "generate_filename", "select_template"}:
                self.assertTrue(tool.annotations.openWorldHint, name)
            else:
                self.assertFalse(tool.annotations.openWorldHint, name)

    def test_structured_output_keeps_common_metadata(self):
        class FakeProvider:
            async def invoke(self, *_args, **_kwargs):
                return {
                    "answer": "ok",
                    "confidence": None,
                    "confidence_note": "not_calibrated",
                    "model": "fake",
                    "tokens_used": 1,
                    "cost_estimated": 0.0,
                    "cost_note": "estimate",
                }

        async def call():
            with patch.object(server, "get_provider", return_value=FakeProvider()):
                return await server.mcp._tool_manager.call_tool(
                    "summarize", {"text": "abc"}, convert_result=True,
                )

        _, structured = run(call())
        self.assertIn("truncation", structured)
        self.assertEqual(structured["confidence_note"], "not_calibrated")
        self.assertEqual(structured["cost_note"], "estimate")

    def test_local_ollama_profile_uses_default_base_url(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.json"
            cfg.write_text(json.dumps({
                "provider": "local_ollama",
                "model": "qwen:test",
            }))
            with patch.dict(os.environ, {"CHEAP_RESEARCH_CONFIG": str(cfg)}):
                profile = server._provider_profile()
        self.assertTrue(profile["configured"])
        self.assertEqual(profile["missing_fields"], [])


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

    def test_llm_tool_missing_config_degrades(self):
        with tempfile.TemporaryDirectory() as td:
            missing = str(Path(td) / "missing.json")
            with patch.dict(os.environ, {"CHEAP_RESEARCH_CONFIG": missing}):
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

    def test_add_template_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            r = run(server.validate_migration_ops(
                {"add": [{"path": "src/a.py", "template": "print('ok')\n"}]}, td))
            self.assertEqual(r["answer"]["ops"][0]["content"], "print('ok')\n")

    def test_rename_requires_and_normalizes_destination(self):
        with tempfile.TemporaryDirectory() as td:
            missing = run(server.validate_migration_ops(
                {"rename": [{"path": "src/old.py"}]}, td))
            self.assertIn("error", missing)
            escaped = run(server.validate_migration_ops(
                {"rename": [{"path": "src/old.py", "destination": "../outside.py"}]}, td))
            self.assertIn("error", escaped)
            valid = run(server.validate_migration_ops(
                {"rename": [{"path": "src/old.py", "destination": "src/new.py"}]}, td))
            op = valid["answer"]["ops"][0]
            self.assertEqual(Path(op["destination"]), Path(td) / "src/new.py")
            self.assertEqual(len(valid["answer"]["files_affected"]), 2)


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

    def test_redirects_are_manual_and_revalidated(self):
        import inspect
        src = inspect.getsource(server.fetch_remote)
        helper_src = inspect.getsource(server._fetch_pinned_hop)
        self.assertIn("_resolve_safe_target(next_url)", src)
        self.assertIn("_fetch_pinned_hop", src)
        self.assertIn("follow_redirects=False", helper_src)
        self.assertIn("trust_env=False", helper_src)

    def test_redirect_to_private_target_is_blocked(self):
        class FakeResponse:
            status_code = 302
            headers = {"location": "http://127.0.0.1/private"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def stream(self, *_args, **_kwargs):
                return FakeResponse()

        with patch("httpx.AsyncClient", side_effect=FakeClient), patch.object(
            server,
            "_resolve_safe_target",
            side_effect=[(["93.184.216.34"], None), ([], "URL 指向内网/危险 IP")],
        ):
            result = run(server.fetch_remote("https://public.example/start"))
        self.assertEqual(result.get("error_code"), "ssrf_blocked")
        self.assertIn("重定向目标", result.get("error", ""))

    def test_fetch_uses_validated_ip_and_preserves_host_sni(self):
        requests = []
        dns_calls = 0

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "text/plain"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def raise_for_status(self):
                return None

            async def aiter_bytes(self, chunk_size=8192):
                yield b"PUBLIC_RESPONSE"

        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def stream(self, method, url, **kwargs):
                requests.append((method, url, kwargs, self.kwargs))
                return FakeResponse()

        def fake_getaddrinfo(*_args, **_kwargs):
            nonlocal dns_calls
            dns_calls += 1
            ip = "93.184.216.34" if dns_calls == 1 else "127.0.0.1"
            return [(2, 1, 6, "", (ip, 443))]

        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo), patch(
            "httpx.AsyncClient", side_effect=FakeClient,
        ):
            result = run(server.fetch_remote("https://rebind.test/path?q=1"))

        self.assertEqual(result["answer"]["content"], "PUBLIC_RESPONSE")
        self.assertEqual(dns_calls, 1)
        _, request_url, request_kwargs, client_kwargs = requests[0]
        self.assertEqual(request_url, "https://93.184.216.34/path?q=1")
        self.assertEqual(request_kwargs["headers"]["Host"], "rebind.test")
        self.assertEqual(request_kwargs["extensions"]["sni_hostname"], "rebind.test")
        self.assertFalse(client_kwargs["trust_env"])


class TestLocalDeterministic(unittest.TestCase):
    """8. 本地工具确定性 / 与 rg 基线在简单样例一致。"""

    def test_scan_patterns_finds_match(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a.py").write_text("def helper():\n    pass\n")
            r = run(server.scan_patterns(["helper"], td))
            self.assertIn("answer", r)
            matches = r["answer"].get("matches", [])
            self.assertTrue(any("a.py" in str(m.get("file", "")) for m in matches))
            self.assertIsInstance(r["answer"]["files_with_matches"], int)

    def test_allowed_roots_rejects_outside_path(self):
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "a.py").write_text("needle\n")
            with patch.dict(os.environ, {"CHEAP_RESEARCH_ALLOWED_ROOTS": allowed}):
                r = run(server.scan_patterns(["needle"], outside))
            self.assertIn("allowed_roots", r.get("error", ""))

    def test_symlink_file_is_not_scanned(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
            root = Path(td)
            source = Path(outside) / "secret.py"
            source.write_text("outside_needle\n")
            (root / "link.py").symlink_to(source)
            r = run(server.scan_patterns(["outside_needle"], str(root)))
            self.assertEqual(r["answer"]["matches"], [])


class TestProviderRuntime(unittest.TestCase):
    def test_local_ollama_default_base_url(self):
        provider = server.LocalOllamaProvider({"model": "test-model"})
        self.assertEqual(provider.base_url, "http://localhost:11434/v1")

    def test_prompt_injection_guard_is_connected(self):
        import inspect
        self.assertIn("sanitize_for_llm", inspect.getsource(server.summarize))
        provider_src = inspect.getsource(server.OpenAICompatProvider.invoke)
        self.assertIn('"role": "system"', provider_src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
