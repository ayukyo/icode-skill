"""cheap-research MCP server 入口。

配置来源: $CHEAP_RESEARCH_CONFIG 指向的 JSON 文件 (默认 ./config.json)。
session 模型只通过 mcp 工具调用, 不直连 LLM API。

启动: python server.py

5 核心工具（LLM 推理）：
  - summarize            长上下文压缩
  - retrieve_similar     历史工单相似度匹配
  - fill_template        模板填充
  - extract              结构化提取
  - propose_repo_facts   仓库事实候选（原 audit_facts，只产候选不做裁决）

10 增强工具（含 7 工具型 + 3 LLM 摘要）：
  - describe_capabilities
  - scan_patterns / trace_refs / diff_summary / validate_migration_ops
  - fetch_remote / generate_filename / select_template
  - parse_project_id / scan_modules

工具分三类 capability（`tools_manifest.json` 为真源）：
  - local: 纯本地确定性工具（describe_capabilities/scan_patterns/trace_refs/validate_migration_ops/parse_project_id/scan_modules）
  - fetch: 网络工具（fetch_remote）
  - llm:   依赖 LLM provider（summarize/retrieve_similar/fill_template/extract/propose_repo_facts/diff_summary/generate_filename/select_template）
  本地/网络工具不因 provider 未配置而整体降级；LLM 工具必须 provider 可用。

所有工具严格遵循单闸门: 价值 ≥ 3 ★ + 低风险 = 入选。
不接管决策: 3 质疑者对抗 / 架构决策 / 终审裁决 / 修复方案一律不走本工具。

数据出境闸门（v1.1）：LLM 类工具外发前必须 scan_sensitive，
命中高危敏感模式（私钥/证书/AWS 密钥/键值型密钥）→ 阻断并提示脱敏。
"""
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urljoin, urlparse, urlunparse

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
from mcp.types import ToolAnnotations  # noqa: E402

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
    truncate_with_meta,
    truncate_candidates,
    json_dumps_safe,
    iter_source_files,
    safe_read_text,
    safe_run_git,
    sanitize_for_llm,
    scan_sensitive,
    detect_language_from_ext,
    build_symbol_regex,
    DEFAULT_SOURCE_EXTS,
    DEFAULT_EXCLUDE_DIRS,
)
from providers._logger import log_call  # noqa: E402

# fetch_remote 配置
FETCH_MAX_BYTES = 5 * 1024 * 1024  # 5MB 响应上限
FETCH_TIMEOUT_SECONDS = 10  # 10s 超时（防 30s 卡住会话）
FETCH_MAX_REDIRECTS = 5
SCHEMA_MAX_CHARS = 16000

LOCAL_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
REMOTE_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
LLM_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


class ToolResponse(TypedDict, total=False):
    """所有 MCP 工具共用的最小输出合同。具体 ``answer`` 由各工具定义。

    ⚠️ 字段必须全部可接受 None（`str | None` / `int | None`…）：FastMCP
    ``structured_output=True`` 时会从本 TypedDict 生成 outputSchema,并把
    返回 dict 中缺失的字段补成 ``null`` 再做 jsonschema 校验——若某字段声明
    为非空 `str`/`int`/`bool`,stdio 层会报
    ``Output validation error: None is not of type '...'`` 且该工具全部调用失败
    (2026-09-12 实机发现,c9b47f2 引入 structured_output 后 cheap-research
    全工具在真实 stdio 下不可用)。缺省字段置 None 是预期行为,不表示错误。
    """

    answer: Any
    error_code: str | None
    error: str | None
    model: str | None
    confidence: float | None
    tokens_used: int | None
    cost_estimated: float | None
    truncation: Any
    candidates_truncated: bool | None
    confidence_note: str | None
    cost_note: str | None


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


def _resolve_safe_target(url: str) -> tuple[list[str], str | None]:
    """解析并校验 URL，返回本次请求必须使用的公网 IP 快照。

    拒绝：
    - loopback (127.0.0.0/8, ::1)
    - 私网 (10/8, 172.16/12, 192.168/16, fc00::/7)
    - link-local (169.254/16, fe80::/10) — AWS / GCP metadata 入口
    - 阿里云 metadata (100.100.100.200)
    - 主机名解析失败

    接受：公网 IP（防止内网探测 / 元数据读取）

    调用方必须直接连接返回的 IP，不能再次用 hostname 建连；否则会在
    DNS 预检与实际连接之间留下 rebinding TOCTOU 窗口。

    Returns: (可连接 IP 列表, error_msg)。error_msg 为 None 表示校验成功。
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return [], f"仅允许 http/https, 实际 {parsed.scheme}"
        hostname = parsed.hostname
        if not hostname:
            return [], "URL 缺少 hostname"
        if parsed.username is not None or parsed.password is not None:
            return [], "URL 不允许内嵌用户名或密码"
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            return [], f"URL 端口无效: {exc}"

        try:
            infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return [], f"DNS 解析失败: {hostname}"

        safe_ips: list[str] = []
        for info in infos:
            sockaddr = info[4]
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if _is_private_or_dangerous_ip(ip):
                return [], (
                    f"URL 指向内网/危险 IP {ip_str}（{ip.__class__.__name__}），"
                    "已拒绝（SSRF 防护）"
                )
            normalized = str(ip)
            if normalized not in safe_ips:
                safe_ips.append(normalized)

        if not safe_ips:
            return [], f"DNS 未返回可用 IP: {hostname}"
        return safe_ips, None
    except Exception as e:
        return [], f"URL 解析失败: {e}"


def _validate_url_safe(url: str) -> str | None:
    """兼容校验入口；真实 fetch 必须使用 ``_resolve_safe_target`` 的 IP。"""
    _, error = _resolve_safe_target(url)
    return error


def _build_pinned_request(url: str, ip: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    """把原 URL 改写为已校验 IP，同时保留 HTTP Host 与 HTTPS SNI。"""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    ascii_hostname = hostname.encode("idna").decode("ascii")
    port = parsed.port
    ip_netloc = f"[{ip}]" if ":" in ip else ip
    if port is not None:
        ip_netloc = f"{ip_netloc}:{port}"
    host_header = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    if port is not None:
        host_header = f"{host_header}:{port}"
    pinned_url = urlunparse(
        (parsed.scheme, ip_netloc, parsed.path or "/", parsed.params, parsed.query, "")
    )
    extensions: dict[str, Any] = {}
    if parsed.scheme == "https":
        extensions["sni_hostname"] = ascii_hostname
    return pinned_url, {"Host": host_header, "User-Agent": "cheap-research/1.1"}, extensions

PROVIDERS = {
    "openai_compat": OpenAICompatProvider,
    "local_ollama": LocalOllamaProvider,
}

_PROVIDER_CACHE_KEY: str | None = None
_PROVIDER_CACHE_INSTANCE = None


async def _close_provider(provider: Any) -> None:
    close = getattr(provider, "aclose", None)
    if callable(close):
        await close()


@asynccontextmanager
async def _server_lifespan(_server: FastMCP):
    """确保复用的 provider HTTP 连接池在 MCP 退出时关闭。"""
    try:
        yield {}
    finally:
        await _close_provider(_PROVIDER_CACHE_INSTANCE)


mcp = FastMCP("cheap-research", lifespan=_server_lifespan)


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
    return json.loads(p.read_text(encoding="utf-8"))


def _configured_allowed_roots() -> tuple[list[Path], str | None]:
    """读取可选本地目录白名单；未配置时保持既有的不限制行为。

    环境变量 ``CHEAP_RESEARCH_ALLOWED_ROOTS`` 优先，使用 os.pathsep 分隔。
    配置存在但格式非法时 fail closed，避免用户以为已启用边界而实际失效。
    """
    env_value = os.environ.get("CHEAP_RESEARCH_ALLOWED_ROOTS")
    if env_value is not None:
        raw_roots: object = [item for item in env_value.split(os.pathsep) if item]
    else:
        cfg_path = Path(os.environ.get(
            "CHEAP_RESEARCH_CONFIG",
            str(_SERVER_DIR / "config.json"),
        ))
        if not cfg_path.exists():
            return [], None
        try:
            raw_roots = json.loads(cfg_path.read_text(encoding="utf-8")).get("allowed_roots", [])
        except (OSError, json.JSONDecodeError) as exc:
            return [], f"读取 allowed_roots 失败: {exc}"

    if not isinstance(raw_roots, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_roots
    ):
        return [], "allowed_roots 必须是非空路径字符串列表"

    roots: list[Path] = []
    for item in raw_roots:
        try:
            root = Path(item).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            return [], f"allowed_roots 路径不可用: {item}: {exc}"
        if not root.is_dir():
            return [], f"allowed_roots 只允许目录: {item}"
        roots.append(root)
    return roots, None


def _validate_local_dir(value: str, field_name: str) -> Path | str:
    """校验本地目录，并在配置白名单时限制到允许根目录。"""
    result = validate_path_exists(value, field_name)
    if isinstance(result, str):
        return result
    try:
        resolved = result.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return f"{field_name} 路径不可解析: {value}: {exc}"
    if not resolved.is_dir():
        return f"{field_name} 必须是目录: {value}"

    roots, roots_error = _configured_allowed_roots()
    if roots_error:
        return roots_error
    if roots and not any(resolved.is_relative_to(root) for root in roots):
        return (
            f"{field_name} 不在 allowed_roots 白名单内: {resolved}; "
            f"允许范围: {[str(root) for root in roots]}"
        )
    return resolved


def _provider_profile() -> dict:
    """返回不含凭据、且不把“已配置”误报为“已验证可用”的 provider 画像。"""
    cfg_path = Path(os.environ.get(
        "CHEAP_RESEARCH_CONFIG",
        str(_SERVER_DIR / "config.json"),
    ))
    if not cfg_path.exists():
        return {
            "provider": "unconfigured",
            "model": "",
            "configured": False,
            "readiness": "unconfigured",
            "runtime_probe_performed": False,
        }
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "provider": "invalid_config",
            "model": "",
            "configured": False,
            "readiness": "invalid_config",
            "runtime_probe_performed": False,
            "error": str(exc),
        }

    name = str(cfg.get("provider", "openai_compat")).lower()
    required = ["model"]
    if name == "openai_compat":
        required.extend(["base_url", "api_key"])
    missing = [key for key in required if not cfg.get(key)]
    known = name in PROVIDERS
    configured = known and not missing
    return {
        "provider": name,
        "model": str(cfg.get("model", "")),
        "configured": configured,
        "readiness": "configured_unverified" if configured else "unconfigured",
        "runtime_probe_performed": False,
        "missing_fields": missing,
        "known_provider": known,
    }


def _replace_cached_provider(cache_key: str, provider: Any) -> None:
    """替换 provider 缓存，并异步回收旧连接池。"""
    global _PROVIDER_CACHE_KEY, _PROVIDER_CACHE_INSTANCE
    old_provider = _PROVIDER_CACHE_INSTANCE
    _PROVIDER_CACHE_KEY = cache_key
    _PROVIDER_CACHE_INSTANCE = provider
    if old_provider is None or old_provider is provider:
        return
    try:
        asyncio.get_running_loop().create_task(_close_provider(old_provider))
    except RuntimeError:
        # 正常 MCP 工具调用总在事件循环内；同步导入/测试路径由 lifespan 兜底。
        pass


def get_provider():
    """返回 provider 实例。openai_compat 缺字段时返 UnconfiguredProvider (不抛错)。"""
    global _PROVIDER_CACHE_KEY, _PROVIDER_CACHE_INSTANCE
    try:
        cfg = load_config()
    except FileNotFoundError:
        return UnconfiguredProvider(missing=["config.json"])
    except (OSError, json.JSONDecodeError) as exc:
        return UnconfiguredProvider(missing=[f"有效 config.json ({exc})"])
    name = cfg.get("provider", "openai_compat").lower()
    cache_payload = json.dumps(cfg, ensure_ascii=False, sort_keys=True)
    cache_key = hashlib.sha256(cache_payload.encode("utf-8")).hexdigest()
    if cache_key == _PROVIDER_CACHE_KEY and _PROVIDER_CACHE_INSTANCE is not None:
        return _PROVIDER_CACHE_INSTANCE

    if name == "openai_compat":
        missing = [k for k in ("base_url", "api_key", "model") if not cfg.get(k)]
        if missing:
            provider = UnconfiguredProvider(missing=missing)
            _replace_cached_provider(cache_key, provider)
            return provider
    elif name == "local_ollama" and not cfg.get("model"):
        provider = UnconfiguredProvider(missing=["model"])
        _replace_cached_provider(cache_key, provider)
        return provider
    cls = PROVIDERS.get(name)
    if not cls:
        return UnconfiguredProvider(
            missing=[f"有效 provider（当前 {name!r}；可选 {list(PROVIDERS)}）"]
        )
    provider = cls(cfg)
    _replace_cached_provider(cache_key, provider)
    return provider


# ===========================================================================
# 5 核心工具
# ===========================================================================

@mcp.tool(annotations=LLM_TOOL_ANNOTATIONS, structured_output=True)
async def summarize(
    text: str,
    max_tokens: int = 512,
    focus: str = "",
) -> ToolResponse:
    """长上下文压缩。

    把传入的文本交给便宜 LLM 做摘要, 返回结构化结果。
    严格不接管决策/对抗/架构: 推理类工作一律不走本工具。

    Args:
        text: 待压缩文本 (支持中英文, 自动截断到 8000 字符; 截断时返回 meta.truncated=true)
        max_tokens: 最大输出 token 数 (默认 512)
        focus: 可选聚焦角度 (如"异常根因" / "改动点" / "风险"), 为空时让 LLM 自己判

    Returns:
        成功: {answer: {summary, key_points}, confidence, model, tokens_used, cost_estimated,
               truncation: {truncated, source_chars, consumed_chars, source_digest, ...}}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(text, "text"):
        return make_error_response(err)

    # 数据出境闸门：外发前扫描敏感内容
    if sensitive := scan_sensitive(text + focus):
        return make_error_response(
            f"[数据出境闸门] 输入命中高危敏感模式, 已拒绝外发: {sensitive[:3]}。"
            f"请先脱敏（排除密钥/令牌/私钥）后重试。"
        )

    provider = get_provider()

    # 文本截断（防 prompt 爆）+ 截断硬信号
    text_safe, trunc_meta = truncate_with_meta(text, max_chars=8000)
    text_safe = sanitize_for_llm(text_safe, max_chars=8500)

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

    focus_safe = sanitize_for_llm(focus, max_chars=1000) if focus else ""
    focus_part = f"重点关注: {focus_safe}\n" if focus_safe else ""
    prompt = (
        f"请对以下文本做摘要压缩。{focus_part}"
        f"输出要求: 1) summary (简洁摘要, 保留核心信息); "
        f"2) key_points (3-5 条关键点列表, 每条 1 句话)。\n\n"
        f"原文:\n{text_safe}"
    )

    result = await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=max_tokens,
    )
    if "error" not in result:
        result["truncation"] = trunc_meta
    return result


@mcp.tool(annotations=LLM_TOOL_ANNOTATIONS, structured_output=True)
async def retrieve_similar(
    query: str,
    candidates: list,
    k: int = 5,
) -> ToolResponse:
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

    # 数据出境闸门：外发前扫描 query 与候选
    if sensitive := scan_sensitive(query + json_dumps_safe(candidates, max_chars=100000)):
        return make_error_response(
            f"[数据出境闸门] 输入命中高危敏感模式, 已拒绝外发: {sensitive[:3]}。"
            f"请先脱敏（排除密钥/令牌/私钥）后重试。"
        )

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

    query_safe = sanitize_for_llm(query, max_chars=2000)
    candidates_str = sanitize_for_llm(
        json_dumps_safe(candidates_compact, max_chars=6000),
        max_chars=6500,
    )
    prompt = (
        f"请对以下候选工单按 query 评分 (0~1, 越高越相似), 并返回 top-{k}。\n"
        f"评分标准: 与 query 语义匹配度、关键词重合度。\n\n"
        f"query: {query_safe}\n\n"
        f"候选 ({len(candidates_compact)} 条"
        f"{', 已截断' if was_truncated else ''}):\n"
        f"{candidates_str}\n\n"
        f"只返 top-{k}, 按 score 降序。"
    )

    result = await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=8192,
    )

    if "error" in result:
        return result

    # 限流到 k
    items = result.get("answer", {}).get("items", [])
    if isinstance(items, list):
        items = items[:k]
        result["answer"] = {"items": items, "query": query}
    result["candidates_truncated"] = was_truncated

    return result


@mcp.tool(annotations=LLM_TOOL_ANNOTATIONS, structured_output=True)
async def fill_template(
    template: str,
    data: dict,
) -> ToolResponse:
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

    # 数据出境闸门：外发前扫描模板与数据
    if sensitive := scan_sensitive(
        template + json_dumps_safe(data, max_chars=100000)
    ):
        return make_error_response(
            f"[数据出境闸门] 模板/数据命中高危敏感模式, 已拒绝外发: {sensitive[:3]}。"
            f"请先脱敏（排除密钥/令牌/私钥）后重试。"
        )

    provider = get_provider()

    # 模板截断（防 prompt 爆）+ 截断硬信号
    template_safe, trunc_meta = truncate_with_meta(template, max_chars=4000)
    template_safe = sanitize_for_llm(template_safe, max_chars=4500)
    data_str = sanitize_for_llm(
        json_dumps_safe(data, max_chars=2000),
        max_chars=2500,
    )

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

    result = await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=8192,
    )
    if "error" not in result:
        result["truncation"] = trunc_meta
    return result


@mcp.tool(annotations=LLM_TOOL_ANNOTATIONS, structured_output=True)
async def extract(
    text: str,
    schema: dict,
    instruction: str = "",
) -> ToolResponse:
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
    try:
        schema_payload = json.dumps(schema, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        return make_error_response(f"schema 必须可 JSON 序列化: {exc}")
    if len(schema_payload) > SCHEMA_MAX_CHARS:
        return make_error_response(
            f"schema 过大: {len(schema_payload)} 字符，最大 {SCHEMA_MAX_CHARS}"
        )

    # 数据出境闸门：外发前扫描敏感内容
    outbound_contract = text + instruction + schema_payload
    if sensitive := scan_sensitive(outbound_contract):
        return make_error_response(
            f"[数据出境闸门] 输入命中高危敏感模式, 已拒绝外发: {sensitive[:3]}。"
            f"请先脱敏（排除密钥/令牌/私钥）后重试。"
        )

    provider = get_provider()

    text_safe, trunc_meta = truncate_with_meta(text, max_chars=8000)
    text_safe = sanitize_for_llm(text_safe, max_chars=8500)
    instruction_safe = sanitize_for_llm(instruction, max_chars=1000) if instruction else ""
    instruction_part = f"\n附加指令: {instruction_safe}" if instruction_safe else ""

    # 复用 openai_compat.py 的 schema 强约束逻辑
    prompt = (
        f"请从以下文本中按 JSON schema 提取字段。{instruction_part}\n\n"
        f"文本:\n{text_safe}"
    )

    result = await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=8192,
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
        result["truncation"] = trunc_meta

    return result


@mcp.tool(annotations=LLM_TOOL_ANNOTATIONS, structured_output=True)
async def propose_repo_facts(
    repo_path: str,
    focus: str = "",
    max_files: int = 10,
) -> ToolResponse:
    """仓库事实候选（不接管裁决）。

    扫描 repo_path 下的关键文件 (README / CLAUDE.md / pyproject.toml / package.json / 入口 main.*),
    让 LLM 生成**候选事实** (用途、依赖、入口、关键 API)。

    权限边界（v1.1 改名自 audit_facts）:
      - 只读少量文件片段, 无法证明完整调用链/代码行为/测试覆盖/边界条件;
      - 输出必须标 `candidate=true`, 每条事实不保证准确;
      - 主模型必须用 Read/rg 实证后才可写入 plan / log_analysis / audit 等正式产物。

    Args:
        repo_path: 仓库路径 (本地绝对路径)
        focus: 审计重点 (如"对外 API" / "依赖关系" / "测试覆盖"), 默认通用
        max_files: 最多扫描文件数 (默认 10, 防超大 repo)

    Returns:
        成功: {answer: {candidate, facts: [str], source_files: [str], focus},
               confidence, model, tokens_used, cost_estimated, truncation}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(repo_path, "repo_path"):
        return make_error_response(err)
    if not isinstance(max_files, int) or max_files < 1 or max_files > 100:
        return make_error_response(f"max_files 必须是 1~100 的整数, 实际 {max_files}")

    p = _validate_local_dir(repo_path, "repo_path")
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
        if candidate.exists() and candidate.is_file() and not candidate.is_symlink():
            source_files.append(str(candidate))
            if len(source_files) >= max_files:
                break

    if not source_files:
        return make_error_response(
            f"未在 {repo_path} 找到关键文件 (README/CLAUDE.md/入口文件等), "
            f"无法审计。"
        )

    # 读取文件内容（每个文件截断到 1500 字符，v1.0 修复：原 2000 → 1500 防 prompt 爆）
    # v1.1：逐文件记录截断硬信号，合并后同样记录，返回 truncation 汇总
    file_contents = []
    file_truncations = []
    for f in source_files:
        content = safe_read_text(Path(f), max_chars=100000, root_path=Path(repo_path))
        if content is None:
            continue
        content_safe, f_meta = truncate_with_meta(content, max_chars=1500)
        file_contents.append(f"=== {f} ===\n{content_safe}")
        file_truncations.append({
            "file": f,
            **f_meta,
        })

    combined_raw = "\n\n".join(file_contents)
    # 数据出境闸门：外发前扫描全部已读源码片段
    if sensitive := scan_sensitive(combined_raw + focus):
        return make_error_response(
            f"[数据出境闸门] 仓库内容命中高危敏感模式, 已拒绝外发: {sensitive[:3]}。"
            f"请先脱敏（排除密钥/令牌/私钥）后重试。"
        )
    combined, trunc_meta = truncate_with_meta(combined_raw, max_chars=8000)  # v1.0 修复：原 15000 → 8000
    combined = sanitize_for_llm(combined, max_chars=8500)
    trunc_meta["per_file"] = file_truncations

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

    focus_safe = sanitize_for_llm(focus, max_chars=1000) if focus else ""
    focus_part = f"审计重点: {focus_safe}\n" if focus_safe else ""
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
        max_tokens=8192,
    )

    if "error" in result:
        return result

    # 附加 source_files / 候选标记 / 截断汇总到 answer
    if isinstance(result.get("answer"), dict):
        result["answer"]["source_files"] = source_files
        result["answer"]["focus"] = focus
        result["answer"]["candidate"] = True
        result["truncation"] = trunc_meta

    return result


# ===========================================================================
# 10 增强工具
# ===========================================================================

@mcp.tool(annotations=LOCAL_TOOL_ANNOTATIONS, structured_output=True)
async def describe_capabilities() -> ToolResponse:
    """返回不含密钥的工具清单、能力边界和当前配置画像。

    这是会话级发现接口，不执行 LLM 或网络探测。``configured`` 仅表示
    配置字段齐全，真实可调用性仍以首次实际工具调用为准。
    """
    try:
        manifest = json.loads((_SERVER_DIR / "tools_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "error_code": "manifest_invalid",
            "error": f"读取 tools_manifest.json 失败: {exc}",
            "model": "describe_capabilities",
        }

    tools = manifest.get("tools", [])
    counts = {
        capability: sum(1 for item in tools if item.get("capability") == capability)
        for capability in ("local", "fetch", "llm")
    }
    roots, roots_error = _configured_allowed_roots()
    return {
        "answer": {
            "schema_version": 1,
            "tool_count": len(tools),
            "capability_counts": counts,
            "tools": tools,
            "provider": _provider_profile(),
            "filesystem_boundary": {
                "mode": "allowlist" if roots else "caller_selected",
                "allowed_roots": [str(root) for root in roots],
                "config_error": roots_error,
                "symlink_files_rejected": True,
            },
            "network_boundary": {
                "public_http_only": True,
                "redirects_revalidated": True,
                "max_redirects": FETCH_MAX_REDIRECTS,
                "max_response_bytes": FETCH_MAX_BYTES,
            },
            "data_scope_boundary": manifest.get("data_scope_boundary", {}),
            "decision_boundary": (
                "只做压缩、导航、提取、候选生成和机械校验；"
                "不承担架构裁决、修复方案、视觉判断或硬件正确性结论"
            ),
        },
        "model": "describe_capabilities",
    }


@mcp.tool(annotations=LOCAL_TOOL_ANNOTATIONS, structured_output=True)
async def scan_patterns(
    patterns: list,
    scope_path: str = ".",
    exclude_dirs: list | None = None,
    max_files: int = 1000,
    max_matches: int = 200,
) -> ToolResponse:
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

    p = _validate_local_dir(scope_path, "scope_path")
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
        content = safe_read_text(f, max_chars=100000, root_path=scope)
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
            "files_with_matches": len(files_with_matches),
            "truncated": False,
        },
        "model": "scan_patterns",
    }


@mcp.tool(annotations=LOCAL_TOOL_ANNOTATIONS, structured_output=True)
async def trace_refs(
    symbol: str,
    scope_path: str = ".",
    max_files: int = 500,
    max_refs: int = 100,
) -> ToolResponse:
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

    p = _validate_local_dir(scope_path, "scope_path")
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
        content = safe_read_text(f, max_chars=100000, root_path=scope)
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


async def _fetch_pinned_hop(
    url: str,
    safe_ips: list[str],
) -> tuple[int, dict[str, str], bytes, bool]:
    """对一个 URL hop 使用已校验 IP 建连；hostname 只用于 Host/SNI。"""
    import httpx

    last_error: httpx.RequestError | None = None
    for ip in safe_ips:
        pinned_url, headers, extensions = _build_pinned_request(url, ip)
        try:
            # 每个 IP 使用独立连接池，避免不同 hostname 共用同一 IP 时复用错
            # TLS 会话；禁用环境代理，防止请求绕过已固定的目标地址。
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "GET",
                    pinned_url,
                    headers=headers,
                    extensions=extensions,
                ) as response:
                    if response.status_code not in {301, 302, 303, 307, 308}:
                        response.raise_for_status()

                    content_bytes = bytearray()
                    truncated_by_size = False
                    if response.status_code not in {301, 302, 303, 307, 308}:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            remaining = FETCH_MAX_BYTES - len(content_bytes)
                            if len(chunk) > remaining:
                                content_bytes.extend(chunk[:remaining])
                                truncated_by_size = True
                                break
                            content_bytes.extend(chunk)
                    return (
                        response.status_code,
                        dict(response.headers),
                        bytes(content_bytes),
                        truncated_by_size,
                    )
        except httpx.RequestError as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise last_error
    raise httpx.ConnectError(f"没有可连接的已校验 IP: {url}")


@mcp.tool(annotations=REMOTE_TOOL_ANNOTATIONS, structured_output=True)
async def fetch_remote(
    url: str,
    max_chars: int = 5000,
) -> ToolResponse:
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

    # SSRF 防护：解析结果同时作为实际连接目标，消除二次 DNS 解析窗口。
    safe_ips, ssrf_err = _resolve_safe_target(url)
    if ssrf_err:
        log_call("fetch_remote", "error", url=url, error="ssrf_blocked")
        return {
            "error_code": "ssrf_blocked",
            "error": ssrf_err,
            "model": "fetch_remote",
        }

    import httpx
    try:
        current_url = url
        redirects: list[str] = []
        current_ips = safe_ips
        for redirect_count in range(FETCH_MAX_REDIRECTS + 1):
            status_code, headers, content_bytes, truncated_by_size = (
                await _fetch_pinned_hop(current_url, current_ips)
            )
            if status_code in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    return {
                        "error_code": "redirect_invalid",
                        "error": f"HTTP {status_code} 缺少 Location: {current_url}",
                        "model": "fetch_remote",
                    }
                if redirect_count >= FETCH_MAX_REDIRECTS:
                    return {
                        "error_code": "redirect_limit",
                        "error": f"重定向超过 {FETCH_MAX_REDIRECTS} 次: {url}",
                        "model": "fetch_remote",
                    }
                next_url = urljoin(current_url, location)
                next_ips, redirect_err = _resolve_safe_target(next_url)
                if redirect_err:
                    log_call(
                        "fetch_remote", "error", url=next_url,
                        error="redirect_ssrf_blocked",
                    )
                    return {
                        "error_code": "ssrf_blocked",
                        "error": f"重定向目标被拒绝: {redirect_err}",
                        "model": "fetch_remote",
                    }
                redirects.append(next_url)
                current_url = next_url
                current_ips = next_ips
                continue

            content_digest = hashlib.sha256(content_bytes).hexdigest()
            content = content_bytes.decode("utf-8", errors="replace")
            truncated_by_chars = False
            if len(content) > max_chars:
                content = truncate_text(content, max_chars)
                truncated_by_chars = True

            log_call(
                "fetch_remote",
                "success",
                url=url,
                final_url=current_url,
                status_code=status_code,
                bytes=len(content_bytes),
                redirect_count=len(redirects),
                truncated_size=truncated_by_size,
                truncated_chars=truncated_by_chars,
            )
            return {
                "answer": {
                    "content": content,
                    "status_code": status_code,
                    "content_type": headers.get("content-type", ""),
                    "content_length": headers.get("content-length", ""),
                    "source_digest": f"sha256:{content_digest}",
                    "digest_scope": (
                        "downloaded_prefix" if truncated_by_size else "full_response"
                    ),
                    "requested_url": url,
                    "final_url": current_url,
                    "redirects": redirects,
                    "etag": headers.get("etag", ""),
                    "last_modified": headers.get("last-modified", ""),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "truncated": truncated_by_size or truncated_by_chars,
                    "truncated_by_size": truncated_by_size,
                    "truncated_by_chars": truncated_by_chars,
                    "trust_level": "untrusted",
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


@mcp.tool(annotations=LOCAL_TOOL_ANNOTATIONS, structured_output=True)
async def validate_migration_ops(
    schema_diff: dict,
    repo_path: str = ".",
) -> ToolResponse:
    """迁移 ops 校验与规范化（v1.1 改名自 apply_migration）。

    只校验并规范化**调用者已经给出**的 schema_diff, 转成 pending ops。
    不发现 schema 差异, 不决定应该迁移什么——「迁移方案决定」是主模型 + 用户权限范畴。

    严格不直接改文件: 返 ops 给主会话审核 + 执行, 避免误操作。
    remove/rename 类 op 仍需主模型和用户权限检查后才能执行。

    Args:
        schema_diff: 调用者给出的 schema 变更描述, e.g.
                     {
                       "add": [{"path": "src/foo.py", "template": "..."}],
                       "remove": [{"path": "src/old.py"}],
                       "modify": [{"path": "src/main.py", "changes": "..."}]
                     }
        repo_path: 仓库路径 (默认 ".")

    Returns:
        成功: {answer: {ops: [{type, target, content}], files_affected: [str]}, model: "validate_migration_ops"}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_dict(schema_diff, "schema_diff"):
        return make_error_response(err)

    p = _validate_local_dir(repo_path, "repo_path")
    if isinstance(p, str):
        return make_error_response(p)
    repo = p

    # 支持的 op 类型
    allowed_ops = {"add", "remove", "rename", "modify"}
    ops = []
    files_affected = set()
    repo_resolved = repo.resolve()

    def normalize_target(value: object, op_type: str, field: str) -> Path | str:
        if not isinstance(value, str) or not value.strip():
            return f"op '{op_type}' 缺少 {field} 字段"
        try:
            path = (repo_resolved / value).resolve()
            if not path.is_relative_to(repo_resolved):
                return f"op '{op_type}' {field} 路径逃逸（不在 repo 内）: {value}"
            if path == repo_resolved:
                return f"op '{op_type}' {field} 不能指向 repo 根: {value}"
            return path
        except (OSError, RuntimeError, TypeError, ValueError):
            return f"op '{op_type}' {field} 路径无效: {value}"

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

            target_path = normalize_target(target, op_type, "target")
            if isinstance(target_path, str):
                return make_error_response(target_path)

            normalized = {
                "type": op_type,
                "target": str(target_path),
                "content": item.get("content") or item.get("changes") or item.get("template") or "",
                "status": "pending",
            }
            if op_type == "rename":
                destination = item.get("destination") or item.get("dst") or item.get("to")
                destination_path = normalize_target(destination, op_type, "destination")
                if isinstance(destination_path, str):
                    return make_error_response(destination_path)
                if destination_path == target_path:
                    return make_error_response("op 'rename' 的 target 与 destination 不能相同")
                normalized["destination"] = str(destination_path)
                files_affected.add(str(destination_path))
            ops.append(normalized)
            files_affected.add(str(target_path))

    return {
        "answer": {
            "ops": ops,
            "files_affected": sorted(files_affected),
            "op_count": len(ops),
            "repo_path": str(repo),
            "warning": "ops 已校验并规范化, 未执行 —— 主会话需审核后手动执行",
        },
        "model": "validate_migration_ops",
    }


@mcp.tool(annotations=LOCAL_TOOL_ANNOTATIONS, structured_output=True)
async def parse_project_id(
    repo_path: str = ".",
) -> ToolResponse:
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

    p = _validate_local_dir(repo_path, "repo_path")
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
    is_submodule = False
    submodule_path = None
    try:
        allowed_roots, roots_error = _configured_allowed_roots()
        if roots_error:
            return make_error_response(roots_error)
        # 检查每个父目录是否有 .gitmodules
        current = repo.parent
        while current != current.parent:
            if allowed_roots and not any(
                current.is_relative_to(root) for root in allowed_roots
            ):
                break
            gitmodules = current / ".gitmodules"
            if gitmodules.exists():
                # 找到 .gitmodules 上级目录（submodule 顶层）
                rel_path = repo.resolve().relative_to(current.resolve())
                # 检查 .gitmodules 是否包含此路径
                try:
                    gm_content = safe_read_text(gitmodules, max_chars=100000)
                    if gm_content is None:
                        current = current.parent
                        continue
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


@mcp.tool(annotations=LOCAL_TOOL_ANNOTATIONS, structured_output=True)
async def scan_modules(
    repo_path: str = ".",
    max_files: int = 1000,
) -> ToolResponse:
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

    p = _validate_local_dir(repo_path, "repo_path")
    if isinstance(p, str):
        return make_error_response(p)
    repo = p

    modules = []

    # 优先级 1: git submodule
    gitmodules = repo / ".gitmodules"
    if gitmodules.exists():
        try:
            content = safe_read_text(gitmodules, max_chars=100000, root_path=repo)
            if content is None:
                content = ""
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
    if repo_dir.exists() and repo_dir.is_dir() and not repo_dir.is_symlink():
        manifest = repo_dir / "manifest.xml"
        if manifest.exists():
            try:
                content = safe_read_text(manifest, max_chars=100000, root_path=repo)
                if content is None:
                    content = ""
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
        if cm.is_symlink() or "node_modules" in str(cm) or ".venv" in str(cm):
            continue
        if len(cmake_files) >= 10:
            break
        cmake_files.append(cm)
    for cf in cmake_files:
        try:
            content = safe_read_text(cf, max_chars=20000, root_path=repo)
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
                content = safe_read_text(ws_path, max_chars=100000, root_path=repo)
                if content is None:
                    continue
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
    if vendor_dir.exists() and vendor_dir.is_dir() and not vendor_dir.is_symlink():
        for v in vendor_dir.iterdir():
            if v.is_dir() and not v.is_symlink():
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
            content = safe_read_text(user_config, max_chars=100000, root_path=repo)
            if content is None:
                content = ""
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

@mcp.tool(annotations=LLM_TOOL_ANNOTATIONS, structured_output=True)
async def diff_summary(
    text_a: str,
    text_b: str,
    focus: str = "",
    max_tokens: int = 1024,
) -> ToolResponse:
    """差异摘要。

    调 LLM 摘要两段文本的差异, 适合"代码变更/long log diff"场景。
    严格不接管决策: LLM 只做摘要, 不做"该不该改"的判断。
    只作索引/导航, 不得替代 diff/逐项验收。

    Args:
        text_a: 旧文本
        text_b: 新文本
        focus: 摘要重点 (如"接口变更" / "配置变更" / "风险")
        max_tokens: 最大输出 token (默认 1024)

    Returns:
        成功: {answer: {summary, key_changes: [str]}, confidence, model, tokens_used, cost_estimated, truncation}
        失败: {error: str, model: str}
    """
    # 入参校验
    if err := validate_non_empty_str(text_a, "text_a"):
        return make_error_response(err)
    if err := validate_non_empty_str(text_b, "text_b"):
        return make_error_response(err)

    # 数据出境闸门：外发前扫描两侧文本
    if sensitive := scan_sensitive(text_a + text_b + focus):
        return make_error_response(
            f"[数据出境闸门] 输入命中高危敏感模式, 已拒绝外发: {sensitive[:3]}。"
            f"请先脱敏（排除密钥/令牌/私钥）后重试。"
        )

    # 截断保护 + 截断硬信号
    text_a_safe, trunc_a = truncate_with_meta(text_a, max_chars=6000)
    text_b_safe, trunc_b = truncate_with_meta(text_b, max_chars=6000)
    text_a_safe = sanitize_for_llm(text_a_safe, max_chars=6500)
    text_b_safe = sanitize_for_llm(text_b_safe, max_chars=6500)

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

    focus_safe = sanitize_for_llm(focus, max_chars=1000) if focus else ""
    focus_part = f"重点关注: {focus_safe}\n" if focus_safe else ""
    prompt = (
        f"请对比以下两段文本, 简洁摘要差异。{focus_part}\n"
        f"输出: 1) summary (1-2 句话); 2) key_changes (3-5 条关键变更, 每条 1 句话)。\n\n"
        f"旧文本 (A):\n{text_a_safe}\n\n"
        f"新文本 (B):\n{text_b_safe}"
    )

    result = await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=max_tokens,
    )
    if "error" not in result:
        result["truncation"] = {"text_a": trunc_a, "text_b": trunc_b}
    return result


@mcp.tool(annotations=LLM_TOOL_ANNOTATIONS, structured_output=True)
async def generate_filename(
    context: dict,
    prefix: str = "feature",
    max_tokens: int = 8192,
) -> ToolResponse:
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

    # 数据出境闸门：外发前扫描 context
    if sensitive := scan_sensitive(
        json_dumps_safe(context, max_chars=100000) + prefix
    ):
        return make_error_response(
            f"[数据出境闸门] context 命中高危敏感模式, 已拒绝外发: {sensitive[:3]}。"
            f"请先脱敏后重试。"
        )

    provider = get_provider()

    context_str = sanitize_for_llm(
        json_dumps_safe(context, max_chars=2000),
        max_chars=2500,
    )

    schema = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "建议文件名 (kebab-case + 时间戳)"},
            "reason": {"type": "string", "description": "1 句话理由"},
        },
        "required": ["filename"],
    }

    prefix_safe = sanitize_for_llm(prefix, max_chars=200)
    prompt = (
        f"请根据 context 生成 1 个文件名（用于工程文档/交付报告存储）。\n"
        f"格式: <prefix>-<kebab-case-summary>-<YYYYMMDD>.md\n"
        f"prefix: {prefix_safe}\n"
        f"命名规则: 简短 (≤50 字符), kebab-case, 反映变更核心。\n\n"
        f"context:\n{context_str}"
    )

    return await provider.invoke(
        prompt=prompt,
        schema=schema,
        max_tokens=max_tokens,
    )


@mcp.tool(annotations=LLM_TOOL_ANNOTATIONS, structured_output=True)
async def select_template(
    context: dict,
    options: list | None = None,
    max_tokens: int = 8192,
) -> ToolResponse:
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

    # 数据出境闸门：外发前扫描 context
    if sensitive := scan_sensitive(
        json_dumps_safe(context, max_chars=100000)
        + json_dumps_safe(options or [], max_chars=100000)
    ):
        return make_error_response(
            f"[数据出境闸门] context 命中高危敏感模式, 已拒绝外发: {sensitive[:3]}。"
            f"请先脱敏后重试。"
        )

    provider = get_provider()

    context_str = sanitize_for_llm(
        json_dumps_safe(context, max_chars=1500),
        max_chars=2000,
    )
    options_str = sanitize_for_llm(", ".join(options), max_chars=2000)

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
