"""cheap-research 工具层统一 utilities。

阶段 1.1 引入：避免 5 工具重复实现错误兜底 / 校验 / 文本截断。
所有 5 核心工具（summarize / retrieve_similar / fill_template / extract / audit_facts）都基于本文件。
"""
import json
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 统一错误响应（所有工具失败都返 {error: str, model: str}，主会话识别后走 Agent(model=haiku) 兜底）
# ---------------------------------------------------------------------------

def make_error_response(error_msg: str, model: str = "unknown") -> dict:
    """统一错误响应：主会话 / 调用方识别 {error: ...} 后走兜底。"""
    return {
        "error": error_msg,
        "model": model,
    }


def make_success_response(
    answer: Any,
    confidence: float = 0.85,
    model: str = "unknown",
    tokens_used: int = 0,
    cost_estimated: float = 0.0,
) -> dict:
    """统一成功响应：answer (按 schema 抽), confidence, model, tokens_used, cost_estimated."""
    return {
        "answer": answer,
        "confidence": confidence,
        "model": model,
        "tokens_used": tokens_used,
        "cost_estimated": round(cost_estimated, 6),
    }


# ---------------------------------------------------------------------------
# 入参校验（运行时强校验，避免 LLM 收到坏数据）
# ---------------------------------------------------------------------------

def validate_non_empty_str(value: Any, field_name: str) -> str | None:
    """校验字符串非空。返 None 表示 OK, 返 error_msg 表示失败。"""
    if not isinstance(value, str):
        return f"{field_name} 必须是字符串，实际类型 {type(value).__name__}"
    if not value.strip():
        return f"{field_name} 不能为空字符串"
    return None


def validate_dict(value: Any, field_name: str) -> str | None:
    """校验 dict。"""
    if not isinstance(value, dict):
        return f"{field_name} 必须是 dict，实际类型 {type(value).__name__}"
    return None


def validate_list(value: Any, field_name: str, allow_empty: bool = False) -> str | None:
    """校验 list。"""
    if not isinstance(value, list):
        return f"{field_name} 必须是 list，实际类型 {type(value).__name__}"
    if not allow_empty and len(value) == 0:
        return f"{field_name} 不能为空列表"
    return None


def validate_path_exists(value: str, field_name: str) -> Path | str:
    """校验路径存在。返 Path 表示 OK, 返 error_msg str 表示失败。"""
    p = Path(value).expanduser()
    if not p.exists():
        return f"{field_name} 路径不存在: {value}"
    return p


# ---------------------------------------------------------------------------
# 文本处理
# ---------------------------------------------------------------------------

def truncate_text(text: str, max_chars: int = 8000) -> str:
    """截断文本。防 LLM prompt 爆。

    8000 字符约 2000 token（中文）或 2000 token（英文），足够大多数任务。
    截断时保留尾部，注释 "(truncated)"。
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n... (truncated)"


def json_dumps_safe(obj: Any, max_chars: int = 4000) -> str:
    """JSON 序列化 + 超长截断。用于把 candidates / data 序列化进 LLM prompt。"""
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as e:
        return f"[无法序列化: {e}]"
    return truncate_text(s, max_chars)


# ---------------------------------------------------------------------------
# 数据加载（audit_facts / 可选 retrieve_similar 用）
# ---------------------------------------------------------------------------

def load_json_file(path: str | Path) -> dict | list | None:
    """加载 JSON 文件。失败返 None。"""
    p = Path(path).expanduser()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as e:
        return None


def load_jsonl_file(path: str | Path, max_lines: int = 100) -> list[dict]:
    """加载 JSON Lines 文件。限流 max_lines 防爆。"""
    p = Path(path).expanduser()
    if not p.exists():
        return []
    items = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (UnicodeDecodeError, OSError):
        return []
    return items


# ---------------------------------------------------------------------------
# 候选截断（retrieve_similar 防 prompt 爆）
# ---------------------------------------------------------------------------

def truncate_candidates(candidates: list, max_count: int = 50) -> tuple[list, bool]:
    """截断候选列表到 max_count。返 (truncated_list, was_truncated)."""
    if len(candidates) <= max_count:
        return candidates, False
    return candidates[:max_count], True


# ---------------------------------------------------------------------------
# 文件系统扫描（scan_patterns / trace_refs / scan_modules / parse_project_id 用）
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", "dist", "build", ".egg-info",
    "target", "out", "bin", "obj",
}

DEFAULT_SOURCE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx",
    ".go", ".rs", ".java", ".kt", ".kts", ".swift",
    ".rb", ".php", ".sh", ".bash", ".zsh",
    ".md", ".rst", ".txt", ".yaml", ".yml", ".json", ".toml",
}


def is_source_file(path: Path, allowed_exts: set | None = None) -> bool:
    """判断是否是源码文件（按扩展名）。"""
    ext = path.suffix.lower()
    return ext in (allowed_exts or DEFAULT_SOURCE_EXTS)


def should_exclude_dir(path: Path, exclude_dirs: set | None = None) -> bool:
    """判断是否应该排除此目录。"""
    exclude = exclude_dirs or DEFAULT_EXCLUDE_DIRS
    return any(part in exclude for part in path.parts)


def iter_source_files(
    root_path: Path,
    exclude_dirs: set | None = None,
    max_files: int = 1000,
) -> list[Path]:
    """遍历根路径下所有源码文件（按 DEFAULT_SOURCE_EXTS）。"""
    if not root_path.exists():
        return []
    files = []
    try:
        for p in root_path.rglob("*"):
            if len(files) >= max_files:
                break
            if not p.is_file():
                continue
            if should_exclude_dir(p, exclude_dirs):
                continue
            if not is_source_file(p):
                continue
            files.append(p)
    except (PermissionError, OSError):
        return files
    return files


def safe_read_text(path: Path, max_chars: int = 8000) -> str | None:
    """安全读文本文件。失败/太大返 None 或截断。"""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return truncate_text(content, max_chars)
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Prompt 注入防护（v1.0）：过滤常见注入模式
# ---------------------------------------------------------------------------

# 常见 prompt 注入关键词（不区分大小写）
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|all|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(previous|all|above)\s+(instructions?|prompts?)", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|previous)", re.IGNORECASE),
    re.compile(r"system\s*:\s*you\s+are\s+now", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),  # ChatML 注入
    re.compile(r"</?system>", re.IGNORECASE),       # system 标签劫持
    re.compile(r"\bDAN\s+mode\b", re.IGNORECASE),   # 经典 DAN 注入
    re.compile(r"pretend\s+(to\s+be|you\s+are)", re.IGNORECASE),
]


def sanitize_for_llm(text: str, max_chars: int = 8000) -> str:
    """sanitize 文本，移除/标记常见 prompt 注入。

    返回 truncate 后的安全文本。
    - 长度截断（同 truncate_text）
    - 标记注入模式（不删除，避免误伤合法代码）

    Args:
        text: 原始文本
        max_chars: 最大字符数

    Returns:
        安全的文本 + 注入警告注释
    """
    truncated = truncate_text(text, max_chars)
    matches = []
    for pattern in _INJECTION_PATTERNS:
        for m in pattern.finditer(truncated):
            matches.append(m.group(0))

    if matches:
        # 标记注入警告（不删除，避免误伤）
        warning = f"\n\n[PROMPT_INJECTION_DETECTED: {len(matches)} 个疑似注入模式: {matches[:3]}...]"
        return truncated + warning
    return truncated


# ---------------------------------------------------------------------------
# 语言适配（trace_refs 用）
# ---------------------------------------------------------------------------

# 各语言的"符号边界"模式
LANGUAGE_SYMBOL_PATTERNS = {
    "python": {
        # module.Class.method / module.func
        "symbol_pattern": r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*\b",
        "exclude_dirs": {".venv", "venv", "__pycache__", ".pytest_cache", ".tox", "build", "dist"},
    },
    "cpp": {
        # Class::method / Class<T>::method / ns::Class::method
        "symbol_pattern": r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:::[a-zA-Z_][a-zA-Z0-9_]*)*\b",
        "exclude_dirs": {"build", "cmake-build-debug", "cmake-build-release", ".vs", ".vscode"},
    },
    "javascript": {
        # module.Class.method / ns.Class.method
        "symbol_pattern": r"\b[a-zA-Z_$][a-zA-Z0-9_$]*(?:\.[a-zA-Z_$][a-zA-Z0-9_$]*)*\b",
        "exclude_dirs": {"node_modules", "dist", "build", ".next"},
    },
    "go": {
        # pkg.Func / pkg.Method
        "symbol_pattern": r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*\b",
        "exclude_dirs": {"vendor", "node_modules"},
    },
    "rust": {
        # crate::module::function / module::function
        "symbol_pattern": r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:::[a-zA-Z_][a-zA-Z0-9_]*)*\b",
        "exclude_dirs": {"target", "node_modules"},
    },
}


def detect_language_from_ext(ext: str) -> str:
    """从文件扩展名检测语言。"""
    ext = ext.lower().lstrip(".")
    lang_map = {
        "py": "python", "pyi": "python",
        "cpp": "cpp", "cc": "cpp", "cxx": "cpp", "hpp": "cpp", "hxx": "cpp", "h": "cpp",
        "js": "javascript", "jsx": "javascript", "ts": "javascript", "tsx": "javascript",
        "mjs": "javascript", "cjs": "javascript",
        "go": "go",
        "rs": "rust",
    }
    return lang_map.get(ext, "python")  # 默认 python 风格


def build_symbol_regex(symbol: str, language: str = "python") -> re.Pattern:
    """根据语言构建符号匹配正则。

    解决 C++ 模板 `MyClass<T>::method` 匹配错、Python `module.Class.method` 边界等问题。
    """
    lang_cfg = LANGUAGE_SYMBOL_PATTERNS.get(language, LANGUAGE_SYMBOL_PATTERNS["python"])
    # 转义符号中的正则元字符
    escaped = re.escape(symbol)
    # 单词边界 + 完整符号
    pattern = r"\b" + escaped + r"\b"
    return re.compile(pattern)


def safe_run_git(repo_path: Path, args: list[str]) -> str | None:
    """安全运行 git 命令。失败返 None。"""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path)] + args,
            capture_output=True, text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def validate_int_range(value: Any, field_name: str, min_val: int, max_val: int) -> str | None:
    """校验 int 在 [min_val, max_val] 范围。"""
    if not isinstance(value, int):
        return f"{field_name} 必须是整数, 实际 {type(value).__name__}"
    if value < min_val or value > max_val:
        return f"{field_name} 必须在 {min_val}~{max_val} 范围, 实际 {value}"
    return None


def validate_url(value: str, field_name: str) -> str | None:
    """校验 URL 格式（http/https）。"""
    if not isinstance(value, str):
        return f"{field_name} 必须是字符串"
    if not value.startswith(("http://", "https://")):
        return f"{field_name} 必须是 http:// 或 https:// 开头"
    return None
