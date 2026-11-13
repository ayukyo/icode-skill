"""Bounded real stdio verification, with scratch homes/graphs and no business calls.

Uses stdlib reader threads, including on Windows where asyncio subprocess support
may be unavailable. npm is allowed only inside ensure_runtime during provisioning.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

SERVERS = ("context7", "memory", "playwright", "sequential-thinking")


class StdioClient:
    def __init__(self, command, env, timeout=20):
        self.timeout = timeout
        self.messages = queue.Queue()
        self.notifications = []
        self.next_id = 0
        self.stderr_bytes = 0
        self.process = subprocess.Popen(command, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.readers = [threading.Thread(target=self._read_stdout, daemon=True),
                        threading.Thread(target=self._read_stderr, daemon=True)]
        for reader in self.readers:
            reader.start()

    def _read_stdout(self):
        try:
            while True:
                line = self.process.stdout.readline(4 * 1024 * 1024)
                if not line:
                    break
                self.messages.put(json.loads(line.decode("utf-8")))
        except (ValueError, OSError) as error:
            self.messages.put(error)
        finally:
            self.messages.put(None)

    def _read_stderr(self):
        # Drain without retaining graph contents, secrets or unbounded diagnostic logs.
        while True:
            data = self.process.stderr.read(8192)
            if not data:
                return
            self.stderr_bytes += len(data)

    def send(self, method, params, request_id=None):
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            message["id"] = request_id
        try:
            self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError("Server pipe closed before request") from error

    def _receive(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("MCP response deadline exceeded")
        try:
            message = self.messages.get(timeout=remaining)
        except queue.Empty as error:
            raise TimeoutError("MCP response deadline exceeded") from error
        if message is None:
            raise RuntimeError(f"Server stdout EOF (exit={self.process.poll()})")
        if isinstance(message, Exception):
            raise RuntimeError("Server stdout is not valid JSON-RPC") from message
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise RuntimeError("Invalid JSON-RPC envelope")
        return message

    def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        self.send(method, params, request_id)
        deadline = time.monotonic() + self.timeout
        while True:
            message = self._receive(deadline)
            if "id" not in message:
                self.notifications.append(message)
                continue
            if message["id"] != request_id:
                raise RuntimeError("Unexpected JSON-RPC response id")
            if "error" in message:
                raise RuntimeError(f"MCP {method} failed: code={message['error'].get('code')}")
            if "result" not in message:
                raise RuntimeError("Missing JSON-RPC result")
            return message["result"]

    def notification(self, method):
        deadline = time.monotonic() + self.timeout
        while True:
            for index, message in enumerate(self.notifications):
                if message.get("method") == method:
                    return self.notifications.pop(index)
            self.notifications.append(self._receive(deadline))

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for reader in self.readers:
            reader.join(timeout=3)
        self.process.stdout.close()
        self.process.stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        if exc_type is None and self.process.returncode != 0:
            raise RuntimeError(f"Server exited with code {self.process.returncode}")


def process_descendants(pid):
    """Snapshot only PID/name/parent metadata; never inspect unrelated command lines."""
    rows = []
    if os.name == "nt":
        from ctypes import wintypes

        class ProcessEntry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        for name in ("Process32FirstW", "Process32NextW"):
            getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "Cannot snapshot process tree")
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while available:
                rows.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
                available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
    elif Path("/proc").is_dir():
        for directory in Path("/proc").iterdir():
            if not directory.name.isdigit():
                continue
            try:
                status = (directory / "status").read_text()
                fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
                rows.append((int(directory.name), int(fields["PPid"]), fields["Name"].strip()))
            except (OSError, KeyError, ValueError):
                continue  # Process exited during the snapshot.
    else:
        raise RuntimeError("Process-tree verification supports Windows and Linux")
    descendants = []
    parents = {pid}
    while True:
        found = [row for row in rows if row[1] in parents and row[0] not in parents]
        if not found:
            return descendants
        descendants.extend({"pid": child, "parent": parent, "name": name} for child, parent, name in found)
        parents.update(row[0] for row in found)


def initialize(client):
    result = client.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                          "clientInfo": {"name": "icode-isolated-verifier", "version": "1.0"}})
    assert result.get("protocolVersion") and result.get("serverInfo"), "Missing initialization metadata"
    client.send("notifications/initialized", {})
    return result


def tool_result(result):
    assert not result.get("isError"), "Memory fixture tool returned an error"
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


def verify_runtime(server, runtime, scratch, timeout=20, deny_network=True):
    home = scratch / server / "home"
    home.mkdir(parents=True)
    graph = home / "fixture-graph.jsonl"
    env = os.environ.copy()
    env.update(HOME=str(home), USERPROFILE=str(home), MEMORY_FILE_PATH=str(graph),
               DISABLE_THOUGHT_LOGGING="true", NODE_OPTIONS="")
    if deny_network:
        preload = home / "deny-network.cjs"
        preload.write_text("const deny=()=>{throw Error('Isolated MCP verifier denies network')};\n"
                           "require('net').Socket.prototype.connect=deny;\n"
                           "globalThis.fetch=deny;\n", encoding="utf-8")
        env["NODE_OPTIONS"] = '--require="' + str(preload).replace("\\", "/") + '"'
    command = [str(runtime["node"]), str(runtime["entry"])]
    with StdioClient(command, env, timeout) as client:
        info = initialize(client)
        tools = client.request("tools/list", {}).get("tools")
        assert isinstance(tools, list) and tools, "No tools discovered"
        assert len({tool["name"] for tool in tools}) == len(tools), "Duplicate tool names"
        assert all(isinstance(tool.get("inputSchema"), dict) for tool in tools), "Missing tool schema"
        children = process_descendants(client.process.pid)
        assert not children, f"Direct server has unexpected child processes: {children}"
        if server == "memory":
            assert info.get("capabilities", {}).get("resources", {}).get("subscribe"), "Resource subscription missing"
            resources = client.request("resources/list", {})["resources"]
            uri = next(resource["uri"] for resource in resources if resource["uri"] == "memory://knowledge-graph")
            client.request("resources/subscribe", {"uri": uri})
            client.request("tools/call", {"name": "create_entities", "arguments": {"entities": [
                {"name": "icode-fixed-fixture", "entityType": "verification", "observations": ["temporary test only"]}]}})
            notification = client.notification("notifications/resources/updated")
            assert notification["params"]["uri"] == uri
            contents = client.request("resources/read", {"uri": uri})["contents"]
            assert json.loads(contents[0]["text"])["entities"][0]["name"] == "icode-fixed-fixture"
            client.request("resources/unsubscribe", {"uri": uri})
    report = {"server": server, "package": runtime.get("package"), "version": runtime.get("version"),
              "command": command, "protocol": info["protocolVersion"], "server_info": info["serverInfo"],
              "tools": sorted(tool["name"] for tool in tools),
              "tool_schema_sha256": hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest(),
              "children": children, "exit_code": client.process.returncode, "network_denied": deny_network}
    if server == "memory":
        assert graph.is_file(), "Scratch graph was not persisted"
        with StdioClient(command, env, timeout) as restarted:
            initialize(restarted)
            graph_result = tool_result(restarted.request("tools/call", {"name": "read_graph", "arguments": {}}))
            assert graph_result["entities"][0]["name"] == "icode-fixed-fixture", "Scratch graph lost on restart"
        report["scratch_memory_write_restart_resource_subscription"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", "--node-path", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--catalog-root", type=Path)
    parser.add_argument("--scratch-root", type=Path)
    parser.add_argument("--server", action="append", choices=SERVERS)
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp/_lib"))
    from node_packages import ensure_runtime
    with tempfile.TemporaryDirectory(prefix="icode node stdio ", dir=args.scratch_root) as directory:
        reports = []
        for server in args.server or SERVERS:
            kwargs = {"runtime_root": args.runtime_root}
            if args.catalog_root is not None:
                kwargs["catalog_root"] = args.catalog_root
            runtime = ensure_runtime(server, args.node, **kwargs)
            reports.append(verify_runtime(server, runtime, Path(directory), args.timeout))
        print(json.dumps({"scope": "isolated stdio discovery; memory fixture only", "results": reports}, indent=2))


if __name__ == "__main__":
    main()
