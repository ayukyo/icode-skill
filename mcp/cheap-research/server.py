"""cheap-research MCP server 入口。

配置来源: $CHEAP_RESEARCH_CONFIG 指向的 JSON 文件 (默认 ./config.json)。
session 模型只通过 mcp 工具调用, 不直连 LLM API。

启动: python server.py

5 核心工具（LLM 推理）：
  - summarize        长上下文压缩
  - retrieve_similar 历史工单相似度匹配
  - fill_template    模板填充
  - extract          结构化提取
  - audit_facts      代码事实审计

9 增强工具（含 6 工具型 + 3 LLM 摘要）：
  - scan_patterns / trace_refs / diff_summary / apply_migration
  - fetch_remote / generate_filename / select_template
  - parse_project_id / scan_modules

所有工具严格遵循单闸门: 价值 ≥ 3 ★ + 低风险 = 入选。
不接管决策: 3 质疑者对抗 / 架构决策 / 终审裁决 / 修复方案一律不走本工具。
"""
import ipaddress
import json
import os
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

# 可选：jsonschema 严格校验（extract 工具用）
try:
    import jsonschema
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False

# 强制 stdout/stderr 用 UTF-8,兼容 Windows 默认 GBK 控制台。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 确保 server.py 所在目录 (即 cheap-research/) 在 sys.path 第一位。
_SERVER_DIR = Path(__file__).resolve().parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from providers.base import UnconfiguredProvider  # noqa: E402
from providers.local_ollama import LocalOllamaProvider  # noqa: E402
from providers.openai_compat import OpenAICompatProvider  # noqa: E402

from _utils import (  # noqa: E402
    make_error_response,
    validate_non_empty_str,
    validate_dict,
    validate_list,
    validate_path_exists,
    validate_int_range,
    validate_url,
    truncate_text,
    truncate_candidates,
    json_dumps_safe,
    iter_source_files,
    safe_read_text,
    safe_run_git,
    sanitize_for_llm,
    detect_language_from_ext,
    build_symbol_regex,
    DEFAULT_SOURCE_EXTS,
    DEFAULT_EXCLUDE_DIRS,
)
from providers._logger import log_call  # noqa: E402

# fetch_remote 配置
FETCH_MAX_BYTES = 5 * 1024 * 1024  # 5MB 响应上限
FETCH_TIMEOUT_SECONDS = 10  # 10s 超时（防 30s 卡住会话）


def _is_private_or_dangerous_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """检查 IP 是否在内网 / loopback / metadata 范围（SSRF 防护）。"""
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    # 阿里云 metadata (100.100.0.0/16) — Python is_private 不覆盖
    if isinstance(ip, ipaddress.IPv4Address):
        try:
            if ip in ipaddress.IPv4Network("100.100.0.0/16", strict=False):
                return True
        except ValueError:
            pass
    return False


def _validate_url_safe(url: str) -> str | None:
    """SSRF 防护：解析 URL、解析 host IP、拒绝内网/loopback/metadata。

    拒绝：
    - loopback (127.0.0.0/8, ::1)
    - 私网 (10/8, 172.16/12, 192.168/16, fc00::/7)
    - link-local (169.254/16, fe80::/10) — AWS / GCP metadata 入口
    - 阿里云 metadata (100.100.100.200)
    - 主机名解析失败

    接受：公网 IP（防止内网探测 / 元数据读取）

    Returns: error_msg 或 None (None 表示 URL 安全)
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"仅允许 http/https, 实际 {parsed.scheme}"
        hostname = parsed.hostname
        if not hostname:
            return "URL 缺少 hostname"

        # 解析 hostname → IP（防 DNS rebinding：解析时锁定）
        try:
            infos = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return f"DNS 解析失败: {hostname}"

        # 检查所有返回的 IP
        for info in infos:
            sockaddr = info[4]
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if _is_private_or_dangerous_ip(ip):
                return f"URL 指向内网/危险 IP {ip_str}（{ip.__class__.__name__}），已拒绝（SSRF 防护）"

        return None
    except Exception as e:
        return f"URL 解析失败: {e}"

PROVIDERS = {
    "openai_compat": OpenAICompatProvider,
    "local_ollama": LocalOllamaProvider,
}

mcp = FastMCP("cheap-research")


def load_config() -> dict:
    """读 $CHEAP_RESEARCH_CONFIG 或 ./config.json."""
    cfg_path = os.environ.get(
        "CHEAP_RESEARCH_CONFIG",
        str(_SERVER_DIR / "config.json"),
    )
    p = Path(cfg_path)
    if not p.exists():
        raise FileNotFoundError(
            f"未找到配置文件 {cfg_path}。\n"
            f"首次安装请: cp config.example.json config.json, "
            f"然后填 base_url / api_key / model。\n"
            f"详见 README.md。"
        )
    return json.loads(p.read_text())


def get_provider():
    """返回 provider 实例。openai_compat 缺字段时返 UnconfiguredProvider (不抛错)。"""
    cfg = load_config()
    name = cfg.get("provider", "openai_compat").lower()
    if name == "openai_compat":
        missing = [k for k in ("base_url", "api_key", "model") if not cfg.get(k)]
        if missing:
            return UnconfiguredProvider(missing=missing)
    cls = PROVIDERS.get(name)
    if not cls:
        raise ValueError(f"未知 provider='{name}', 可选: {list(PROVIDERS)}")
    return cls(cfg)


# ===========================================================================
# 5 核心工具
# ===========================================================================

@mcp.tool()
async def summarize(
    text: str,
    max_tokens: int = 512,
    focus: str = "",
) -> dict:
    """长上下文压缩。

    把传入的文本交给便宜 LLM 做摘要, 返回结构化结果。
    严格不接管决策/对抗/架构: 推理类工作一律不走本工具。

    Args:
        text: 待压缩文本 (支持中英文, 自动截断到 8000 字符)
        max_tokens: 最大输出 token 数 (默认 512)
        focus: 可选聚焦角度 (如"异常根因" / "改动点" / "风险"), 为空时让 LLM 自己判

    Returns:
        成功: {answer: {summary, key_points}, confidence, model, tokens_used, cost_estimated}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(text, "text"):
        return make_error_response(err)

    provider = get_provider()

    # 文本截断（防 prompt 爆）
    text_safe = truncate_text(text, max_chars=8000)

    # 构造 schema 强制结构化输出
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "简洁摘要, 保留核心信息"},
            "key_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-5 条关键点列表",
            },
        },
        "required": ["summary", "key_points"],
    }

    focus_part = f"重点关注: {focus}\n" if focus else ""
    prompt = (
        f"请对以下文本做摘要压缩。{focus_part}"
        f"输出要求: 1) summary (简洁摘要, 保留核心信息); "
        f"2) key_points (3-5 条关键点列表, 每条 1 句话)。\n\n"
        f"原文:\n{text_safe}"
    )

    return await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=max_tokens,
    )


@mcp.tool()
async def retrieve_similar(
    query: str,
    candidates: list,
    k: int = 5,
) -> dict:
    """历史工单相似度匹配。

    调用方提供候选列表 (从 ~/.claude/icode_data/index.json 之类数据源筛选),
    让 LLM 对每个候选按 query 评分 (0~1), 返回 top-k。

    流程:
        1. 校验 candidates 非空
        2. 截断到最多 50 条 (防 prompt 爆)
        3. LLM 评分 + 排序
        4. 返回 top-k

    严格不接管决策: 评分只是参考, 主会话负责最终采纳。

    Args:
        query: 查询关键词或描述
        candidates: 候选工单列表 [{id, summary, keywords, status, ...}], 调方负责预处理
        k: top-k 个数 (1~20, 默认 5)

    Returns:
        成功: {answer: {items: [{id, score, summary}], query}, confidence, model, tokens_used, cost_estimated}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(query, "query"):
        return make_error_response(err)
    if err := validate_list(candidates, "candidates"):
        return make_error_response(err)
    if not isinstance(k, int) or k < 1 or k > 20:
        return make_error_response(f"k 必须是 1~20 的整数, 实际 {k}")

    provider = get_provider()

    # 截断候选（防 prompt 爆）
    truncated, was_truncated = truncate_candidates(candidates, max_count=50)

    # 构造候选摘要（只保留关键字段: id + summary + keywords）
    candidates_compact = []
    for c in truncated:
        if not isinstance(c, dict):
            continue
        candidates_compact.append({
            "id": c.get("id") or c.get("ticket_id") or c.get("name") or "",
            "summary": c.get("summary") or c.get("description") or "",
            "keywords": c.get("keywords") or [],
        })

    # 构造 LLM prompt
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "score": {"type": "number", "description": "0~1 相似度评分"},
                        "reason": {"type": "string", "description": "1 句话理由"},
                    },
                    "required": ["id", "score"],
                },
            },
        },
        "required": ["items"],
    }

    candidates_str = json_dumps_safe(candidates_compact, max_chars=6000)
    prompt = (
        f"请对以下候选工单按 query 评分 (0~1, 越高越相似), 并返回 top-{k}。\n"
        f"评分标准: 与 query 语义匹配度、关键词重合度。\n\n"
        f"query: {query}\n\n"
        f"候选 ({len(candidates_compact)} 条"
        f"{', 已截断' if was_truncated else ''}):\n"
        f"{candidates_str}\n\n"
        f"只返 top-{k}, 按 score 降序。"
    )

    result = await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=1500,
    )

    if "error" in result:
        return result

    # 限流到 k
    items = result.get("answer", {}).get("items", [])
    if isinstance(items, list):
        items = items[:k]
        result["answer"] = {"items": items, "query": query}

    return result


@mcp.tool()
async def fill_template(
    template: str,
    data: dict,
) -> dict:
    """模板填充。

    给 LLM 一个模板（如 changelog 模板、review 模板）和数据 dict,
    让 LLM 智能填充占位符并保证语义连贯。
    严格不接管决策: LLM 只做填充, 不做判断。

    Args:
        template: 模板字符串 (可用 {placeholder} 或自由描述)
        data: 填充数据 dict

    Returns:
        成功: {answer: {filled: str, fields_used: [str]}, confidence, model, tokens_used, cost_estimated}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(template, "template"):
        return make_error_response(err)
    if err := validate_dict(data, "data"):
        return make_error_response(err)

    provider = get_provider()

    # 模板截断（防 prompt 爆）
    template_safe = truncate_text(template, max_chars=4000)
    data_str = json_dumps_safe(data, max_chars=2000)

    schema = {
        "type": "object",
        "properties": {
            "filled": {"type": "string", "description": "填充后的完整文本"},
            "fields_used": {
                "type": "array",
                "items": {"type": "string"},
                "description": "实际用到的 data 字段名",
            },
        },
        "required": ["filled"],
    }

    prompt = (
        f"请按 data 填充模板, 保持语义连贯。\n"
        f"如果模板中的占位符 data 没对应字段, 保留占位符文字。\n\n"
        f"模板:\n{template_safe}\n\n"
        f"data:\n{data_str}"
    )

    return await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=2048,
    )


@mcp.tool()
async def extract(
    text: str,
    schema: dict,
    instruction: str = "",
) -> dict:
    """结构化提取。

    给 LLM 一段文本 + JSON schema, 让 LLM 按 schema 抽取字段。
    严格不接管决策: LLM 只做提取, 不做判断或分类。

    Args:
        text: 待提取文本 (自动截断到 8000 字符)
        schema: 期望 JSON schema (e.g. {"type":"object","properties":{...}})
        instruction: 附加指令 (可选, 如"聚焦变更点")

    Returns:
        成功: {answer: {parsed: {符合 schema}, fields_count: int}, confidence, model, tokens_used, cost_estimated}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(text, "text"):
        return make_error_response(err)
    if err := validate_dict(schema, "schema"):
        return make_error_response(err)

    provider = get_provider()

    text_safe = truncate_text(text, max_chars=8000)
    instruction_part = f"\n附加指令: {instruction}" if instruction else ""

    # 复用 openai_compat.py 的 schema 强约束逻辑
    prompt = (
        f"请从以下文本中按 JSON schema 提取字段。{instruction_part}\n\n"
        f"文本:\n{text_safe}"
    )

    result = await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=2048,
    )

    if "error" in result:
        return result

    # 计算字段数（仅顶层）
    parsed = result.get("answer", {})
    if isinstance(parsed, dict):
        # jsonschema 严格校验（v1.0 修复：原本仅靠 LLM 自报 schema，现客户端校验）
        if _HAS_JSONSCHEMA:
            try:
                jsonschema.validate(instance=parsed, schema=schema)
            except jsonschema.ValidationError as e:
                return {
                    "error_code": "schema_validation_failed",
                    "error": f"LLM 输出不符合 schema: {e.message}",
                    "model": result.get("model", "unknown"),
                }
        result["answer"] = {
            "parsed": parsed,
            "fields_count": len(parsed),
            "schema_validated": _HAS_JSONSCHEMA,
        }

    return result


@mcp.tool()
async def audit_facts(
    repo_path: str,
    focus: str = "",
    max_files: int = 10,
) -> dict:
    """代码事实审计。

    扫描 repo_path 下的关键文件 (README / CLAUDE.md / pyproject.toml / package.json / 入口 main.*),
    让 LLM 总结关键事实 (用途、依赖、入口、关键 API)。

    严格不接管决策: LLM 只做事实抽取, 不做架构评分或改进建议。

    Args:
        repo_path: 仓库路径 (本地绝对路径)
        focus: 审计重点 (如"对外 API" / "依赖关系" / "测试覆盖"), 默认通用
        max_files: 最多扫描文件数 (默认 10, 防超大 repo)

    Returns:
        成功: {answer: {facts: [str], source_files: [str], focus}, confidence, model, tokens_used, cost_estimated}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(repo_path, "repo_path"):
        return make_error_response(err)
    if not isinstance(max_files, int) or max_files < 1 or max_files > 100:
        return make_error_response(f"max_files 必须是 1~100 的整数, 实际 {max_files}")

    p = validate_path_exists(repo_path, "repo_path")
    if isinstance(p, str):
        return make_error_response(p)
    repo_path = str(p)

    # 关键文件模式（按优先级）
    key_patterns = [
        "README.md", "README.en.md", "README.zh.md",
        "CLAUDE.md", "AGENTS.md",
        "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
        "main.py", "main.cpp", "main.c", "main.go", "main.rs",
        "src/main.py", "src/main.cpp", "src/main.c",
        "app.py", "server.py", "index.ts", "index.js",
    ]

    source_files = []
    for pattern in key_patterns:
        candidate = Path(repo_path) / pattern
        if candidate.exists() and candidate.is_file():
            source_files.append(str(candidate))
            if len(source_files) >= max_files:
                break

    if not source_files:
        return make_error_response(
            f"未在 {repo_path} 找到关键文件 (README/CLAUDE.md/入口文件等), "
            f"无法审计。"
        )

    # 读取文件内容（每个文件截断到 1500 字符，v1.0 修复：原 2000 → 1500 防 prompt 爆）
    file_contents = []
    for f in source_files:
        try:
            content = Path(f).read_text(encoding="utf-8", errors="replace")
            file_contents.append(f"=== {f} ===\n{truncate_text(content, max_chars=1500)}")
        except Exception:
            continue

    combined = "\n\n".join(file_contents)
    combined = truncate_text(combined, max_chars=8000)  # v1.0 修复：原 15000 → 8000

    provider = get_provider()

    schema = {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "5-10 条关键事实, 每条 1 句话",
            },
            "source_summary": {
                "type": "string",
                "description": "工程 1 句话总览",
            },
        },
        "required": ["facts", "source_summary"],
    }

    focus_part = f"审计重点: {focus}\n" if focus else ""
    prompt = (
        f"请审计以下代码仓库, 抽取关键事实。{focus_part}\n"
        f"输出: 1) source_summary (工程 1 句话总览); 2) facts (5-10 条关键事实, "
        f"如用途、依赖、入口、关键 API)。\n\n"
        f"源文件清单 ({len(source_files)} 个):\n"
        f"{chr(10).join(source_files)}\n\n"
        f"文件内容:\n{combined}"
    )

    result = await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=2048,
    )

    if "error" in result:
        return result

    # 附加 source_files 到 answer
    if isinstance(result.get("answer"), dict):
        result["answer"]["source_files"] = source_files
        result["answer"]["focus"] = focus

    return result


# ===========================================================================
# 9 增强工具
# ===========================================================================

@mcp.tool()
async def scan_patterns(
    patterns: list,
    scope_path: str = ".",
    exclude_dirs: list | None = None,
    max_files: int = 1000,
    max_matches: int = 200,
) -> dict:
    """机械模式匹配（grep 风格）。

    用 re 扫描 scope_path 下的源码文件, 匹配 patterns 中的每个模式。
    纯本地纯文本, 不调 LLM —— 适合机械匹配场景（找引用、找遗留 TODO 等）。

    Args:
        patterns: 模式列表 (正则字符串)
        scope_path: 扫描根路径 (默认 ".")
        exclude_dirs: 额外排除目录 (默认 .git/.venv/node_modules 等)
        max_files: 最多扫描文件数 (防超大 repo)
        max_matches: 最多匹配条数 (防结果爆炸)

    Returns:
        成功: {answer: {matches: [{file, line, content, pattern}], total_count, files_scanned}, model: "scan_patterns"}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_list(patterns, "patterns"):
        return make_error_response(err)
    if err := validate_non_empty_str(scope_path, "scope_path"):
        return make_error_response(err)
    if err := validate_int_range(max_files, "max_files", 1, 10000):
        return make_error_response(err)
    if err := validate_int_range(max_matches, "max_matches", 1, 10000):
        return make_error_response(err)

    p = validate_path_exists(scope_path, "scope_path")
    if isinstance(p, str):
        return make_error_response(p)
    scope = p

    # 编译正则
    compiled = []
    for pat in patterns:
        if not isinstance(pat, str):
            return make_error_response(f"pattern 必须都是字符串, 实际 {type(pat).__name__}")
        try:
            compiled.append((pat, re.compile(pat)))
        except re.error as e:
            return make_error_response(f"pattern '{pat}' 正则错误: {e}")

    # 合并额外排除目录
    all_exclude = set(DEFAULT_EXCLUDE_DIRS)
    if exclude_dirs:
        for d in exclude_dirs:
            if isinstance(d, str):
                all_exclude.add(d)

    # 扫描文件
    files = iter_source_files(scope, exclude_dirs=all_exclude, max_files=max_files)

    # 匹配
    matches = []
    files_with_matches = set()
    for f in files:
        content = safe_read_text(f, max_chars=100000)
        if content is None:
            continue
        file_has_match = False
        for line_no, line in enumerate(content.splitlines(), 1):
            for pat_str, pat_re in compiled:
                if pat_re.search(line):
                    matches.append({
                        "file": str(f),
                        "line": line_no,
                        "content": line.strip()[:200],
                        "pattern": pat_str,
                    })
                    file_has_match = True
                    if len(matches) >= max_matches:
                        return {
                            "answer": {
                                "matches": matches,
                                "total_count": len(matches),
                                "files_scanned": len(files),
                                "files_with_matches": len(files_with_matches) + (1 if file_has_match else 0),
                                "truncated": True,
                            },
                            "model": "scan_patterns",
                        }
        if file_has_match:
            files_with_matches.add(str(f))

    return {
        "answer": {
            "matches": matches,
            "total_count": len(matches),
            "files_scanned": len(files),
            "files_with_matches": files_with_matches,
            "truncated": False,
        },
        "model": "scan_patterns",
    }


@mcp.tool()
async def trace_refs(
    symbol: str,
    scope_path: str = ".",
    max_files: int = 500,
    max_refs: int = 100,
) -> dict:
    """符号引用追溯。

    扫描 scope_path 下的源码文件, 找 symbol 的所有引用位置。
    纯本地纯文本, 不调 LLM —— 适合"找某函数被谁调用"场景。

    Args:
        symbol: 符号名 (e.g. "MyClass::method", "foo")
        scope_path: 扫描根路径 (默认 ".")
        max_files: 最多扫描文件数
        max_refs: 最多返引用条数

    Returns:
        成功: {answer: {refs: [{file, line, context}], count}, model: "trace_refs"}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(symbol, "symbol"):
        return make_error_response(err)
    if err := validate_non_empty_str(scope_path, "scope_path"):
        return make_error_response(err)
    if err := validate_int_range(max_files, "max_files", 1, 5000):
        return make_error_response(err)
    if err := validate_int_range(max_refs, "max_refs", 1, 1000):
        return make_error_response(err)

    p = validate_path_exists(scope_path, "scope_path")
    if isinstance(p, str):
        return make_error_response(p)
    scope = p

    # 编译正则（v1.0 修复：语言适配，build_symbol_regex 处理 C++ 模板 / Python 包路径）
    # 默认 python 风格，正则仍按单词边界；具体语言正则由 build_symbol_regex 处理
    try:
        # 默认 regex（Python 风格）
        default_regex = re.compile(r"\b" + re.escape(symbol) + r"\b")
    except re.error as e:
        return make_error_response(f"symbol 正则错误: {e}")

    files = iter_source_files(scope, exclude_dirs=DEFAULT_EXCLUDE_DIRS, max_files=max_files)

    refs = []
    for f in files:
        content = safe_read_text(f, max_chars=100000)
        if content is None:
            continue
        # 按文件语言构建更精确的正则（v1.0 修复：避免 C++ 模板 `MyClass<T>::method` 误识别）
        lang = detect_language_from_ext(f.suffix)
        try:
            symbol_re = build_symbol_regex(symbol, lang)
        except re.error:
            # 退化到默认 regex
            symbol_re = default_regex
        lines = content.splitlines()
        for line_no, line in enumerate(lines, 1):
            if symbol_re.search(line):
                # 上下文：前后各 1 行
                start = max(0, line_no - 2)
                end = min(len(lines), line_no + 1)
                context = "\n".join(
                    f"{i+1}: {lines[i]}" for i in range(start, end)
                )
                refs.append({
                    "file": str(f),
                    "line": line_no,
                    "context": truncate_text(context, max_chars=300),
                })
                if len(refs) >= max_refs:
                    return {
                        "answer": {
                            "refs": refs,
                            "count": len(refs),
                            "files_scanned": len(files),
                            "truncated": True,
                        },
                        "model": "trace_refs",
                    }

    return {
        "answer": {
            "refs": refs,
            "count": len(refs),
            "files_scanned": len(files),
            "truncated": False,
        },
        "model": "trace_refs",
    }


@mcp.tool()
async def fetch_remote(
    url: str,
    max_chars: int = 5000,
) -> dict:
    """远程 HTTP 拉取（不调 LLM）。

    通用 HTTP GET 工具, 适用于 TB 缺陷源 / 远程文档 / GitHub API 等。
    严格不接管决策: 拉到的内容由主会话解析, 本工具只负责拉原文本。

    SSRF 防护：拒绝内网 / loopback / metadata 端点（fetch_remote P0 修复）。
    Size cap：5MB 响应上限（防 OOM）。
    Timeout：10s（防 30s 卡住会话）。

    Args:
        url: 完整 URL (http/https, 必须是公网)
        max_chars: 响应最大字符数 (默认 5000, 防超大响应)

    Returns:
        成功: {answer: {content, status_code, content_type, truncated}, model: "fetch_remote"}
        失败: {error_code: str, error: str, model: str}
    """
    # 入参校验
    if err := validate_url(url, "url"):
        return make_error_response(err)
    if err := validate_int_range(max_chars, "max_chars", 100, 100000):
        return make_error_response(err)

    # SSRF 防护
    if ssrf_err := _validate_url_safe(url):
        log_call("fetch_remote", "error", url=url, error="ssrf_blocked")
        return {
            "error_code": "ssrf_blocked",
            "error": ssrf_err,
            "model": "fetch_remote",
        }

    import httpx
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            r = await client.get(
                url,
                headers={"User-Agent": "cheap-research/1.0"},
            )
            r.raise_for_status()

            # Size cap：实时累积
            content_bytes = bytearray()
            truncated_by_size = False
            async for chunk in r.aiter_bytes(chunk_size=8192):
                content_bytes.extend(chunk)
                if len(content_bytes) > FETCH_MAX_BYTES:
                    content_bytes = content_bytes[:FETCH_MAX_BYTES]
                    truncated_by_size = True
                    break

            content = content_bytes.decode("utf-8", errors="replace")
            truncated_by_chars = False
            if len(content) > max_chars:
                content = truncate_text(content, max_chars)
                truncated_by_chars = True

            log_call(
                "fetch_remote",
                "success",
                url=url,
                status_code=r.status_code,
                bytes=len(content_bytes),
                truncated_size=truncated_by_size,
                truncated_chars=truncated_by_chars,
            )
            return {
                "answer": {
                    "content": content,
                    "status_code": r.status_code,
                    "content_type": r.headers.get("content-type", ""),
                    "truncated": truncated_by_size or truncated_by_chars,
                    "truncated_by_size": truncated_by_size,
                    "truncated_by_chars": truncated_by_chars,
                },
                "model": "fetch_remote",
            }
    except httpx.HTTPStatusError as e:
        log_call("fetch_remote", "error", url=url, error=f"HTTP {e.response.status_code}")
        return {
            "error_code": "api_http_error",
            "error": f"HTTP {e.response.status_code}: {url}",
            "model": "fetch_remote",
        }
    except httpx.TimeoutException:
        log_call("fetch_remote", "error", url=url, error="timeout")
        return {
            "error_code": "api_timeout",
            "error": f"请求超时 ({FETCH_TIMEOUT_SECONDS}s): {url}",
            "model": "fetch_remote",
        }
    except httpx.RequestError as e:
        log_call("fetch_remote", "error", url=url, error=str(e))
        return {
            "error_code": "api_connection_error",
            "error": f"请求失败: {e}",
            "model": "fetch_remote",
        }


@mcp.tool()
async def apply_migration(
    schema_diff: dict,
    repo_path: str = ".",
) -> dict:
    """Schema 迁移操作生成（不直接执行）。

    解析 schema_diff, 生成迁移 ops (add/remove/rename/modify)。
    严格不直接改文件: 返 ops 给主会话审核 + 执行, 避免误操作。

    Args:
        schema_diff: schema 变更描述, e.g.
                     {
                       "add": [{"path": "src/foo.py", "template": "..."}],
                       "remove": [{"path": "src/old.py"}],
                       "modify": [{"path": "src/main.py", "changes": "..."}]
                     }
        repo_path: 仓库路径 (默认 ".")

    Returns:
        成功: {answer: {ops: [{type, target, content}], files_affected: [str]}, model: "apply_migration"}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_dict(schema_diff, "schema_diff"):
        return make_error_response(err)

    p = validate_path_exists(repo_path, "repo_path")
    if isinstance(p, str):
        return make_error_response(p)
    repo = p

    # 支持的 op 类型
    allowed_ops = {"add", "remove", "rename", "modify"}
    ops = []
    files_affected = set()

    for op_type, items in schema_diff.items():
        if op_type not in allowed_ops:
            return make_error_response(f"不支持 op 类型 '{op_type}', 仅支持 {allowed_ops}")
        if not isinstance(items, list):
            return make_error_response(f"op '{op_type}' 必须是 list, 实际 {type(items).__name__}")

        for item in items:
            if not isinstance(item, dict):
                return make_error_response(f"op '{op_type}' 的项必须是 dict, 实际 {type(item).__name__}")
            target = item.get("path") or item.get("target") or item.get("src")
            if not target:
                return make_error_response(f"op '{op_type}' 缺少 path/target/src 字段")

            # 路径安全性检查（防 ../ 逃逸，且必须 relative to repo）
            try:
                target_path = (repo / target).resolve()
                repo_resolved = repo.resolve()
                # 必须 relative to repo（严防逃逸到 repo 外或 root）
                if not target_path.is_relative_to(repo_resolved):
                    return make_error_response(
                        f"op '{op_type}' 路径逃逸（不在 repo 内）: {target}"
                    )
                # 防止 root 路径
                if target_path == repo_resolved:
                    return make_error_response(
                        f"op '{op_type}' 不能指向 repo 根: {target}"
                    )
            except Exception:
                return make_error_response(f"op '{op_type}' 路径无效: {target}")

            ops.append({
                "type": op_type,
                "target": str(target_path),
                "content": item.get("content") or item.get("changes") or "",
                "status": "pending",
            })
            files_affected.add(str(target_path))

    return {
        "answer": {
            "ops": ops,
            "files_affected": sorted(files_affected),
            "op_count": len(ops),
            "repo_path": str(repo),
            "warning": "ops 已生成, 未执行 —— 主会话需审核后手动执行",
        },
        "model": "apply_migration",
    }


@mcp.tool()
async def parse_project_id(
    repo_path: str = ".",
) -> dict:
    """解析 project_id（基于 git 仓库根 + basename）。

    强证据三件套: 仓库根 basename + 章节前 50 行内容 + KEYS + 摘要。
    本工具只提供 project_id 和 branch, 章节/摘要由 doc/readme 链路生成。

    严格不接管决策: parse_project_id 只做"是/否"判定, 不做命名建议。

    Args:
        repo_path: 仓库路径 (默认 ".")

    Returns:
        成功: {answer: {project_id, branch, repo_root}, model: "parse_project_id"}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(repo_path, "repo_path"):
        return make_error_response(err)

    p = validate_path_exists(repo_path, "repo_path")
    if isinstance(p, str):
        return make_error_response(p)
    repo = p

    # 强证据 1: 仓库根 basename
    project_id = repo.resolve().name

    # 强证据 2: git branch
    branch = safe_run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])

    # 强证据 3: 仓库根路径
    repo_root = safe_run_git(repo, ["rev-parse", "--show-toplevel"])
    if repo_root is None:
        # 非 git 仓库也允许（仅给 project_id）
        return {
            "answer": {
                "project_id": project_id,
                "branch": "",
                "repo_root": str(repo.resolve()),
                "is_git_repo": False,
            },
            "model": "parse_project_id",
        }

    # v1.0 修复：submodule 检测（之前只返 basename，submodule 路径错误）
    # 如果 repo_path 是 git submodule 内的目录，project_id 应反映 submodule 路径
    repo_root_resolved = Path(repo_root).resolve()
    is_submodule = False
    submodule_path = None
    try:
        # 检查每个父目录是否有 .gitmodules
        current = repo.parent
        while current != current.parent:
            gitmodules = current / ".gitmodules"
            if gitmodules.exists():
                # 找到 .gitmodules 上级目录（submodule 顶层）
                rel_path = repo.resolve().relative_to(current.resolve())
                # 检查 .gitmodules 是否包含此路径
                try:
                    gm_content = gitmodules.read_text(encoding="utf-8", errors="replace")
                    # 解析 [submodule "name"] 段 + path
                    for m in re.finditer(
                        r'\[submodule\s+"([^"]+)"\][^[]*?path\s*=\s*(\S+)',
                        gm_content, re.DOTALL
                    ):
                        if m.group(2) == str(rel_path):
                            is_submodule = True
                            submodule_path = f"{current.name}/{rel_path}"
                            project_id = f"{current.name}/{rel_path}"
                            break
                except (OSError, UnicodeDecodeError):
                    pass
                if is_submodule:
                    break
            current = current.parent
    except (OSError, ValueError):
        pass

    return {
        "answer": {
            "project_id": project_id,
            "branch": branch or "",
            "repo_root": repo_root,
            "is_git_repo": True,
            "is_submodule": is_submodule,
            "submodule_path": submodule_path,
        },
        "model": "parse_project_id",
    }


@mcp.tool()
async def scan_modules(
    repo_path: str = ".",
    max_files: int = 1000,
) -> dict:
    """模块检测（6 级优先级）。

    按优先级扫描 repo_path 下的独立模块:
        1. git submodule (.gitmodules)
        2. repo 工具 (.repo/)
        3. CMake FetchContent / add_subdirectory
        4. monorepo (lerna / pnpm / yarn workspaces)
        5. vendor (Go / vendor 目录)
        6. 用户配置 (.icode_modules.yaml)

    纯本地文件系统扫描, 不调 LLM。

    Args:
        repo_path: 仓库路径 (默认 ".")
        max_files: 最多扫描文件数

    Returns:
        成功: {answer: {modules: [{path, type, priority}], count}, model: "scan_modules"}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(repo_path, "repo_path"):
        return make_error_response(err)
    if err := validate_int_range(max_files, "max_files", 1, 10000):
        return make_error_response(err)

    p = validate_path_exists(repo_path, "repo_path")
    if isinstance(p, str):
        return make_error_response(p)
    repo = p

    modules = []

    # 优先级 1: git submodule
    gitmodules = repo / ".gitmodules"
    if gitmodules.exists():
        try:
            content = gitmodules.read_text(encoding="utf-8", errors="replace")
            # 解析 [submodule "name"] 段
            for match in re.finditer(r'\[submodule\s+"([^"]+)"\][^[]*?path\s*=\s*(\S+)', content, re.DOTALL):
                modules.append({
                    "path": match.group(2),
                    "name": match.group(1),
                    "type": "git-submodule",
                    "priority": 1,
                })
        except (OSError, UnicodeDecodeError):
            pass

    # 优先级 2: repo 工具（Android）
    repo_dir = repo / ".repo"
    if repo_dir.exists() and repo_dir.is_dir():
        manifest = repo_dir / "manifest.xml"
        if manifest.exists():
            try:
                content = manifest.read_text(encoding="utf-8", errors="replace")
                for match in re.finditer(r'<project\s+[^>]*path="([^"]+)"', content):
                    modules.append({
                        "path": match.group(1),
                        "name": match.group(1).split("/")[-1],
                        "type": "repo-manifest",
                        "priority": 2,
                    })
            except (OSError, UnicodeDecodeError):
                pass

    # 优先级 3: CMake FetchContent
    cmake_files = []
    for cm in repo.rglob("CMakeLists.txt"):
        if "node_modules" in str(cm) or ".venv" in str(cm):
            continue
        if len(cmake_files) >= 10:
            break
        cmake_files.append(cm)
    for cf in cmake_files:
        try:
            content = safe_read_text(cf, max_chars=20000)
            if not content:
                continue
            # v1.0 修复：移除注释（避免 # 开头的模块名误识别）
            # CMake 行内注释：# 后面到行尾
            # CMake 块注释：#[[ ... ]]（暂不处理大块注释，普通 # 注释足够）
            content_no_comments = re.sub(r'#[^\n]*', '', content)
            # 用 DOTALL 支持跨行 FetchContent_Declare(
            for match in re.finditer(
                r'(?:FetchContent_Declare|add_subdirectory)\s*\(\s*(\S+)',
                content_no_comments,
                re.DOTALL,
            ):
                # 提取第一个非空白 token 作为模块名（去除变量 ${} 等）
                name_candidate = match.group(1).strip().rstrip(')')
                # 跳过变量引用（如 ${...}）和字符串
                if name_candidate.startswith('$') or name_candidate.startswith('"'):
                    continue
                modules.append({
                    "path": f"<cmake:{name_candidate}>",
                    "name": name_candidate,
                    "type": "cmake",
                    "priority": 3,
                    "source_file": str(cf),
                })
        except (OSError, UnicodeDecodeError):
            pass

    # 优先级 4: monorepo
    for ws_file, ws_type in [
        ("lerna.json", "lerna"),
        ("pnpm-workspace.yaml", "pnpm-workspace"),
        ("package.json", None),  # 包检查
    ]:
        ws_path = repo / ws_file
        if ws_path.exists():
            try:
                content = ws_path.read_text(encoding="utf-8", errors="replace")
                if ws_type == "lerna":
                    for m in re.finditer(r'"@?([^/"]+)/([^"]+)"', content):
                        modules.append({
                            "path": f"{m.group(1)}/{m.group(2)}",
                            "name": m.group(2),
                            "type": "lerna",
                            "priority": 4,
                        })
                elif ws_type == "pnpm-workspace":
                    for m in re.finditer(r'-\s*[\'"]?([^\'"\s]+)', content):
                        modules.append({
                            "path": m.group(1),
                            "name": m.group(1).split("/")[-1],
                            "type": "pnpm-workspace",
                            "priority": 4,
                        })
                elif ws_file == "package.json":
                    try:
                        data = json.loads(content)
                        if "workspaces" in data:
                            ws = data["workspaces"]
                            if isinstance(ws, list):
                                for p in ws:
                                    modules.append({
                                        "path": p,
                                        "name": p.split("/")[-1],
                                        "type": "yarn-workspaces",
                                        "priority": 4,
                                    })
                            elif isinstance(ws, dict) and "packages" in ws:
                                for p in ws["packages"]:
                                    modules.append({
                                        "path": p,
                                        "name": p.split("/")[-1],
                                        "type": "yarn-workspaces",
                                        "priority": 4,
                                    })
                    except json.JSONDecodeError:
                        pass
            except (OSError, UnicodeDecodeError):
                pass

    # 优先级 5: vendor 目录
    vendor_dir = repo / "vendor"
    if vendor_dir.exists() and vendor_dir.is_dir():
        for v in vendor_dir.iterdir():
            if v.is_dir():
                modules.append({
                    "path": f"vendor/{v.name}",
                    "name": v.name,
                    "type": "vendor",
                    "priority": 5,
                })

    # 优先级 1（用户配置最高优先级）：.icode_modules.yaml
    # v1.0 修复：原 P6 错位（用户配置应最权威），改 P1
    user_config = repo / ".icode_modules.yaml"
    if user_config.exists():
        try:
            content = user_config.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'-\s*path:\s*(\S+)', content):
                modules.append({
                    "path": m.group(1),
                    "name": m.group(1).split("/")[-1],
                    "type": "user-config",
                    "priority": 1,
                })
        except (OSError, UnicodeDecodeError):
            pass

    # 去重 + 限流
    seen = set()
    unique_modules = []
    for m in modules:
        key = (m.get("path"), m.get("type"))
        if key not in seen:
            seen.add(key)
            unique_modules.append(m)
            if len(unique_modules) >= max_files:
                break

    return {
        "answer": {
            "modules": unique_modules,
            "count": len(unique_modules),
            "repo_path": str(repo.resolve()),
        },
        "model": "scan_modules",
    }


# ===========================================================================
# LLM 工具（diff_summary / generate_filename / select_template）
# ===========================================================================

@mcp.tool()
async def diff_summary(
    text_a: str,
    text_b: str,
    focus: str = "",
    max_tokens: int = 1024,
) -> dict:
    """差异摘要。

    调 LLM 摘要两段文本的差异, 适合"代码变更/long log diff"场景。
    严格不接管决策: LLM 只做摘要, 不做"该不该改"的判断。

    Args:
        text_a: 旧文本
        text_b: 新文本
        focus: 摘要重点 (如"接口变更" / "配置变更" / "风险")
        max_tokens: 最大输出 token (默认 1024)

    Returns:
        成功: {answer: {summary, key_changes: [str]}, confidence, model, tokens_used, cost_estimated}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(text_a, "text_a"):
        return make_error_response(err)
    if err := validate_non_empty_str(text_b, "text_b"):
        return make_error_response(err)

    # 截断保护
    text_a_safe = truncate_text(text_a, max_chars=6000)
    text_b_safe = truncate_text(text_b, max_chars=6000)

    provider = get_provider()

    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "差异摘要"},
            "key_changes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-5 条关键变更",
            },
        },
        "required": ["summary", "key_changes"],
    }

    focus_part = f"重点关注: {focus}\n" if focus else ""
    prompt = (
        f"请对比以下两段文本, 简洁摘要差异。{focus_part}\n"
        f"输出: 1) summary (1-2 句话); 2) key_changes (3-5 条关键变更, 每条 1 句话)。\n\n"
        f"旧文本 (A):\n{text_a_safe}\n\n"
        f"新文本 (B):\n{text_b_safe}"
    )

    return await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=max_tokens,
    )


@mcp.tool()
async def generate_filename(
    context: dict,
    prefix: str = "feature",
    max_tokens: int = 256,
) -> dict:
    """文件名生成（readme / 文档存储）。

    调 LLM 根据 context 生成 1-2 行文件名。
    严格不接管决策: LLM 只生成建议, 主会话最终采纳。

    Args:
        context: 上下文字典 (e.g. {"change_type": "feature", "summary": "I2C 驱动"})
        prefix: 文件名前缀 (默认 "feature")

    Returns:
        成功: {answer: {filename: str, reason: str}, confidence, model, tokens_used, cost_estimated}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(prefix, "prefix"):
        return make_error_response(err)
    if err := validate_dict(context, "context"):
        return make_error_response(err)
    if not context:
        return make_error_response("context 不能为空 dict")

    provider = get_provider()

    context_str = json_dumps_safe(context, max_chars=2000)

    schema = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "建议文件名 (kebab-case + 时间戳)"},
            "reason": {"type": "string", "description": "1 句话理由"},
        },
        "required": ["filename"],
    }

    prompt = (
        f"请根据 context 生成 1 个文件名（用于工程文档/交付报告存储）。\n"
        f"格式: <prefix>-<kebab-case-summary>-<YYYYMMDD>.md\n"
        f"prefix: {prefix}\n"
        f"命名规则: 简短 (≤50 字符), kebab-case, 反映变更核心。\n\n"
        f"context:\n{context_str}"
    )

    return await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=max_tokens,
    )


@mcp.tool()
async def select_template(
    context: dict,
    options: list | None = None,
    max_tokens: int = 256,
) -> dict:
    """模板选择（readme / 文档）。

    调 LLM 根据 context 从 options 中选最合适的模板。
    严格不接管决策: LLM 只给建议, 主会话最终采纳。

    Args:
        context: 上下文字典
        options: 可选模板列表 (默认 ["feature", "bug", "refactor", "docs"])
        max_tokens: 最大输出 token

    Returns:
        成功: {answer: {template: str, reason: str}, confidence, model, tokens_used, cost_estimated}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_dict(context, "context"):
        return make_error_response(err)

    if options is None:
        options = ["feature", "bug", "refactor", "docs"]
    if err := validate_list(options, "options", allow_empty=False):
        return make_error_response(err)
    for opt in options:
        if not isinstance(opt, str):
            return make_error_response("options 必须都是字符串")

    provider = get_provider()

    context_str = json_dumps_safe(context, max_chars=1500)
    options_str = ", ".join(options)

    schema = {
        "type": "object",
        "properties": {
            "template": {"type": "string", "description": f"选中的模板 (必须在 {options_str} 中)"},
            "reason": {"type": "string", "description": "1 句话理由"},
        },
        "required": ["template"],
    }

    prompt = (
        f"请根据 context 从候选模板中选最合适的一个。\n"
        f"候选模板: {options_str}\n"
        f"输出: 1) template (必须从候选中选, 不允许自创); 2) reason (1 句话理由)。\n\n"
        f"context:\n{context_str}"
    )

    result = await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=max_tokens,
    )

    if "error" in result:
        return result

    # 验证 template 必须在 options 中
    selected = result.get("answer", {}).get("template", "")
    if selected not in options:
        return {
            "error": f"LLM 返了非法 template '{selected}', 不在候选 {options} 中",
            "model": result.get("model", "unknown"),
        }

    return result


if __name__ == "__main__":
    mcp.run()
