"""platform_entry 的 Windows PATH 注入单测（纯离线，无宿主依赖）。

背景：npx-cli.js 最终执行的是 _npx 缓存里包自带的 .cmd 垫片，垫片内部再调一次
`node`，只能靠 PATH 解析。安装期 node 常在非 PATH 位置（portable / nvm / scoop /
自动 provision 的运行时），所以 Windows 主方案必须显式注入 env.PATH。

断言刻意不依赖运行平台：Windows 分支通过替换 platform_entry 的 os 引用触发，
路径用 tmp 目录构造，避免 CI 在 Linux 上跳过。
"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp" / "_lib"))

import platform_entry  # noqa: E402


class WindowsPathTests(unittest.TestCase):
    def test_node_dir_is_first_path_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = os.path.join(tmp, "node.exe")
            env = platform_entry._windows_env_with_node_path(node, None)
            head = env["PATH"].split(os.pathsep)[0]
            self.assertEqual(head, os.path.dirname(os.path.abspath(node)))

    def test_system_directories_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = os.path.join(tmp, "node.exe")
            parts = platform_entry._windows_env_with_node_path(node, None)["PATH"].split(
                os.pathsep
            )
            lowered = [part.lower() for part in parts]
            self.assertTrue(any(part.endswith("system32") for part in lowered))
            self.assertTrue(any("windowspowershell" in part for part in lowered))

    def test_extra_env_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = platform_entry._windows_env_with_node_path(
                os.path.join(tmp, "node.exe"), {"DISABLE_THOUGHT_LOGGING": "true"}
            )
            self.assertEqual(env["DISABLE_THOUGHT_LOGGING"], "true")
            self.assertIn("PATH", env)

    def test_caller_supplied_path_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = platform_entry._windows_env_with_node_path(
                os.path.join(tmp, "node.exe"), {"Path": os.path.join(tmp, "custom")}
            )
            self.assertEqual(env["Path"], os.path.join(tmp, "custom"))
            self.assertNotIn("PATH", env)


class WindowsEntryShapeTests(unittest.TestCase):
    """在任意平台验证 Windows 分支：替换 platform_entry 的 os 引用，不改全局 os。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.node = root / "node.exe"
        self.node.write_bytes(b"")
        npx_cli = root / "node_modules" / "npm" / "bin" / "npx-cli.js"
        npx_cli.parent.mkdir(parents=True)
        npx_cli.write_text("", encoding="utf-8")
        self.npx_cli = npx_cli

    def _build(self, env=None):
        fake_os = types.SimpleNamespace(
            name="nt", pathsep=";", environ={}, path=os.path
        )
        original = platform_entry.os
        platform_entry.os = fake_os
        try:
            return platform_entry.build_server_entry(
                str(self.node), str(self.node.parent / "npx.cmd"), "@scope/pkg", env
            )
        finally:
            platform_entry.os = original

    def test_entry_runs_node_with_npx_cli(self):
        entry = self._build()
        self.assertEqual(entry["command"], str(self.node))
        self.assertEqual(entry["args"][0], str(self.npx_cli))
        self.assertEqual(entry["args"][-1], "@scope/pkg")

    def test_entry_injects_path(self):
        entry = self._build()
        self.assertIn("env", entry)
        self.assertIn("PATH", entry["env"])
        self.assertEqual(
            entry["env"]["PATH"].split(";")[0],
            os.path.dirname(os.path.abspath(str(self.node))),
        )

    def test_entry_keeps_fallback_and_extra_env(self):
        entry = self._build({"DISABLE_THOUGHT_LOGGING": "true"})
        self.assertIn("_fallback", entry)
        self.assertEqual(entry["env"]["DISABLE_THOUGHT_LOGGING"], "true")


if __name__ == "__main__":
    unittest.main()
